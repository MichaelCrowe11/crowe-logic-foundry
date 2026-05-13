"""Tests for AICL persistence in MemoryStore.

The persistence layer must round-trip every AICLMessage field through
SQLite, preserve thread order, and create its schema on a fresh DB
without any prior migration. JSON columns (evidence, constraints,
payload) decode back to Python types; ``requires_human`` round-trips
through INTEGER.
"""

from __future__ import annotations

import pytest

from crowe_synapse_engine.aicl import AICLMessage, Act, Conversation
from crowe_synapse_engine.memory import MemoryStore


@pytest.fixture()
def store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "memory.db"))


def test_fresh_store_has_aicl_table(store):
    tables = {
        row[0]
        for row in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "aicl_messages" in tables


def test_record_and_load_single_message(store):
    session_id = store.start_session(thread_id="t1")
    msg = AICLMessage(
        act=Act.INTENT,
        from_agent="research",
        subject="find papers on mycelium computing",
    )
    store.record_aicl_message(session_id, msg)

    rows = store.get_aicl_messages(session_id)
    assert len(rows) == 1
    assert rows[0]["act"] == "intent"
    assert rows[0]["from_agent"] == "research"
    assert rows[0]["subject"] == msg.subject


def test_round_trip_through_conversation(store):
    session_id = store.start_session(thread_id="t2")

    intent = AICLMessage(
        act=Act.INTENT,
        from_agent="orchestrator",
        subject="research mycelium computing",
        confidence=0.9,
        payload={"model": "crowelm-pro", "max_turns": 5},
        constraints=["peer_reviewed_only"],
    )
    delegate = AICLMessage(
        act=Act.DELEGATE,
        from_agent="orchestrator",
        to_agent="deep-researcher",
        subject="search arxiv for Adamatzky",
        parent_message_id=intent.id,
    )
    report = AICLMessage(
        act=Act.REPORT,
        from_agent="deep-researcher",
        to_agent="orchestrator",
        subject="found 3 candidates",
        evidence=["doi:10.1038/x", "arxiv:2406.12345"],
        confidence=0.87,
        parent_message_id=delegate.id,
    )

    for msg in (intent, delegate, report):
        store.record_aicl_message(session_id, msg)

    conv = store.get_aicl_conversation(session_id)
    assert isinstance(conv, Conversation)
    assert len(conv) == 3
    msgs = list(conv)
    assert msgs[0].act == Act.INTENT
    assert msgs[0].confidence == 0.9
    assert msgs[0].payload == {"model": "crowelm-pro", "max_turns": 5}
    assert msgs[0].constraints == ["peer_reviewed_only"]
    assert msgs[2].evidence == ["doi:10.1038/x", "arxiv:2406.12345"]
    # Threading preserved end-to-end.
    assert conv.parent_of(msgs[2]) is msgs[1]
    assert conv.parent_of(msgs[1]) is msgs[0]


def test_requires_human_round_trips_as_bool(store):
    session_id = store.start_session(thread_id="t3")
    msg = AICLMessage(
        act=Act.UNCERTAIN,
        from_agent="x",
        subject="cannot resolve alone",
        requires_human=True,
    )
    store.record_aicl_message(session_id, msg)

    conv = store.get_aicl_conversation(session_id)
    loaded = list(conv)[0]
    assert loaded.requires_human is True


def test_get_aicl_messages_ordered_by_timestamp_then_rowid(store):
    """Even when timestamps tie (same-microsecond inserts), insertion order wins."""
    session_id = store.start_session(thread_id="t4")
    first = AICLMessage(act=Act.INTENT, from_agent="a", subject="one")
    second = AICLMessage(act=Act.INTENT, from_agent="a", subject="two")
    store.record_aicl_message(session_id, first)
    store.record_aicl_message(session_id, second)

    rows = store.get_aicl_messages(session_id)
    assert [r["subject"] for r in rows] == ["one", "two"]
