"""The default TTFT first-token budget must be 5s (bounded hedge), not 60s."""


def test_default_ttft_budget_is_5s_when_env_unset(monkeypatch):
    monkeypatch.delenv("CROWELM_TTFT_DEADLINE_SECONDS", raising=False)
    import importlib

    import providers._ttft_watchdog as w

    # Reloading rebinds the module's globals — including the TTFTTimeout class —
    # to fresh objects. Other test modules (e.g. test_ttft_watchdog) imported the
    # original class/functions at collection time; if we leave the reloaded module
    # in place, their functions raise the NEW TTFTTimeout while their
    # `pytest.raises(TTFTTimeout)` still references the OLD one, so the exception
    # escapes uncaught. Snapshot and restore the module namespace to keep the
    # collection-time identities intact for everyone else.
    saved = dict(w.__dict__)
    try:
        importlib.reload(w)
        assert w.DEFAULT_TTFT_DEADLINE_SECONDS == 5.0
    finally:
        w.__dict__.clear()
        w.__dict__.update(saved)
