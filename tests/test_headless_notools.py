import json
import subprocess
import sys


def _run(prompt, extra_args):
    proc = subprocess.run(
        [sys.executable, "-m", "cli.headless", "--model", "auto", *extra_args],
        input=json.dumps({"messages": [{"role": "user", "content": prompt}]}),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc


def test_no_tools_flag_is_accepted():
    proc = _run("hi", ["--no-tools"])
    assert "unrecognized arguments" not in proc.stderr


def test_tools_flag_is_accepted():
    proc = _run("hi", ["--tools"])
    assert "unrecognized arguments" not in proc.stderr


def _make_base_provider():
    from providers._shared import BaseOpenAIProvider

    p = BaseOpenAIProvider(model="x", system_instructions="", label="Test")
    p.messages = [{"role": "user", "content": "hi"}]
    return p


def _stream_with_tools(provider, *, tools_enabled, monkeypatch):
    """Call stream_response far enough to pass the tool-loading gate, spying on
    load_tools. The downstream API call has no creds and will raise; we only
    care whether load_tools fired before that."""
    import providers._shared as shared

    called = {"load_tools": False}
    real = shared.load_tools

    def spy():
        called["load_tools"] = True
        return real()

    monkeypatch.setattr(shared, "load_tools", spy)

    class _NullRenderer:
        def start(self):
            pass

        def set_spinner(self, *_a, **_k):
            pass

        def stop_spinner(self):
            pass

        def feed(self, *_a, **_k):
            pass

        def feed_reasoning(self, *_a, **_k):
            pass

        def end_segment(self):
            pass

        def finish(self, *_a, **_k):
            pass

        def abort(self, *_a, **_k):
            pass

        current_segment_text = ""

    try:
        provider.stream_response(
            console=None,
            render_tool_card=lambda *a, **k: None,
            session_state={},
            _get_orchestrator=lambda: None,
            renderer=_NullRenderer(),
            tools_enabled=tools_enabled,
        )
    except Exception:
        pass  # no API creds — expected; the gate already ran
    return called["load_tools"]


def test_no_tools_skips_tool_loading(monkeypatch):
    p = _make_base_provider()
    assert _stream_with_tools(p, tools_enabled=False, monkeypatch=monkeypatch) is False


def test_tools_enabled_loads_tools(monkeypatch):
    # This is the anti-vacuous-pass guard for test_no_tools_skips_tool_loading:
    # if stream_response ever crashed BEFORE the tool-loading gate, this test
    # would fail (load_tools never fires), exposing the false negative.
    p = _make_base_provider()
    assert _stream_with_tools(p, tools_enabled=True, monkeypatch=monkeypatch) is True
