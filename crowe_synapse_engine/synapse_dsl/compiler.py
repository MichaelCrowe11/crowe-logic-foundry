"""synapse-agent compiler · AST → AgentConfig-shaped dict.

The compiler takes the raw dicts produced by ``parser.parse()`` and:

1. Maps DSL field names to the YAML schema names used by AgentRegistry.
   (``prompt`` in the DSL → ``prompt_override`` in the YAML.)
2. Validates required fields (``name``) and rejects unknown keys.
3. Applies defaults for missing optional fields.
4. Returns a list of dicts ready to feed ``yaml.safe_dump()`` or pass
   directly to ``AgentConfig(**dict)``.

Keep the compiler dumb: no I/O, no side effects, no Lark or YAML
imports. That makes it trivial to unit-test and easy to repurpose for
other emitters (JSON, Protobuf, in-memory AgentConfig construction).
"""

from __future__ import annotations

from crowe_synapse_engine.synapse_dsl.lexer import tokenize
from crowe_synapse_engine.synapse_dsl.parser import parse


class CompileError(Exception):
    """Raised when an agent block fails validation."""


# DSL field name → YAML/AgentConfig field name.
_FIELD_ALIASES: dict[str, str] = {
    "prompt": "prompt_override",
}

# Fields known to the schema. Anything else is a hard error so typos
# surface early instead of silently round-tripping into the YAML.
_KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "model",
        "tools",
        "prompt_override",
        "pipelines",
        "quantum_evaluator",
        "cluster",
        "alias_of",
        "runtime",
        "permission_mode",
        "mcp_servers",
        "subagents",
        "hooks",
    }
)

_DEFAULTS: dict[str, object] = {
    "description": "",
    "model": "crowelm-pro",
    "tools": [],
    "prompt_override": "",
    "pipelines": [],
    "quantum_evaluator": None,
    "cluster": None,
    "alias_of": None,
    "runtime": None,
    "permission_mode": "default",
    "mcp_servers": {},
    "subagents": [],
    "hooks": [],
}


def _compile_one(raw: dict) -> dict:
    if "name" not in raw or not raw["name"]:
        raise CompileError("Agent block is missing required field 'name'.")

    out: dict = {}
    for key, value in raw.items():
        normalized = _FIELD_ALIASES.get(key, key)
        if normalized not in _KNOWN_FIELDS:
            raise CompileError(
                f"Unknown field {key!r} in agent {raw['name']!r}. "
                f"Known fields: {sorted(_KNOWN_FIELDS)}"
            )
        out[normalized] = value

    for field_name, default in _DEFAULTS.items():
        out.setdefault(field_name, default)
    return out


def compile_agents(parsed: list[dict]) -> list[dict]:
    """Compile a list of parsed agent dicts into validated, defaulted dicts."""
    return [_compile_one(item) for item in parsed]


def compile_source(source: str) -> list[dict]:
    """One-shot helper: source text → list of AgentConfig-shaped dicts."""
    return compile_agents(parse(tokenize(source)))
