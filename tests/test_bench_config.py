from bench import config


def test_flagship_tiers_are_a_nonempty_list():
    assert isinstance(config.FLAGSHIP_TIERS, list)
    assert config.FLAGSHIP_TIERS  # non-empty smoke default


def test_judge_tier_is_set():
    assert isinstance(config.JUDGE_TIER, str) and config.JUDGE_TIER


def test_results_dir_path():
    assert config.RESULTS_DIR.name == "results"
    assert config.DATASETS_DIR.name == "datasets"
