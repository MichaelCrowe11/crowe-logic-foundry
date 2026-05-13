"""synapse-agent DSL · declarative agent specification.

A ``.synapse-agent`` file describes one or more agents in a syntax
purpose-built for the shape of an agent definition (blocks, key-value
pairs, triple-quoted system prompts, list literals). The compiler emits
the same dict shape that ``AgentRegistry`` loads from YAML, so the DSL
is a strict superset of the existing YAML format: every existing YAML
file has a 1-to-1 ``.synapse-agent`` equivalent, and the engine treats
both as the same AgentConfig.

Pipeline:

    .synapse-agent text
        ↓ lexer.tokenize()
    list[Token]
        ↓ parser.parse()
    list[dict]   (one dict per agent block)
        ↓ compiler.compile_agents()
    list[dict]   (validated, defaulted; AgentConfig-shaped)

The compiled dicts can be persisted with ``yaml.safe_dump()`` for
back-compat with anything that reads ``agents/*.yaml`` today.
"""

from crowe_synapse_engine.synapse_dsl.compiler import (
    CompileError,
    compile_agents,
    compile_source,
)
from crowe_synapse_engine.synapse_dsl.lexer import (
    LexError,
    Token,
    tokenize,
)
from crowe_synapse_engine.synapse_dsl.parser import (
    ParseError,
    parse,
)

__all__ = [
    "CompileError",
    "LexError",
    "ParseError",
    "Token",
    "compile_agents",
    "compile_source",
    "parse",
    "tokenize",
]
