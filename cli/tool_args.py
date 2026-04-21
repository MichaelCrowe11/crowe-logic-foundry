"""Resilient tool-argument JSON parser.

Anthropic / OpenAI tool calls are transmitted as a JSON string in the
`arguments` field. Models occasionally emit JSON that isn't strictly
valid. Common failure modes observed in production:

  1. Unescaped newlines inside string values (especially Python source
     with triple-quoted docstrings).
  2. Rich-markup payloads whose inner braces confuse brace-counting
     tokenizers in the model's sampler.
  3. Trailing commas / mismatched quotes in multi-line string values.
  4. Raw tabs inside strings.

Strict ``json.loads`` rejects all of these with ``JSONDecodeError``,
which the provider loops surface as a tool failure. The model often
retries with the same malformed payload, burning a turn and triggering
visible errors.

This module provides ``parse_tool_arguments(args_json)`` which tries
progressively more lenient strategies before giving up:

  - strict json.loads
  - escape-recovery (unescaped \\n, \\t, \\r inside string literals)
  - support for a ``content_b64`` field that the tool dispatcher
    can unwrap back into ``content`` after parsing

Returns ``(parsed_dict, recovered_flag)``. ``recovered_flag`` is True
when the lenient path fired so the call site can log a telemetry event.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any


def parse_tool_arguments(args_json: str | dict) -> tuple[dict[str, Any], bool]:
    """Parse a tool-call arguments blob, recovering from common escape bugs.

    :param args_json: raw arguments string from the model, or a pre-parsed dict
    :return: (parsed_args, recovered), where recovered is True if lenient path fired
    :raises json.JSONDecodeError: when no strategy produces valid JSON
    """
    if isinstance(args_json, dict):
        return _post_process(args_json), False

    if not args_json:
        return {}, False

    try:
        parsed = json.loads(args_json)
        if isinstance(parsed, dict):
            return _post_process(parsed), False
        raise json.JSONDecodeError(
            f"expected object, got {type(parsed).__name__}", args_json, 0
        )
    except json.JSONDecodeError:
        pass

    repaired = _repair_string_escapes(args_json)
    try:
        parsed = json.loads(repaired)
        if isinstance(parsed, dict):
            return _post_process(parsed), True
    except json.JSONDecodeError:
        pass

    parsed = _repair_via_raw_string_extraction(args_json)
    if parsed is not None:
        return _post_process(parsed), True

    raise json.JSONDecodeError(
        "Could not recover malformed tool arguments", args_json, 0
    )


def _post_process(args: dict[str, Any]) -> dict[str, Any]:
    """Unwrap ``content_b64`` into ``content`` if present.

    The ``write_file`` tool (and any future tools that take large text
    payloads) accept a ``content_b64`` alternative so the model can
    bypass JSON string escaping entirely. When present we decode it
    before the tool function ever runs.
    """
    if "content_b64" in args and "content" not in args:
        try:
            raw = args.pop("content_b64")
            if isinstance(raw, str):
                args["content"] = base64.b64decode(raw).decode("utf-8")
        except Exception:
            args["content_b64"] = raw  # restore so the error is visible
    return args


_IN_STRING = re.compile(r'"((?:\\.|[^"\\])*)"')


def _repair_string_escapes(args_json: str) -> str:
    """Escape raw control characters inside double-quoted string literals.

    Walks the blob one character at a time, tracking whether we're inside
    a string literal. When inside, substitutes raw newline/tab/return with
    their escape sequences. Leaves structural whitespace outside strings
    untouched.
    """
    out: list[str] = []
    in_string = False
    escape_next = False

    for ch in args_json:
        if escape_next:
            out.append(ch)
            escape_next = False
            continue

        if ch == "\\":
            out.append(ch)
            escape_next = True
            continue

        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue

        if in_string:
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        else:
            out.append(ch)

    return "".join(out)


_TOP_LEVEL_FIELD = re.compile(
    r'"(?P<key>[A-Za-z_][A-Za-z0-9_]*)"\s*:\s*"(?P<value>.*?)"(?=\s*[,}])',
    re.DOTALL,
)


def _repair_via_raw_string_extraction(args_json: str) -> dict[str, Any] | None:
    """Last-resort recovery: extract top-level `"key": "value"` pairs by hand.

    When ``_repair_string_escapes`` still fails (usually because the model
    emitted unescaped backslash-letter sequences mid-string), we fall back
    to regex-matching flat ``"key": "value"`` pairs at the top level and
    rebuilding a dict. This is lossy for nested structures but recovers
    the common case: {"file_path": "...", "content": "..."}.
    """
    trimmed = args_json.strip()
    if not (trimmed.startswith("{") and trimmed.endswith("}")):
        return None

    body = trimmed[1:-1]
    result: dict[str, Any] = {}

    for match in _TOP_LEVEL_FIELD.finditer(body):
        key = match.group("key")
        raw_value = match.group("value")
        result[key] = _decode_string_value(raw_value)

    return result or None


_ESCAPE_MAP = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
}


def _decode_string_value(raw: str) -> str:
    """Decode a JSON string body, tolerating invalid escape sequences."""
    out: list[str] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt in _ESCAPE_MAP:
                out.append(_ESCAPE_MAP[nxt])
                i += 2
                continue
            if nxt == "u" and i + 5 < len(raw):
                try:
                    out.append(chr(int(raw[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)
