---
title: AICL Protocol Specification
version: 0.1
status: Draft
date: 2026-05-13
author: Michael Crowe
implementation: crowe_synapse_engine/aicl/
---

# AICL: Agent Inter-Communication Language

A protocol for structured agent-to-agent communication. Every message
has a verb, a sender, a subject, an evidence basis, a confidence, and
a parent pointer. Agents can be reasoned about, audited, replayed,
verified, and disputed because every exchange has the same shape.

## 0. TL;DR

```python
from crowe_synapse_engine.aicl import Act, AICLMessage, Conversation

conv = Conversation(topic="find recent mycelium computing paper")

delegate = conv.append(AICLMessage(
    act=Act.DELEGATE,
    from_agent="research-orchestrator",
    to_agent="deep-researcher",
    subject="find the most recent peer-reviewed paper",
    constraints=["peer_reviewed_only", "published_2024_or_later"],
    confidence=0.95,
))

report = conv.append(AICLMessage(
    act=Act.REPORT,
    from_agent="deep-researcher",
    to_agent="research-orchestrator",
    subject="found 3 candidates; top is Adamatzky 2024",
    evidence=["doi:10.1038/s41598-024-12345", "arxiv:2406.12345"],
    confidence=0.87,
    parent_message_id=delegate.id,
))

conv.append(AICLMessage(
    act=Act.COMMIT,
    from_agent="research-orchestrator",
    subject="accept Adamatzky 2024 as primary source",
    parent_message_id=report.id,
))

conv.to_jsonl()  # persist; later: Conversation.from_jsonl(text)
```

That is the protocol. The rest of this document is what each field
means, what guarantees the runtime makes, and what is intentionally
left for later versions.

## 1. Motivation

Multi-agent systems built on LLMs converse in prose. Prose is
expressive but lossy: an agent's plan, its uncertainty, the evidence
it relied on, what it expects in reply, and which prior claim it is
rejecting are all encoded as natural language that the receiving model
must re-parse on every turn. This loses information at every hop,
makes audit impossible, and makes regression testing of agent behavior
practically intractable.

AICL replaces the prose envelope with a structured contract:

* **verb** (the speech act) is the type of move being made
* **sender** and **recipient** are explicit agent identifiers
* **subject** is the one-line summary of what is being said
* **evidence** cites the artifacts that justify the move
* **confidence** quantifies the speaker's certainty
* **parent_message_id** threads replies into a verifiable DAG
* **constraints** declare any limits that bind the recipient
* **payload** carries arbitrary dialect-specific content

The natural-language part still exists, inside the payload or as the
text the model writes around the AICL message. AICL is the control
layer under the conversation, not a replacement for it.

## 2. Architecture position

AICL sits one layer above the runtime and one layer below the agent
implementation:

```
┌────────────────────────────────────────────────────────────────┐
│ AGENT (model + tools + system prompt + permission policy)      │
│ produces and consumes AICL messages alongside its prose output │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│ AICL (this document)                                           │
│ acts, messages, conversations, validation, persistence         │
└──────────────────────────────┬─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│ RUNTIME (crowe_synapse_engine.runtime)                         │
│ streaming, tool calls, hooks, provider dispatch                │
│ emits each AICL message as RuntimeChunk(kind=AICL)             │
└────────────────────────────────────────────────────────────────┘
```

The runtime knows AICL exists (it emits AICL boundary messages around
each agent turn). AICL does not know which runtime is below it. New
runtimes (a Rust port, a different SDK) speak AICL by emitting the
same chunk shape.

## 3. Speech acts

The speech-act vocabulary is closed. Seven verbs cover every move an
agent needs to make at this layer. If a workflow needs an unmodeled
verb, encode it in `payload` rather than inventing a new act.

| Act         | Meaning                                                                  | Parent required? |
|-------------|--------------------------------------------------------------------------|------------------|
| `INTENT`    | Speaker announces what they are about to do                              | no               |
| `DELEGATE`  | Speaker assigns work to `to_agent`; expects a `REPORT` in reply          | no               |
| `REPORT`    | Speaker reports the result of work; typically replies to a `DELEGATE`    | **yes**          |
| `VERIFY`    | Speaker asks recipient to confirm a claim or result                      | no               |
| `DISPUTE`   | Speaker rejects a prior message and cites why                            | **yes**          |
| `COMMIT`    | Speaker declares a result final; closes a thread                         | no               |
| `UNCERTAIN` | Speaker flags low confidence on something they cannot resolve alone      | no               |

Two of these are **reply acts**: `REPORT` and `DISPUTE`. They require
`parent_message_id`. The runtime rejects construction without one.

### 3.1 Why this set

`DELEGATE`, `REPORT`, `VERIFY`, `DISPUTE`, `COMMIT` cover task
lifecycle. `INTENT` covers transparency about plans before action.
`UNCERTAIN` covers escalation to a more capable model or to a human.
Everything else (negotiation, commitment protocols, proposal
exchanges) reduces to repeated patterns of these seven plus a payload
that names the specific protocol being followed.

### 3.2 Relationship to FIPA ACL and KQML

This vocabulary is consciously thinner than FIPA ACL (1997) or KQML
(1993). Those protocols had 20+ performatives because their target
agents were rule engines that needed every protocol move explicit.
LLM-driven agents are general reasoners; seven verbs plus payload
is sufficient because the recipient can interpret nuance. AICL is
ACL-shaped but LLM-native.

## 4. Message schema

Every AICL message is a frozen record with the following fields. Field
names are normative; persistence formats use them verbatim.

| Field               | Type            | Required | Default          | Notes                                               |
|---------------------|-----------------|----------|------------------|-----------------------------------------------------|
| `id`                | string          | yes      | uuid4 hex        | 128-bit, lowercase hex, no dashes                   |
| `timestamp`         | string          | yes      | now (UTC, ISO)   | ISO 8601 with offset                                |
| `act`               | Act (string)    | yes      |                  | One of the seven verbs                              |
| `from_agent`        | string          | yes      |                  | Non-empty                                           |
| `to_agent`          | string or null  | no       | null             | null = broadcast / record                           |
| `subject`           | string          | yes      |                  | One-line summary; may be empty for `COMMIT`         |
| `confidence`        | number          | no       | 1.0              | Range [0.0, 1.0]                                    |
| `evidence`          | array<string>   | no       | []               | References (file paths, URLs, message ids)          |
| `constraints`       | array<string>   | no       | []               | Bindings the recipient must honor                   |
| `requires_human`    | boolean         | no       | false            | Escalation hint                                     |
| `parent_message_id` | string or null  | no       | null             | Required when `act` in {`REPORT`, `DISPUTE`}        |
| `payload`           | object          | no       | {}               | Dialect-specific free-form content                  |
| `dialect`           | string          | no       | "core"           | Identifies a vocabulary extension                   |

### 4.1 Validation rules

Construction raises `AICLValidationError` when:

* `from_agent` is empty
* `confidence` is outside [0.0, 1.0]
* `act in {REPORT, DISPUTE}` and `parent_message_id` is null

Messages are otherwise unvalidated at the AICL layer. Semantic
validation (does the cited evidence exist? is the subject coherent?)
is the recipient's job, not the protocol's.

### 4.2 Immutability

Messages are immutable once constructed (`@dataclass(frozen=True)`).
Once a message has an `id`, that id always points to the same content.
This is the foundation of replay, audit, and dispute: a `DISPUTE`
citing message `abc123` references content that cannot have changed.

To "edit" a message, append a new one with an updated `subject` and a
`parent_message_id` pointing to the original.

## 5. Threading

A conversation is a DAG, not a list. Every message except the root has
a `parent_message_id`. A message can have any number of children
(multiple agents may reply to the same delegation; multiple disputes
may target the same report).

```
INTENT (root)
  └─ DELEGATE
       └─ REPORT
            └─ COMMIT
       └─ DISPUTE     (a different agent rejected the same delegation)
            └─ REPORT (the dispute was itself answered)
```

The `Conversation` class indexes parent-child relationships and exposes:

* `parent_of(message)` returns the parent or None
* `children_of(message)` returns the immediate children
* `thread_ending_at(message)` returns the root-to-message chain

There is no protocol-level notion of "the answer" to a question with
multiple replies; that is a dialect or application concern.

## 6. Wire format

### 6.1 In-process: RuntimeChunk

The runtime emits AICL messages alongside text and tool events as a
`RuntimeChunk(kind=ChunkKind.AICL)`. The chunk has a one-line `text`
digest for renderers that don't speak AICL, and the full message in
`meta["aicl"]`:

```python
RuntimeChunk(
    kind=ChunkKind.AICL,
    text="[delegate] research-orchestrator -> deep-researcher: find papers",
    meta={"aicl": message.to_dict()},
)
```

Consumers switch on `chunk.kind`. AICL-aware consumers reconstruct
the full message via `AICLMessage.from_dict(chunk.meta["aicl"])`.

### 6.2 Cross-process: JSONL

The persistence format is newline-delimited JSON. One message per
line, in the order produced by the runtime (insertion order; ties on
timestamp broken by record id):

```jsonl
{"id":"a1...","timestamp":"2026-05-13T...","act":"intent","from_agent":"research","to_agent":null,"subject":"...","confidence":1.0,"evidence":[],"constraints":[],"requires_human":false,"parent_message_id":null,"payload":{},"dialect":"core"}
{"id":"b2...","timestamp":"2026-05-13T...","act":"delegate","from_agent":"research","to_agent":"deep","subject":"...","parent_message_id":"a1...", ...}
```

`Conversation.to_jsonl()` produces this format. `Conversation.from_jsonl(text)`
reconstructs the DAG, validating parent pointers as it goes.

### 6.3 Database

`crowe_synapse_engine.memory.MemoryStore` persists messages in an
`aicl_messages` table keyed by `id`, indexed on `session_id` and
`parent_message_id`. JSON columns hold `evidence`, `constraints`, and
`payload`. The schema is created idempotently on every store startup.

```sql
CREATE TABLE IF NOT EXISTS aicl_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    timestamp TEXT NOT NULL,
    act TEXT NOT NULL,
    from_agent TEXT NOT NULL,
    to_agent TEXT,
    subject TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    parent_message_id TEXT,
    evidence TEXT,         -- JSON
    constraints TEXT,      -- JSON
    requires_human INTEGER DEFAULT 0,
    payload TEXT,          -- JSON
    dialect TEXT DEFAULT 'core',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

`MemoryStore.get_aicl_conversation(session_id)` returns a hydrated
`Conversation`.

## 7. Examples

### 7.1 Lifecycle (runtime-emitted)

Every agent turn emits at least:

```
INTENT  agent: "<one-line user prompt summary>"
... (text, tool calls, reasoning between) ...
COMMIT  agent: "run complete: <stop_reason>"  parent=<INTENT.id>
```

This is the v0 runtime contract. Tool calls and subagent dispatch get
their own AICL events in later versions; v0 only emits boundaries.

### 7.2 Three-message delegation

```python
intent = AICLMessage(
    act=Act.INTENT, from_agent="orchestrator",
    subject="research mycelium computing literature",
)
delegate = AICLMessage(
    act=Act.DELEGATE, from_agent="orchestrator", to_agent="deep-researcher",
    subject="search arxiv + pubmed for Adamatzky 2024",
    constraints=["peer_reviewed_only"],
    parent_message_id=intent.id,
)
report = AICLMessage(
    act=Act.REPORT, from_agent="deep-researcher", to_agent="orchestrator",
    subject="3 candidates; top is doi:10.1038/s41598-024-12345",
    evidence=["doi:10.1038/s41598-024-12345", "arxiv:2406.12345"],
    confidence=0.87,
    parent_message_id=delegate.id,
)
```

### 7.3 Dispute

```python
dispute = AICLMessage(
    act=Act.DISPUTE, from_agent="critic",
    subject="Adamatzky 2024 covers signaling, not computing per se",
    evidence=["section 3 of doi:10.1038/s41598-024-12345"],
    confidence=0.92,
    parent_message_id=report.id,
)
```

### 7.4 Escalation

```python
escalate = AICLMessage(
    act=Act.UNCERTAIN, from_agent="deep-researcher",
    subject="cannot find peer-reviewed source for claim X",
    confidence=0.3,
    requires_human=True,
)
```

## 8. Brand Veil interaction

AICL is the user-visible communication layer. Surface renderings of
AICL messages must not include vendor names. The runtime's dispatcher
is the only component that holds the logical-to-vendor mapping;
everything above the dispatcher refers only to logical CroweLM names
(`crowelm-pro`, `crowelm-talon`, etc.) and the agent names declared
in YAML.

Payloads MAY contain vendor identifiers when the message is internal
(e.g. routing metadata persisted for debugging). Renderers consuming
AICL for end-user display must strip or rewrite `payload.model_*` and
similar vendor strings.

## 9. Dialects

`dialect` is a free-form string identifying a vocabulary extension on
top of core AICL. Examples:

* `"core"` (default): no extensions
* `"research"`: payloads carry `query`, `sources`, `synthesis`
* `"music"`: payloads carry `section`, `key`, `bpm`, `harmonic_intent`
* `"code"`: payloads carry `diff`, `file_paths`, `test_status`

Dialects do not add new acts; they add structured fields inside
`payload`. A dialect's schema lives in a separate document.

## 10. Replay

Because every message is immutable and threading is explicit, a
recorded JSONL file is sufficient to replay the conversation. Replay
semantics:

* Re-execute each `DELEGATE` against the same agent runtime
* Compare the new `REPORT.subject` and `REPORT.evidence` to the recorded one
* Diverging on `subject` is expected (model nondeterminism); diverging on
  `evidence` set is a regression worth investigating

A reference replay function is not in v0 of the implementation. The
file format guarantees that one can be added later without changing
the protocol.

## 11. What's NOT in v0

This specification covers what the v0 implementation provides. The
following are intentionally deferred:

* **Subagent dispatch**: the runtime does not yet spawn a sub-loop on
  `DELEGATE`. Currently `DELEGATE` is constructed by application code,
  not by the runtime itself, when one agent decides to call another.
  v0.2 wires this into `SynapseRuntime.run()` so subagents inside the
  same process exchange real AICL.
* **Replay function**: spec'd, not implemented. v0.2 or v0.3.
* **Dialect schemas**: dialects exist as free-form strings; dedicated
  schema docs come when ≥3 real agents use the same dialect and
  patterns stabilize.
* **Cross-process AICL transport**: the JSONL format is portable, but
  no daemon or message bus delivers AICL between processes today.
  Plan 2 of the Crowe Cortex extraction adds a length-prefixed JSON
  wire format (CSEP) for IPC; AICL flows inside CSEP frames.

## 12. Versioning

The protocol version is in this document's frontmatter. Each version
bump documents:

* New fields added (always optional)
* New acts added (additive only; existing acts never change semantics)
* Validation rule changes (must remain backward-compatible)

Removing or changing the semantics of an existing field or act
requires a major-version bump and a documented migration path.

Implementations declare which version they support. The Conversation
class will refuse to load a JSONL file whose first message carries a
`dialect` extension the implementation doesn't recognize, unless
constructed with `strict=False`.

## 13. Reference implementation

`crowe_synapse_engine/aicl/` in this repository.

| File             | Contents                                              |
|------------------|-------------------------------------------------------|
| `acts.py`        | `Act` enum, `REPLY_ACTS` constraint set               |
| `messages.py`    | `AICLMessage`, `AICLValidationError`, `aicl_chunk()`  |
| `conversation.py`| `Conversation` class, JSONL round-trip                |
| `__init__.py`    | Public exports                                        |

Runtime integration: `crowe_synapse_engine/runtime/synapse.py` emits
`INTENT` and `COMMIT` around each `SynapseRuntime.run()` invocation.

Persistence: `crowe_synapse_engine/memory.py` provides
`record_aicl_message` and `get_aicl_conversation`.

CLI: `cli/synapse_cli.py` exposes `synapse run / list / show`, where
`show` renders a recorded conversation from MemoryStore.

Tests: `tests/test_aicl.py`, `tests/test_memory_aicl.py`,
`tests/test_synapse_runtime.py`.

## 14. References

* FIPA ACL Specification (1997, revised 2002): https://www.fipa.org/specs/fipa00037/
* KQML: Finin, Fritzson, McKay, McEntire (1994), "KQML as an Agent Communication Language"
* Crowe Cortex Design Spec: `docs/superpowers/specs/2026-04-30-crowe-cortex-design.md`
* CroweLM Engine Extraction Plan: `docs/superpowers/plans/2026-04-30-crowelm-engine-extraction.md`

## 15. Status

Draft, v0.1. The implementation is live in `quality-stack` as of
2026-05-13 and serves real CroweLM agent runs via NVIDIA NIM.
Comments, disputes, or pull requests welcome at the implementation
locations above.
