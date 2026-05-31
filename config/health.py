"""In-process circuit breaker for model tiers.

Complements the persisted provider-health in cli/crowe_logic.py: that layer
blocks a whole provider after a provider-wide error; this layer trips a single
model tier after repeated per-tier failures (incl. TTFT-budget breaches) so a
slow/dead tier is skipped for a cooldown instead of retried every turn.

Fail-open: any internal error resolves to "available" so paying users are
never hard-blocked by the health layer itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _State:
    failures: int = 0
    opened_at: float | None = None  # when the breaker tripped open
    half_open: bool = False  # a probe is in flight


@dataclass
class HealthRegistry:
    failure_threshold: int = 3
    cooldown_seconds: float = 60.0
    ttft_budget_seconds: float = 5.0
    clock: "callable" = time.monotonic
    _states: dict = field(default_factory=dict)

    def is_available(self, name: str) -> bool:
        try:
            st = self._states.get(name)
            if st is None or st.opened_at is None:
                return True
            if self.clock() - st.opened_at >= self.cooldown_seconds:
                st.half_open = True  # allow a single probe
                return True
            return False
        except Exception:
            return True  # fail-open

    def record_success(self, name: str) -> None:
        try:
            self._states[name] = _State()
        except Exception:
            pass

    def record_failure(self, name: str, reason: str = "") -> None:
        try:
            st = self._states.setdefault(name, _State())
            st.failures += 1
            st.half_open = False
            if st.failures >= self.failure_threshold and st.opened_at is None:
                st.opened_at = self.clock()
            elif st.opened_at is not None:
                st.opened_at = self.clock()  # re-open after a failed probe
        except Exception:
            pass

    def record_ttft(self, name: str, seconds: float) -> None:
        if seconds > self.ttft_budget_seconds:
            self.record_failure(
                name, f"ttft {seconds:.1f}s > {self.ttft_budget_seconds:.1f}s"
            )
        else:
            self.record_success(name)


# Process-wide singleton consulted by _auto_route_available.
registry = HealthRegistry()
