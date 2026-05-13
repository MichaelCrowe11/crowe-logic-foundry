"""AICL tests · acts, message validation, conversation threading, smoke test.

The smoke test simulates a two-agent DELEGATE/REPORT/COMMIT exchange end
to end without invoking any model. It exercises the same emission path
the runtime will use once AICL is wired into agent loops, so when a real
LLM-driven exchange runs, the surrounding machinery is already verified.
"""

from __future__ import annotations

import pytest

from crowe_synapse_engine.aicl import (
    Act,
    AICLMessage,
    AICLValidationError,
    Conversation,
    aicl_chunk,
)
from crowe_synapse_engine.runtime.base import ChunkKind


# ── AICLMessage construction & validation ──────────────────────────────


def test_message_autogenerates_id_and_timestamp():
    msg = AICLMessage(act=Act.INTENT, from_agent="a", subject="x")
    assert msg.id  # non-empty
    assert msg.timestamp  # non-empty
    assert "T" in msg.timestamp  # ISO 8601


def test_message_is_immutable():
    msg = AICLMessage(act=Act.INTENT, from_agent="a", subject="x")
    with pytest.raises(Exception):  # FrozenInstanceError
        msg.subject = "y"  # type: ignore[misc]


def test_message_rejects_invalid_confidence():
    with pytest.raises(AICLValidationError, match="confidence"):
        AICLMessage(act=Act.INTENT, from_agent="a", subject="x", confidence=1.5)
    with pytest.raises(AICLValidationError, match="confidence"):
        AICLMessage(act=Act.INTENT, from_agent="a", subject="x", confidence=-0.1)


def test_message_rejects_missing_from_agent():
    with pytest.raises(AICLValidationError, match="from_agent"):
        AICLMessage(act=Act.INTENT, from_agent="", subject="x")


def test_report_requires_parent_message_id():
    with pytest.raises(AICLValidationError, match="parent_message_id"):
        AICLMessage(act=Act.REPORT, from_agent="a", subject="done")


def test_dispute_requires_parent_message_id():
    with pytest.raises(AICLValidationError, match="parent_message_id"):
        AICLMessage(act=Act.DISPUTE, from_agent="a", subject="wrong")


def test_intent_does_not_require_parent_message_id():
    # INTENT can be unsolicited; DELEGATE/COMMIT/VERIFY likewise.
    AICLMessage(act=Act.INTENT, from_agent="a", subject="planning")
    AICLMessage(act=Act.DELEGATE, from_agent="a", to_agent="b", subject="do X")
    AICLMessage(act=Act.COMMIT, from_agent="a", subject="final")
    AICLMessage(act=Act.VERIFY, from_agent="a", subject="check Y")


# ── Serialization round-trip ────────────────────────────────────────────


def test_to_dict_from_dict_round_trip():
    original = AICLMessage(
        act=Act.DELEGATE,
        from_agent="research-orchestrator",
        to_agent="deep-researcher",
        subject="find recent mycelium computing paper",
        confidence=0.95,
        evidence=["doi:10.1038/s41598-024-12345"],
        constraints=["peer_reviewed_only", "published_2024_or_later"],
        payload={"max_results": 5},
        dialect="research",
    )
    restored = AICLMessage.from_dict(original.to_dict())
    assert restored.to_dict() == original.to_dict()
    assert restored.id == original.id
    assert restored.timestamp == original.timestamp


# ── RuntimeChunk wrapping ───────────────────────────────────────────────


def test_aicl_chunk_emits_correct_runtime_chunk():
    msg = AICLMessage(
        act=Act.DELEGATE,
        from_agent="parent",
        to_agent="child",
        subject="please find X",
    )
    chunk = aicl_chunk(msg)
    assert chunk.kind == ChunkKind.AICL
    assert "delegate" in chunk.text
    assert "parent -> child" in chunk.text
    assert chunk.meta["aicl"]["id"] == msg.id
    # The full message must be reconstructible from chunk.meta.
    assert AICLMessage.from_dict(chunk.meta["aicl"]).subject == msg.subject


# ── Conversation threading ──────────────────────────────────────────────


def test_conversation_tracks_parent_and_children():
    conv = Conversation(topic="test")
    delegate = conv.append(
        AICLMessage(act=Act.DELEGATE, from_agent="a", to_agent="b", subject="do X")
    )
    report = conv.append(
        AICLMessage(
            act=Act.REPORT,
            from_agent="b",
            to_agent="a",
            subject="done",
            parent_message_id=delegate.id,
        )
    )
    assert conv.parent_of(report) is delegate
    assert conv.children_of(delegate) == [report]
    assert conv.parent_of(delegate) is None


def test_conversation_rejects_unknown_parent():
    conv = Conversation()
    with pytest.raises(ValueError, match="not found"):
        conv.append(
            AICLMessage(
                act=Act.REPORT,
                from_agent="b",
                subject="orphan",
                parent_message_id="does-not-exist",
            )
        )


def test_conversation_jsonl_round_trip(tmp_path):
    conv = Conversation(topic="jsonl test")
    a = conv.append(
        AICLMessage(act=Act.DELEGATE, from_agent="a", to_agent="b", subject="X")
    )
    conv.append(
        AICLMessage(
            act=Act.REPORT,
            from_agent="b",
            to_agent="a",
            subject="X done",
            parent_message_id=a.id,
        )
    )
    path = tmp_path / "conv.jsonl"
    conv.write_jsonl(path)
    restored = Conversation.read_jsonl(path, topic="jsonl test")
    assert len(restored) == 2
    assert list(restored)[0].subject == "X"
    assert list(restored)[1].parent_message_id == a.id


# ── Smoke test: two-agent DELEGATE → REPORT → COMMIT round trip ─────────


def test_two_agent_delegate_report_commit_smoke():
    """End-to-end thread that mirrors what the runtime will emit once
    AICL is wired into subagent dispatch. No models involved; this
    verifies the protocol shape and threading invariants the runtime
    relies on.
    """
    conv = Conversation(topic="find mycelium computing paper")

    # 1. Orchestrator delegates research to a subagent.
    delegate = conv.append(
        AICLMessage(
            act=Act.DELEGATE,
            from_agent="research-orchestrator",
            to_agent="deep-researcher",
            subject="find the most recent peer-reviewed paper on mycelium computing",
            constraints=["peer_reviewed_only", "published_2024_or_later"],
            confidence=0.95,
        )
    )

    # 2. Subagent reports back with evidence.
    report = conv.append(
        AICLMessage(
            act=Act.REPORT,
            from_agent="deep-researcher",
            to_agent="research-orchestrator",
            subject="found 3 candidate papers; top match is Adamatzky 2024",
            evidence=[
                "doi:10.1038/s41598-024-12345",
                "arxiv:2406.12345",
            ],
            confidence=0.87,
            parent_message_id=delegate.id,
        )
    )

    # 3. Orchestrator commits the selection.
    commit = conv.append(
        AICLMessage(
            act=Act.COMMIT,
            from_agent="research-orchestrator",
            subject="accept Adamatzky 2024 as primary source",
            confidence=0.92,
            parent_message_id=report.id,
        )
    )

    # Thread invariants.
    assert len(conv) == 3
    assert conv.parent_of(commit) is report
    assert conv.parent_of(report) is delegate
    assert conv.parent_of(delegate) is None
    assert conv.thread_ending_at(commit) == [delegate, report, commit]

    # Each message becomes a RuntimeChunk the runtime can yield.
    for msg in conv:
        chunk = aicl_chunk(msg)
        assert chunk.kind == ChunkKind.AICL
        assert chunk.meta["aicl"]["act"] == msg.act.value

    # The whole exchange persists as JSONL.
    serialized = conv.to_jsonl()
    restored = Conversation.from_jsonl(serialized)
    assert len(restored) == 3
    assert list(restored)[-1].act == Act.COMMIT
