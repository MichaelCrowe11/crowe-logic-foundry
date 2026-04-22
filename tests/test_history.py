"""Tests for cli/history.py turn log and replay helpers."""

from __future__ import annotations

import threading

from cli.history import TurnHistory, ensure_history


def test_append_assigns_sequential_indices():
    h = TurnHistory()
    r1 = h.append("first")
    r2 = h.append("second")
    r3 = h.append("third")
    assert r1.index == 1
    assert r2.index == 2
    assert r3.index == 3
    assert len(h) == 3


def test_get_returns_record_by_1_based_index():
    h = TurnHistory()
    h.append("a")
    h.append("b")
    assert h.get(1).user_input == "a"
    assert h.get(2).user_input == "b"


def test_get_returns_none_for_out_of_range():
    h = TurnHistory()
    h.append("only one")
    assert h.get(0) is None
    assert h.get(2) is None
    assert h.get(-1) is None


def test_truncate_after_drops_tail():
    h = TurnHistory()
    for text in ("a", "b", "c", "d", "e"):
        h.append(text)
    dropped = h.truncate_after(2)
    assert len(h) == 2
    assert [r.user_input for r in dropped] == ["c", "d", "e"]
    assert h.get(3) is None


def test_truncate_after_end_is_noop():
    h = TurnHistory()
    h.append("only")
    dropped = h.truncate_after(1)
    assert len(h) == 1
    assert dropped == []


def test_recent_returns_last_n_in_order():
    h = TurnHistory()
    for i in range(10):
        h.append(f"turn-{i}")
    last3 = h.recent(3)
    assert [r.user_input for r in last3] == ["turn-7", "turn-8", "turn-9"]


def test_append_captures_dual_and_synth_metadata():
    h = TurnHistory()
    r = h.append(
        "explain quantum",
        model_label="CroweLM Supreme  ‖  CroweLM Eclipse",
        dual_active=True,
        synth_active=True,
        synth_mode="merge",
    )
    assert r.dual_active is True
    assert r.synth_active is True
    assert r.synth_mode == "merge"


def test_thread_safe_append():
    """Dual-mode worker threads could post concurrently in future."""
    h = TurnHistory()
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        for i in range(50):
            h.append(f"t{i}")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(h) == 200
    # Every record must have a unique increasing index (1..200).
    indices = sorted(h.recent(200), key=lambda r: r.index)
    assert [r.index for r in indices] == list(range(1, 201))


def test_ensure_history_is_session_scoped():
    """Same session_state should return the same TurnHistory instance."""
    state = {}
    h1 = ensure_history(state)
    h1.append("a")
    h2 = ensure_history(state)
    assert h1 is h2
    assert len(h2) == 1
