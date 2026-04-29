"""Crowe Terminal remote tool bridge.

Discovers tools advertised by the Crowe Terminal control plane
(`http://127.0.0.1:8012/v1/tools`) and registers each one as a Foundry
tool whose execution proxies back to the terminal over HTTP.

Activation:
  * `CROWE_AGENT_TOOLS=1` — turn on
  * `CROWE_AGENT_HOST` / `CROWE_AGENT_PORT` — override default 127.0.0.1:8012
  * `WAVETERM_AUTH_KEY` — bearer for X-AuthKey (Crowe Terminal sets this
    when it spawns the bridge)

Two-stage registration so the model actually sees the tools:

  1. Each remote tool gets an exec()-built proxy function with REAL named
     parameters (from the upstream JSON schema). build_tool_schemas in
     providers/_shared.py uses inspect.signature, so generic **kwargs
     wrappers wouldn't surface any params.

  2. The proxy is added to `tools.user_functions` (the catalog the
     providers actually load) AND to `tools.registry._registry` (legacy
     compatibility). The cache in providers/_shared.py keyed on
     id(user_functions) is busted by mutating in place BEFORE the first
     load_tools() call — which is fine because we run at import time.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import httpx
except ImportError:
    httpx = None

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8012
DEFAULT_TIMEOUT = 60.0
ENABLE_ENV = "CROWE_AGENT_TOOLS"
HOST_ENV = "CROWE_AGENT_HOST"
PORT_ENV = "CROWE_AGENT_PORT"
AUTH_ENV = "WAVETERM_AUTH_KEY"

# Track the proxy fns we created so we can drop them again on test resets.
_REGISTERED_PROXIES: List[Callable] = []


def _base_url() -> str:
    host = os.environ.get(HOST_ENV) or DEFAULT_HOST
    port = int(os.environ.get(PORT_ENV) or DEFAULT_PORT)
    return f"http://{host}:{port}"


def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(AUTH_ENV)
    if key:
        headers["X-AuthKey"] = key
    return headers


def _fetch_catalog() -> Optional[List[Dict[str, Any]]]:
    if httpx is None:
        logger.warning("[crowe-terminal-tools] httpx not installed; skipping")
        return None
    try:
        with httpx.Client(timeout=2.0) as client:
            r = client.get(f"{_base_url()}/v1/tools", headers=_headers())
            if r.status_code != 200:
                logger.info("[crowe-terminal-tools] catalog fetch returned %s", r.status_code)
                return None
            data = r.json()
            return data.get("tools") or []
    except Exception as exc:  # noqa: BLE001 - terminal often not running, that's fine
        logger.debug("[crowe-terminal-tools] catalog probe failed: %s", exc)
        return None


def _call_remote(tool_name: str, kwargs: Dict[str, Any]) -> str:
    """POST a tool call back to Crowe Terminal and return the result as a string."""
    if httpx is None:
        return json.dumps({"error": "httpx not installed in foundry env"})
    body = {"name": tool_name, "arguments": {k: v for k, v in kwargs.items() if v is not None}}
    try:
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            r = client.post(
                f"{_base_url()}/v1/call",
                headers=_headers(),
                json=body,
            )
        if r.status_code != 200:
            return json.dumps({
                "error": f"crowe-terminal call failed: {r.status_code}",
                "body": r.text[:512],
            })
        payload = r.json()
        if payload.get("iserror"):
            return json.dumps({
                "error": payload.get("errortext") or "tool returned error",
                "tool": tool_name,
            })
        content = payload.get("content")
        if isinstance(content, (dict, list)):
            return json.dumps(content)
        if content is None:
            return json.dumps({"ok": True, "pending": payload.get("pending", False)})
        return str(content)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"crowe-terminal proxy failed: {exc}"})


def _python_name(tool_name: str) -> str:
    return "ct_" + tool_name.replace(".", "_")


# JSON-schema → Python type-annotation mapping for the generated source.
# Defaults are conservative; build_tool_schemas falls back to "string" for
# unknown annotations anyway.
_TYPE_MAP = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}


def _build_proxy_source(
    tool_name: str,
    py_name: str,
    description: str,
    schema: Dict[str, Any],
) -> Tuple[str, List[str]]:
    """Generate Python source for a proxy function with real signature.

    Built line-by-line (no textwrap.dedent) so multi-line tool descriptions
    can't accidentally break source indentation. The docstring is written
    as a single concatenated string with explicit newlines escaped.
    """
    props: Dict[str, Any] = (schema or {}).get("properties") or {}
    required: List[str] = (schema or {}).get("required") or []

    sig_parts: List[str] = []
    doc_param_lines: List[str] = []

    # Required first (no default), then optional (default=None).
    ordered = sorted(props.keys(), key=lambda k: (k not in required, k))
    for pname in ordered:
        pdef = props.get(pname) or {}
        ptype = _TYPE_MAP.get(pdef.get("type"), "str")
        pdesc = " ".join(str(pdef.get("description") or "").split()).strip()
        if pname in required:
            sig_parts.append(f"{pname}: {ptype}")
        else:
            sig_parts.append(f"{pname}: {ptype} = None")
        if pdesc:
            doc_param_lines.append(f":param {pname}: {pdesc}")

    sig = ", ".join(sig_parts)
    doc_first = " ".join(description.split()).strip()
    if not doc_first:
        doc_first = f"Crowe Terminal tool: {tool_name}"
    if doc_param_lines:
        doc = doc_first + " " + " ".join(doc_param_lines)
    else:
        doc = doc_first
    # Use repr() so any quotes / special chars in the doc are properly escaped
    # and stay on a single line in the source.
    doc_repr = repr(doc)

    # Build kwargs dict explicitly so we don't pass through None defaults.
    if ordered:
        kw_dict_expr = "{" + ", ".join(f'"{k}": {k}' for k in ordered) + "}"
    else:
        kw_dict_expr = "{}"

    lines = [
        f"def {py_name}({sig}) -> str:",
        f"    {doc_repr}",
        f"    return _call_remote({tool_name!r}, {kw_dict_expr})",
        "",
    ]
    return "\n".join(lines), ordered


def _make_proxy(catalog_entry: Dict[str, Any]) -> Optional[Callable]:
    fn_meta = catalog_entry.get("function") or {}
    name = fn_meta.get("name")
    if not name:
        return None
    description = fn_meta.get("description") or f"Crowe Terminal tool: {name}"
    schema = fn_meta.get("parameters") or {"type": "object"}
    mutating = bool(catalog_entry.get("x_mutating"))
    if mutating:
        description = description + " [mutating; user confirmation required]"

    py_name = _python_name(name)
    src, _ = _build_proxy_source(name, py_name, description, schema)

    namespace: Dict[str, Any] = {"_call_remote": _call_remote}
    try:
        exec(src, namespace)  # noqa: S102 - we generate the source ourselves
    except SyntaxError as exc:
        logger.error("[crowe-terminal-tools] failed to compile proxy %s: %s", py_name, exc)
        return None
    return namespace[py_name]


def _add_to_user_functions(fn: Callable) -> None:
    """Add a function to tools.user_functions and bust the schema cache."""
    try:
        import tools as _tools_pkg
        from providers import _shared as _shared_pkg
    except ImportError:
        return
    uf = getattr(_tools_pkg, "user_functions", None)
    if uf is None:
        return
    uf.add(fn)
    # Bust the memoized schema cache so the new tool is picked up on the
    # next load_tools() call. Identity-keyed cache, so we just clear it.
    cache = getattr(_shared_pkg, "_TOOL_CACHE", None)
    if isinstance(cache, dict):
        cache.clear()


def _add_to_legacy_registry(fn: Callable, name: str, description: str, schema: Dict[str, Any], mutating: bool) -> None:
    try:
        from tools.registry import ToolMeta, get_registry
    except ImportError:
        return
    meta = ToolMeta(
        name=fn.__name__,
        fn=fn,
        category="crowe-terminal",
        description=description,
        parameters=schema,
        returns="JSON string with tool result.",
        is_async=False,
        tags={"crowe-terminal", "remote", "mutating"} if mutating else {"crowe-terminal", "remote"},
    )
    registry = get_registry()
    registry._tools[fn.__name__] = meta  # noqa: SLF001
    registry._by_category.setdefault("crowe-terminal", set()).add(fn.__name__)
    for tag in meta.tags:
        registry._by_tag.setdefault(tag, set()).add(fn.__name__)


def discover_and_register() -> List[str]:
    """Probe Crowe Terminal and register every advertised tool. Returns names."""
    if os.environ.get(ENABLE_ENV) != "1":
        return []
    catalog = _fetch_catalog()
    if not catalog:
        return []
    registered: List[str] = []
    for entry in catalog:
        fn = _make_proxy(entry)
        if fn is None:
            continue
        fn_meta = entry.get("function") or {}
        _add_to_user_functions(fn)
        _add_to_legacy_registry(
            fn,
            fn.__name__,
            (fn_meta.get("description") or "") + (" [mutating]" if entry.get("x_mutating") else ""),
            fn_meta.get("parameters") or {"type": "object"},
            bool(entry.get("x_mutating")),
        )
        _REGISTERED_PROXIES.append(fn)
        registered.append(fn.__name__)
    if registered:
        logger.info(
            "[crowe-terminal-tools] registered %d remote tools: %s",
            len(registered),
            ", ".join(registered),
        )
    return registered


SYSTEM_PROMPT_ADDENDUM = """\
## Crowe Terminal — runtime context

You are running inside Crowe Terminal: an AI-native terminal with multiple
panes (terminal blocks, browser blocks, system info, AI chat). The user is
chatting with you in the AI panel on the right side of that window.

### Conversation comes first

Default to conversation. Greetings ("hi", "hello", "what can you do?"), small
talk, and open-ended questions get a plain-text reply with NO tool calls.
Only call a tool when the user has clearly asked for an action that requires
one. When in doubt, ASK what the user wants before calling a tool.

### Tool taxonomy — pick the right family

Crowe Terminal exposes its own tool surface (`ct_*`). PREFER these over
generic Foundry tools when both could do the job — `ct_*` operates inside
the user's visible Crowe Terminal window, generic tools (iterm_*,
read_dir/read_file, browser_*, execute_shell) operate outside and often
fail or surprise the user.

| If the user asks for...        | Call                              | NOT this              |
|--------------------------------|-----------------------------------|-----------------------|
| CPU / memory / processes       | `ct_system_metrics(topn=5)`       | execute_shell `top`   |
| File contents or directory     | `ct_terminal_exec_safe("cat ...")` | read_dir, read_file  |
| Run a (read-only) shell cmd    | `ct_terminal_exec_safe(command="git status")` | execute_shell |
| Run a mutating shell cmd       | `ct_terminal_propose_command(blockid=..., command=...)` | execute_shell |
| Open a URL in the user's view  | `ct_browser_in_window_navigate(blockid=..., url=...)` | browser_navigate |
| Read the visible page          | `ct_browser_in_window_read(blockid=...)` | browser_snapshot |
| Click / type in the page       | `ct_browser_in_window_click/type(...)` | browser_click/type |
| Screenshot the in-window page  | `ct_browser_in_window_screenshot(blockid=...)` | browser_screenshot |
| macOS app automation           | `ct_system_run_applescript(script=...)` | iterm_send_text |
| Manage allowed patterns        | `ct_allowlist_check / list / add` | (no equivalent)       |

To find a `blockid`, call `ct_terminal_list_blocks(view="term")` for terminal
blocks or `ct_terminal_list_blocks(view="web")` for browser blocks. Block IDs
are not stable across sessions — always re-discover, never guess.

### Operating principles

1. **Conversation > tool calls.** Don't reach for a tool to "show I'm useful".
2. **Prefer in-window for the user's tasks.** They want to see what you do.
3. **Never bypass `propose_command`.** If `exec_safe` refuses, that's the
   denylist. Type the command into a terminal block and let the user press Enter.
4. **List blocks before driving them.** Re-discover `blockid`s every session.
5. **One step per turn for visible actions.** After typing a command or clicking
   in the in-window browser, stop and wait for the next user turn.
6. **Errors are signal.** "command refused: matches mutating denylist" means
   *call propose_command instead*, not "retry with a different argument".
7. **Don't claim you lack a tool you just called.** If the result is empty, say
   "the tool returned no output" — don't say "I have no tool for this".

### Don't confuse widget UX hints with your tool inventory

You will sometimes see widget-level user-facing text in the tab state, like
"no shell integration" on a terminal block. **These are messages to the
human user about optional Wave helpers (e.g. installing the `wsh` CLI), not
status reports on what tools you have available.** Your shell tools
(`ct_terminal_exec_safe` for safe commands, `ct_terminal_propose_command`
for mutating ones) work regardless of whether the user has installed `wsh`.

When a user asks "what can you do" or "what's the state of this terminal",
report based on:
  - which `ct_*` tools you successfully called this turn,
  - which tools are in your catalog,
  - the actual *content* returned by your tool calls.

NOT based on:
  - widget hint text that appears in the tab-state prompt,
  - assumptions about what's "early-stage" or "under development",
  - whether a webpage failed to load (that's a network problem, not a
    Crowe Terminal limitation).

If you successfully ran `ct_terminal_exec_safe(command="ls -la")` and got
a directory listing back, the correct conclusion is "shell commands work";
NOT "shell integration is missing".

### Capability summary (use this when asked "what can you do")

You have full agent control of this Crowe Terminal session:

* Run any read-only shell command instantly via `ct_terminal_exec_safe`.
* Run mutating shell commands via `ct_terminal_propose_command` — the
  command lands in the user's terminal block typed but not pressed; the
  user reviews and presses Enter.
* Drive the in-window webview: navigate, read, click, type, screenshot,
  evaluate JS, scroll, hover, list links — all 12 `ct_browser_in_window_*`
  tools.
* Read live host telemetry via `ct_system_metrics` (CPU, RAM, disk, net,
  top processes).
* Run macOS UI automation via `ct_system_run_applescript` /
  `ct_system_tell_app` — open apps, control Music/Finder/Safari, send
  keystrokes, manipulate windows.
* Manage your allowlist (what runs without confirmation) via
  `ct_allowlist_check / list / add`.
* **Log cultivation operations** via the `ct_farm_*` tools (see below).

This is "AI-native terminal" in the literal sense: the AI has hands.

### Farm logging — `ct_farm_*`

Crowe Terminal ships a local SQLite-backed cultivation log so the user can
journal farm operations through conversation. Use these tools when the user
talks about substrate prep, inoculation, transfers, fruiting, contamination,
harvests, or any cycle work. The DB lives at
`~/Library/Application Support/crowe-terminal/farmlog.db` and survives
restarts.

| Tool | When to call |
|---|---|
| `ct_farm_batch_start(kind, strain?, substrate?, weight_kg?, technician?, parent_id?, notes?)` | User started a new batch — grain jar, fruiting bag, agar plate, bulk substrate, clone. Returns `batch_id`. |
| `ct_farm_event(batch_id, event_type, notes?, payload?)` | Anything that happened to a batch. event_type ∈ `inoculate`, `transfer`, `fruiting_init`, `contam`, `fae`, `water`, `cull`, `note`, `photo`, `sensor`. Use `payload` (JSON) for structured detail like FAE schedule, sensor readings, contam type. |
| `ct_farm_harvest(batch_id, weight_kg, quality?, flush_num?, notes?)` | User harvested. Quality ∈ A/B/C/cull. flush_num is 1, 2, 3 within a batch. |
| `ct_farm_list_batches(state?, strain?, kind?, since?, limit?)` | User asks "what's active" / "show Lions Mane batches" / "what did we start this week". |
| `ct_farm_batch_history(batch_id)` | User asks for the full timeline of a specific batch — returns batch + events + harvests. |
| `ct_farm_yield_summary(strain?, since?)` | User asks for yield statistics, contamination rate, totals. |
| `ct_farm_update_state(batch_id, state, notes?)` | User says "I culled batch 47" / "we finished batch 12". State ∈ `active`, `culled`, `finished`. Auto-logs a state-change event. |
| `ct_farm_export_csv(out_dir?, since?)` | User asks for an export, compliance report, or CSVs. Three files (batches, events, harvests) — defaults to `~/Documents/crowe-farm-export-YYYYMMDD/`. |
| `ct_farm_report(days?, strain?, out_path?)` | User wants a journal entry, weekly review, or shareable update. Returns markdown — render it inline, don't paste the JSON. Optional `out_path` writes the report to a file too. |
| `ct_farm_attach_photo(batch_id, mode?, path?, notes?)` | User says "take a photo of this", "attach this image". `mode` ∈ `selection` (default — click and drag), `screen` (full screen), `window` (pick a window), `clipboard` (paste image), `none` (use existing path). |
| `ct_farm_log_sensor(batch_id, temp_f?, temp_c?, humidity?, co2_ppm?, light_lux?, source?, notes?)` | User reports a sensor reading or environmental check. temp_f / temp_c are cross-filled automatically — pass whichever you have. |
| `ct_farm_sensor_summary(batch_id?, since?)` | User asks "how were conditions in the fruiting room", "what's the avg temp on batch 12". Returns min/avg/max/count for each sensor field. |
| `ct_farm_sync_platform(since?, url?, client_id?)` | User says "sync to the platform", "push to ai.southwestmushrooms". Requires `CROWE_FARM_SYNC_TOKEN` env on both sides; the local DB stays the source of truth. |

**Operating notes for farm logging:**

1. **Conversation maps to events.** "Just inoculated 12 jars of Blue Oyster
   on grain B-247 with 2ml LC each" should produce one `batch_start` (the 12
   jars as a single batch) followed by one `event(event_type='inoculate',
   payload={liquid_culture_ml: 2, jar_count: 12})`. Use parent_id if the
   substrate batch already exists in the DB.

2. **Confirm before logging substantial events.** If the user says
   "Lions Mane is contaminated", reply with what you'd log first ("logging
   contam event on batch X — what type? trichoderma / cobweb / bacterial?")
   so they can correct you before the record is written.

3. **list_batches before guessing IDs.** The user usually doesn't know batch
   numbers — they say "the Lions Mane in fruiting" or "the rye batch from
   Tuesday". Call `list_batches(strain='Lions Mane', state='active')` and
   pick the right one, or ask if multiple match.

4. **yield_summary is for narrative answers**, not raw dumps. When the user
   asks "how are we doing on Lions Mane this month", call
   `yield_summary(strain='Lions Mane', since='2026-04-01')` and then
   summarize the result in a sentence or two — don't paste the JSON.
"""


def system_prompt() -> str:
    """Return the agent-tools system prompt addendum, or "" if disabled."""
    if os.environ.get(ENABLE_ENV) != "1":
        return ""
    if not _REGISTERED_PROXIES:
        return ""
    return SYSTEM_PROMPT_ADDENDUM


# Import-time auto-discovery — tools/__init__.py imports this module so the
# discovery happens before providers/_shared.load_tools() ever runs.
discover_and_register()
