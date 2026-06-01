"""Auto-routing must be on by default (CROWE_LOGIC_AUTO_ROUTE=0 disables)."""

from cli.crowe_logic import _auto_route_enabled


def test_routing_on_by_default(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_AUTO_ROUTE", raising=False)
    assert _auto_route_enabled() is True


def test_routing_can_be_disabled(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_AUTO_ROUTE", "0")
    assert _auto_route_enabled() is False


def test_routing_explicit_on_still_works(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_AUTO_ROUTE", "1")
    assert _auto_route_enabled() is True
