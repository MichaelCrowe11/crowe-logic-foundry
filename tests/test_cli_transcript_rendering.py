"""Tests for phase-1 CLI transcript rendering primitives."""

from __future__ import annotations

import time

from prompt_toolkit.formatted_text import to_formatted_text
from rich.console import Console

from cli.branding import (
    build_toolbar,
    preview_tool_args,
    record_action,
    render_error,
    render_recent_actions,
    render_session_hud,
    render_tool_card,
    render_transcript_markdown,
    session_state,
    show_last_transcript,
)


def _recorded_console(width: int = 100) -> Console:
    return Console(record=True, width=width)


def test_preview_tool_args_compacts_json_payloads():
    preview = preview_tool_args(
        '{"url":"https://example.com/very/long/path","timeout":5000,"headers":{"a":"b"},"method":"GET"}'
    )

    assert preview.startswith("url=")
    assert "timeout=5000" in preview
    assert "headers=" in preview
    assert "+1 more" in preview


def test_render_transcript_markdown_prints_authored_answer_block():
    console = _recorded_console()

    render_transcript_markdown(console, "# Hello\n\nworld", title="answer", meta="final")

    output = console.export_text()
    assert "ANSWER · final" in output
    assert "Hello" in output
    assert "world" in output


def test_render_tool_card_uses_action_panel_for_success():
    console = _recorded_console()

    render_tool_card(
        console,
        "browser_navigate",
        '{"url":"https://example.com","timeout":5000}',
        status="ok",
        result="loaded page",
        duration_ms=1200,
    )

    output = console.export_text()
    assert "ACTION · ok" in output
    assert "browser_navigate" in output
    assert "url=https://example.com" in output
    assert "loaded (11 chars)" in output


def test_render_error_preserves_literal_detail_text():
    console = _recorded_console()

    render_error(console, "Run Failed", "bad [detail]")

    output = console.export_text()
    assert "ERROR" in output
    assert "Run Failed" in output
    assert "bad [detail]" in output
    assert "\\[detail\\]" not in output


def test_render_session_hud_prints_latest_action_and_metrics():
    console = _recorded_console(width=120)
    state = {
        "started_at": time.monotonic() - 75,
        "tool_count": 3,
        "api_status": "ok",
        "retry_seconds": 0,
        "active_model": "CroweLM Apex",
        "last_tokens": 128,
        "last_tps": 32.0,
        "total_tokens": 512,
        "recent_actions": [],
    }
    record_action(
        state,
        name="browser_navigate",
        status="ok",
        result="loaded page",
        duration_ms=1200,
        args='{"url":"https://example.com"}',
    )

    render_session_hud(console, state=state, cwd="/tmp/crowe-logic-foundry", meta="turn")

    output = console.export_text()
    assert "SESSION · turn" in output
    assert "CroweLM Apex" in output
    assert "crowe-logic-foundry" in output
    assert "browser_navigate ok" in output
    assert "128 tok @ 32/s" in output


def test_render_recent_actions_prints_timeline_panel():
    console = _recorded_console(width=120)
    state = {
        "started_at": time.monotonic() - 10,
        "tool_count": 1,
        "api_status": "ok",
        "retry_seconds": 0,
        "active_model": "CroweLM Apex",
        "last_tokens": 0,
        "last_tps": 0.0,
        "total_tokens": 0,
        "recent_actions": [],
    }
    record_action(
        state,
        name="browser_navigate",
        status="ok",
        result="loaded page",
        duration_ms=1200,
        args='{"url":"https://example.com"}',
    )

    render_recent_actions(console, state=state)

    output = console.export_text()
    assert "TIMELINE · recent" in output
    assert "#1" in output
    assert "browser_navigate" in output
    assert "loaded (11 chars)" in output


def test_show_last_transcript_prints_answer_and_reasoning_panels():
    console = _recorded_console(width=120)
    state = {
        "started_at": time.monotonic(),
        "tool_count": 0,
        "api_status": "ok",
        "retry_seconds": 0,
        "session_id": "",
        "active_model": "CroweLM Apex",
        "steering_instruction": "",
        "dataset_selection": "all",
        "last_tokens": 0,
        "last_tps": 0.0,
        "total_tokens": 0,
        "last_answer_text": "## Result\n\nDone.",
        "last_reasoning_text": "Reasoning details",
        "recent_actions": [],
    }

    show_last_transcript(console, state)

    output = console.export_text()
    assert "ANSWER · last" in output
    assert "REASONING · full" in output
    assert "Done." in output
    assert "Reasoning details" in output


def test_build_toolbar_surfaces_steering_and_dataset_status():
    prior = dict(session_state)
    try:
        session_state.update({
            "started_at": time.monotonic() - 10,
            "tool_count": 1,
            "api_status": "ok",
            "retry_seconds": 0,
            "session_id": "test",
            "active_model": "CroweLM Apex",
            "steering_instruction": "Stay concise",
            "dataset_selection": "specialized_reasoning",
            "last_tokens": 20,
            "last_tps": 10.0,
            "total_tokens": 20,
            "last_answer_text": "",
            "last_reasoning_text": "",
            "recent_actions": [],
        })

        toolbar = build_toolbar()
        rendered = "".join(fragment for _style, fragment in to_formatted_text(toolbar))
        assert "steer" in rendered
        assert "data specialized_reas" in rendered
    finally:
        session_state.clear()
        session_state.update(prior)
