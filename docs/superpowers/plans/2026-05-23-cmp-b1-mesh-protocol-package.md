# CMP B1: `@crowelogic/mesh-protocol` Package, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the `@crowelogic/mesh-protocol` package: the canonical CMP event + frame types in TypeScript AND Python, kept in lockstep by a shared fixture contract. Zero behavior change, no emitters/consumers wired (that is B2-B5).

**Architecture:** A new npm workspace at `~/Projects/crowe-logic-shared/` holds one TS package and a parallel Python package. A single shared `fixtures/` directory (`manifest.json` listing every CMP `type`, `messages.json` with one example per type) is the source of truth. Parity tests in BOTH languages assert: the language's type-union list equals the manifest, every message round-trips through JSON, and the messages cover every manifest type exactly once. Drift in either language fails the other's test by construction.

**Tech Stack:** Node 24 + npm (no pnpm/bun), TypeScript, vitest. Python 3.11 + uv + pytest. Canonical wire format: discriminator `type`, snake_case (decision C1 in the CMP spec).

**Source spec:** `docs/superpowers/specs/2026-05-23-cmp-crowe-mesh-protocol-design.md`. B1 implements the canonical taxonomy (C1-C5) and the attach/control frames. Telemetry events (`telemetry.tick`, `variant.swap`, `cache.hit`, `memory.touch`) are explicitly deferred (YAGNI; the spec marks them optional).

---

## File structure

```
~/Projects/crowe-logic-shared/
  .gitignore
  package.json                      # npm workspace root (private)
  fixtures/
    manifest.json                   # the canonical list of CMP types
    messages.json                   # one example message per type
  packages/mesh-protocol/
    package.json                    # @crowelogic/mesh-protocol
    tsconfig.json
    vitest.config.ts
    src/events.ts                   # in-turn event types + CMP_EVENT_TYPES
    src/frames.ts                   # attach/control frame types + CMP_FRAME_TYPES
    src/index.ts                    # re-exports + cmpType() helper
    test/parity.test.ts             # TS parity test against ../../../fixtures
  python/
    pyproject.toml                  # crowe-mesh-protocol
    crowe_mesh_protocol/
      __init__.py
      events.py                     # event TypedDicts + CMP_EVENT_TYPES
      frames.py                     # frame TypedDicts + CMP_FRAME_TYPES
    tests/test_parity.py            # Python parity test against ../../fixtures
```

---

## Task 1: Scaffold the workspace and both package skeletons

**Files:**
- Create: `~/Projects/crowe-logic-shared/.gitignore`, `package.json`
- Create: `~/Projects/crowe-logic-shared/packages/mesh-protocol/package.json`, `tsconfig.json`, `vitest.config.ts`, `src/index.ts`
- Create: `~/Projects/crowe-logic-shared/python/pyproject.toml`, `crowe_mesh_protocol/__init__.py`

- [ ] **Step 1: Create directories and init git**

```bash
mkdir -p ~/Projects/crowe-logic-shared/packages/mesh-protocol/src ~/Projects/crowe-logic-shared/packages/mesh-protocol/test ~/Projects/crowe-logic-shared/fixtures ~/Projects/crowe-logic-shared/python/crowe_mesh_protocol ~/Projects/crowe-logic-shared/python/tests
cd ~/Projects/crowe-logic-shared && git init -q && echo "init ok"
```

- [ ] **Step 2: Write `~/Projects/crowe-logic-shared/.gitignore`**

```
node_modules/
dist/
.venv/
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 3: Write `~/Projects/crowe-logic-shared/package.json`** (workspace root)

```json
{
  "name": "crowe-logic-shared",
  "private": true,
  "version": "0.0.0",
  "workspaces": ["packages/*"]
}
```

- [ ] **Step 4: Write `~/Projects/crowe-logic-shared/packages/mesh-protocol/package.json`**

```json
{
  "name": "@crowelogic/mesh-protocol",
  "version": "0.1.0",
  "type": "module",
  "main": "./src/index.ts",
  "exports": { ".": "./src/index.ts" },
  "scripts": {
    "typecheck": "tsc --noEmit",
    "test": "vitest run --passWithNoTests"
  },
  "devDependencies": {
    "typescript": "^5.6.0",
    "vitest": "^2.1.0"
  }
}
```

- [ ] **Step 5: Write `~/Projects/crowe-logic-shared/packages/mesh-protocol/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "noEmit": true,
    "skipLibCheck": true,
    "types": []
  },
  "include": ["src", "test"]
}
```

- [ ] **Step 6: Write `~/Projects/crowe-logic-shared/packages/mesh-protocol/vitest.config.ts`**

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: { include: ["test/**/*.test.ts"] },
});
```

- [ ] **Step 7: Write a placeholder `~/Projects/crowe-logic-shared/packages/mesh-protocol/src/index.ts`** (replaced in Task 3)

```ts
export {};
```

- [ ] **Step 8: Write `~/Projects/crowe-logic-shared/python/pyproject.toml`**

```toml
[project]
name = "crowe-mesh-protocol"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 9: Write `~/Projects/crowe-logic-shared/python/crowe_mesh_protocol/__init__.py`** (empty placeholder, replaced in Task 4)

```python
```

- [ ] **Step 10: Install toolchains and verify the runners start clean**

```bash
cd ~/Projects/crowe-logic-shared && npm install 2>&1 | tail -2
cd ~/Projects/crowe-logic-shared && npm test --workspace @crowelogic/mesh-protocol 2>&1 | tail -5
cd ~/Projects/crowe-logic-shared/python && uv venv -p 3.11 .venv 2>&1 | tail -1 && .venv/bin/python -m pip install -q pytest && .venv/bin/python -m pytest 2>&1 | tail -3
```

Expected: npm install succeeds; `vitest run` reports "No test files found" (exit 0 or a clean no-tests message); pytest reports "no tests ran" (exit code 5 is fine for no tests). This confirms both runners work before any types exist.

- [ ] **Step 11: Commit**

```bash
cd ~/Projects/crowe-logic-shared && git add -A && git commit -q -m "chore: scaffold crowe-logic-shared workspace + mesh-protocol skeletons

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: The canonical contract fixtures

**Files:**
- Create: `~/Projects/crowe-logic-shared/fixtures/manifest.json`, `~/Projects/crowe-logic-shared/fixtures/messages.json`

These are pure data, the shared source of truth both languages validate against. No test of their own; Tasks 3 and 4 consume them.

- [ ] **Step 1: Write `~/Projects/crowe-logic-shared/fixtures/manifest.json`**

```json
{
  "event_types": [
    "ready", "token",
    "reasoning.begin", "reasoning.delta", "reasoning.end",
    "segment_end", "status",
    "tool.started", "tool.progress", "tool.result",
    "command.proposed", "command.approved", "command.rejected",
    "error", "done"
  ],
  "frame_types": [
    "attach", "attach_ack", "surface_joined", "surface_left",
    "ping", "pong", "events_dropped"
  ]
}
```

- [ ] **Step 2: Write `~/Projects/crowe-logic-shared/fixtures/messages.json`** (one complete example per type)

```json
[
  {"type": "ready", "seq": 1, "ts": 1748030000000, "session_id": "s_abc", "model_tier": "Apex"},
  {"type": "token", "seq": 2, "ts": 1748030000100, "session_id": "s_abc", "delta": "Hello"},
  {"type": "reasoning.begin", "seq": 3, "ts": 1748030000200, "session_id": "s_abc", "reasoning_id": "r1"},
  {"type": "reasoning.delta", "seq": 4, "ts": 1748030000300, "session_id": "s_abc", "reasoning_id": "r1", "delta": "thinking"},
  {"type": "reasoning.end", "seq": 5, "ts": 1748030000400, "session_id": "s_abc", "reasoning_id": "r1"},
  {"type": "segment_end", "seq": 6, "ts": 1748030000500, "session_id": "s_abc", "reason": "segment"},
  {"type": "status", "seq": 7, "ts": 1748030000600, "session_id": "s_abc", "label": "searching"},
  {"type": "tool.started", "seq": 8, "ts": 1748030000700, "session_id": "s_abc", "tool_call_id": "tc1", "name": "kb_search", "args": {"q": "lions mane"}},
  {"type": "tool.progress", "seq": 9, "ts": 1748030000800, "session_id": "s_abc", "tool_call_id": "tc1", "message": "scanning", "fraction": 0.5},
  {"type": "tool.result", "seq": 10, "ts": 1748030000900, "session_id": "s_abc", "tool_call_id": "tc1", "status": "ok", "result": "3 chunks", "duration_ms": 142},
  {"type": "command.proposed", "seq": 11, "ts": 1748030001000, "session_id": "s_abc", "tool_call_id": "tc2", "block_id": "b1", "command": "rm -rf build"},
  {"type": "command.approved", "seq": 12, "ts": 1748030001100, "session_id": "s_abc", "tool_call_id": "tc2", "block_id": "b1"},
  {"type": "command.rejected", "seq": 13, "ts": 1748030001200, "session_id": "s_abc", "tool_call_id": "tc2", "block_id": "b1"},
  {"type": "error", "seq": 14, "ts": 1748030001300, "session_id": "s_abc", "code": "provider", "message": "upstream timeout", "recoverable": true},
  {"type": "done", "seq": 15, "ts": 1748030001400, "session_id": "s_abc", "tokens": 512, "reasoning_tokens": 64, "elapsed_ms": 1400, "ttft_ms": 120},
  {"type": "attach", "session_id": null, "surface_type": "web", "resume_after_seq": null},
  {"type": "attach_ack", "session_id": "s_abc", "surface_id": "sf1", "attached_at": 1748030000000, "last_seq": 0},
  {"type": "surface_joined", "session_id": "s_abc", "surface_id": "sf2", "surface_type": "cli"},
  {"type": "surface_left", "session_id": "s_abc", "surface_id": "sf2", "surface_type": "cli"},
  {"type": "ping", "ts": 1748030002000},
  {"type": "pong", "ts": 1748030002001},
  {"type": "events_dropped", "session_id": "s_abc", "count": 12, "since_seq": 40}
]
```

- [ ] **Step 3: Verify JSON validity and commit**

```bash
cd ~/Projects/crowe-logic-shared && node -e "JSON.parse(require('fs').readFileSync('fixtures/manifest.json')); const m=JSON.parse(require('fs').readFileSync('fixtures/messages.json')); console.log('messages:', m.length)"
```

Expected: `messages: 22`.

```bash
cd ~/Projects/crowe-logic-shared && git add fixtures && git commit -q -m "feat(mesh-protocol): canonical CMP fixture contract (manifest + messages)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: TypeScript types + parity test (TDD)

**Files:**
- Create/replace: `~/Projects/crowe-logic-shared/packages/mesh-protocol/src/events.ts`, `src/frames.ts`, `src/index.ts`
- Test: `~/Projects/crowe-logic-shared/packages/mesh-protocol/test/parity.test.ts`

- [ ] **Step 1: Write the failing test `test/parity.test.ts`**

```ts
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { CMP_EVENT_TYPES } from "../src/events";
import { CMP_FRAME_TYPES } from "../src/frames";

const FIX = fileURLToPath(new URL("../../../fixtures/", import.meta.url));
const manifest = JSON.parse(readFileSync(FIX + "manifest.json", "utf8"));
const messages: { type: string }[] = JSON.parse(readFileSync(FIX + "messages.json", "utf8"));

describe("CMP TS parity", () => {
  it("event union equals the manifest event_types", () => {
    expect([...CMP_EVENT_TYPES].sort()).toEqual([...manifest.event_types].sort());
  });
  it("frame union equals the manifest frame_types", () => {
    expect([...CMP_FRAME_TYPES].sort()).toEqual([...manifest.frame_types].sort());
  });
  it("messages cover every manifest type exactly once", () => {
    const all = [...manifest.event_types, ...manifest.frame_types].sort();
    expect(messages.map((m) => m.type).sort()).toEqual(all);
  });
  it("every message round-trips through JSON", () => {
    for (const m of messages) expect(JSON.parse(JSON.stringify(m))).toEqual(m);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/Projects/crowe-logic-shared && npm test --workspace @crowelogic/mesh-protocol 2>&1 | tail -8
```

Expected: FAIL, cannot resolve `../src/events` / `CMP_EVENT_TYPES` not exported.

- [ ] **Step 3: Write `src/events.ts`**

```ts
export interface CmpBase {
  seq: number;
  ts: number;
  session_id: string;
  surface_id?: string;
}

export interface ReadyEvent extends CmpBase { type: "ready"; model_tier: string; }
export interface TokenEvent extends CmpBase { type: "token"; delta: string; }
export interface ReasoningBeginEvent extends CmpBase { type: "reasoning.begin"; reasoning_id: string; }
export interface ReasoningDeltaEvent extends CmpBase { type: "reasoning.delta"; reasoning_id: string; delta: string; }
export interface ReasoningEndEvent extends CmpBase { type: "reasoning.end"; reasoning_id: string; }
export interface SegmentEndEvent extends CmpBase { type: "segment_end"; reason: "segment" | "round"; }
export interface StatusEvent extends CmpBase { type: "status"; label: string | null; }
export interface ToolStartedEvent extends CmpBase { type: "tool.started"; tool_call_id: string; name: string; args: Record<string, unknown>; }
export interface ToolProgressEvent extends CmpBase { type: "tool.progress"; tool_call_id: string; message: string; fraction: number | null; }
export interface ToolResultEvent extends CmpBase { type: "tool.result"; tool_call_id: string; status: "ok" | "fail"; result: unknown; duration_ms: number; }
export interface CommandProposedEvent extends CmpBase { type: "command.proposed"; tool_call_id: string; block_id: string; command: string; }
export interface CommandApprovedEvent extends CmpBase { type: "command.approved"; tool_call_id: string; block_id: string; }
export interface CommandRejectedEvent extends CmpBase { type: "command.rejected"; tool_call_id: string; block_id: string; }
export interface ErrorEvent extends CmpBase { type: "error"; code: string; message: string; recoverable: boolean; }
export interface DoneEvent extends CmpBase { type: "done"; tokens: number; reasoning_tokens: number; elapsed_ms: number; ttft_ms: number; }

export type CmpEvent =
  | ReadyEvent | TokenEvent
  | ReasoningBeginEvent | ReasoningDeltaEvent | ReasoningEndEvent
  | SegmentEndEvent | StatusEvent
  | ToolStartedEvent | ToolProgressEvent | ToolResultEvent
  | CommandProposedEvent | CommandApprovedEvent | CommandRejectedEvent
  | ErrorEvent | DoneEvent;

export type CmpEventType = CmpEvent["type"];

export const CMP_EVENT_TYPES: CmpEventType[] = [
  "ready", "token",
  "reasoning.begin", "reasoning.delta", "reasoning.end",
  "segment_end", "status",
  "tool.started", "tool.progress", "tool.result",
  "command.proposed", "command.approved", "command.rejected",
  "error", "done",
];
```

- [ ] **Step 4: Write `src/frames.ts`**

```ts
export interface AttachFrame { type: "attach"; session_id: string | null; surface_type: string; resume_after_seq: number | null; }
export interface AttachAckFrame { type: "attach_ack"; session_id: string; surface_id: string; attached_at: number; last_seq: number; }
export interface SurfaceJoinedFrame { type: "surface_joined"; session_id: string; surface_id: string; surface_type: string; }
export interface SurfaceLeftFrame { type: "surface_left"; session_id: string; surface_id: string; surface_type: string; }
export interface PingFrame { type: "ping"; ts: number; }
export interface PongFrame { type: "pong"; ts: number; }
export interface EventsDroppedFrame { type: "events_dropped"; session_id: string; count: number; since_seq: number; }

export type CmpFrame =
  | AttachFrame | AttachAckFrame | SurfaceJoinedFrame | SurfaceLeftFrame
  | PingFrame | PongFrame | EventsDroppedFrame;

export type CmpFrameType = CmpFrame["type"];

export const CMP_FRAME_TYPES: CmpFrameType[] = [
  "attach", "attach_ack", "surface_joined", "surface_left",
  "ping", "pong", "events_dropped",
];
```

- [ ] **Step 5: Write `src/index.ts`**

```ts
export * from "./events";
export * from "./frames";
import type { CmpEvent } from "./events";
import type { CmpFrame } from "./frames";

export type CmpMessage = CmpEvent | CmpFrame;

export function cmpType(msg: CmpMessage): CmpMessage["type"] {
  return msg.type;
}
```

- [ ] **Step 6: Run test + typecheck to verify pass**

```bash
cd ~/Projects/crowe-logic-shared && npm test --workspace @crowelogic/mesh-protocol 2>&1 | tail -8 && npm run typecheck --workspace @crowelogic/mesh-protocol 2>&1 | tail -3
```

Expected: 4 tests pass; typecheck exits 0.

- [ ] **Step 7: Commit**

```bash
cd ~/Projects/crowe-logic-shared && git add packages/mesh-protocol/src packages/mesh-protocol/test && git commit -q -m "feat(mesh-protocol): canonical CMP event + frame types (TypeScript)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: Python types + parity test (TDD)

**Files:**
- Create/replace: `~/Projects/crowe-logic-shared/python/crowe_mesh_protocol/events.py`, `frames.py`, `__init__.py`
- Test: `~/Projects/crowe-logic-shared/python/tests/test_parity.py`

- [ ] **Step 1: Write the failing test `python/tests/test_parity.py`**

```python
import json
from pathlib import Path

from crowe_mesh_protocol.events import CMP_EVENT_TYPES
from crowe_mesh_protocol.frames import CMP_FRAME_TYPES

FIX = Path(__file__).resolve().parents[2] / "fixtures"
MANIFEST = json.loads((FIX / "manifest.json").read_text())
MESSAGES = json.loads((FIX / "messages.json").read_text())


def test_event_union_equals_manifest():
    assert sorted(CMP_EVENT_TYPES) == sorted(MANIFEST["event_types"])


def test_frame_union_equals_manifest():
    assert sorted(CMP_FRAME_TYPES) == sorted(MANIFEST["frame_types"])


def test_messages_cover_every_type_once():
    allt = sorted(MANIFEST["event_types"] + MANIFEST["frame_types"])
    assert sorted(m["type"] for m in MESSAGES) == allt


def test_round_trip():
    for m in MESSAGES:
        assert json.loads(json.dumps(m)) == m
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/Projects/crowe-logic-shared/python && .venv/bin/python -m pytest -q 2>&1 | tail -8
```

Expected: FAIL, `ImportError: cannot import name 'CMP_EVENT_TYPES'`.

- [ ] **Step 3: Write `python/crowe_mesh_protocol/events.py`**

```python
from typing import Literal, NotRequired, TypedDict, Union


class _Base(TypedDict):
    seq: int
    ts: int
    session_id: str
    surface_id: NotRequired[str]


class Ready(_Base):
    type: Literal["ready"]
    model_tier: str


class Token(_Base):
    type: Literal["token"]
    delta: str


class ReasoningBegin(_Base):
    type: Literal["reasoning.begin"]
    reasoning_id: str


class ReasoningDelta(_Base):
    type: Literal["reasoning.delta"]
    reasoning_id: str
    delta: str


class ReasoningEnd(_Base):
    type: Literal["reasoning.end"]
    reasoning_id: str


class SegmentEnd(_Base):
    type: Literal["segment_end"]
    reason: Literal["segment", "round"]


class Status(_Base):
    type: Literal["status"]
    label: str | None


class ToolStarted(_Base):
    type: Literal["tool.started"]
    tool_call_id: str
    name: str
    args: dict


class ToolProgress(_Base):
    type: Literal["tool.progress"]
    tool_call_id: str
    message: str
    fraction: float | None


class ToolResult(_Base):
    type: Literal["tool.result"]
    tool_call_id: str
    status: Literal["ok", "fail"]
    result: object
    duration_ms: int


class CommandProposed(_Base):
    type: Literal["command.proposed"]
    tool_call_id: str
    block_id: str
    command: str


class CommandApproved(_Base):
    type: Literal["command.approved"]
    tool_call_id: str
    block_id: str


class CommandRejected(_Base):
    type: Literal["command.rejected"]
    tool_call_id: str
    block_id: str


class Error(_Base):
    type: Literal["error"]
    code: str
    message: str
    recoverable: bool


class Done(_Base):
    type: Literal["done"]
    tokens: int
    reasoning_tokens: int
    elapsed_ms: int
    ttft_ms: int


CmpEvent = Union[
    Ready, Token, ReasoningBegin, ReasoningDelta, ReasoningEnd,
    SegmentEnd, Status, ToolStarted, ToolProgress, ToolResult,
    CommandProposed, CommandApproved, CommandRejected, Error, Done,
]

CMP_EVENT_TYPES: list[str] = [
    "ready", "token",
    "reasoning.begin", "reasoning.delta", "reasoning.end",
    "segment_end", "status",
    "tool.started", "tool.progress", "tool.result",
    "command.proposed", "command.approved", "command.rejected",
    "error", "done",
]
```

- [ ] **Step 4: Write `python/crowe_mesh_protocol/frames.py`**

```python
from typing import Literal, TypedDict, Union


class Attach(TypedDict):
    type: Literal["attach"]
    session_id: str | None
    surface_type: str
    resume_after_seq: int | None


class AttachAck(TypedDict):
    type: Literal["attach_ack"]
    session_id: str
    surface_id: str
    attached_at: int
    last_seq: int


class SurfaceJoined(TypedDict):
    type: Literal["surface_joined"]
    session_id: str
    surface_id: str
    surface_type: str


class SurfaceLeft(TypedDict):
    type: Literal["surface_left"]
    session_id: str
    surface_id: str
    surface_type: str


class Ping(TypedDict):
    type: Literal["ping"]
    ts: int


class Pong(TypedDict):
    type: Literal["pong"]
    ts: int


class EventsDropped(TypedDict):
    type: Literal["events_dropped"]
    session_id: str
    count: int
    since_seq: int


CmpFrame = Union[Attach, AttachAck, SurfaceJoined, SurfaceLeft, Ping, Pong, EventsDropped]

CMP_FRAME_TYPES: list[str] = [
    "attach", "attach_ack", "surface_joined", "surface_left",
    "ping", "pong", "events_dropped",
]
```

- [ ] **Step 5: Write `python/crowe_mesh_protocol/__init__.py`**

```python
from crowe_mesh_protocol.events import CMP_EVENT_TYPES, CmpEvent
from crowe_mesh_protocol.frames import CMP_FRAME_TYPES, CmpFrame

CmpMessage = CmpEvent | CmpFrame

__all__ = ["CMP_EVENT_TYPES", "CMP_FRAME_TYPES", "CmpEvent", "CmpFrame", "CmpMessage"]
```

- [ ] **Step 6: Install the package editable + run test to verify pass**

```bash
cd ~/Projects/crowe-logic-shared/python && .venv/bin/python -m pip install -q -e . && .venv/bin/python -m pytest -q 2>&1 | tail -6
```

Expected: 4 tests pass.

- [ ] **Step 7: Commit**

```bash
cd ~/Projects/crowe-logic-shared && git add python && git commit -q -m "feat(mesh-protocol): canonical CMP event + frame types (Python mirror)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: README + final both-green verification

**Files:**
- Create: `~/Projects/crowe-logic-shared/packages/mesh-protocol/README.md`

- [ ] **Step 1: Write `packages/mesh-protocol/README.md`**

```markdown
# @crowelogic/mesh-protocol

Canonical CMP (Crowe Mesh Protocol, crowe-stream v1) types in TypeScript and Python.

- Wire format: JSON, discriminator `type`, snake_case fields.
- `fixtures/manifest.json` + `fixtures/messages.json` (workspace root) are the source of truth.
- Parity tests in BOTH languages assert each language's union equals the manifest and every message round-trips, so the two stay in lockstep.

To add a CMP message type: add it to `manifest.json`, add an example to `messages.json`, add the type to `src/events.ts` or `src/frames.ts` (+ the `CMP_*_TYPES` list) AND to `python/crowe_mesh_protocol/`. Both test suites fail until all are aligned.

Spec: `crowe-logic-foundry/docs/superpowers/specs/2026-05-23-cmp-crowe-mesh-protocol-design.md`.
```

- [ ] **Step 2: Run BOTH suites green in one shot**

```bash
cd ~/Projects/crowe-logic-shared && npm test --workspace @crowelogic/mesh-protocol 2>&1 | tail -4 && (cd python && .venv/bin/python -m pytest -q 2>&1 | tail -4)
```

Expected: TS 4 passed; Python 4 passed.

- [ ] **Step 3: Commit**

```bash
cd ~/Projects/crowe-logic-shared && git add packages/mesh-protocol/README.md && git commit -q -m "docs(mesh-protocol): README documenting the lockstep contract

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Acceptance criteria

- [ ] `~/Projects/crowe-logic-shared/` is an npm workspace with `@crowelogic/mesh-protocol` (TS) and `crowe_mesh_protocol` (Python).
- [ ] The TS `CmpEvent`/`CmpFrame` unions and the Python `TypedDict` unions cover exactly the 15 event types + 7 frame types in `manifest.json`.
- [ ] Both languages' parity tests pass (4 each): union==manifest, messages cover every type once, every message round-trips.
- [ ] Zero emitters/consumers wired; no existing repo touched (B1 is the package only).
- [ ] Adding a type to one language without the other (or without manifest/fixture) fails the parity tests, enforcing lockstep.
```
