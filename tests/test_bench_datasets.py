import json

from bench import config


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_track_a_slices_are_valid():
    for name in ("gsm8k", "mmlu", "humaneval"):
        path = config.DATASETS_DIR / "track_a" / f"{name}.jsonl"
        rows = _rows(path)
        assert rows, f"{name} empty"
        for r in rows:
            assert {"id", "question", "answer", "type"} <= set(r)


def test_mycology_set_has_source_grounding():
    path = config.DATASETS_DIR / "track_b" / "mycology.jsonl"
    rows = _rows(path)
    assert rows
    for r in rows:
        assert {"id", "question", "source_passage", "reference_answer"} <= set(r)
        assert r["source_passage"].strip()


def test_score_results_file_scores_track_a(tmp_path):
    from bench.scoring import score_results_file

    raw = tmp_path / "raw.jsonl"
    raw.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "track": "a",
                        "condition": "default",
                        "tier": "t",
                        "question_id": "q1",
                        "qtype": "numeric",
                        "expected": "4",
                        "answer": "the answer is 4",
                    }
                ),
                json.dumps(
                    {
                        "track": "a",
                        "condition": "default",
                        "tier": "t",
                        "question_id": "q2",
                        "qtype": "multiple_choice",
                        "expected": "B",
                        "answer": "ANSWER: B",
                    }
                ),
            ]
        )
    )
    scored = tmp_path / "scored.jsonl"
    score_results_file(raw, scored, judge=lambda prompt: "SCORE: 5")
    rows = _rows(scored)
    assert all("score" in r for r in rows)
    assert rows[0]["score"] == 1.0
    assert rows[1]["score"] == 1.0


def test_score_results_file_scores_track_b_with_judge(tmp_path):
    from bench.scoring import score_results_file

    raw = tmp_path / "raw.jsonl"
    raw.write_text(
        json.dumps(
            {
                "track": "b",
                "condition": "grounded",
                "tier": "t",
                "question_id": "m1",
                "source_passage": "Oysters fruit at 60-65% moisture.",
                "reference_answer": "60-65%",
                "answer": "About 60-65% moisture.",
            }
        )
    )
    scored = tmp_path / "scored.jsonl"
    # inject a fake judge so no live API is needed
    score_results_file(raw, scored, judge=lambda prompt: "SCORE: 4")
    rows = _rows(scored)
    assert rows[0]["score"] == 4
