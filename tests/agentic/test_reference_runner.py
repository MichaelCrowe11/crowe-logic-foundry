from types import SimpleNamespace

from bench.agentic.agents.reference import ReferenceRunner


class _Block(SimpleNamespace):
    pass


def _tool_use(tid, name, inp):
    return _Block(type="tool_use", id=tid, name=name, input=inp)


def test_reference_loop_writes_then_verifies(tmp_path, monkeypatch):
    (tmp_path / "code.py").write_text("def add(a,b):\n    return a-b\n")
    runner = ReferenceRunner()
    calls = {"n": 0}

    def fake_complete(messages, model):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(
                content=[
                    _tool_use(
                        "1",
                        "write_file",
                        {
                            "path": "code.py",
                            "content": "def add(a,b):\n    return a+b\n",
                        },
                    )
                ]
            )
        if calls["n"] == 2:
            return SimpleNamespace(content=[_tool_use("2", "run_tests", {})])
        return SimpleNamespace(content=[_Block(type="text", text="done")])

    monkeypatch.setattr(runner, "_complete", fake_complete)
    res = runner.run(
        prompt="fix add",
        workdir=tmp_path,
        model="opus",
        tools=[],
        max_rounds=10,
        timeout_s=60,
    )
    assert (tmp_path / "code.py").read_text().strip().endswith("return a+b")
    assert res.self_verified is True
    assert res.tool_calls == 2
    assert res.error is None
    assert res.rounds == 3  # write, test, final text-only stop


def test_reference_loop_stops_at_max_rounds(tmp_path, monkeypatch):
    runner = ReferenceRunner()
    monkeypatch.setattr(
        runner,
        "_complete",
        lambda m, model: SimpleNamespace(content=[_tool_use("x", "run_tests", {})]),
    )
    res = runner.run(
        prompt="loop",
        workdir=tmp_path,
        model="opus",
        tools=[],
        max_rounds=4,
        timeout_s=60,
    )
    assert res.rounds == 4
