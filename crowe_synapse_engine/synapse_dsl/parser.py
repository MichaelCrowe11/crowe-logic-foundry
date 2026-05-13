"""synapse-agent recursive-descent parser.

Grammar (informal):

    source       := agent_block+
    agent_block  := "agent" (STRING|IDENT) "{" field* "}"
    field        := IDENT ":" value
    value        := STRING | TRIPLE_STRING | NUMBER | IDENT | bool | null | list
    list         := "[" [ value ("," value)* ","? ] "]"
    bool         := "true" | "false"
    null         := "null"

The parser is intentionally small. It returns a list of dicts (one per
agent block) with raw Python values; validation, defaulting, and schema
normalization live in ``compiler.py``.
"""

from __future__ import annotations

from crowe_synapse_engine.synapse_dsl.lexer import Token


class ParseError(Exception):
    """Raised when the token stream doesn't conform to the grammar."""


_LITERAL_KEYWORDS = {"true": True, "false": False, "null": None}


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    def _peek(self, offset: int = 0) -> Token:
        return self.tokens[self.pos + offset]

    def _advance(self) -> Token:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def _expect(self, kind: str, value: str | None = None) -> Token:
        token = self._peek()
        if token.kind != kind or (value is not None and token.value != value):
            wanted = f"{kind}({value!r})" if value else kind
            raise ParseError(
                f"Expected {wanted} at line {token.line}, col {token.col}; "
                f"got {token.kind}({token.value!r})"
            )
        return self._advance()

    def parse_source(self) -> list[dict]:
        agents: list[dict] = []
        while self._peek().kind != "EOF":
            agents.append(self.parse_agent_block())
        return agents

    def parse_agent_block(self) -> dict:
        self._expect("KEYWORD", "agent")
        name_token = self._peek()
        if name_token.kind in ("STRING", "IDENT"):
            name = self._advance().value
        else:
            raise ParseError(
                f"Expected agent name at line {name_token.line}; got {name_token.kind}"
            )
        self._expect("LBRACE")
        fields: dict[str, object] = {"name": name}
        while self._peek().kind != "RBRACE":
            key, value = self.parse_field()
            fields[key] = value
        self._expect("RBRACE")
        return fields

    def parse_field(self) -> tuple[str, object]:
        key_token = self._peek()
        if key_token.kind not in ("IDENT", "KEYWORD"):
            raise ParseError(
                f"Expected field key (identifier) at line {key_token.line}; "
                f"got {key_token.kind}"
            )
        key = self._advance().value
        self._expect("COLON")
        value = self.parse_value()
        return key, value

    def parse_value(self) -> object:
        token = self._peek()
        if token.kind in ("STRING", "TRIPLE_STRING"):
            return self._advance().value
        if token.kind == "NUMBER":
            raw = self._advance().value
            return float(raw) if "." in raw else int(raw)
        if token.kind == "KEYWORD" and token.value in _LITERAL_KEYWORDS:
            return _LITERAL_KEYWORDS[self._advance().value]
        if token.kind == "IDENT":
            return self._advance().value
        if token.kind == "LBRACKET":
            return self.parse_list()
        raise ParseError(
            f"Unexpected token in value position at line {token.line}: "
            f"{token.kind}({token.value!r})"
        )

    def parse_list(self) -> list:
        self._expect("LBRACKET")
        items: list[object] = []
        if self._peek().kind != "RBRACKET":
            items.append(self.parse_value())
            while self._peek().kind == "COMMA":
                self._advance()
                if self._peek().kind == "RBRACKET":
                    break
                items.append(self.parse_value())
        self._expect("RBRACKET")
        return items


def parse(tokens: list[Token]) -> list[dict]:
    """Parse a token stream into a list of agent-spec dicts."""
    return _Parser(tokens).parse_source()
