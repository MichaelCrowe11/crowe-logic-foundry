"""Tests for crowe_synapse_engine.agent_registry, YAML agent loading."""

import os
import pytest
from crowe_synapse_engine.agent_registry import AgentRegistry, AgentConfig, ClusterConfig


@pytest.fixture
def registry():
    agents_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agents")
    return AgentRegistry(agents_dir=agents_dir)


class TestAgentLoading:
    def test_loads_all_agents(self, registry):
        agents = registry.list_agents()
        names = [a.name for a in agents]
        assert "code" in names
        assert "music" in names
        assert "research" in names
        assert "quantum" in names
        assert "cultivation" in names

    def test_get_agent_by_name(self, registry):
        agent = registry.get_agent("music")
        assert agent is not None
        assert agent.name == "music"
        assert "talon_*" in agent.tools

    def test_get_unknown_agent_returns_none(self, registry):
        assert registry.get_agent("nonexistent") is None


class TestAgentConfig:
    def test_agent_has_prompt_override(self, registry):
        agent = registry.get_agent("music")
        assert agent.prompt_override is not None
        assert len(agent.prompt_override) > 10

    def test_agent_has_description(self, registry):
        agent = registry.get_agent("code")
        assert agent.description is not None

    def test_agent_tools_are_list(self, registry):
        agent = registry.get_agent("research")
        assert isinstance(agent.tools, list)
        assert len(agent.tools) > 0


class TestToolResolution:
    def test_resolve_glob_pattern(self, registry):
        available_tools = {"talon_generate_chords", "talon_generate_drums", "talon_generate_melody",
                           "talon_quantum_melody", "read_file", "write_file", "execute_shell"}
        agent = registry.get_agent("music")
        resolved = registry.resolve_tools(agent, available_tools)
        assert "talon_generate_chords" in resolved
        assert "talon_generate_drums" in resolved
        assert "read_file" in resolved
        assert "write_file" not in resolved  # not in music agent's tools list

    def test_resolve_exact_names(self, registry):
        available_tools = {"web_search", "browse_url", "grep_search", "read_file", "execute_shell"}
        agent = registry.get_agent("research")
        resolved = registry.resolve_tools(agent, available_tools)
        assert "web_search" in resolved
        assert "browse_url" in resolved
        assert "execute_shell" not in resolved


class TestRecursiveLoading:
    """Sub-cluster directories under agents/ also load their member yaml files."""

    def test_loads_agents_from_subdirectories(self, registry):
        names = [a.name for a in registry.list_agents()]
        assert "music-orchestrator" in names
        assert "music-compose" in names
        assert "music-critic" in names
        assert "music-test" in names

    def test_subdirectory_agents_keep_full_config(self, registry):
        critic = registry.get_agent("music-critic")
        assert critic is not None
        assert critic.cluster == "crowelm-music"
        assert critic.model == "crowelm-eclipse"
        assert "BLOCK" in critic.prompt_override


class TestClusterManifest:
    """cluster.yaml files declare a cluster, not an agent."""

    def test_cluster_manifest_not_loaded_as_agent(self, registry):
        agent_names = [a.name for a in registry.list_agents()]
        # 'crowelm-music' is the cluster name, not an agent name; ensure it
        # is recognized as a cluster instead of accidentally treated as an agent.
        assert "crowelm-music" not in agent_names

    def test_cluster_loaded_separately(self, registry):
        cluster = registry.get_cluster("crowelm-music")
        assert cluster is not None
        assert isinstance(cluster, ClusterConfig)
        assert "music-orchestrator" in cluster.agents
        assert "music-critic" in cluster.agents

    def test_agents_in_cluster(self, registry):
        members = registry.agents_in_cluster("crowelm-music")
        member_names = {a.name for a in members}
        # All ten cluster members plus the legacy `music` alias.
        assert "music-orchestrator" in member_names
        assert "music-compose" in member_names
        assert "music-mix" in member_names
        assert "music-master" in member_names
        assert "music-dsp" in member_names
        assert "music-native" in member_names
        assert "music-web" in member_names
        assert "music-provenance" in member_names
        assert "music-critic" in member_names
        assert "music-test" in member_names
        assert "music" in member_names  # legacy alias


class TestAliasResolution:
    def test_alias_field_loaded(self, registry):
        legacy = registry.get_agent("music")
        assert legacy is not None
        assert legacy.alias_of == "music-orchestrator"

    def test_resolve_alias_returns_target(self, registry):
        target = registry.resolve_alias("music")
        assert target is not None
        assert target.name == "music-orchestrator"

    def test_resolve_alias_on_non_alias_returns_self(self, registry):
        agent = registry.resolve_alias("music-compose")
        assert agent is not None
        assert agent.name == "music-compose"

    def test_resolve_alias_on_unknown_returns_none(self, registry):
        assert registry.resolve_alias("does-not-exist") is None
