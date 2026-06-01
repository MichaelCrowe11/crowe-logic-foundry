from bench import config, runner


def test_smoke_default_uses_flagships():
    assert runner.resolve_tiers(all_tiers=False, explicit=None) == config.FLAGSHIP_TIERS


def test_all_flag_expands_to_all_chat_tiers(monkeypatch):
    monkeypatch.setattr(runner, "_all_chat_tiers", lambda: ["a", "b", "c"])
    assert runner.resolve_tiers(all_tiers=True, explicit=None) == ["a", "b", "c"]


def test_explicit_tiers_override_everything():
    assert runner.resolve_tiers(all_tiers=True, explicit=["x"]) == ["x"]


def test_all_chat_tiers_excludes_nonchat():
    tiers = runner._all_chat_tiers()
    assert isinstance(tiers, list) and tiers
    # non-chat backends must be excluded
    for bad in ("Cohere-embed-v4", "text-embedding-3-large", "sora-2", "model-router"):
        assert bad not in tiers
