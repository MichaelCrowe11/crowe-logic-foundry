# Crowe-Synapse Framework — Phase 2 Design Specification

**Date:** 2026-03-30
**Author:** Michael Crowe / Crowe Logic, Inc.
**Status:** Approved
**Version:** 0.1.0

## Overview

Crowe-Synapse is the orchestration framework layer for Crowe Logic. It sits between the CLI/web interfaces and the Azure AI Foundry agent, adding pipeline orchestration, multi-agent coordination, persistent memory, and pluggable quantum decision-making.

The framework is additive — Phase 1 (CLI, tools, streaming, Azure integration) remains intact. Crowe-Synapse wraps and enhances the existing architecture without replacing it.

## Architecture

Three composable layers built on the Phase 1 foundation:

```
Interfaces (CLI / Web)
    |
    v
crowe-synapse/ (Framework Layer)
    ├── Orchestrator (router + dispatcher + context manager)
    ├── Pipeline Engine (step chaining, state, retries, templates)
    ├── Agent Registry (YAML-defined sub-agents)
    ├── Memory Store (SQLite + Azure thread sync)
    └── Quantum Bridge (pluggable Synapse-Lang/Qubit-Flow decision points)
    |
    v
Foundation (Phase 1)
    ├── Azure AI Foundry (gpt-oss120-120b)
    ├── 33 built-in tools + 5,800 MCP servers
    └── Talon Music Engine + Synapse/Qubit-Flow quantum packages
```

## Directory Structure

```
crowe-logic-foundry/
  cli/                        # Phase 1 (unchanged)
  tools/                      # 33 tool functions (unchanged)
  config/                     # agent_config.py (unchanged)
  crowe_synapse/              # NEW — framework layer
    __init__.py               # Public API: Orchestrator, Pipeline, Agent
    orchestrator.py           # Router + Dispatcher + Context Manager
    pipeline.py               # Pipeline engine (step chaining, state, retries)
    agent_registry.py         # Load + manage YAML agent definitions
    memory.py                 # SQLite memory store + Azure thread sync
    quantum_bridge.py         # Pluggable Synapse-Lang/Qubit-Flow decision points
    templates/                # Built-in pipeline templates
      refactor.yaml
      research.yaml
      compose.yaml
  agents/                     # NEW — agent definitions (YAML)
    code.yaml
    research.yaml
    music.yaml
    quantum.yaml
    cultivation.yaml
  migrations/                 # NEW — SQLite schema versioning
    001_initial.sql
```

## 1. Pipeline Engine

### Execution Modes

**Agent-directed (default):** The model decides each step. The pipeline engine wraps the existing `stream_response()` tool execution loop and adds:

- **State passing** — each tool's output is stored as typed context and available as input to subsequent tool calls. Replaces raw string pass-through.
- **Retry logic** — failed tool calls are retried with exponential backoff before reporting failure to the model. Configurable max retries per tool.
- **Output validation** — optional validators per tool (e.g., `edit_file` confirms the edit applied, `git_commit` returns a commit hash). Catches silent failures before propagation.
- **Execution log** — every step logged to SQLite with timing, inputs, outputs, and status.

**Framework-directed (templates):** For registered pipeline patterns, the framework runs the pipeline directly without model round-trips:

```yaml
# templates/refactor.yaml
name: refactor
trigger: "refactor|rename|extract method"
steps:
  - tool: grep_search
    input_from: task.target
  - tool: read_file
    input_from: previous.matches[0].file
  - tool: edit_file
    input_from: task.changes
  - tool: git_diff
  - validate: diff_not_empty
  - tool: git_commit
    input_from: task.message
```

### Hybrid Execution

The model always has the final say. If a template step fails validation, control returns to the model. If the model explicitly overrides a template match ("don't use the template, I want to do this differently"), agent-directed mode takes over.

Pipeline templates are an optimization, not a replacement for model reasoning.

### Pipeline State

Each pipeline run maintains:
- `run_id` — unique identifier
- `steps[]` — ordered list of completed/pending steps
- `state` — accumulated context (tool outputs, intermediate results)
- `checkpoints` — snapshot at each step for crash recovery
- `status` — running, completed, failed, paused

## 2. Agent Registry

### Definition Format

Sub-agents are YAML configuration files:

```yaml
# agents/music.yaml
name: music
description: "Talon composition specialist"
model: gpt-oss120-120b
tools:
  - talon_*
  - read_file
  - execute_shell
prompt_override: |
  You are the music specialist within Crowe Logic.
  You compose using the Talon Music Engine.
  You think in terms of key, tempo, groove, and emotion.
  You never use emojis. Output is clean and professional.
pipelines:
  - compose.yaml
  - analyze.yaml
quantum_evaluator: melody_path
```

### Routing

The orchestrator routes tasks through three paths:

1. **Direct execution** — simple tasks go straight to tool execution without sub-agent involvement.
2. **Pipeline match** — tasks matching a registered template trigger run through the pipeline engine.
3. **Agent delegation** — complex domain tasks are routed to the appropriate sub-agent based on task classification.

### Sub-Agent Execution

Sub-agents do NOT get their own Azure agent deployments. They reuse the same gpt-oss120-120b model with specialized system prompts injected contextually. One model, many personas. The framework swaps instructions per delegation:

- Creates a new Azure thread (or reuses an existing one for the domain)
- Injects the sub-agent's system prompt
- Restricts tool access to the sub-agent's declared tool subset
- Executes the task
- Returns results through the orchestrator to the user

### Initial Agents

| Agent | Tools | Domain |
|-------|-------|--------|
| code | filesystem, shell, git, grep | Code editing, refactoring, debugging |
| research | web_search, browse_url, grep | Web research, information gathering |
| music | talon_*, read_file, execute_shell | Composition, MIDI generation, audio analysis |
| quantum | run_quantum_circuit, synapse_evaluate, qubit_flow_execute | Quantum circuit design, evaluation |
| cultivation | web_search, read_file, browse_url | Mycology knowledge, growing protocols |

## 3. Memory Store

### Architecture

Hybrid persistence — SQLite for structured long-term data, Azure threads for active conversation state.

**Database location:** `~/.crowe-logic/memory.db` (portable across machines)

### Schema

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP,
    summary TEXT,
    project_context TEXT
);

CREATE TABLE pipeline_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    pipeline_name TEXT NOT NULL,
    steps TEXT NOT NULL,  -- JSON array
    status TEXT DEFAULT 'running',
    duration_ms INTEGER,
    result TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tool_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id),
    pipeline_run_id TEXT REFERENCES pipeline_runs(id),
    tool_name TEXT NOT NULL,
    arguments TEXT,  -- JSON
    output TEXT,
    duration_ms INTEGER,
    status TEXT DEFAULT 'success',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE agent_delegations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id),
    agent_name TEXT NOT NULL,
    task TEXT NOT NULL,
    result TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE project_knowledge (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    source TEXT,  -- 'user', 'extracted', 'agent'
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_run_id TEXT REFERENCES pipeline_runs(id),
    step_index INTEGER NOT NULL,
    state_snapshot TEXT NOT NULL,  -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Integration Points

- **Session start:** Query `sessions` and `project_knowledge` for prior context. Inject summary into system prompt.
- **During execution:** Log tool calls, pipeline steps, and agent delegations in real-time. Checkpoint pipeline state at each step.
- **Session end:** Summarize conversation, extract key facts into `project_knowledge`.
- **Cross-session:** On reopen, the orchestrator knows what was done previously. No cold start.
- **Thread overflow:** When an Azure thread hits token limits, start a new thread with relevant history injected from SQLite.

## 4. Quantum Decision Layer

### Decision Point Interface

Any routing decision, pipeline step, or parameter selection can declare an optional quantum evaluator:

```python
@dataclass
class DecisionPoint:
    name: str                      # identifier
    candidates: list[str]          # possible outcomes
    classical_default: str         # fallback when quantum unavailable
    quantum_evaluator: str | None  # Synapse-Lang expression (optional)
```

### Integration Points

| Decision Point | Classical Default | Quantum Enhancement |
|---|---|---|
| Agent routing | Keyword match + model classification | Superposition across candidate agents, collapse to best fit |
| Talon melody paths | Random within scale constraints | Quantum interference patterns (via @talon/quantum) |
| Pipeline step ordering | Sequential as defined | Qubit-Flow evaluates parallel vs sequential |
| Search result ranking | Source return order | Synapse-Lang amplitude-weighted relevance |
| Creative parameters | Emotion preset defaults | Quantum probability distribution across parameter space |

### Graceful Degradation

Quantum is never a hard dependency. If `synapse-lang` is not installed (the `[quantum]` optional extra), all decision points use their `classical_default`. Zero overhead when quantum isn't active. The framework checks availability once at import time and caches the result.

## 5. CLI Integration

### Changes to cli/crowe_logic.py

Minimal, additive changes:

1. **Lazy-loaded orchestrator:**
```python
_orchestrator = None
def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        from crowe_synapse import Orchestrator
        _orchestrator = Orchestrator(db_path="~/.crowe-logic/memory.db")
    return _orchestrator
```

2. **Pre-message hook** — orchestrator prepares context before Azure call:
```python
orch = _get_orchestrator()
context = orch.prepare(user_input, thread_id=thread.id)
```

3. **Post-execution hook** — log tool execution to memory store:
```python
orch.record_execution(tool_name, args, result, duration)
```

4. **Session lifecycle** — start/end session for memory persistence:
```python
orch.start_session(thread_id=thread.id)
# ... chat loop ...
orch.end_session(summary=True)
```

### New CLI Commands

| Command | Description |
|---------|-------------|
| `crowe-logic agents` | List registered agents from agents/*.yaml |
| `crowe-logic pipelines` | List available pipeline templates |
| `crowe-logic history` | Show recent sessions from SQLite |
| `crowe-logic resume` | Resume last session with context injection |

### What Doesn't Change

- Streaming architecture (stream_response, 3-phase tool execution)
- Terminal branding (welcome screen, avatar, gold theme)
- Tool modules (tools/*.py)
- Azure agent deployment (create_agent.py)
- Deploy command

## 6. Build Sequence

Implementation order based on dependencies:

1. **Memory store** (memory.py + migrations/) — foundation for everything else
2. **Pipeline engine** (pipeline.py + templates/) — core execution model
3. **Orchestrator** (orchestrator.py) — ties pipeline + memory together
4. **CLI integration** — wire orchestrator into chat() and stream_response()
5. **Agent registry** (agent_registry.py + agents/) — builds on orchestrator + pipeline
6. **Quantum bridge** (quantum_bridge.py) — plugs into decision points created by steps 2-5
7. **New CLI commands** — agents, pipelines, history, resume

Each step is independently testable. Step 1-4 delivers a working pipeline-enhanced CLI. Steps 5-6 add the multi-agent and quantum layers on top.

## 7. Dependencies

### New Python Dependencies

- None required. SQLite is in Python's stdlib. YAML parsing uses PyYAML (add to pyproject.toml).

### Optional Dependencies

- `synapse-lang>=2.0.0` — for quantum decision points (already in [quantum] extra)
- `synapse-qubit-flow>=1.0.0` — for Qubit-Flow circuits (already in [quantum] extra)

### pyproject.toml Changes

```toml
[tool.setuptools.packages.find]
include = ["cli*", "tools*", "config*", "scripts*", "crowe_synapse*"]

dependencies = [
    # ... existing ...
    "pyyaml>=6.0",
]
```

## 8. Future (Phase 3)

The crowe-synapse framework is designed to be interface-agnostic. When Phase 3 wires this into ai.southwestmushrooms.com:

- The web app imports `crowe_synapse.Orchestrator` the same way the CLI does
- Same pipeline engine, same agent registry, same memory store
- The web interface adds its own presentation layer (React) but the framework logic is shared
- Memory store becomes the bridge — work started in CLI continues in web, and vice versa

This is the "same brain, two interfaces" pattern from the Phase 1 roadmap, now with the framework layer making it concrete.
