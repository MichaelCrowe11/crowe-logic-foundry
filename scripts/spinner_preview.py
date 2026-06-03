#!/usr/bin/env python3
"""
Standalone preview for the Crowe Logic slot-machine spinner variants.

Usage
-----
Animated (default) — cycle through every style, ~3.5s each at 24 fps::

    PYTHONPATH=/Users/crowelogic/Projects/crowe-logic-foundry \
        python scripts/spinner_preview.py

Animated, single style::

    PYTHONPATH=/Users/crowelogic/Projects/crowe-logic-foundry \
        python scripts/spinner_preview.py wordmark

Static sampling (no animation; for capturing output) — prints 3 sampled frames
per style at t = 0.0, 0.5, 1.1::

    PYTHONPATH=/Users/crowelogic/Projects/crowe-logic-foundry \
        python scripts/spinner_preview.py --sample
    PYTHONPATH=/Users/crowelogic/Projects/crowe-logic-foundry \
        python scripts/spinner_preview.py --sample classic

Always run with PYTHONPATH pointed at the repo root so ``cli.spinners`` (and the
``cli.branding`` primitives it depends on) import cleanly.
"""

import sys
import time

from rich.console import Console
from rich.live import Live

from cli.spinners import REGISTRY, get_spinner

# Per-style one-line concept blurbs for the header line.
CONCEPTS = {
    "classic": (
        "Casino one-armed-bandit reels: each column free-spins casino glyphs, "
        "then locks in a travelling left-to-right wave (flash-bright crest, "
        "settle to gold) before the whole row re-spins."
    ),
    "wordmark": (
        "Brand slot machine: per-lane letter reels spin fast (dim/blurred) then "
        "LOCK left-to-right, snapping the correct letter in bright crest gold to "
        "spell CROWELOGIC, hold, and re-spin."
    ),
    "cascade": (
        "Parallel reel-drums of glyphs scroll downward through the rows like "
        "slot-machine reels, columns spinning at staggered rates while a bright "
        "crest band sweeps left-to-right lighting each settling reel."
    ),
    "hybrid": (
        "The Crowe sine pulse-wave field, but each lane periodically breaks into "
        "a fast dim/blurred reel-spin that ripples left-to-right then re-locks to "
        "its true sine value with a bright crest flash."
    ),
}

# How long each style animates, and at what refresh rate, in --live mode.
ANIM_SECONDS = 3.5
REFRESH_PER_SECOND = 24

# Static sample timestamps (seconds) used in --sample mode.
SAMPLE_TIMES = (0.0, 0.5, 1.1)


def _header(style: str) -> str:
    """The header line shown above each style's animation / samples."""
    concept = CONCEPTS.get(style, "")
    return f"STYLE: {style} — {concept}"


def _resolve_styles(positional: str | None) -> list[str]:
    """Which styles to show: a single named one, or all in registry order."""
    if positional is None:
        return list(REGISTRY)
    if positional not in REGISTRY:
        valid = ", ".join(REGISTRY)
        print(f"unknown style {positional!r}; choose from: {valid}", file=sys.stderr)
        raise SystemExit(2)
    return [positional]


def run_live(styles: list[str]) -> None:
    """Animate each style in turn under a rich Live loop."""
    console = Console()
    for style in styles:
        console.print()
        console.print(_header(style), style="bold")
        spinner = get_spinner(style)
        start = time.monotonic()
        with Live(
            spinner.frame(start),
            console=console,
            refresh_per_second=REFRESH_PER_SECOND,
            transient=False,
        ) as live:
            while True:
                now = time.monotonic()
                if now - start >= ANIM_SECONDS:
                    break
                live.update(spinner.frame(now))
                time.sleep(1.0 / REFRESH_PER_SECOND)
        console.print()


def run_sample(styles: list[str]) -> None:
    """Print sampled static frames per style (no animation)."""
    console = Console()
    for style in styles:
        console.print(_header(style))
        spinner = get_spinner(style)
        for t in SAMPLE_TIMES:
            console.print(spinner.frame(t))
        console.print()  # blank line between styles


def main(argv: list[str]) -> int:
    sample = False
    positional: str | None = None
    for arg in argv:
        if arg == "--sample":
            sample = True
        elif arg.startswith("-"):
            print(f"unknown flag {arg!r}", file=sys.stderr)
            return 2
        else:
            positional = arg

    styles = _resolve_styles(positional)
    if sample:
        run_sample(styles)
    else:
        run_live(styles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
