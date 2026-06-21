from pathlib import Path

from bench.agentic.agents.stub import StubRunner


def test_stub_applies_mutation(tmp_path):
    def fix(work: Path):
        (work / "patched.txt").write_text("ok")

    r = StubRunner(name="fixer", mutate=fix)
    res = r.run(
        prompt="p", workdir=tmp_path, model="m", tools=[], max_rounds=5, timeout_s=10
    )
    assert (tmp_path / "patched.txt").read_text() == "ok"
    assert res.error is None and res.workdir == tmp_path


def test_stub_can_report_error(tmp_path):
    r = StubRunner(name="crasher", error="boom")
    res = r.run(
        prompt="p", workdir=tmp_path, model="m", tools=[], max_rounds=5, timeout_s=10
    )
    assert res.error == "boom"
