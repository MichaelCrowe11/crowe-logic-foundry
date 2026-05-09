"""
Crowe-Synapse Agent Registry · load and manage YAML-defined sub-agents.

Each agent is a persona: a system prompt override, a tool subset, and
optional pipeline templates. Agents reuse the same branded model tier
(CroweLM Pro by default)
with different instructions.

Layout supported:
    agents/<agent>.yaml                 # flat agent
    agents/<cluster>/<agent>.yaml       # agent inside a sub-cluster
    agents/<cluster>/cluster.yaml       # cluster manifest (not an agent)
    agents/<cluster>/README.md          # ignored
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
    cluster: str | None = None
    alias_of: str | None = None


@dataclass
class ClusterConfig:
    name: str
    description: str = ""
    version: str = "0.1.0"
    agents: list[str] = field(default_factory=list)
    tiers: dict = field(default_factory=dict)
    ai_panel_entry: str | None = None
    pipelines: list[str] = field(default_factory=list)
    style_rules: list[str] = field(default_factory=list)
    contract: dict = field(default_factory=dict)


class AgentRegistry:
    def __init__(self, agents_dir: str = ""):
        self._agents: dict[str, AgentConfig] = {}
        self._clusters: dict[str, ClusterConfig] = {}
        if agents_dir and os.path.isdir(agents_dir):
            self._load_agents(agents_dir)

    @staticmethod
    def _is_cluster_manifest(data: dict) -> bool:
        """A cluster manifest declares an `agents:` list of names; an agent
        does not. This is the disambiguator between sibling yaml files."""
        agents_field = data.get("agents")
        return isinstance(agents_field, list) and all(
            isinstance(item, str) for item in agents_field
        )

    def _load_agents(self, agents_dir: str):
        # First pass: discover cluster manifests so directory membership can
        # auto-tag agents in the second pass.
        dir_to_cluster: dict[str, str] = {}
        agent_files: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(agents_dir):
            for filename in sorted(filenames):
                if not filename.endswith((".yaml", ".yml")):
                    continue
                path = os.path.join(dirpath, filename)
                with open(path) as f:
                    data = yaml.safe_load(f)
                if not data or "name" not in data:
                    continue

                if self._is_cluster_manifest(data):
                    cluster = ClusterConfig(
                        name=data["name"],
                        description=data.get("description", ""),
                        version=str(data.get("version", "0.1.0")),
                        agents=list(data.get("agents", [])),
                        tiers=dict(data.get("tiers", {})),
                        ai_panel_entry=data.get("ai_panel_entry"),
                        pipelines=list(data.get("pipelines", [])),
                        style_rules=list(data.get("style_rules", [])),
                        contract=dict(data.get("contract", {})),
                    )
                    self._clusters[cluster.name] = cluster
                    dir_to_cluster[dirpath] = cluster.name
                else:
                    agent_files.append(path)

        # Second pass: load agents. If an agent yaml lives inside a directory
        # that has a cluster.yaml manifest, inherit cluster membership unless
        # the yaml sets its own `cluster` field explicitly.
        for path in agent_files:
            with open(path) as f:
                data = yaml.safe_load(f)
            inferred_cluster = dir_to_cluster.get(os.path.dirname(path))
            agent = AgentConfig(
                name=data["name"],
                description=data.get("description", ""),
                model=data.get("model", "crowelm-pro"),
                tools=data.get("tools", []),
                prompt_override=data.get("prompt_override", ""),
                pipelines=data.get("pipelines", []),
                quantum_evaluator=data.get("quantum_evaluator"),
                cluster=data.get("cluster", inferred_cluster),
                alias_of=data.get("alias_of"),
            )
            self._agents[agent.name] = agent

    def list_agents(self) -> list[AgentConfig]:
        return list(self._agents.values())

    def list_clusters(self) -> list[ClusterConfig]:
        return list(self._clusters.values())

    def get_agent(self, name: str) -> AgentConfig | None:
        return self._agents.get(name)

    def get_cluster(self, name: str) -> ClusterConfig | None:
        return self._clusters.get(name)

    def agents_in_cluster(self, cluster_name: str) -> list[AgentConfig]:
        return [a for a in self._agents.values() if a.cluster == cluster_name]

    def resolve_alias(self, name: str) -> AgentConfig | None:
        """Resolve an alias to its target agent. Returns the target, not the alias.
        Returns None if `name` does not exist or its target does not exist."""
        agent = self._agents.get(name)
        if agent is None:
            return None
        if agent.alias_of is None:
            return agent
        return self._agents.get(agent.alias_of)

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
