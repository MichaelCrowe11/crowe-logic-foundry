"""
Crowe-Synapse Agent Registry — load and manage YAML-defined sub-agents.

Each agent is a persona: a system prompt override, a tool subset, and
optional pipeline templates. Agents reuse the same branded model tier
(CroweLM Pro by default)
with different instructions.
"""

import fnmatch
import os
from dataclasses import dataclass, field

import yaml


@dataclass
class AgentConfig:
    name: str
    description: str = ""
    model: str = "crowelm-pro"
    tools: list[str] = field(default_factory=list)
    prompt_override: str = ""
    pipelines: list[str] = field(default_factory=list)
    quantum_evaluator: str | None = None


class AgentRegistry:
    def __init__(self, agents_dir: str = ""):
        self._agents: dict[str, AgentConfig] = {}
        if agents_dir and os.path.isdir(agents_dir):
            self._load_agents(agents_dir)

    def _load_agents(self, agents_dir: str):
        for filename in sorted(os.listdir(agents_dir)):
            if filename.endswith((".yaml", ".yml")):
                path = os.path.join(agents_dir, filename)
                with open(path) as f:
                    data = yaml.safe_load(f)
                if data and "name" in data:
                    agent = AgentConfig(
                        name=data["name"],
                        description=data.get("description", ""),
                        model=data.get("model", "crowelm-pro"),
                        tools=data.get("tools", []),
                        prompt_override=data.get("prompt_override", ""),
                        pipelines=data.get("pipelines", []),
                        quantum_evaluator=data.get("quantum_evaluator"),
                    )
                    self._agents[agent.name] = agent

    def list_agents(self) -> list[AgentConfig]:
        return list(self._agents.values())

    def get_agent(self, name: str) -> AgentConfig | None:
        return self._agents.get(name)

    def resolve_tools(self, agent: AgentConfig, available_tools: set[str]) -> set[str]:
        """Resolve tool patterns (including globs like 'talon_*') against available tools."""
        resolved = set()
        for pattern in agent.tools:
            if "*" in pattern or "?" in pattern:
                for tool_name in available_tools:
                    if fnmatch.fnmatch(tool_name, pattern):
                        resolved.add(tool_name)
            elif pattern in available_tools:
                resolved.add(pattern)
        return resolved
