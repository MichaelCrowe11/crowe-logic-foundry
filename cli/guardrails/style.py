"""
StyleEnforcer: normalize output to match user MEMORY style rules.

Rules enforced (from /Users/crowelogic/.claude/projects/-Users-crowelogic/memory):
    feedback_no_em_dashes.md  - no em-dash characters in any generated content.
    feedback_no_emojis.md     - no emojis unless custom-designed by Crowe Logic.

The em-dash rule is rewriting (auto-fix). The emoji rule is detection plus
reporting; we do not silently strip emoji from rendered output because doing so
may corrupt code blocks that intentionally include unicode glyphs. Instead we
emit a StyleIssue and let the chain decide whether to surface it as a warning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# U+2014 (em dash) and U+2013 (en dash, often misused as em).
# We only rewrite the genuine em dash by default; en dash sometimes is
# legitimately a range separator (e.g. "5-10" rendered as "5 to 10" would be
# wrong if the model meant 5-en-10).
_EM_DASH = "—"
_EM_DASH_REGEX = re.compile(r"\s*—\s*")

# Emoji unicode blocks. Conservative list - covers the common emoji ranges.
# We do NOT strip these from output; we report them.
_EMOJI_REGEX = re.compile(
    "["
    "\U0001F300-\U0001F5FF"  # symbols and pictographs
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport and map
    "\U0001F700-\U0001F77F"  # alchemical
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess, etc.
    "\U0001FA70-\U0001FAFF"  # symbols and pictographs extended-A
    "\U00002600-\U000027BF"  # misc symbols and dingbats
    "]"
)


@dataclass(frozen=True)
class StyleIssue:
    """A style violation found in output."""

    kind: str  # "em_dash" or "emoji"
    count: int
    sample: str  # short illustrative excerpt


class StyleEnforcer:
    """Apply style rules to generated text.

    Returns (cleaned_text, issues). Em-dashes are rewritten. Emoji is reported
    but left in place; the chain may strip or surface based on policy.
    """

    def __init__(self, rewrite_em_dash: bool = True, strip_emoji: bool = False):
        self.rewrite_em_dash = rewrite_em_dash
        self.strip_emoji = strip_emoji

    def enforce(self, text: str) -> tuple[str, list[StyleIssue]]:
        if not text:
            return text, []
        issues: list[StyleIssue] = []
        cleaned = text

        em_count = cleaned.count(_EM_DASH)
        if em_count:
            sample = self._sample_around(cleaned, _EM_DASH)
            issues.append(StyleIssue(kind="em_dash", count=em_count, sample=sample))
            if self.rewrite_em_dash:
                cleaned = _EM_DASH_REGEX.sub(" - ", cleaned)

        emoji_matches = _EMOJI_REGEX.findall(cleaned)
        if emoji_matches:
            issues.append(
                StyleIssue(
                    kind="emoji",
                    count=len(emoji_matches),
                    sample="".join(emoji_matches[:8]),
                )
            )
            if self.strip_emoji:
                cleaned = _EMOJI_REGEX.sub("", cleaned)

        return cleaned, issues

    @staticmethod
    def _sample_around(text: str, marker: str, radius: int = 24) -> str:
        idx = text.find(marker)
        if idx < 0:
            return ""
        start = max(0, idx - radius)
        end = min(len(text), idx + len(marker) + radius)
        snippet = text[start:end].replace("\n", " ")
        return snippet
