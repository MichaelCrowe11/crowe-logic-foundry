"""Crowe Terminal remote tool bridge.

Discovers tools advertised by the Crowe Terminal control plane
(`http://127.0.0.1:8012/v1/tools`) and registers each one as a Foundry
tool whose execution proxies back to the terminal over HTTP.

This keeps Foundry's existing agent loop (cli.headless, providers,
telemetry, dual-mode rendering) unchanged while letting the model drive
the user's terminal blocks, browser, system monitor, and allowlist.

Behavior:

* Disabled by default. Set ``CROWE_AGENT_TOOLS=1`` to enable.
* Probes the catalog at import time. If the terminal is not running,
  no tools are registered (silent — the bridge is still usable from
  non-terminal hosts).
* Auth: the wave authkey is passed through via env var
  ``WAVETERM_AUTH_KEY`` (the terminal sets this when it spawns the
  bridge). All requests use header ``X-AuthKey``.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional

try:
    import httpx
except ImportError:
    httpx = None

from tools.registry import ToolMeta, get_registry

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8012
DEFAULT_TIMEOUT = 60.0
ENABLE_ENV = "CROWE_AGENT_TOOLS"
HOST_ENV = "CROWE_AGENT_HOST"
PORT_ENV = "CROWE_AGENT_PORT"
AUTH_ENV = "WAVETERM_AUTH_KEY"


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


def _make_proxy(tool_name: str) -> Callable[..., Any]:
    """Build a proxy callable that forwards kwargs as the tool arguments."""

    def proxy(**kwargs: Any) -> str:
        if httpx is None:
            return json.dumps({"error": "httpx not installed in foundry env"})
        body = {"name": tool_name, "arguments": kwargs}
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
            # Surface tool-level errors so the model sees the refusal,
            # not an empty success.
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

    proxy.__name__ = tool_name.replace(".", "_")
    proxy.__doc__ = f"Proxy to Crowe Terminal tool '{tool_name}'."
    return proxy


def _safe_python_name(tool_name: str) -> str:
    return "ct_" + tool_name.replace(".", "_")


def _register_proxy(catalog_entry: Dict[str, Any]) -> Optional[str]:
    fn_meta = catalog_entry.get("function") or {}
    name = fn_meta.get("name")
    if not name:
        return None
    description = fn_meta.get("description") or f"Crowe Terminal tool: {name}"
    parameters = fn_meta.get("parameters") or {"type": "object"}
    mutating = bool(catalog_entry.get("x_mutating"))

    proxy_name = _safe_python_name(name)
    proxy_fn = _make_proxy(name)

    meta = ToolMeta(
        name=proxy_name,
        fn=proxy_fn,
        category="crowe-terminal",
        description=(
            description + (" [mutating; user confirmation required]" if mutating else "")
        ),
        parameters=parameters,
        returns="JSON string with tool result.",
        is_async=False,
        tags={"crowe-terminal", "remote", "mutating"} if mutating else {"crowe-terminal", "remote"},
    )

    registry = get_registry()
    registry._tools[proxy_name] = meta  # noqa: SLF001 - bypass introspection on purpose
    registry._by_category.setdefault("crowe-terminal", set()).add(proxy_name)
    for tag in meta.tags:
        registry._by_tag.setdefault(tag, set()).add(proxy_name)
    return proxy_name


def discover_and_register() -> List[str]:
    """Probe Crowe Terminal and register every advertised tool. Returns names."""
    if os.environ.get(ENABLE_ENV) != "1":
        return []
    catalog = _fetch_catalog()
    if not catalog:
        return []
    registered = []
    for entry in catalog:
        name = _register_proxy(entry)
        if name:
            registered.append(name)
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
one — like "show me my CPU", "open the docs in a browser block", "run git
status", "screenshot this page".

When in doubt, ASK what the user wants before calling a tool. A short
clarifying question is always cheaper than a wrong action.

### Tool taxonomy — pick the right family

Crowe Terminal has exposed its own tool surface (`ct_*`). PREFER these over
generic Foundry tools when both could do the job, because the `ct_*` tools
operate inside the user's visible Crowe Terminal window — what you do, the
user sees. Generic tools (iterm_*, filesystem.read_dir, browser_*) operate
outside the window and often fail or surprise the user.

Family-by-family preference:

| If you want to...                  | Use                              | Don't use                |
|------------------------------------|----------------------------------|--------------------------|
| Read a file or list a directory    | `ct_terminal_exec_safe` (cat,ls) | read_dir, read_file (Foundry) — they hit a different sandbox |
| Run a shell command                | `ct_terminal_exec_safe` (safe)   | execute_shell — separate process, separate cwd |
|                                    | or `ct_terminal_propose_command` | iterm_send_text — needs iTerm2 running, won't work |
| Get host metrics / processes       | `ct_system_metrics`              | execute_shell with `top` — slower + uglier |
| Open a URL / read / click / type   | `ct_browser_in_window_*`         | playwright `browser_*` — runs in a headless browser the user can't see |
| Do macOS app automation            | `ct_system_run_applescript`      | iterm_* — only valid if user is in iTerm2 |
| Manage approved patterns           | `ct_allowlist_*`                 | (no equivalent)          |


### Shell commands

* `ct_terminal_exec_safe(command, cwd?, timeout_sec?)` — run a *read-only*
  command in a fresh subprocess and return stdout/stderr/exit. Refuses
  anything matching the mutating-pattern denylist (`rm`, `sudo`, `git push`,
  package installs, redirects outside `/tmp`, subshells, pipes to a shell,
  etc.). Use this for `git status`, `ls`, `cat`, `grep`, `ps`, `df`,
  `kubectl get`, `docker ps`, version checks, anything that only observes.
* `ct_terminal_propose_command(blockid, command)` — type a command into the
  user's visible terminal block but DO NOT press Enter. Use this for ANY
  command exec_safe refuses. The user reviews the typed line and approves
  by pressing Enter (or rejects by clearing). Get a `blockid` first with
  `ct_terminal_list_blocks(view="term")`.
* `ct_allowlist_check(kind="command", candidate=...)` — before suggesting a
  one-off mutation, check if it's already approved. If allowed, you may
  call exec_safe-equivalent paths; if not, use propose_command.

### Browser — pick the surface that matches the intent

Two browser surfaces, distinct semantics:

* `ct_browser_in_window_*` — drives the **same Wave window's** web block.
  The user sees the page load and can grab the URL bar at any point.
  Use for "show me X", "open the docs", "log into our admin and read Y",
  anything where the user benefits from watching. List web blocks first
  with `ct_terminal_list_blocks(view="web")`.
* `browser_*` (Playwright tools, when present) — runs in a **separate**
  browser process, hidden from the Wave window. Use for headless
  automation: scraping multi-page lists, scripted login flows the user
  doesn't need to watch, anything where opening many tabs in the user's
  view would be intrusive.

In-window tool inventory:
* `ct_browser_in_window_navigate(blockid, url)`
* `ct_browser_in_window_read(blockid, max_chars?)` — visible text + url + title
* `ct_browser_in_window_click(blockid, selector)`
* `ct_browser_in_window_type(blockid, selector, text, press_enter?, clear?)`
* `ct_browser_in_window_screenshot(blockid)` — base64 PNG
* `ct_browser_in_window_eval(blockid, script, timeout_ms?)` — escape hatch

### Host metrics

* `ct_system_metrics(topn?)` — CPU per core, memory, disk, network, top
  processes. Read-only. Use whenever the user asks "what's running",
  "is X using a lot of RAM", "why is my fan loud", or to confirm an
  action's effect.

### macOS UI automation (darwin only)

* `ct_system_run_applescript(script, timeout_sec?)` — run literal AppleScript.
* `ct_system_tell_app(app, command, timeout_sec?)` — sugar for the common
  `tell application "X" to Y` shape.

These are mutating: AppleScript can affect any app's state, send keystrokes,
control Music/Finder/Mail/Safari, manipulate Notification Center.

### Allowlist management

* `ct_allowlist_list()` — show the user what they've approved.
* `ct_allowlist_add(kind, pattern, notes?)` — add a pattern. **Refuses any
  pattern matching the mutating denylist** — that's by design, don't try to
  work around it.

### Operating principles

1. **Conversation > tool calls.** When the user is just chatting, just chat.
   Don't reach for a tool to "show I'm useful" — that's how you fail at
   a "hi" greeting by calling iterm_read_screen.
2. **Prefer in-window for the user's browser tasks.** They want to see what
   you're doing. Only fall back to Playwright for genuine bulk automation.
3. **Never bypass `propose_command`.** If exec_safe refuses, that's the
   denylist working. Type the command into a terminal block and let the
   user press Enter — that's the safety contract.
4. **List blocks before driving them.** Block IDs aren't stable across
   sessions; always re-discover with `ct_terminal_list_blocks` (view=`term`
   for terminal blocks, view=`web` for browser blocks). Don't guess IDs.
5. **One step per turn for visible actions.** When you `propose_command`
   or `ct_browser_in_window_click` something the user is watching, stop and
   wait for the next user turn before chaining the next visible step. The
   user is part of the loop.
6. **Errors from agent tools are signal, not noise.** "command refused:
   matches mutating denylist" means *call propose_command instead*, not
   "retry with similar arguments". A tool failing twice in a row means
   stop calling it and ask the user what they meant.
"""


def system_prompt() -> str:
    """Return the agent-tools system prompt addendum, or "" if disabled.

    Called by Foundry's session_runtime to compose the model's system
    instructions when Crowe Terminal tools are active. Empty when the
    proxy didn't register anything (terminal not running) so the prompt
    doesn't promise tools the model can't actually call.
    """
    if os.environ.get(ENABLE_ENV) != "1":
        return ""
    # Only emit the addendum if we successfully registered tools — no
    # point telling the model about a tool catalog that doesn't exist.
    from tools.registry import get_registry
    if not any(name.startswith("ct_") for name in get_registry()._tools):  # noqa: SLF001
        return ""
    return SYSTEM_PROMPT_ADDENDUM


# Import-time auto-discovery — tools/__init__.py just needs to import this
# module. This keeps the integration zero-config for the common case.
discover_and_register()
