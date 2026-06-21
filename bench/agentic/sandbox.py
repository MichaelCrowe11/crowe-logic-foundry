from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def sandbox(seed: Path) -> Iterator[Path]:
    """Copy seed/ into a fresh tmp dir, yield it, guarantee teardown."""
    seed = Path(seed)
    if not seed.is_dir():
        raise ValueError(f"seed dir not found: {seed}")
    tmp = Path(tempfile.mkdtemp(prefix="agentic-bench-"))
    work = tmp / "work"
    shutil.copytree(seed, work)
    try:
        yield work
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
