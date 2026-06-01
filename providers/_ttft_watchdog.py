"""
TTFT (time-to-first-token) watchdog.

Eclipse session 2026-04-30: TTFT 1,095s on the Ollama cloud bridge.
Talon session 2026-04-30: TTFT 337s on the same bridge.

Both hung the user for so long that the model's output became irrelevant by
the time it arrived. The fix is a watchdog that cancels a stream if the first
token does not arrive in a bounded time. The caller can then fall through to
the next variant in `TASK_CLASS_FALLBACKS`.

Public surface:

    TTFTTimeout                 - exception raised when the watchdog fires
    with_ttft_watchdog(stream)  - generator wrapper enforcing the deadline
    fallback_chain(...)         - convenience: try a stream, fall through

Design notes:

    - The watchdog only watches the FIRST token. Once a stream has produced
      something, we assume the provider is alive and the user can decide
      whether to wait. ScopeBudget handles the ratio problem after that.
    - Default deadline is 5 seconds. Tunable via env var
      `CROWELM_TTFT_DEADLINE_SECONDS` for ops to dial in per environment.
    - The wrapper is generator-based, not threading-based, to avoid
      cross-thread provider state issues.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Generator, Iterable, Iterator


DEFAULT_TTFT_DEADLINE_SECONDS = float(
    os.environ.get("CROWELM_TTFT_DEADLINE_SECONDS", "5.0")
)


class TTFTTimeout(Exception):
    """Raised when the first token does not arrive before the deadline."""

    def __init__(self, variant: str, deadline_seconds: float, elapsed_seconds: float):
        self.variant = variant
        self.deadline_seconds = deadline_seconds
        self.elapsed_seconds = elapsed_seconds
        super().__init__(
            f"variant {variant!r} produced no token in {elapsed_seconds:.1f}s "
            f"(deadline {deadline_seconds:.1f}s)"
        )


def with_ttft_watchdog(
    stream: Iterable[Any],
    *,
    variant: str = "unknown",
    deadline_seconds: float = DEFAULT_TTFT_DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> Generator[Any, None, None]:
    """Wrap a token stream with a first-token deadline.

    Yields tokens unchanged once the first token arrives; raises TTFTTimeout
    if the deadline elapses with no token produced.

    The deadline is checked when the next iteration begins (i.e., when we
    pull from the underlying iterator). For purely-blocking streams, the
    deadline check happens after the underlying call returns; we cannot
    cancel a stuck `requests.get()` from this layer alone.

    For streams where the underlying provider supports a timeout argument
    (httpx, openai SDK, etc.), pass the same deadline to the provider so
    its own socket timeout fires first.
    """
    iterator: Iterator[Any] = iter(stream)
    started = clock()
    first_token_seen = False
    while True:
        try:
            token = next(iterator)
        except StopIteration:
            if not first_token_seen:
                # Stream ended without producing any token. If the elapsed
                # wall time also exceeded the deadline, treat as timeout.
                elapsed = clock() - started
                if elapsed > deadline_seconds:
                    raise TTFTTimeout(
                        variant=variant,
                        deadline_seconds=deadline_seconds,
                        elapsed_seconds=elapsed,
                    )
            return
        if not first_token_seen:
            # Check the wall time AFTER the first token arrives. The watchdog
            # cannot preempt a blocking next() call, but it can reject a
            # first token that arrived past the deadline; the caller falls
            # through to the next variant. This is the Eclipse 1095s case:
            # the token finally arrives but is no longer worth rendering.
            elapsed = clock() - started
            first_token_seen = True
            if elapsed > deadline_seconds:
                raise TTFTTimeout(
                    variant=variant,
                    deadline_seconds=deadline_seconds,
                    elapsed_seconds=elapsed,
                )
        yield token


def fallback_chain(
    streams: list[Callable[[], Iterable[Any]]],
    *,
    variants: list[str] | None = None,
    deadline_seconds: float = DEFAULT_TTFT_DEADLINE_SECONDS,
    on_fallback: Callable[[str, str, TTFTTimeout], None] | None = None,
) -> Generator[Any, None, None]:
    """Try each stream factory in order; fall through on TTFT timeout.

    Args:
        streams: list of zero-arg callables that return an iterable of tokens.
                 Lazy so we don't open every connection up front.
        variants: optional list of variant names parallel to streams; used in
                  TTFTTimeout messages.
        deadline_seconds: per-variant TTFT deadline.
        on_fallback: optional callback fired when one variant times out and
                     we fall to the next. Useful for telemetry.

    Yields tokens from the first variant that produces one in time.
    Raises TTFTTimeout from the LAST variant if every variant times out.
    """
    variants = variants or [f"variant-{i}" for i in range(len(streams))]
    last_timeout: TTFTTimeout | None = None
    for index, factory in enumerate(streams):
        variant = variants[index] if index < len(variants) else f"variant-{index}"
        try:
            stream = factory()
        except Exception:
            # Provider failed before producing a stream; treat as fallback.
            continue
        try:
            yielded = False
            for token in with_ttft_watchdog(
                stream, variant=variant, deadline_seconds=deadline_seconds
            ):
                yielded = True
                yield token
            if yielded:
                return  # successful completion
        except TTFTTimeout as timeout:
            last_timeout = timeout
            if on_fallback is not None:
                next_variant = (
                    variants[index + 1] if index + 1 < len(variants) else "(none)"
                )
                on_fallback(variant, next_variant, timeout)
            continue
    if last_timeout is not None:
        raise last_timeout
