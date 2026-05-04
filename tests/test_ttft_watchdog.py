"""Tests for providers._ttft_watchdog."""
from __future__ import annotations

import pytest

from providers._ttft_watchdog import (
    TTFTTimeout,
    fallback_chain,
    with_ttft_watchdog,
)


class _FakeClock:
    """Deterministic clock: each call advances by `step` seconds."""

    def __init__(self, step: float = 1.0):
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def test_normal_stream_passes_through() -> None:
    """A stream that yields immediately should be unaffected."""
    out = list(with_ttft_watchdog(iter(["a", "b", "c"]), variant="test"))
    assert out == ["a", "b", "c"]


def test_first_token_arrives_in_time() -> None:
    clock = _FakeClock(step=1.0)
    out = list(
        with_ttft_watchdog(
            iter(["first"]),
            variant="test",
            deadline_seconds=10.0,
            clock=clock,
        )
    )
    assert out == ["first"]


def test_deadline_exceeded_raises_timeout() -> None:
    """If the iterator yields after the deadline check, we already raised."""
    def slow_stream():
        # Simulate a stream that takes too long to yield.
        # We cannot truly sleep in the test; instead we use a fake clock
        # that advances faster than the deadline.
        yield "late"

    clock = _FakeClock(step=70.0)  # each tick = 70s, deadline = 60s

    with pytest.raises(TTFTTimeout) as exc_info:
        list(
            with_ttft_watchdog(
                slow_stream(),
                variant="eclipse",
                deadline_seconds=60.0,
                clock=clock,
            )
        )
    assert exc_info.value.variant == "eclipse"
    assert exc_info.value.deadline_seconds == 60.0


def test_eclipse_2026_04_30_ttft_failure_would_have_fired() -> None:
    """The Eclipse incident: TTFT 1095s with default 60s deadline."""
    def stream_that_hangs():
        yield "this never arrives in time"

    clock = _FakeClock(step=1095.0)
    with pytest.raises(TTFTTimeout) as exc_info:
        list(
            with_ttft_watchdog(
                stream_that_hangs(),
                variant="eclipse",
                deadline_seconds=60.0,
                clock=clock,
            )
        )
    assert exc_info.value.elapsed_seconds > 1000.0


def test_fallback_chain_uses_next_variant_on_timeout() -> None:
    """First variant times out; second produces tokens; we get the second's output."""
    fallback_calls: list[tuple[str, str]] = []

    def slow_first():
        # Stream factory that yields one late token (will trigger timeout).
        return iter(["late"])

    def fast_second():
        return iter(["fast", "tokens"])

    # Use module-level monkey-patched watchdog: we cannot inject the clock
    # into fallback_chain easily, so instead we use a real but very small
    # deadline to force the slow path. Real time-based test.

    out: list[str] = []
    # Mock by giving the first stream a generator that calls a clock pump
    def slow_first_pumped():
        # Sleep long enough to exceed the 0.001s deadline
        import time as _t
        _t.sleep(0.05)
        yield "too late"

    def on_fallback(from_v: str, to_v: str, timeout: TTFTTimeout) -> None:
        fallback_calls.append((from_v, to_v))

    out = list(
        fallback_chain(
            [slow_first_pumped, fast_second],
            variants=["primary", "secondary"],
            deadline_seconds=0.001,
            on_fallback=on_fallback,
        )
    )
    assert out == ["fast", "tokens"]
    assert fallback_calls == [("primary", "secondary")]


def test_fallback_chain_raises_when_all_variants_timeout() -> None:
    import time as _t

    def slow_stream():
        _t.sleep(0.05)
        yield "late"

    with pytest.raises(TTFTTimeout):
        list(
            fallback_chain(
                [slow_stream, slow_stream],
                variants=["a", "b"],
                deadline_seconds=0.001,
            )
        )


def test_first_token_then_long_pause_does_not_trip_watchdog() -> None:
    """Watchdog only watches FIRST token. Subsequent gaps are not its job."""
    def stream():
        yield "first"
        # Subsequent yield comes "later" but watchdog doesn't care.
        yield "second"

    out = list(
        with_ttft_watchdog(
            stream(),
            variant="test",
            deadline_seconds=0.001,
            clock=_FakeClock(step=0.0001),
        )
    )
    assert out == ["first", "second"]


def test_empty_stream_completes_silently() -> None:
    out = list(with_ttft_watchdog(iter([]), variant="test", deadline_seconds=10.0))
    assert out == []
