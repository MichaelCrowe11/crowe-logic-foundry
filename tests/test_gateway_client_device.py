"""Tests for gateway_client device-token support and defaults."""
import importlib
import pytest


def test_default_gateway_is_api_crowelogic(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_GATEWAY_URL", raising=False)
    import cli.gateway_client as gc
    try:
        importlib.reload(gc)
        assert gc.GATEWAY_BASE == "https://api.crowelogic.com"
    finally:
        importlib.reload(gc)  # restore module state for the rest of the suite


def test_device_store_roundtrip(tmp_path, monkeypatch):
    import cli.gateway_client as gc

    store = tmp_path / "device.json"
    monkeypatch.setattr(gc, "DEVICE_STORE", str(store))
    gc.save_device({"device_id": "d1", "token": "crowe_anon_d1.sig"})
    assert oct(store.stat().st_mode & 0o777) == "0o600"
    assert gc.load_device()["device_id"] == "d1"


def test_load_device_missing_returns_none(tmp_path, monkeypatch):
    import cli.gateway_client as gc

    monkeypatch.setattr(gc, "DEVICE_STORE", str(tmp_path / "nope.json"))
    assert gc.load_device() is None


def test_load_device_corrupt_returns_none(tmp_path, monkeypatch):
    import cli.gateway_client as gc

    store = tmp_path / "device.json"
    store.write_text("{not json")
    monkeypatch.setattr(gc, "DEVICE_STORE", str(store))
    assert gc.load_device() is None


def test_chat_402_raises_free_tier_capped(monkeypatch):
    import cli.gateway_client as gc

    class FakeResp:
        status_code = 402

        def json(self):
            return {"detail": {"code": "anon_daily_cap", "message": "capped", "upsell": "login"}}

    monkeypatch.setattr(gc.httpx, "post", lambda *a, **kw: FakeResp())
    with pytest.raises(gc.FreeTierCapped) as exc:
        gc.chat("crowelm-mycelium", [{"role": "user", "content": "hi"}], bearer="crowe_anon_x.y")
    assert exc.value.detail["code"] == "anon_daily_cap"
