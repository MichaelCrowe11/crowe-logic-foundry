"""AICL speech acts · the verbs of agent communication.

A small closed vocabulary. Closed because every act has documented
semantics that runtimes and dialects rely on; if a workflow needs a verb
that doesn't fit, encode it in the message ``payload`` rather than
inventing a new act and forking the protocol.

Each act has a documented response contract: REPORT replies to a
DELEGATE, COMMIT closes a thread, DISPUTE references a prior message
that the speaker rejects. Following the contract makes conversations
deterministic to replay and verifiable post hoc.
"""

from __future__ import annotations

from enum import Enum


class Act(str, Enum):
    """The verb of an AICL message.

    Keep the vocabulary small. The semantics matter more than the count.
    """

    INTENT = "intent"
    """Speaker announces what they are about to do. No recipient required.
    Useful for transparency and for hooks to inspect plans before execution."""

    DELEGATE = "delegate"
    """Speaker assigns work to ``to_agent``. Expects a REPORT in reply,
    threaded via ``parent_message_id``."""

    REPORT = "report"
    """Speaker reports the result of work. Typically a reply to a DELEGATE.
    ``evidence`` should cite the artifacts that justify the result."""

    VERIFY = "verify"
    """Speaker asks for confirmation that a claim or result is correct.
    Expects a REPORT (confirming) or DISPUTE (rejecting) in reply."""

    DISPUTE = "dispute"
    """Speaker rejects a prior message. ``parent_message_id`` is required;
    ``evidence`` should cite why the prior message is wrong."""

    COMMIT = "commit"
    """Speaker declares a result final. Closes a thread. No further messages
    in this thread carry decision authority after a COMMIT."""

    UNCERTAIN = "uncertain"
    """Speaker flags low confidence on something they cannot resolve alone.
    Often triggers escalation to a more capable model or to a human."""


# Acts that REQUIRE a parent_message_id. Validation enforces this so
# replies cannot accidentally orphan from their thread.
REPLY_ACTS: frozenset[Act] = frozenset({Act.REPORT, Act.DISPUTE})
