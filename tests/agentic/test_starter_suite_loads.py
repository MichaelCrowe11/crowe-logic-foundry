from pathlib import Path

from bench.agentic.tasks_io import load_tasks

ROOT = Path("bench/agentic/tasks")


def test_starter_suite_has_at_least_twelve_valid_tasks():
    tasks = [t for t in load_tasks(ROOT) if not t.task_id.startswith("_")]
    assert len(tasks) >= 12
    for t in tasks:
        assert t.prompt and t.meta.timeout_s > 0
        assert (t.seed).is_dir()
