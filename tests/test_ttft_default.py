"""The default TTFT first-token budget must be 5s (bounded hedge), not 60s."""


def test_default_ttft_budget_is_5s_when_env_unset(monkeypatch):
    monkeypatch.delenv("CROWELM_TTFT_DEADLINE_SECONDS", raising=False)
    import importlib

    import providers._ttft_watchdog as w

    importlib.reload(w)
    assert w.DEFAULT_TTFT_DEADLINE_SECONDS == 5.0
