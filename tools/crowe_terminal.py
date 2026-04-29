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
