# Crowe Logic — VS Code Extension

> **Universal AI Agent** inside your IDE — chat, plan, execute, and review with the full CroweLM model chain.

![Crowe Logic](media/avatar-light.png)

## Features

- **`@crowe` Chat Participant** — Talk to the Foundry agent directly in VS Code's chat panel. Streaming responses with real-time reasoning display.
- **Slash Commands** — `/plan`, `/run`, `/explain`, `/dataset`, `/steer`, `/transcript`
- **Plan View** — Watch the agent's execution plan build step-by-step in the activity bar
- **Tool Activity View** — Live feed of every tool call: name, args, status, duration, result
- **CroweLM Model Chain** — Smart routing across Titan, Apex, Oracle, Sovereign, Prime, Nexus, Reason
- **Color Themes** — Crowe Logic Dark and Crowe Logic Light, both built around the signature gold accent palette
- **Keyboard Shortcuts** — `Cmd+Shift+L` (macOS) / `Ctrl+Shift+L` to open chat

## Architecture

```
┌──────────────────┐  stdin (JSON)   ┌──────────────────────┐
│ VS Code chat     │ ──────────────▶ │ python -m cli.headless│
│ participant      │                 │ (Foundry agent loop)  │
│ (this extension) │ ◀────────────── │                       │
└──────────────────┘  stdout (NDJSON)└──────────────────────┘
```

The extension never imports Foundry code directly. Everything goes through the line-delimited JSON protocol defined in `cli/headless.py`. The wire format is a strict contract — adding a new event type means updating both the Python emitter and `src/agent.ts`'s `FoundryEvent` union.

## Quick Start

1. **Install the extension**
   ```bash
   code --install-extension crowe-logic.vsix
   ```

2. **Open Chat** — Press `Cmd+Shift+L` or click the Crowe Logic icon in the activity bar

3. **Start talking** — Type `@crowe` followed by your request

4. **Watch it work** — Plans, tool calls, and reasoning stream in real time

## Build

```bash
cd deploy/ide/extensions/crowe-logic
./build.sh
```

Produces `crowe-logic.vsix`. Install locally or deploy via `code-server --install-extension`.

## Configuration

| Setting                       | Default                          | Description                                                      |
|-------------------------------|----------------------------------|------------------------------------------------------------------|
| `croweLogic.pythonPath`       | `/opt/venv/bin/python3`          | Python interpreter for the headless agent                        |
| `croweLogic.foundryPath`      | `/workspace/crowe-logic-foundry` | Path to the Foundry checkout (cwd + PYTHONPATH)                  |
| `croweLogic.model`            | `auto`                           | CroweLM model (auto, Titan, Apex, Oracle, Sovereign, Prime, etc)|
| `croweLogic.maxToolRounds`    | `20`                             | Max tool-call rounds per turn (1–100)                            |
| `croweLogic.theme`            | `auto`                           | Color theme preference (auto, dark, light)                       |

## Keyboard Shortcuts

| Shortcut              | Action                    |
|-----------------------|---------------------------|
| `Cmd+Shift+L`        | Open Crowe Logic Chat     |
| `Cmd+Shift+P` (in Plan view) | Focus Plan View    |

## Slash Commands

| Command       | Description                                           |
|---------------|-------------------------------------------------------|
| `/plan`       | Draft a plan before running tools                     |
| `/run`        | Execute the current plan end-to-end                   |
| `/explain`    | Explain the selected file or symbol                   |
| `/dataset`    | Show or set the active CroweLM dataset context        |
| `/steer`      | Persist operator steering for this chat session       |
| `/transcript` | Show the last full answer and reasoning transcript    |

## Color Themes

### Crowe Logic Dark
Deep blacks (`#0a0b0e`, `#0e0f12`) with warm gold (`#bfa669`) accents. Designed for extended sessions.

### Crowe Logic Light
Warm cream (`#faf8f5`) background with amber gold (`#8c7a3e`) accents. Clean and readable in bright environments.

## Development

```bash
npm install          # Install dependencies
npm run compile      # Build TypeScript
npm run watch        # Watch mode for development
npm run package      # Create .vsix package
```

## License

See LICENSE in the repository root.
