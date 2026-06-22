"""Tests for StreamRenderer live/final panel transitions."""

from __future__ import annotations

from rich.console import Console

import cli.renderer as renderer_mod


class _FakeLive:
    instances: list["_FakeLive"] = []

    def __init__(self, renderable, *, console, refresh_per_second, vertical_overflow, transient):
        self.renderable = renderable
        self.console = console
        self.refresh_per_second = refresh_per_second
        self.vertical_overflow = vertical_overflow
        self.transient = transient
        self.started = False
        self.stopped = False
        self.updates = [renderable]
        self.__class__.instances.append(self)

    def start(self):
        self.started = True

    def update(self, renderable):
        self.renderable = renderable
        self.updates.append(renderable)

    def stop(self):
        self.stopped = True


def _recorded_console(width: int = 100) -> Console:
    return Console(record=True, width=width)


def test_reasoning_live_prints_one_final_summary_panel_for_apex(monkeypatch):
    monkeypatch.setattr(renderer_mod, "Live", _FakeLive)
    monkeypatch.setattr(renderer_mod.StreamRenderer, "_show_header", lambda self: None)
    _FakeLive.instances = []

    console = _recorded_console()
    renderer = renderer_mod.StreamRenderer(
        console,
        "CroweLM Apex",
        show_reasoning=True,
    )

    renderer.feed_reasoning("**Planning** ")
    renderer.feed_reasoning("Plan carefully. ")
    renderer.feed_reasoning("This is a longer internal note that should be compacted into a short summary block.")
    renderer.end_segment()

    output = console.export_text()
    assert output.count("REASONING · summary") == 1
    assert "Planning Plan carefully." in output
    assert "**Planning**" not in output
    assert len(_FakeLive.instances) == 1
    assert _FakeLive.instances[0].transient is True
    assert _FakeLive.instances[0].vertical_overflow == "crop"


def test_reasoning_live_keeps_full_captured_panel_for_prime(monkeypatch):
    monkeypatch.setattr(renderer_mod, "Live", _FakeLive)
    monkeypatch.setattr(renderer_mod.StreamRenderer, "_show_header", lambda self: None)
    _FakeLive.instances = []

    console = _recorded_console()
    renderer = renderer_mod.StreamRenderer(
        console,
        "CroweLM Prime",
        show_reasoning=True,
    )

    renderer.feed_reasoning("Plan carefully.")
    renderer.end_segment()

    output = console.export_text()
    assert output.count("REASONING · captured") == 1
    assert "Plan carefully." in output


def test_answer_live_prints_one_final_answer_panel(monkeypatch):
    monkeypatch.setattr(renderer_mod, "Live", _FakeLive)
    monkeypatch.setattr(renderer_mod.StreamRenderer, "_show_header", lambda self: None)
    _FakeLive.instances = []

    console = _recorded_console()
    renderer = renderer_mod.StreamRenderer(
        console,
        "CroweLM Apex",
        show_streaming=True,
    )

    renderer.feed("Hello")
    renderer.feed(" world")
    renderer.end_segment()

    output = console.export_text()
    assert output.count("ANSWER · final") == 1
    assert "Hello world" in output
    assert len(_FakeLive.instances) == 1
    assert _FakeLive.instances[0].transient is True
    assert _FakeLive.instances[0].vertical_overflow == "crop"


def test_answer_streaming_hidden_by_default_prints_only_final(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_SHOW_STREAMING", raising=False)
    monkeypatch.delenv("CROWELM_SHOW_STREAMING", raising=False)
    monkeypatch.setattr(renderer_mod, "Live", _FakeLive)
    monkeypatch.setattr(renderer_mod.StreamRenderer, "_show_header", lambda self: None)
    _FakeLive.instances = []

    console = _recorded_console()
    renderer = renderer_mod.StreamRenderer(console, "CroweLM")

    renderer.feed("Hello")
    renderer.feed(" world")
    renderer.end_segment()

    output = console.export_text()
    assert "ANSWER · streaming" not in output
    assert output.count("ANSWER · final") == 1
    assert "Hello world" in output
    assert _FakeLive.instances == []


def test_reasoning_hidden_by_default_but_persisted(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_SHOW_REASONING", raising=False)
    monkeypatch.delenv("CROWELM_SHOW_REASONING", raising=False)
    monkeypatch.setattr(renderer_mod, "Live", _FakeLive)
    monkeypatch.setattr(renderer_mod.StreamRenderer, "_show_header", lambda self: None)
    _FakeLive.instances = []

    console = _recorded_console()
    renderer = renderer_mod.StreamRenderer(console, "CroweLM")
    state = {}

    renderer.feed_reasoning("private plan")
    renderer.feed("Final answer")
    renderer.finish(state)

    output = console.export_text()
    assert "REASONING" not in output
    assert "private plan" not in output
    assert "reasoning" not in output
    assert "Final answer" in output
    assert state["last_reasoning_text"] == "private plan"


def test_reasoning_can_be_enabled_with_env(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_SHOW_REASONING", "1")
    monkeypatch.setattr(renderer_mod, "Live", _FakeLive)
    monkeypatch.setattr(renderer_mod.StreamRenderer, "_show_header", lambda self: None)
    _FakeLive.instances = []

    console = _recorded_console()
    renderer = renderer_mod.StreamRenderer(console, "CroweLM Prime")

    renderer.feed_reasoning("Plan carefully.")
    renderer.end_segment()

    output = console.export_text()
    assert output.count("REASONING · captured") == 1
    assert "Plan carefully." in output
