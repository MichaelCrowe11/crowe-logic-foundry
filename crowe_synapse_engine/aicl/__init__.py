"""AICL · Agent Inter-Communication Language.

A protocol layer on top of the synapse runtime. Agents speak AICL to each
other instead of free-form prose, giving every cross-agent exchange a
structured contract: who acts, on whom, with what evidence, at what
confidence, threaded against which prior message. The runtime emits each
AICL message inside a ``RuntimeChunk`` (``kind=ChunkKind.AICL``) so
existing consumers receive AICL events alongside the text/tool stream
with no protocol break.

Design points:

* Messages are immutable (``frozen=True``). Persist by append; never edit.
* IDs and timestamps auto-generate so callers rarely pass them.
* Threading is parent-pointer only. The conversation derives the DAG.
* Dialects are free-form strings; the core stays vocabulary-neutral.
* Persistence is JSONL by convention. ``to_dict()`` / ``from_dict()``
  round-trip every field.

The acts come from agent communication tradition (FIPA ACL, KQML) but
the LLM-native part is that the *recipient* is itself a model capable
of reasoning about the speech act, not a rule engine matching patterns.
"""

from crowe_synapse_engine.aicl.acts import Act
from crowe_synapse_engine.aicl.conversation import Conversation
from crowe_synapse_engine.aicl.messages import (
    AICLMessage,
    AICLValidationError,
    aicl_chunk,
)

__all__ = [
    "AICLMessage",
    "AICLValidationError",
    "Act",
    "Conversation",
    "aicl_chunk",
]
