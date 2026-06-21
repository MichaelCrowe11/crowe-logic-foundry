from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from bench.agentic.agents.base import AgentResult

_TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file in the workdir.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write a file in the workdir.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_tests",
        "description": "Run pytest -q in the workdir; returns output.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

_SYSTEM = (
    "You are a coding agent. Plan, then act using the tools, then VERIFY by "
    "running the tests before you finish. Only stop once tests pass. "
    "All paths are relative to the working directory."
)


def _exec_tool(name: str, args: dict, workdir: Path) -> str:
    workroot = workdir.resolve()
    if name == "read_file":
        p = (workdir / args["path"]).resolve()
        if workroot not in p.parents and p != workroot:
            return "error: path escapes workdir"
        return p.read_text() if p.exists() else f"error: {args['path']} not found"
    if name == "write_file":
        p = (workdir / args["path"]).resolve()
        if workroot not in p.parents:
            return "error: path escapes workdir"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"])
        return f"wrote {args['path']}"
    if name == "run_tests":
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        return (proc.stdout + proc.stderr)[-3000:]
    return f"error: unknown tool {name}"


class ReferenceRunner:
    name = "reference"

    def __init__(self, model: str | None = None):
        self._model_override = model

    def _complete(self, messages: list[dict], model: str):
        """One Anthropic completion through the foundry's Azure-routed client.

        Reuses ``AnthropicProvider``'s client construction (Azure base_url +
        creds + prompt caching) so model ACCESS is identical to the crowe-logic
        side — only the surrounding loop differs (the variable under test). This
        honors the cloud-exclusive sourcing rule (Azure, never direct vendor
        API). Isolated in one method so tests monkeypatch it (zero tokens).
        """
        from providers.anthropic import AnthropicProvider

        endpoint = os.environ.get("AZURE_ANTHROPIC_ENDPOINT", "")
        api_key = os.environ.get("AZURE_ANTHROPIC_API_KEY", "")
        if not endpoint or not api_key:
            raise RuntimeError(
                "reference agent needs AZURE_ANTHROPIC_ENDPOINT / AZURE_ANTHROPIC_API_KEY"
            )
        provider = AnthropicProvider(
            model=model,
            system_instructions=_SYSTEM,
            endpoint=endpoint,
            api_key=api_key,
        )
        return provider.client.messages.create(
            model=model,
            max_tokens=4096,
            system=_SYSTEM,
            tools=_TOOLS,
            messages=messages,
        )

    def run(
        self, *, prompt, workdir, model, tools, max_rounds, timeout_s
    ) -> AgentResult:
        workdir = Path(workdir)
        model = self._model_override or model
        messages: list[dict] = [{"role": "user", "content": prompt}]
        rounds = tool_calls = 0
        self_verified = False
        error = None
        t0 = time.monotonic()
        try:
            while rounds < max_rounds:
                rounds += 1
                resp = self._complete(messages, model)
                tool_uses = [
                    b for b in resp.content if getattr(b, "type", None) == "tool_use"
                ]
                messages.append({"role": "assistant", "content": resp.content})
                if not tool_uses:
                    break
                results = []
                for tu in tool_uses:
                    tool_calls += 1
                    if tu.name == "run_tests":
                        self_verified = True
                    out = _exec_tool(tu.name, dict(tu.input), workdir)
                    results.append(
                        {"type": "tool_result", "tool_use_id": tu.id, "content": out}
                    )
                messages.append({"role": "user", "content": results})
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
        return AgentResult(
            workdir=workdir,
            transcript=messages,
            rounds=rounds,
            tool_calls=tool_calls,
            wall_s=time.monotonic() - t0,
            tokens=None,
            cost_usd=None,
            self_verified=self_verified,
            error=error,
        )
