from typing import Optional, List
"""
Universal MCP Client — Spawn and communicate with any MCP server.

Handles the full MCP lifecycle:
1. Spawn a server process (npm/npx, pip, or docker)
2. Initialize via JSON-RPC handshake
3. Discover available tools
4. Call tools and return results
5. Cache running servers for reuse within a session

Supports stdio transport (the standard for local MCP servers).
"""

import json
import subprocess
import threading
import os
import sys
import time

# ── Active server pool ──────────────────────────────────────
# Maps server_key -> MCPServerConnection
_server_pool = {}
_pool_lock = threading.Lock()

# Max servers alive at once (prevent resource exhaustion in Docker)
MAX_POOL_SIZE = 10

# Server idle timeout in seconds
SERVER_TIMEOUT = 300


class MCPServerConnection:
    """Manages a single MCP server subprocess and JSON-RPC communication."""

    def __init__(self, command: List[str], env: Optional[dict] = None):
        self.command = command
        self._process = None
        self._request_id = 0
        self._lock = threading.Lock()
        self.server_info = {}
        self.tools = []
        self.last_used = time.time()
        self._env = {**os.environ, **(env or {})}

    def start(self) -> dict:
        """Spawn the server process and perform MCP initialization."""
        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._env,
            cwd="/tmp",
        )

        # MCP initialization handshake
        init_result = self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "crowe-logic",
                "version": "0.1.0",
            },
        })

        self.server_info = init_result.get("result", {})

        # Send initialized notification
        self._send_notification("notifications/initialized", {})

        # Discover tools
        tools_result = self._send_request("tools/list", {})
        self.tools = tools_result.get("result", {}).get("tools", [])

        return {
            "server_info": self.server_info,
            "tools": [{"name": t["name"], "description": t.get("description", "")} for t in self.tools],
        }

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call a tool on this MCP server."""
        self.last_used = time.time()
        result = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return result.get("result", result)

    def stop(self):
        """Terminate the server process."""
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and read the matching response.

        Servers may interleave notifications (logging, progress) on stdout;
        reading the next line blindly would return those in place of the
        response and shift every subsequent reply off by one. Skip anything
        whose id does not match ours.
        """
        with self._lock:
            self._request_id += 1
            msg = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            }
            self._write(msg)
            deadline = time.time() + 30
            while True:
                resp = self._read(deadline)
                if resp.get("id") == self._request_id and (
                    "result" in resp or "error" in resp
                ):
                    return resp

    def _send_notification(self, method: str, params: dict):
        """Send a JSON-RPC notification (no response expected)."""
        with self._lock:
            msg = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
            self._write(msg)


    def _write(self, msg: dict):
        """Write a JSON-RPC message to the server's stdin."""
        if not self.alive:
            raise ConnectionError("MCP server process is not running")
        payload = json.dumps(msg)
        self._process.stdin.write(payload.encode() + b"\n")
        self._process.stdin.flush()

    def _read(self, deadline: float) -> dict:
        """Read one JSON-RPC message line from the server's stdout."""
        if not self.alive:
            raise ConnectionError("MCP server process is not running")

        line = b""
        while time.time() < deadline:
            byte = self._process.stdout.read(1)
            if byte == b"\n":
                if line:
                    break
                continue
            if byte == b"":
                if self._process.poll() is not None:
                    stderr = self._process.stderr.read().decode()[:500]
                    raise ConnectionError(f"MCP server exited unexpectedly: {stderr}")
                time.sleep(0.01)
                continue
            line += byte

        if not line:
            raise TimeoutError("MCP server did not respond within 30s")

        return json.loads(line.decode())


# ── Server pool management ──────────────────────────────────

def _build_command(package: str, pkg_type: str = "npm") -> List[str]:
    """Build the shell command to spawn an MCP server."""
    if pkg_type == "npm":
        return ["npx", "-y", package]
    elif pkg_type == "pypi":
        return [sys.executable, "-m", package]
    elif pkg_type == "docker":
        return ["docker", "run", "-i", "--rm", package]
    else:
        # Assume it's a direct command
        return package.split()


def _get_or_start_server(server_key: str, command: List[str], env: Optional[dict] = None) -> MCPServerConnection:
    """Get a running server from the pool or start a new one."""
    with _pool_lock:
        # Check if already running
        if server_key in _server_pool:
            conn = _server_pool[server_key]
            if conn.alive:
                conn.last_used = time.time()
                return conn
            else:
                del _server_pool[server_key]

        # Evict oldest if pool is full
        if len(_server_pool) >= MAX_POOL_SIZE:
            oldest_key = min(_server_pool, key=lambda k: _server_pool[k].last_used)
            _server_pool[oldest_key].stop()
            del _server_pool[oldest_key]

        # Start new server
        conn = MCPServerConnection(command, env=env)
        conn.start()
        _server_pool[server_key] = conn
        return conn


def _cleanup_idle():
    """Stop servers that have been idle too long."""
    with _pool_lock:
        now = time.time()
        to_remove = [
            k for k, v in _server_pool.items()
            if now - v.last_used > SERVER_TIMEOUT or not v.alive
        ]
        for k in to_remove:
            _server_pool[k].stop()
            del _server_pool[k]


# ── Public tool functions (registered with Azure agent) ─────

def mcp_list_tools(package: str, package_type: str = "npm") -> str:
    """
    Connect to an MCP server and list all tools it provides.
    Spawns the server if not already running. Use mcp_search first to find packages.

    :param package: The npm package name (e.g. "@modelcontextprotocol/server-filesystem"),
                    PyPI package (e.g. "mcp-server-git"), or Docker image.
    :param package_type: Package registry type: "npm", "pypi", or "docker" (default "npm").
    :return: JSON with server info and list of available tools with descriptions.
    :rtype: str
    """
    _cleanup_idle()

    try:
        command = _build_command(package, package_type)
        conn = _get_or_start_server(package, command)

        return json.dumps({
            "package": package,
            "server_info": conn.server_info.get("serverInfo", {}),
            "tool_count": len(conn.tools),
            "tools": [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": list(t.get("inputSchema", {}).get("properties", {}).keys()),
                }
                for t in conn.tools
            ],
        })
    except Exception as e:
        return json.dumps({"error": str(e), "package": package})


def mcp_call_tool(package: str, tool_name: str, arguments: str = "{}", package_type: str = "npm") -> str:
    """
    Call a specific tool on an MCP server. The server is spawned automatically
    if not already running. Use mcp_list_tools first to see available tools.

    :param package: The MCP server package (e.g. "@modelcontextprotocol/server-filesystem").
    :param tool_name: Name of the tool to call (e.g. "read_file", "list_directory").
    :param arguments: JSON string of arguments to pass to the tool.
    :param package_type: Package registry type: "npm", "pypi", or "docker" (default "npm").
    :return: JSON result from the tool execution.
    :rtype: str
    """
    _cleanup_idle()

    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid arguments JSON: {e}"})

    try:
        command = _build_command(package, package_type)
        conn = _get_or_start_server(package, command)
        result = conn.call_tool(tool_name, args)

        # Extract content from MCP result format
        if isinstance(result, dict) and "content" in result:
            contents = result["content"]
            # Flatten text content
            text_parts = []
            for item in contents:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item["text"])
                else:
                    text_parts.append(json.dumps(item))
            return json.dumps({
                "tool": tool_name,
                "result": "\n".join(text_parts) if text_parts else result,
            })

        return json.dumps({"tool": tool_name, "result": result})

    except Exception as e:
        return json.dumps({"error": str(e), "package": package, "tool": tool_name})


def mcp_stop_server(package: str) -> str:
    """
    Stop a running MCP server to free resources. Servers auto-stop after 5 minutes idle.

    :param package: The MCP server package to stop.
    :return: JSON confirmation.
    :rtype: str
    """
    with _pool_lock:
        if package in _server_pool:
            _server_pool[package].stop()
            del _server_pool[package]
            return json.dumps({"stopped": package})
        return json.dumps({"note": f"{package} was not running"})
