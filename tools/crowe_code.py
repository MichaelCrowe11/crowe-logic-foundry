"""Crowe Code editor-block bridge.

Lets the agent read, write, and run **Crowe Code blocks** — the UUID-addressed
editor blocks in Crowe Terminal (a Wave Terminal fork). A Crowe Code block is a
``crowecode``-view block whose editable text lives in a backing file named by
its ``crowecode:file`` meta key; the editor loads that file on mount and Cmd+S
writes it back.

Runtime model (CLI-in-terminal mode):
  When ``crowe-logic`` is launched from a shell **inside** a Crowe Terminal
  block, the process inherits ``WAVETERM_JWT`` and ``wsh`` is on PATH. That lets
  us resolve any block UUID to its backing file via the wsh RPC bridge:

    * ``wsh getmeta -b <uuid> --raw crowecode:file`` -> backing file path
    * ``wsh setmeta -b <uuid> crowecode:file=<path>`` -> bind/create a backing file
    * ``wsh blocks list --json``                      -> enumerate blocks

  Outside a Crowe Terminal block (no JWT, or no wsh) the tools are not
  registered and the system-prompt addendum is empty — nothing is promised
  that can't be delivered.

This is disjoint from ``tools/crowe_terminal.py``: that module is the AI-panel
bridge (``ct_*`` HTTP tools, gated on ``CROWE_AGENT_TOOLS=1``). This one is the
CLI-in-terminal bridge (``wsh`` RPC, gated on ``WAVETERM_JWT``).
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Set

from tools.shell import execute_shell

# Where auto-bound scratch buffers land when the caller gives no file_path.
BUFFER_DIR = Path("~/.crowe-logic/crowecode").expanduser()

# Accepts a UUID, an ORef ("block:<uuid>"), a block number, or "this".
_BLOCK_ARG_RE = re.compile(r"^[A-Za-z0-9:_-]{1,100}$")

# File extension -> interpreter for crowe_code_run_block.
_INTERPRETERS = {
    ".py": "python3",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "zsh",
    ".js": "node",
    ".mjs": "node",
    ".cjs": "node",
    ".rb": "ruby",
    ".pl": "perl",
    ".php": "php",
    ".lua": "lua",
    ".r": "Rscript",
}

_TOOL_NAMES = (
    "crowe_code_read_block",
    "crowe_code_write_block",
    "crowe_code_run_block",
    "crowe_code_list_blocks",
    "crowe_code_current_block",
)


class _WshError(Exception):
    """A wsh CLI invocation returned a non-zero exit code."""


# ---------------------------------------------------------------------------
# Runtime detection + wsh seam
# ---------------------------------------------------------------------------


def _wsh_path() -> Optional[str]:
    """Locate the wsh CLI: PATH first, then known Crowe/Wave install dirs."""
    found = shutil.which("wsh")
    if found:
        return found
    for cand in (
        Path("~/.crowe-terminal/bin/wsh").expanduser(),
        Path("~/.waveterm/bin/wsh").expanduser(),
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def in_crowe_terminal() -> bool:
    """True when running inside a Crowe Terminal block with wsh reachable.

    Requires ``WAVETERM_JWT`` (set by the host block for RPC auth) and a
    resolvable ``wsh`` binary. Both are necessary — but NOT sufficient: stock
    Wave Terminal sets the identical ``WAVETERM_*`` env and ships the same
    ``wsh``. Use :func:`_crowe_code_capable` to confirm the runtime can
    actually serve Crowe Code block work before advertising the tools.
    """
    return bool(os.environ.get("WAVETERM_JWT")) and _wsh_path() is not None


# Cache the one-shot capability probe: it shells out to wsh, so run it at most
# once per process. ``None`` = not yet probed; ``True``/``False`` = result.
_CAPABILITY_CACHE: Optional[bool] = None


def _crowe_code_capable() -> bool:
    """Confirm the runtime can actually serve Crowe Code block work.

    ``in_crowe_terminal()`` only proves we're in *a* Wave-family block with a
    reachable wsh. Stock Wave Terminal passes that check too, yet has no Crowe
    Code (``crowecode``) view and (in some builds) an empty workspace registry,
    so the bridge's wsh calls fail with ``no workspaces found``. This probe
    exercises wsh once to tell a Crowe-Code-capable runtime from stock Wave.

    The result is cached in :data:`_CAPABILITY_CACHE` so wsh is invoked at most
    once per process. Callers gate ``register()`` / ``system_prompt()`` on this
    so the tools never advertise themselves where they cannot work.

    :return: True only when the bridge's wsh operations can succeed.
    :rtype: bool
    """
    global _CAPABILITY_CACHE
    if _CAPABILITY_CACHE is not None:
        return _CAPABILITY_CACHE

    try:
        rc, out, _err = _run_wsh(["blocks", "list", "--json"])
    except _WshError:
        _CAPABILITY_CACHE = False
        return _CAPABILITY_CACHE

    if rc != 0:
        _CAPABILITY_CACHE = False
        return _CAPABILITY_CACHE

    try:
        blocks = json.loads(out or "[]")
    except json.JSONDecodeError:
        _CAPABILITY_CACHE = False
        return _CAPABILITY_CACHE

    _CAPABILITY_CACHE = isinstance(blocks, list)
    return _CAPABILITY_CACHE


def _run_wsh(args: List[str], timeout: int = 10):
    """Invoke wsh with an argv list (no shell). Returns (rc, stdout, stderr)."""
    wsh = _wsh_path()
    if not wsh:
        raise _WshError("wsh CLI not found on PATH")
    try:
        proc = subprocess.run(
            [wsh, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ},
        )
    except subprocess.TimeoutExpired:
        sub = args[0] if args else ""
        raise _WshError(
            f"wsh {sub} timed out after {timeout}s — the Crowe Terminal RPC "
            "bridge is not responding"
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise _WshError(f"wsh became unavailable mid-call: {exc}")
    return proc.returncode, proc.stdout, proc.stderr


def _getmeta_value(block_uuid: str, key: str) -> str:
    """Return a single meta value for a block, or "" if unset/null."""
    rc, out, err = _run_wsh(["getmeta", "-b", block_uuid, "--raw", key])
    if rc != 0:
        raise _WshError((err or out or "wsh getmeta failed").strip())
    val = out.strip()
    # A bare `null` (or empty) means the key is unset. Note: a backing file
    # literally named "null" is not distinguishable here and is unsupported.
    if val in ("", "null"):
        return ""
    # Defensive: --raw unquotes string values, but if a wsh build emits the
    # value JSON-encoded (surrounding quotes), unwrap it so paths stay valid.
    if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
        try:
            decoded = json.loads(val)
            if isinstance(decoded, str):
                val = decoded
        except Exception:  # noqa: BLE001
            pass
    return val


def _setmeta(block_uuid: str, key: str, value: str) -> None:
    rc, out, err = _run_wsh(["setmeta", "-b", block_uuid, f"{key}={value}"])
    if rc != 0:
        raise _WshError((err or out or "wsh setmeta failed").strip())


def _resolve_block_file(block_uuid: str) -> str:
    raw = _getmeta_value(block_uuid, "crowecode:file")
    return os.path.expanduser(raw) if raw else ""


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _require_terminal() -> Optional[str]:
    if not in_crowe_terminal():
        return json.dumps(
            {
                "error": "Not running inside a Crowe Terminal block (no WAVETERM_JWT "
                "or wsh unavailable). Crowe Code block tools only work when "
                "crowe-logic is launched from a shell inside Crowe Terminal.",
            }
        )
    return None


def _require_block_id(block_uuid: str) -> Optional[str]:
    if not block_uuid or not _BLOCK_ARG_RE.match(block_uuid.strip()):
        return json.dumps(
            {
                "error": f"Invalid block id {block_uuid!r}. Expected a block UUID "
                "(e.g. d03e0bea-a89a-4b3b-94b0-1b6680664b2c), a block "
                "number, or 'this'.",
            }
        )
    return None


def _scratch_payload(block_uuid: str) -> str:
    return json.dumps(
        {
            "block": block_uuid,
            "backed_by_file": False,
            "message": "This Crowe Code block is a scratch buffer — it has no "
            "crowecode:file backing file, so its text lives only in the "
            "editor and cannot be read externally. Bind a file with "
            "crowe_code_write_block(block_uuid, content, file_path=...) — that "
            "is the reliable fix. (A bare Cmd+S on a buffer that has no file "
            "yet does not bind one; the user would have to Save-As it to a "
            "path.)",
        }
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def crowe_code_read_block(block_uuid: str) -> str:
    """Read the contents of a Crowe Code editor block by its UUID.

    Resolves the block's crowecode:file backing file and returns its text. If
    the block is a scratch buffer (no backing file), returns a message
    explaining the content is not externally readable.

    :param block_uuid: The Crowe Code block UUID (or 'this' / a block number).
    :return: JSON with block, file, language, and content — or an error/notice.
    :rtype: str
    """
    guard = _require_terminal() or _require_block_id(block_uuid)
    if guard:
        return guard
    block_uuid = block_uuid.strip()
    try:
        file_path = _resolve_block_file(block_uuid)
    except _WshError as exc:
        return json.dumps({"error": f"Could not resolve block {block_uuid}: {exc}"})
    if not file_path:
        return _scratch_payload(block_uuid)
    path = Path(file_path)
    if not path.exists():
        return json.dumps(
            {
                "error": f"Block {block_uuid} points to {file_path}, but that file "
                "does not exist yet.",
                "block": block_uuid,
                "file": file_path,
            }
        )
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc), "block": block_uuid, "file": file_path})
    language = ""
    try:
        language = _getmeta_value(block_uuid, "crowecode:language")
    except _WshError:
        pass
    return json.dumps(
        {
            "block": block_uuid,
            "backed_by_file": True,
            "file": str(path),
            "language": language or _language_from_ext(path),
            "content": content,
        }
    )


def crowe_code_write_block(
    block_uuid: str,
    content: str = "",
    content_b64: str = "",
    file_path: str = "",
) -> str:
    """Write text into a Crowe Code editor block by its UUID.

    Writes to the block's existing crowecode:file backing file. If the block is
    a scratch buffer with no backing file, a file is bound first: at file_path
    if given, otherwise an auto-named file under ~/.crowe-logic/crowecode. The
    open editor live-reloads a clean buffer; if it has unsaved edits the editor
    guards them (your write is on disk, the user keeps their edits).

    For payloads whose characters break JSON escaping (triple quotes,
    backslashes), pass content_b64 (base64-encoded UTF-8) instead of content.

    :param block_uuid: The Crowe Code block UUID (or 'this' / a block number).
    :param content: The full text to write (plain string).
    :param content_b64: Base64-encoded UTF-8 alternative to content.
    :param file_path: Backing file to bind when the block has none yet.
    :return: JSON with block, file, bytes written, and bound_new_file flag.
    :rtype: str
    """
    guard = _require_terminal() or _require_block_id(block_uuid)
    if guard:
        return guard
    block_uuid = block_uuid.strip()

    if not content and content_b64:
        try:
            content = base64.b64decode(content_b64).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Invalid content_b64: {exc}"})

    try:
        existing = _resolve_block_file(block_uuid)
    except _WshError as exc:
        return json.dumps({"error": f"Could not resolve block {block_uuid}: {exc}"})

    bound_new_file = False
    if existing:
        target = Path(existing)
    else:
        if file_path:
            target = Path(os.path.expanduser(file_path))
        else:
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", block_uuid)
            target = BUFFER_DIR / f"{safe}.txt"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            _setmeta(block_uuid, "crowecode:file", str(target))
        except _WshError as exc:
            return json.dumps(
                {"error": f"Wrote {target} but failed to bind it to the block: {exc}"}
            )
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)})
        bound_new_file = True
        return json.dumps(
            {
                "block": block_uuid,
                "file": str(target),
                "bytes": len(content.encode("utf-8")),
                "bound_new_file": True,
                "note": "Bound a new backing file and wrote it. The editor will load "
                "it on next focus.",
            }
        )

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc), "block": block_uuid, "file": str(target)})
    return json.dumps(
        {
            "block": block_uuid,
            "file": str(target),
            "bytes": len(content.encode("utf-8")),
            "bound_new_file": bound_new_file,
            "note": "Wrote the block's backing file. A clean editor buffer reloads "
            "automatically; unsaved edits are guarded.",
        }
    )


def crowe_code_run_block(block_uuid: str, interpreter: str = "", args: str = "") -> str:
    """Execute the backing file of a Crowe Code block and return its output.

    Resolves the block's crowecode:file, picks an interpreter from the file
    extension (override with interpreter), and runs it in the local sandbox,
    capturing stdout/stderr/exit code. The block must have a backing file
    (scratch buffers cannot be run).

    :param block_uuid: The Crowe Code block UUID (or 'this' / a block number).
    :param interpreter: Command to run the file with (e.g. 'python3', 'bash',
                        '.venv/bin/python'). Inferred from extension if empty.
    :param args: Extra arguments appended after the file path.
    :return: JSON with block, file, command, and the captured run result.
    :rtype: str
    """
    guard = _require_terminal() or _require_block_id(block_uuid)
    if guard:
        return guard
    block_uuid = block_uuid.strip()
    try:
        file_path = _resolve_block_file(block_uuid)
    except _WshError as exc:
        return json.dumps({"error": f"Could not resolve block {block_uuid}: {exc}"})
    if not file_path:
        return json.dumps(
            {
                "error": "This Crowe Code block has no backing file, so there is "
                "nothing to run. Write it first with crowe_code_write_block "
                "using a file_path that has a runnable extension.",
                "block": block_uuid,
            }
        )
    path = Path(file_path)
    if not path.exists():
        return json.dumps(
            {
                "error": f"Backing file {file_path} does not exist.",
                "block": block_uuid,
                "file": file_path,
            }
        )

    interp = interpreter.strip() or _INTERPRETERS.get(path.suffix.lower(), "")
    if not interp:
        return json.dumps(
            {
                "error": f"Don't know how to run a {path.suffix or '(no extension)'} "
                "file — pass an explicit interpreter (e.g. interpreter="
                "'python3').",
                "block": block_uuid,
                "file": file_path,
            }
        )

    # Build the command from individually shell-quoted tokens so a
    # model-supplied interpreter/args string cannot inject shell operators
    # (the file path alone being quoted is not enough — execute_shell runs
    # with shell=True).
    try:
        interp_tokens = shlex.split(interp)
        arg_tokens = shlex.split(args) if args.strip() else []
    except ValueError as exc:
        return json.dumps(
            {
                "error": f"Could not parse interpreter/args (unbalanced quotes?): {exc}",
                "block": block_uuid,
            }
        )
    if not interp_tokens:
        return json.dumps({"error": "Empty interpreter.", "block": block_uuid})
    command = " ".join(shlex.quote(t) for t in (*interp_tokens, str(path), *arg_tokens))

    raw = execute_shell(command, working_directory=str(path.parent))
    try:
        result = json.loads(raw)
    except Exception:  # noqa: BLE001
        result = {"raw": raw}
    return json.dumps(
        {
            "block": block_uuid,
            "file": str(path),
            "interpreter": interp,
            "command": command,
            "result": result,
        }
    )


def crowe_code_list_blocks() -> str:
    """List the Crowe Code editor blocks open in this Crowe Terminal.

    Use this when the user refers to "the block" / "this block" without a UUID:
    if exactly one Crowe Code block exists, that's the one; otherwise show the
    list and ask which.

    :return: JSON with count and a list of {block, file, language, tab, workspace}.
    :rtype: str
    """
    guard = _require_terminal()
    if guard:
        return guard
    try:
        rc, out, err = _run_wsh(["blocks", "list", "--json"])
    except _WshError as exc:
        return json.dumps({"error": str(exc)})
    if rc != 0:
        return json.dumps({"error": (err or out or "wsh blocks list failed").strip()})
    try:
        blocks = json.loads(out or "[]")
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Could not parse wsh output: {exc}"})

    out_blocks = []
    for b in blocks:
        meta = b.get("meta") or {}
        if b.get("view") != "crowecode" and meta.get("view") != "crowecode":
            continue
        out_blocks.append(
            {
                "block": b.get("blockid"),
                "file": meta.get("crowecode:file", ""),
                "language": meta.get("crowecode:language", ""),
                "tab": b.get("tabid", ""),
                "workspace": b.get("workspaceid", ""),
            }
        )
    return json.dumps({"count": len(out_blocks), "blocks": out_blocks})


def crowe_code_current_block() -> str:
    """Resolve the Crowe Code block the user is currently looking at, no UUID needed.

    Use this when the user says "this block" / "the block" / "run the block I'm
    looking at" without pasting a UUID. Returns the resolved block so you can
    pass its `block` to read/write/run. Resolution order:
      1. the CROWE_CODE_ACTIVE_BLOCK env var (the active-editor handshake, when
         Crowe Terminal provides it);
      2. the single Crowe Code block in the current tab (WAVETERM_TABID);
      3. the single Crowe Code block open anywhere;
      otherwise it returns the candidate list so you can ask the user which one.

    :return: JSON with the resolved {block, source, file, language} or, when
             ambiguous/none, an error plus candidates.
    :rtype: str
    """
    guard = _require_terminal()
    if guard:
        return guard

    env_block = os.environ.get("CROWE_CODE_ACTIVE_BLOCK", "").strip()
    if env_block:
        return json.dumps({"block": env_block, "source": "active-editor"})

    try:
        rc, out, err = _run_wsh(["blocks", "list", "--json"])
    except _WshError as exc:
        return json.dumps({"error": str(exc), "hint": "Pass an explicit block UUID."})
    if rc != 0:
        return json.dumps(
            {
                "error": (err or out or "wsh blocks list failed").strip(),
                "hint": "Pass an explicit block UUID, or open exactly one Crowe Code block.",
            }
        )
    try:
        blocks = json.loads(out or "[]")
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Could not parse wsh output: {exc}"})

    cc_blocks = [
        b
        for b in blocks
        if b.get("view") == "crowecode"
        or (b.get("meta") or {}).get("view") == "crowecode"
    ]
    if not cc_blocks:
        return json.dumps({"error": "No Crowe Code blocks are open.", "count": 0})

    cur_tab = os.environ.get("WAVETERM_TABID", "").strip()
    in_tab = [b for b in cc_blocks if b.get("tabid") == cur_tab] if cur_tab else []

    chosen, source = None, ""
    if len(in_tab) == 1:
        chosen, source = in_tab[0], "current-tab"
    elif len(cc_blocks) == 1:
        chosen, source = cc_blocks[0], "only-open"

    if chosen is not None:
        meta = chosen.get("meta") or {}
        return json.dumps(
            {
                "block": chosen.get("blockid"),
                "source": source,
                "file": meta.get("crowecode:file", ""),
                "language": meta.get("crowecode:language", ""),
            }
        )

    candidates = [
        {
            "block": b.get("blockid"),
            "file": (b.get("meta") or {}).get("crowecode:file", ""),
            "tab": b.get("tabid", ""),
        }
        for b in cc_blocks
    ]
    return json.dumps(
        {
            "error": "Multiple Crowe Code blocks are open — ask the user which one.",
            "count": len(candidates),
            "candidates": candidates,
        }
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _language_from_ext(path: Path) -> str:
    return {
        ".py": "python",
        ".sh": "shell",
        ".js": "javascript",
        ".ts": "typescript",
        ".rb": "ruby",
        ".go": "go",
        ".rs": "rust",
        ".md": "markdown",
        ".json": "json",
    }.get(path.suffix.lower(), "")


# ---------------------------------------------------------------------------
# Registration + system-prompt addendum
# ---------------------------------------------------------------------------


def register(target: Set) -> List[str]:
    """Add the Crowe Code tools to a user-functions set when in a terminal block.

    No-op (returns []) when not running inside a Crowe Terminal block, so the
    tools never appear in contexts where they can't work.

    :param target: The set of user-facing tool functions to mutate in place.
    :return: The names of the tools that were registered.
    :rtype: list
    """
    if not (in_crowe_terminal() and _crowe_code_capable()):
        return []
    for fn in (
        crowe_code_read_block,
        crowe_code_write_block,
        crowe_code_run_block,
        crowe_code_list_blocks,
        crowe_code_current_block,
    ):
        target.add(fn)
    return list(_TOOL_NAMES)


SYSTEM_PROMPT_ADDENDUM = """\
## Crowe Code blocks — runtime context

You are running inside a Crowe Terminal block. Crowe Terminal has **Crowe Code
blocks**: UUID-addressed editor panes (a `crowecode` view). Each block's text
lives in a backing file named by its `crowecode:file` meta; the editor loads
that file on mount and Cmd+S saves it.

**A bare UUID the user hands you (e.g. `d03e0bea-a89a-4b3b-94b0-1b6680664b2c`)
is a Crowe Code block reference — never treat it as opaque text and never ask
"what does this mean".** Resolve and act on it with these tools:

| The user wants to...                       | Call |
|--------------------------------------------|------|
| See / read what's in a block               | `crowe_code_read_block(block_uuid)` |
| Put code or text into a block              | `crowe_code_write_block(block_uuid, content=...)` |
| **Run the workflow / script in a block**   | `crowe_code_run_block(block_uuid)` |
| Run / read "the block I'm looking at"      | `crowe_code_current_block()` first, then act on its `block` |
| Find which blocks are open                 | `crowe_code_list_blocks()` |

Operating rules:

1. **"Run the block as I watch"** means `crowe_code_run_block(block_uuid)` — it
   executes the block's backing file and returns stdout/stderr/exit code. Report
   the result; don't re-implement the script elsewhere.
2. **"This block" / "the block" / "the block I'm looking at" with no UUID** ->
   call `crowe_code_current_block()`. It resolves the active editor (or the only
   open Crowe Code block) and returns its `block`; pass that to read/run/write.
   If it returns candidates (several open), show them and ask which.
3. **Scratch buffers** (a block with no `crowecode:file`) have no externally
   readable text. To make one readable/runnable, `crowe_code_write_block` it with
   a `file_path` (e.g. ending in `.py`) — that binds a backing file.
4. **Don't fall back to write_file/execute_shell for block work.** These tools
   keep you operating on the user's actual visible block. They are registered
   and working in this session — the `wsh` bridge they use is present, so do
   NOT treat them as optional even if other guidance calls `wsh` an optional
   helper.
5. **If `ct_*` (Crowe Terminal AI-panel) tools are also present**, they and
   `crowe_code_*` are not interchangeable: `crowe_code_*` act on `crowecode`
   editor blocks (the UUIDs the user pastes); `ct_*` act on terminal/web blocks.
   A pasted UUID that resolves via `crowe_code_*` is a Crowe Code block — use
   these tools for it.
"""


def system_prompt() -> str:
    """Return the Crowe Code system-prompt addendum, or "" when unavailable.

    :return: The addendum string when inside a Crowe Terminal block, else "".
    :rtype: str
    """
    if not (in_crowe_terminal() and _crowe_code_capable()):
        return ""
    return SYSTEM_PROMPT_ADDENDUM
