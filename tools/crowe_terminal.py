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


# Import-time auto-discovery — tools/__init__.py just needs to import this
# module. This keeps the integration zero-config for the common case.
discover_and_register()
