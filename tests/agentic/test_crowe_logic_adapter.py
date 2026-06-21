from bench.agentic.agents.crowe_logic import parse_events


def test_counts_rounds_and_tools():
    events = [
        {"type": "segment_end"},
        {"type": "tool", "name": "write_file", "args": "code.py"},
        {"type": "segment_end"},
        {"type": "done", "tokens": 1234},
    ]
    p = parse_events(events)
    assert p["rounds"] == 2 and p["tool_calls"] == 1 and p["tokens"] == 1234
    assert p["self_verified"] is False and p["error"] is None


def test_detects_self_verification():
    events = [{"type": "tool", "name": "run_shell", "args": "python -m pytest -q"}]
    assert parse_events(events)["self_verified"] is True


def test_surfaces_error_event():
    events = [{"type": "error", "message": "model failed"}]
    assert parse_events(events)["error"] == "model failed"
