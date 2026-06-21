from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_REQUIRED_META = ("lang", "difficulty", "tags", "timeout_s", "max_rounds")


@dataclass
class TaskMeta:
    lang: str
    difficulty: str
    tags: list[str]
    timeout_s: int
    max_rounds: int


@dataclass
class Task:
    task_id: str
    seed: Path
    prompt: str
    verify: Path
    meta: TaskMeta


def _load_one(task_dir: Path) -> Task:
    meta_path = task_dir / "meta.json"
    prompt_path = task_dir / "prompt.txt"
    verify_path = task_dir / "verify.sh"
    seed_path = task_dir / "seed"
    for p in (meta_path, prompt_path, verify_path, seed_path):
        if not p.exists():
            raise ValueError(f"{task_dir.name}: missing {p.name}")
    raw = json.loads(meta_path.read_text())
    missing = [k for k in _REQUIRED_META if k not in raw]
    if missing:
        raise ValueError(f"{task_dir.name}: meta.json missing keys {missing}")
    meta = TaskMeta(
        lang=raw["lang"],
        difficulty=raw["difficulty"],
        tags=list(raw["tags"]),
        timeout_s=int(raw["timeout_s"]),
        max_rounds=int(raw["max_rounds"]),
    )
    return Task(
        task_id=task_dir.name,
        seed=seed_path,
        prompt=prompt_path.read_text().strip(),
        verify=verify_path,
        meta=meta,
    )


def load_tasks(root: Path) -> list[Task]:
    root = Path(root)
    dirs = sorted(
        d for d in root.iterdir() if d.is_dir() and (d / "meta.json").exists()
    )
    return [_load_one(d) for d in dirs]
