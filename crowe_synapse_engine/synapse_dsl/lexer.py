"""synapse-agent lexer.

A small regex-based tokenizer. We hand-roll rather than depend on lark
because the grammar is tight (eight token kinds plus a triple-string
escape) and the user's stack already includes other hand-rolled parsers
in the synapse-lang family.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass


class LexError(Exception):
    """Raised when the source contains a character the lexer can't classify."""


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int
    col: int


# Order matters: TRIPLE_STRING must precede STRING; KEYWORD must precede IDENT.
_TOKEN_SPEC: list[tuple[str, str]] = [
    ("COMMENT", r"\#[^\n]*"),
    ("TRIPLE_STRING", r'"""[\s\S]*?"""'),
    ("STRING", r'"(?:\\.|[^"\\])*"'),
    ("NUMBER", r"-?\d+(?:\.\d+)?"),
    ("KEYWORD", r"\b(?:agent|true|false|null)\b"),
    ("IDENT", r"[A-Za-z_][A-Za-z0-9_\-\./]*"),
    ("LBRACE", r"\{"),
    ("RBRACE", r"\}"),
    ("LBRACKET", r"\["),
    ("RBRACKET", r"\]"),
    ("COLON", r":"),
    ("COMMA", r","),
    ("NEWLINE", r"\n"),
    ("WS", r"[ \t\r]+"),
]

_TOKEN_RE = re.compile(
    "|".join(f"(?P<{name}>{pattern})" for name, pattern in _TOKEN_SPEC)
)


def _decode_string(raw: str) -> str:
    """Strip outer quotes and resolve simple escapes from a STRING literal."""
    inner = raw[1:-1]
    # Minimal escape handling: \n \t \" \\
    return (
        inner.replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )


def _decode_triple_string(raw: str) -> str:
    """Strip the triple-quote delimiters and dedent the content.

    Multi-line prompts in the DSL are indented for readability. The
    compiler should receive a clean prompt body, so we ``textwrap.dedent``
    after stripping a single leading and trailing newline.
    """
    inner = raw[3:-3]
    if inner.startswith("\n"):
        inner = inner[1:]
    if inner.endswith("\n"):
        inner = inner[:-1]
    return textwrap.dedent(inner).strip("\n")


def tokenize(source: str) -> list[Token]:
    """Convert source text into a list of tokens.

    Whitespace, newlines, and comments are filtered out of the output —
    the grammar is whitespace-insensitive once tokenized.
    """
    tokens: list[Token] = []
    line = 1
    col = 1
    pos = 0
    while pos < len(source):
        match = _TOKEN_RE.match(source, pos)
        if match is None:
            raise LexError(
                f"Unexpected character {source[pos]!r} at line {line}, col {col}"
            )
        kind = match.lastgroup or ""
        value = match.group()
        if kind == "NEWLINE":
            line += 1
            col = 1
        elif kind in ("WS", "COMMENT"):
            col += len(value)
        else:
            if kind == "STRING":
                token_value: str = _decode_string(value)
            elif kind == "TRIPLE_STRING":
                token_value = _decode_triple_string(value)
            elif kind == "NUMBER":
                token_value = value
            else:
                token_value = value
            tokens.append(Token(kind=kind, value=token_value, line=line, col=col))
            # Update line/col for multi-line triple strings.
            newlines = value.count("\n")
            if newlines:
                line += newlines
                col = len(value) - value.rfind("\n")
            else:
                col += len(value)
        pos = match.end()
    tokens.append(Token(kind="EOF", value="", line=line, col=col))
    return tokens
