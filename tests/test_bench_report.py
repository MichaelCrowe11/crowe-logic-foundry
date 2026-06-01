import json

from bench.report import build_scoreboard


def test_track_a_accuracy_table(tmp_path):
    rows = [
        {
            "track": "a",
            "condition": "default",
            "tier": "gpt-5.4",
            "question_id": "q1",
            "score": 1.0,
        },
        {
            "track": "a",
            "condition": "default",
            "tier": "gpt-5.4",
            "question_id": "q2",
            "score": 0.0,
        },
    ]
    p = tmp_path / "scored.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    md = build_scoreboard(p)
    assert "Track A" in md
    assert "CroweLM Helio" in md  # gpt-5.4 renders as its CroweLM brand label
    assert "50.0%" in md  # 1 of 2 correct


def test_track_b_delta_table(tmp_path):
    rows = [
        {
            "track": "b",
            "condition": "grounded",
            "tier": "gpt-5.4",
            "question_id": "m1",
            "score": 5,
        },
        {
            "track": "b",
            "condition": "bare",
            "tier": "gpt-5.4",
            "question_id": "m1",
            "score": 2,
        },
    ]
    p = tmp_path / "scored.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    md = build_scoreboard(p)
    assert "grounded" in md.lower() and "bare" in md.lower()
    assert "CroweLM Helio" in md  # gpt-5.4 renders as its CroweLM brand label
    assert ("delta" in md.lower()) or ("Δ" in md)
    assert "+3" in md  # grounded 5 - bare 2 = +3.00


def test_empty_results_does_not_crash(tmp_path):
    p = tmp_path / "scored.jsonl"
    p.write_text("")
    md = build_scoreboard(p)
    assert isinstance(md, str)


def test_scoreboard_shows_crowelm_brand_not_backend(tmp_path):
    """Public/website-facing scoreboard must show CroweLM brand names, never the
    underlying vendor backend. gpt-5.4 -> 'CroweLM Helio'."""
    rows = [
        {
            "track": "a",
            "condition": "default",
            "tier": "gpt-5.4",
            "question_id": "q1",
            "score": 1.0,
        },
        {
            "track": "b",
            "condition": "grounded",
            "tier": "gpt-5.4",
            "question_id": "m1",
            "score": 4,
        },
        {
            "track": "b",
            "condition": "bare",
            "tier": "gpt-5.4",
            "question_id": "m1",
            "score": 2,
        },
    ]
    p = tmp_path / "scored.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    md = build_scoreboard(p)
    assert "CroweLM Helio" in md
    # no raw vendor backend strings may leak into a published scoreboard
    for vendor in ("gpt-5.4", "claude-", "Kimi-K2", "DeepSeek", "grok-", "Llama-"):
        assert vendor not in md, f"vendor name leaked: {vendor}"
