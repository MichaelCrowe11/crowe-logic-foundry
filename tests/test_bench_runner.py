import json

from bench import runner
from bench.headless_client import RunResult


def test_runner_writes_one_row_per_run(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner,
        "run_headless",
        lambda prompt, model, tools=True, timeout=300: RunResult(
            answer="42", tokens=1, elapsed_ms=10
        ),
    )
    questions = [{"id": "q1", "question": "2+2?", "answer": "4", "type": "numeric"}]
    out = runner.run_track_a(questions, tiers=["gpt-5.4"], results_dir=tmp_path)
    rows = [json.loads(line) for line in (out / "raw.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["tier"] == "gpt-5.4"
    assert rows[0]["question_id"] == "q1"
    assert rows[0]["condition"] == "default"
    assert rows[0]["track"] == "a"
    assert rows[0]["answer"] == "42"


def test_track_b_runs_two_conditions_per_question(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner,
        "run_headless",
        lambda prompt, model, tools=True, timeout=300: RunResult(answer="x", tokens=1),
    )
    qs = [
        {
            "id": "m1",
            "question": "spawn?",
            "source_passage": "S",
            "reference_answer": "R",
        }
    ]
    out = runner.run_track_b(qs, tiers=["gpt-5.4"], results_dir=tmp_path)
    rows = [json.loads(line) for line in (out / "raw.jsonl").read_text().splitlines()]
    conds = sorted(r["condition"] for r in rows)
    assert conds == ["bare", "grounded"]
    assert all(r["track"] == "b" for r in rows)


def test_runner_is_append_only(tmp_path, monkeypatch):
    monkeypatch.setattr(
        runner,
        "run_headless",
        lambda prompt, model, tools=True, timeout=300: RunResult(answer="a", tokens=1),
    )
    q = [{"id": "q1", "question": "?", "answer": "1", "type": "numeric"}]
    runner.run_track_a(q, tiers=["t1"], results_dir=tmp_path)
    runner.run_track_a(q, tiers=["t2"], results_dir=tmp_path)
    rows = (tmp_path / "raw.jsonl").read_text().splitlines()
    assert len(rows) == 2  # second run appended, did not clobber
