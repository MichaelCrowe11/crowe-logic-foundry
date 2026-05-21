"""Tests for crowe_synapse_engine.agent_registry — YAML agent loading."""

import os
import pytest
from crowe_synapse_engine.agent_registry import AgentRegistry


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
