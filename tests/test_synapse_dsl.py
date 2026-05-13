"""Tests for the synapse-agent DSL · lexer, parser, compiler, round-trip.

The DSL must produce dicts that are byte-for-byte interchangeable with the
existing YAML agents/* files on the core fields (name, description, model,
tools, prompt_override, pipelines). New optional fields default in a way
that leaves existing YAML agents unaffected.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from crowe_synapse_engine.agent_registry import AgentConfig
from crowe_synapse_engine.synapse_dsl import (
    CompileError,
    LexError,
    ParseError,
    compile_source,
    parse,
    tokenize,
)

_REPO = Path(__file__).resolve().parents[1]
_EXAMPLE = _REPO / "crowe_synapse_engine/synapse_dsl/examples/research.synapse-agent"
_YAML_EQUIVALENT = _REPO / "agents/research.yaml"


def test_tokenize_basic_agent_block():
    tokens = tokenize('agent "x" {\n  model: crowelm-pro\n}\n')
    kinds = [t.kind for t in tokens]
    assert "KEYWORD" in kinds and "STRING" in kinds and "LBRACE" in kinds
    assert kinds[-1] == "EOF"


def test_tokenize_rejects_invalid_character():
    with pytest.raises(LexError):
        tokenize("agent @bad {}")


def test_parse_single_agent():
    tokens = tokenize('agent "x" { model: crowelm-pro }')
    agents = parse(tokens)
    assert agents == [{"name": "x", "model": "crowelm-pro"}]


def test_parse_rejects_unclosed_block():
    with pytest.raises(ParseError):
        parse(tokenize('agent "x" { model: crowelm-pro'))


def test_compile_applies_defaults():
    compiled = compile_source('agent "minimal" {}')
    agent = compiled[0]
    assert agent["name"] == "minimal"
    assert agent["model"] == "crowelm-pro"
    assert agent["tools"] == []
    assert agent["prompt_override"] == ""
    assert agent["permission_mode"] == "default"
    assert agent["runtime"] is None


def test_compile_aliases_prompt_to_prompt_override():
    compiled = compile_source('agent "x" { prompt: "be helpful" }')
    assert compiled[0]["prompt_override"] == "be helpful"
    assert "prompt" not in compiled[0]


def test_compile_rejects_unknown_field():
    with pytest.raises(CompileError, match="Unknown field 'frobnicate'"):
        compile_source('agent "x" { frobnicate: 1 }')


def test_compile_requires_name():
    # Names are enforced by the grammar (agent block requires a name token),
    # so the only way to get past the parser without one is a synthetic raw dict.
    from crowe_synapse_engine.synapse_dsl.compiler import compile_agents

    with pytest.raises(CompileError, match="missing required field 'name'"):
        compile_agents([{"model": "crowelm-pro"}])


def test_triple_string_dedented():
    src = '''agent "x" {
      prompt: """
        line one
        line two
      """
    }'''
    agent = compile_source(src)[0]
    assert agent["prompt_override"] == "line one\nline two"


def test_list_with_trailing_comma():
    src = 'agent "x" { tools: [a, b, c,] }'
    agent = compile_source(src)[0]
    assert agent["tools"] == ["a", "b", "c"]


def test_round_trip_research_against_yaml():
    """The DSL example and the YAML must agree on every core field."""
    dsl_agent = compile_source(_EXAMPLE.read_text())[0]
    yaml_agent = yaml.safe_load(_YAML_EQUIVALENT.read_text())

    for field in ("name", "description", "model", "tools", "pipelines"):
        assert dsl_agent[field] == yaml_agent[field], f"mismatch on {field}"
    assert dsl_agent["prompt_override"].strip() == yaml_agent["prompt_override"].strip()


def test_compiled_dict_constructs_agent_config():
    """The compiled dict must be directly usable as AgentConfig kwargs."""
    dsl_agent = compile_source(_EXAMPLE.read_text())[0]
    agent = AgentConfig(**dsl_agent)
    assert agent.name == "research"
    assert agent.model == "crowelm-pro"
    assert agent.runtime is None
    assert agent.permission_mode == "default"
