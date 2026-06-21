from pathlib import Path

from bench.agentic.verify import run_verify


def _script(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "verify.sh"
    p.write_text("#!/bin/sh\n" + body + "\n")
    return p


def test_exit_zero_passes(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    res = run_verify(_script(tmp_path, "exit 0"), work, timeout_s=10)
    assert res.passed and res.exit_code == 0 and not res.timed_out


def test_nonzero_fails(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    res = run_verify(_script(tmp_path, "exit 3"), work, timeout_s=10)
    assert not res.passed and res.exit_code == 3


def test_timeout_fails(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    res = run_verify(_script(tmp_path, "sleep 5"), work, timeout_s=1)
    assert not res.passed and res.timed_out


def test_runs_in_workdir(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    (work / "marker").write_text("yes")
    res = run_verify(_script(tmp_path, "test -f marker"), work, timeout_s=10)
    assert res.passed


def test_verify_exposes_harness_python_with_pytest(tmp_path):
    # verify.sh scripts call `python3 -m pytest`; the bare system python3 has no
    # pytest. run_verify must put the harness interpreter (which does) on PATH.
    work = tmp_path / "w"
    work.mkdir()
    res = run_verify(
        _script(tmp_path, "python3 -c 'import pytest'"), work, timeout_s=30
    )
    assert res.passed
