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


def test_track_b_excludes_tier_missing_a_condition(tmp_path):
    """A grounded-vs-bare delta is undefined without BOTH sides. A tier whose
    grounded rows all failed (only bare data survives) must NOT appear with a
    fabricated 0.00 grounded placeholder — it must be excluded entirely."""
    rows = [
        # healthy tier: both conditions present -> appears
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
        # broken tier: grounded failed (no scored rows), only bare survives -> excluded
        {
            "track": "b",
            "condition": "grounded",
            "tier": "Kimi-K2-6",
            "question_id": "m1",
            "score": None,
        },
        {
            "track": "b",
            "condition": "bare",
            "tier": "Kimi-K2-6",
            "question_id": "m1",
            "score": 0,
        },
    ]
    p = tmp_path / "scored.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    md = build_scoreboard(p)
    assert "| CroweLM Helio |" in md  # healthy tier present as a data row
    # Kimi-K2-6 (missing grounded) must NOT appear as a delta row...
    assert "| CroweLM Hyphae |" not in md
    # ...but is transparently named as excluded (no silent caps).
    assert "Excluded" in md and "CroweLM Hyphae" in md


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
