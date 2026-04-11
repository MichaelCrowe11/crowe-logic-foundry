# Crowe Logic — VS Code extension

The host-side adapter for the Crowe Logic Foundry agent. Registers a chat
participant (`@crowe`) and two activity-bar views (Plan, Tool Activity)
that stream events from the headless Foundry runner.

## Architecture

```
┌──────────────────┐  stdin (JSON)   ┌──────────────────────┐
│ VS Code chat     │ ──────────────▶ │ python -m cli.headless│
│ participant      │                 │ (Foundry agent loop)  │
│ (this extension) │ ◀────────────── │                       │
└──────────────────┘  stdout (NDJSON)└──────────────────────┘
```

The extension never imports Foundry code directly. Everything goes
through the line-delimited JSON protocol defined in
`cli/headless.py`. Adding a new event type means updating both the
Python emitter and `src/agent.ts`'s `FoundryEvent` union — those are
the only two places that know about the wire format.

## Build

```bash
./build.sh
```

Produces `crowe-logic.vsix`. The Dockerfile installs it via
`code-server --install-extension`.

## Configuration

| Setting                 | Default                              | Purpose                                       |
|-------------------------|--------------------------------------|-----------------------------------------------|
| `croweLogic.pythonPath` | `/opt/venv/bin/python3`              | Interpreter that runs the headless agent      |
| `croweLogic.foundryPath`| `/workspace/crowe-logic-foundry`     | Path to the Foundry checkout (cwd + PYTHONPATH)|
| `croweLogic.model`      | `auto`                               | Model id from MODEL_CHAIN (or `auto`)         |
