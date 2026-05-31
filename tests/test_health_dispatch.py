"""_auto_route_available must consult the HealthRegistry circuit breaker so a
tier with an open breaker is skipped during routing.

A fresh registry is injected via monkeypatch (no global-singleton pollution),
and _model_switch_error is mocked reachable so the test isolates the breaker.
"""

import cli.crowe_logic as cl
import config.health as health


def test_auto_route_available_respects_open_breaker(monkeypatch):
    fresh = health.HealthRegistry(failure_threshold=1)
    monkeypatch.setattr(health, "registry", fresh)
    monkeypatch.setattr(cl, "_model_switch_error", lambda c: None)

    cfg = {
        "name": "gpt-5.4-nano",
        "label": "CroweLM Cinder",
        "provider": "azure_openai",
    }
    assert cl._auto_route_available(cfg) is True  # breaker closed

    fresh.record_failure(cfg["name"], "boom")
    assert cl._auto_route_available(cfg) is False  # breaker open
