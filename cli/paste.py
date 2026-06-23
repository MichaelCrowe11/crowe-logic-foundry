"""Paste compression for the chat prompt.

A large bracketed paste lands in the prompt as a one-line
`[paste #1: 42 lines]` placeholder instead of flooding the terminal; the full
text expands back when the turn is submitted. The model always receives the
real paste; only the display is compact. Payloads live in a per-turn stash:
the REPL clears it before each prompt and expands after.
"""

from __future__ import annotations

# A paste is "large" (and compresses to a placeholder) past either threshold.
PASTE_LINE_THRESHOLD = 3
PASTE_CHAR_THRESHOLD = 200


class PasteStash:
    """Holds the payloads behind paste placeholders for one input turn.

    `compress()` decides what lands in the visible buffer: small pastes pass
    through verbatim, large ones are stashed and replaced by a numbered
    placeholder. `expand()` restores every intact placeholder on submit; a
    placeholder the user deleted or edited apart expands to nothing extra -
    what they saw is what is sent.
    """

    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    @staticmethod
    def is_large(data: str) -> bool:
        return data.count("\n") + 1 > PASTE_LINE_THRESHOLD or len(data) > PASTE_CHAR_THRESHOLD

    @staticmethod
    def _label(n: int, data: str) -> str:
        lines = data.count("\n") + 1
        size = f"{lines} lines" if lines > 1 else f"{len(data)} chars"
        return f"[paste #{n}: {size}]"

    def compress(self, data: str) -> str:
        """Return the text to insert into the visible buffer for this paste."""
        data = data.replace("\r\n", "\n").replace("\r", "\n")
        if not self.is_large(data):
            return data
        placeholder = self._label(len(self._items) + 1, data)
        self._items[placeholder] = data
        return placeholder

    def expand(self, text: str) -> str:
        """Replace every intact placeholder in `text` with its stashed paste."""
        for placeholder, data in self._items.items():
            text = text.replace(placeholder, data)
        return text

    def clear(self) -> None:
        self._items.clear()


# Singleton shared by the keybinding layer (which compresses on paste) and the
# REPL loops (which clear per turn and expand on submit).
paste_stash = PasteStash()
