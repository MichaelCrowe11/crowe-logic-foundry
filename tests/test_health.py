"""In-process circuit breaker for model tiers (config/health.py)."""

from config.health import HealthRegistry


def test_opens_after_threshold_failures():
    clock = {"t": 0.0}
    reg = HealthRegistry(
        failure_threshold=2, cooldown_seconds=60, clock=lambda: clock["t"]
    )
    assert reg.is_available("m") is True
    reg.record_failure("m", "boom")
    assert reg.is_available("m") is True  # 1 < threshold
    reg.record_failure("m", "boom")
    assert reg.is_available("m") is False  # breaker open


def test_half_open_after_cooldown_then_close_on_success():
    clock = {"t": 0.0}
    reg = HealthRegistry(
        failure_threshold=1, cooldown_seconds=60, clock=lambda: clock["t"]
    )
    reg.record_failure("m", "boom")
    assert reg.is_available("m") is False
    clock["t"] = 61.0
    assert reg.is_available("m") is True  # half-open probe allowed
    reg.record_success("m")
    clock["t"] = 62.0
    assert reg.is_available("m") is True  # closed


def test_record_ttft_breach_counts_as_failure():
    clock = {"t": 0.0}
    reg = HealthRegistry(
        failure_threshold=1,
        cooldown_seconds=60,
        clock=lambda: clock["t"],
        ttft_budget_seconds=5.0,
    )
    reg.record_ttft("m", 9.0)
    assert reg.is_available("m") is False


def test_fail_open_on_internal_error():
    reg = HealthRegistry(failure_threshold=1, cooldown_seconds=60)
    reg._states = None  # type: ignore  # corrupt internal state
    assert reg.is_available("m") is True  # must not raise; default available
