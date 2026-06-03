"""
Crowe Logic CLI — Slot-machine spinner variants.

A collection of "working" animations for the CLI, each styled as a Crowe Logic
slot machine over the brand gold palette. Every variant is a pure, wall-clock
driven renderable: the same ``now`` always yields the same ``rich.text.Text``,
so they animate correctly whether the renderable persists across ``Live``
refreshes or is rebuilt every frame, and stay legible at both 4 fps and 24 fps.

All variants share the branding color/glyph primitives in :mod:`cli.branding`
(``MARK``, ``GOLD_HEX``, ``GOLD_DIM_HEX``, ``_crest_color``, ``_lerp_hex``,
``_SPINNER_PALETTE``) and expose the same surface as the existing
``ThinkingSpinner``: a ``frame(now)`` method and a ``__rich__`` hook.

Public surface:
    - The variant classes (``ClassicReelsSpinner``, ``WordmarkReelsSpinner``,
      ``GlyphCascadeSpinner``, ``HybridWaveReelSpinner``).
    - ``REGISTRY``: short lowercase key -> spinner class.
    - ``STYLES``: ``list(REGISTRY)`` — the available style keys.
    - ``get_spinner(name, label="thinking", **kw)``: factory.
"""

import math
import time as _time

from rich.text import Text

from cli.branding import (
    MARK,
    GOLD_HEX,
    GOLD_DIM_HEX,
    _crest_color,
    _lerp_hex,
    _SPINNER_PALETTE,
)

# Re-exported so consumers can reach the shared crest palette through this module
# (each variant's color logic is anchored to these stops via ``_crest_color``).
__all__ = [
    "ClassicReelsSpinner",
    "WordmarkReelsSpinner",
    "GlyphCascadeSpinner",
    "HybridWaveReelSpinner",
    "REGISTRY",
    "STYLES",
    "get_spinner",
    "_SPINNER_PALETTE",
]


class ClassicReelsSpinner:
    """Crowe Logic 'working' animation styled as a one-armed bandit: each column is
    a slot-machine reel of casino glyphs spinning independently, then locking in a
    travelling left-to-right wave (col c stops when ``now`` crosses ``c*stagger``),
    flashing bright in the drifting crest hue before settling to gold -- after the
    rightmost reel locks the whole row re-spins. Rows are parallel reels desynced by
    a per-row phase so the field never settles flat.

    Motion is wall-clock driven (no frame counter / no global state), so the same
    ``now`` always yields the same Text whether the renderable persists across Live
    refreshes or is rebuilt every frame.
    """

    # Casino reel: digits then suit / luck / money symbols. The signature MARK is a
    # member of the alphabet so a lock can land on the Crowe diamond.
    GLYPHS = "0123456789" + MARK + "♠♣♥$★"  # 0-9 then diamond, suits, $, star

    def __init__(
        self,
        label: str = "thinking",
        *,
        rows: int = 3,
        lanes: int = 14,
        spin_speed: float = 22.0,  # glyphs/sec while a reel is free-spinning
        stagger: float = 0.13,  # seconds between adjacent columns locking
        settle: float = 0.22,  # seconds a just-locked reel flashes before gold
        row_phase: float = 0.10,  # per-row desync of the lock wave (seconds)
        hue_speed: float = 0.35,  # palette stops advanced per second
        **kw,  # tolerate thinking_spinner()'s extra kwargs
    ):
        self.label = label
        self._rows = rows
        self._lanes = lanes
        self._spin_speed = spin_speed
        self._stagger = stagger
        self._settle = settle
        self._row_phase = row_phase
        self._hue_speed = hue_speed
        # One full cycle = every column locks left->right, then a brief all-spin beat.
        self._cycle = lanes * stagger + settle + 0.45

    def _glyph(self, idx: float) -> str:
        g = self.GLYPHS
        return g[int(idx) % len(g)]

    def frame(self, now: float) -> Text:
        """Pure, time-driven slot-machine frame (same ``now`` -> same Text)."""
        text = Text()
        crest = _crest_color(now * self._hue_speed)
        n = len(self.GLYPHS)

        for r in range(self._rows):
            if r == 0:
                text.append(f"{MARK} ", style=f"bold {crest}")
            else:
                text.append("\n  ")  # continuation rows indent under the mark

            # Per-row local clock: rows run the same cycle desynced by row_phase so
            # the lock wave on each lane is offset (parallel reels, not in lockstep).
            row_now = now - r * self._row_phase
            cycle_t = row_now % self._cycle

            for c in range(self._lanes):
                lock_at = c * self._stagger  # when this column stops
                since_lock = cycle_t - lock_at  # >0 once locked this cycle

                if since_lock < 0:
                    # FREE-SPINNING: rapid glyph churn -> motion blur. Step by
                    # floor(now*speed) so it visibly advances even at 4 fps. Offset
                    # per column+row so neighbouring reels don't show the same face.
                    idx = math.floor(row_now * self._spin_speed) + c * 5 + r * 3
                    glyph = self._glyph(idx)
                    # Dim/blurred: blend deep-bronze toward the crest, low t.
                    style = _lerp_hex("#3a3326", crest, 0.18)
                elif since_lock < self._settle:
                    # JUST LOCKED: snap to a stable face and FLASH bright crest, then
                    # ease back toward gold over the settle window.
                    idx = (c * 7 + r * 13) % n  # deterministic locked face
                    glyph = self._glyph(idx)
                    ease = since_lock / self._settle  # 0 (flash) -> 1 (settled)
                    bright = _lerp_hex("#fff0c8", crest, min(1.0, ease * 1.6))
                    style = f"bold {bright}"
                else:
                    # SETTLED: locked face holds, calm gold until the row re-spins.
                    idx = (c * 7 + r * 13) % n
                    glyph = self._glyph(idx)
                    style = _lerp_hex(GOLD_HEX, crest, 0.35)

                text.append(glyph, style=style)

            if r == 0:
                text.append(f"  {self.label}…", style="dim")
        return text

    def __rich__(self) -> Text:
        return self.frame(_time.monotonic())


class WordmarkReelsSpinner:
    """Crowe Logic 'working' animation as a brand slot machine.

    Each lane is a vertical reel of letters (A-Z plus space) that spins fast while
    "thinking", then LOCKS -- left-to-right -- onto the brand word, snapping the
    correct letter in bright crest gold as it lands. Spinning cells render dim and
    flicker through pseudo-random glyphs (motion blur); a settled cell renders
    bright in this frame's drifting crest hue. After the word is fully spelled the
    reels hold briefly, then re-spin. Row 0 spells the target word and is anchored
    by the diamond mark + label; continuation rows spell desynced copies / THINKING.

    Everything derives from the wall clock (no frame counter, no global state), so
    the same `now` always yields the same Text -- safe under single-pane Live or a
    rebuilt-every-frame layout, and legible at both 4 fps and 24 fps because the
    reel index steps on floor(now * spin_speed).
    """

    GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ "  # 27-cell reel alphabet

    def __init__(
        self,
        label: str = "thinking",
        *,
        rows: int = 3,
        lanes: int = 14,
        word: str = "CROWELOGIC",
        spin_speed: float = 22.0,  # reel glyph-steps per second while spinning
        lock_stagger: float = 0.16,  # seconds between adjacent lanes locking
        hold: float = 0.6,  # seconds the fully-spelled word is held
        hue_speed: float = 0.35,  # palette stops advanced per second
        row_words: tuple = ("CROWELOGIC", "THINKING", "CROWELOGIC"),
        **kw,
    ):
        self.label = label
        self._rows = max(1, rows)
        self._lanes = max(1, lanes)
        self._word = word
        self._spin_speed = spin_speed
        self._lock_stagger = lock_stagger
        self._hold = hold
        self._hue_speed = hue_speed
        self._row_words = row_words
        # Each lane locks lock_stagger apart; the full spell finishes after that,
        # then we hold the word, then loop. Rows are offset so they don't lock in
        # unison -- the field cascades.
        self._spell_time = self._lanes * self._lock_stagger
        self._cycle = self._spell_time + self._hold
        self._row_offset = self._lock_stagger * 1.7  # desync between rows

    # -- helpers ---------------------------------------------------------------
    def _target_row_word(self, r: int) -> str:
        """The word a given row is trying to spell, padded/truncated to lanes."""
        w = self._row_words[r % len(self._row_words)] if self._row_words else self._word
        if len(w) >= self._lanes:
            return w[: self._lanes]
        # center-pad with spaces so short words sit mid-field
        total = self._lanes - len(w)
        left = total // 2
        return " " * left + w + " " * (total - left)

    def _spin_glyph(self, now: float, col: int, row: int) -> str:
        """The blurred glyph a still-spinning reel shows: stepped, deterministic.

        Stepping on floor(now * spin_speed) means the reel visibly advances even
        at 4 fps, while staying smooth at 24 fps.
        """
        step = int(math.floor(now * self._spin_speed))
        # mix step with column/row so adjacent reels never show the same letter
        idx = (step * 7 + col * 11 + row * 17) % len(self.GLYPHS)
        return self.GLYPHS[idx]

    # -- frame -----------------------------------------------------------------
    def frame(self, now: float) -> Text:
        """Build the slot-machine frame for an absolute wall-clock time. Pure."""
        text = Text()
        crest = _crest_color(now * self._hue_speed)  # this frame's drifting hue
        blur_color = _lerp_hex("#3a3326", crest, 0.35)  # dim, just-warm spin blur

        for r in range(self._rows):
            if r == 0:
                text.append(f"{MARK} ", style=f"bold {crest}")
            else:
                text.append("\n  ")  # continuation rows indent under the mark

            target = self._target_row_word(r)
            # phase within this row's spin -> lock -> hold cycle
            phase = (now - r * self._row_offset) % self._cycle

            for col in range(self._lanes):
                lock_at = (col + 1) * self._lock_stagger  # when this lane settles
                settled = phase >= lock_at
                if settled:
                    glyph = target[col]
                    if glyph == " ":
                        # a locked blank -- render as a quiet gap
                        text.append(" ")
                        continue
                    # brightness eases up just after the snap, then holds steady
                    age = phase - lock_at
                    if age < 0.09:
                        # the snap flash: brightest, near-white warm
                        style = f"bold {_lerp_hex(crest, '#fff0c8', 0.5)}"
                    else:
                        style = f"bold {crest}"
                    text.append(glyph, style=style)
                else:
                    # still spinning: blurred, dim, fast-cycling random-looking letter
                    glyph = self._spin_glyph(now, col, r)
                    # the lane about to lock next blurs a touch brighter (anticipation)
                    nearness = max(0.0, 1.0 - (lock_at - phase) / self._lock_stagger)
                    if nearness > 0.55:
                        style = _lerp_hex(blur_color, crest, 0.5)
                    else:
                        style = GOLD_DIM_HEX
                    text.append(glyph, style=style)

            if r == 0:
                text.append(f"  {self.label}…", style="dim")
        return text

    def __rich__(self) -> Text:
        return self.frame(_time.monotonic())


class GlyphCascadeSpinner:
    """Crowe Logic 'working' animation: parallel reel-drums of glyphs turning like a
    slot machine. Each column is a vertical reel whose visible glyphs scroll DOWNWARD
    through the stacked rows; columns spin at staggered speeds and a bright crest band
    sweeps left-to-right, lighting each reel as it 'settles'. The drums never lock hard
    -- they read as smooth continuous rotation, the existing waveform evolved into
    rotating drums. Anchored by the diamond mark + label on row 0.

    Pure + time-driven: the visible glyph in cell (row, col) is
        glyph_set[(row*ladder_skew + col + floor(now*reel_rate(col))) % n]
    so the same `now` always yields the same Text. Reels advance in discrete glyph
    steps (floor of time) so motion is legible at 4 fps yet smooth at 24 fps, while
    the hue/crest drift continuously.
    """

    # A rich glyph ladder: pulse blocks rising, then rotational accents (the drum
    # seam), so the reel reads as a turning drum rather than a flat bar.
    _LADDER = "▁▂▃▄▅▆▇█╱◆╲·"  # ▁▂▃▄▅▆▇█╱◆╲·

    def __init__(
        self,
        label: str = "thinking",
        *,
        rows: int = 3,
        lanes: int = 14,
        reel_speed: float = 7.0,
        speed: float | None = None,
        col_skew: float = 0.6,
        ladder_skew: int = 3,
        crest_width: float = 2.4,
        crest_travel: float = 5.5,
        hue_speed: float = 0.35,
        **kw,
    ):
        self.label = label
        self._rows = rows
        self._lanes = lanes
        # `speed` is an alias so a drop-in caller passing speed= still tunes spin.
        self._reel_speed = float(speed) if speed is not None else reel_speed
        self._col_skew = col_skew  # spread of per-reel base speeds
        self._ladder_skew = ladder_skew  # rows offset within the glyph drum
        self._crest_width = crest_width  # half-width (in cols) of the bright band
        self._crest_travel = crest_travel  # cols/sec the crest band sweeps
        self._hue_speed = hue_speed  # palette stops advanced per second
        self._n = len(self._LADDER)

    def _reel_rate(self, col: int) -> float:
        """Per-column reel speed: staggered so columns spin at different rates and
        appear to start/stop in a wave (classic slot-machine desync)."""
        return self._reel_speed * (1.0 + self._col_skew * ((col % 5) - 2) / 4.0)

    def frame(self, now: float) -> Text:
        text = Text()
        crest = _crest_color(now * self._hue_speed)  # this frame's drifting hue
        n = self._n

        # Crest band center sweeps across the columns and wraps; cells near it are the
        # 'settled' bright glyph, cells far from it are still spinning (dim/blurred).
        band_center = (now * self._crest_travel) % self._lanes

        for r in range(self._rows):
            if r == 0:
                text.append(f"{MARK} ", style=f"bold {crest}")
            else:
                text.append("\n  ")  # continuation rows indent under the mark

            for col in range(self._lanes):
                # Reel index: rows offset by ladder_skew so glyphs scroll downward as
                # the drum turns; floor(now*rate) makes it step glyph-by-glyph.
                step = math.floor(now * self._reel_rate(col))
                idx = (r * self._ladder_skew + col + step) % n
                glyph = self._LADDER[idx]

                # Distance of this column from the sweeping crest band (wrapped).
                d = abs(col - band_center)
                d = min(d, self._lanes - d)

                if d <= self._crest_width:
                    # Settled / in the crest: bright, crest hue, sharpened.
                    t = 1.0 - (d / self._crest_width)  # 0..1, 1 at band center
                    style = f"bold {_lerp_hex(crest, '#ffe9c0', 0.35 * t)}"
                elif d <= self._crest_width * 2:
                    # Trailing edge: mid-tone, motion-blurred.
                    style = _lerp_hex("#4a4030", crest, 0.55)
                else:
                    # Spinning fast, far from the band: dim blur.
                    style = GOLD_DIM_HEX
                text.append(glyph, style=style)

            if r == 0:
                text.append(f"  {self.label}…", style="dim")
        return text

    def __rich__(self) -> Text:
        return self.frame(_time.monotonic())


class HybridWaveReelSpinner:
    """Crowe Logic 'working' animation: the gold sine pulse-wave field, but every
    lane periodically *breaks into a spin* — for a short window its cells cycle
    fast through random pulse-blocks (dim, blurred, like a reel in motion), then
    RE-LOCK to their true sine height with a bright crest flash. The spin windows
    are offset per-lane so they ripple across the field left-to-right, making the
    waveform look like it's shuffling and recombining like slot reels while staying
    recognizably the Crowe crest.

    Motion is derived purely from the wall clock (no frame counter, no global
    state): the same `now` always yields the same Text, so it animates correctly
    whether the renderable persists across refreshes or is rebuilt every frame.
    """

    _BLOCKS = "▁▂▃▄▅▆▇█"  # ▁▂▃▄▅▆▇█
    _BLUR_FLOOR = "#3a3324"  # cells mid-spin blur down toward this ink
    _LOCK_FLASH = "#fff0c8"  # warm-white pop the instant a reel locks
    _MID_BED = "#4a4030"  # bed for settled mid-height cells

    def __init__(
        self,
        label: str = "thinking",
        *,
        rows: int = 3,
        lanes: int = 14,
        speed: float = 2.6,  # radians/sec of the underlying sine travel
        spread: float = 0.45,  # phase offset between lanes (crest travel)
        row_phase: float = 1.1,  # phase offset between rows (they desync)
        hue_speed: float = 0.35,  # palette stops advanced per second
        reel_speed: float = 22.0,  # glyph steps/sec while a lane is spinning
        spin_period: float = 2.4,  # seconds between a lane's spin breaks
        spin_window: float = 0.42,  # fraction of the period spent spinning
        ripple: float = 0.16,  # per-lane delay so spins ripple across cols
        **kw,
    ):
        self.label = label
        self._rows = max(1, rows)
        self._lanes = max(1, lanes)
        self._speed = speed
        self._spread = spread
        self._row_phase = row_phase
        self._hue_speed = hue_speed
        self._reel_speed = reel_speed
        self._spin_period = max(0.1, spin_period)
        self._spin_window = min(0.9, max(0.05, spin_window))
        self._ripple = ripple

    @staticmethod
    def _hash(a: int, b: int, c: int) -> int:
        """Deterministic int hash (stateless) for reel glyph/blur scatter."""
        h = (a * 73856093) ^ (b * 19349663) ^ (c * 83492791)
        return h & 0x7FFFFFFF

    def _true_level(self, now: float, i: int, r: int) -> float:
        """The lane's real Crowe sine height in 0..1 (what it locks back to)."""
        return (
            math.sin(now * self._speed - i * self._spread - r * self._row_phase) + 1
        ) / 2

    def _spin_state(self, now: float, i: int, r: int):
        """(spinning, settle) for lane (i,r). `settle` runs 0..1 across the spin
        window: <0.7 = fast random reel; >=0.7 = the lock/flash tail. Each lane's
        window is shifted by `ripple*i` so spins sweep left-to-right like reels."""
        lane_offset = (i * self._ripple) + (r * self._row_phase * 0.25)
        lane_seed = (self._hash(i + 7, r + 3, 0) % 1000) / 1000.0
        cycle_t = (now / self._spin_period) - lane_offset - lane_seed
        frac = cycle_t - math.floor(cycle_t)
        if frac < self._spin_window:
            return True, frac / self._spin_window
        return False, 1.0

    def frame(self, now: float) -> Text:
        """Build the frame for an absolute wall-clock time (pure; render + tests)."""
        text = Text()
        crest = _crest_color(now * self._hue_speed)  # this frame's drifting hue
        top = len(self._BLOCKS) - 1
        for r in range(self._rows):
            if r == 0:
                text.append(f"{MARK} ", style=f"bold {crest}")
            else:
                text.append("\n  ")  # continuation rows indent under the mark
            for i in range(self._lanes):
                level = self._true_level(now, i, r)
                spinning, settle = self._spin_state(now, i, r)

                # Fast reel: random block, blurred dim — reads as motion.
                if spinning and settle < 0.7:
                    step = math.floor(now * self._reel_speed) + i * 2 + r * 5
                    rnd = self._hash(int(step), i + 11, r + 17) % len(self._BLOCKS)
                    blur = 0.25 + 0.35 * (self._hash(int(step) + 1, i, r) % 100) / 100.0
                    text.append(
                        self._BLOCKS[rnd],
                        style=_lerp_hex(self._BLUR_FLOOR, crest, blur),
                    )
                    continue

                glyph = self._BLOCKS[round(level * top)]

                # Lock tail: snap to the true sine height and flash bright.
                if spinning:
                    flash = min(1.0, (settle - 0.7) / 0.3)
                    text.append(
                        glyph,
                        style=f"bold {_lerp_hex(crest, self._LOCK_FLASH, flash * 0.6)}",
                    )
                    continue

                # Settled steady-state: the recognizable Crowe crest field.
                if level > 0.72:
                    style = f"bold {crest}"
                elif level > 0.4:
                    style = _lerp_hex(self._MID_BED, crest, 0.6)
                else:
                    style = GOLD_DIM_HEX
                text.append(glyph, style=style)

            if r == 0:
                text.append(f"  {self.label}…", style="dim")
        return text

    def __rich__(self) -> Text:
        return self.frame(_time.monotonic())


# ── Registry & factory ───────────────────────────────────────────────────────
REGISTRY = {
    "classic": ClassicReelsSpinner,
    "wordmark": WordmarkReelsSpinner,
    "cascade": GlyphCascadeSpinner,
    "hybrid": HybridWaveReelSpinner,
}

# Available style keys, in registration order.
STYLES = list(REGISTRY)


def get_spinner(name: str, label: str = "thinking", **kw):
    """Construct a spinner variant by its short registry key.

    :param name: one of ``STYLES`` ("classic", "wordmark", "cascade", "hybrid").
    :param label: the trailing label rendered after the mark (e.g. "thinking").
    :param kw: forwarded to the chosen spinner's constructor.
    :return: an instantiated spinner with ``frame(now)`` and ``__rich__``.
    """
    return REGISTRY[name](label, **kw)
