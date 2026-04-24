# Crowe Logic

Crowe Logic is the Foundry agent inside VS Code. It ships as a chat participant, a pair of activity-bar views, and a dark/light theme pair tuned to the same gold palette as the mark.

![Crowe Logic](media/avatar-dark.png)

## What it does

- **`@crowe` chat participant.** Streams responses and reasoning into the chat panel. Routes across the CroweLM model chain (Azure Foundry, NVIDIA NIM, Ollama) based on availability and task.
- **Plan view.** The agent drafts a plan before running anything. You can approve, reorder, or cancel steps.
- **Tool Activity view.** Every tool call surfaces with name, args, status, duration, and result. Nothing runs silently.
- **Slash commands.** `/plan`, `/run`, `/explain`, `/dataset`, `/steer`, `/transcript`.
- **Remote IDE handoff.** `Crowe Logic: Open in Remote IDE` hands you off to a cloud session at `ide.crowelogic.com` when the hosted plane is available. Otherwise the extension runs against your local Foundry checkout.

## Install

```bash
code --install-extension crowe-logic-0.2.10.vsix
```

On first activation the extension auto-detects the Foundry checkout and its Python interpreter. The defaults:

1. **Foundry path**: the active workspace if it contains `cli/headless.py`, else `~/Projects/crowe-logic-foundry` or `~/crowe-logic-foundry`, else the container path `/workspace/crowe-logic-foundry`.
2. **Python path**: `<foundry>/.venv/bin/python3` > `<foundry>/venv/bin/python3` > `/opt/venv/bin/python3` > `python3` on PATH.

Override either in Settings under **Crowe Logic** if the auto-detect picks wrong.

## Quick start

1. Open chat with `Cmd+Shift+L` (or `Ctrl+Shift+L` on Windows/Linux).
2. Type `@crowe plan a PR that updates the README.`
3. Approve tool calls as they stream in the **Tool Activity** pane, or `/run` to proceed end to end.

## Architecture

```
VS Code chat participant
        │  stdin: {messages, model, session}
        ▼
python -m cli.headless
        │  stdout: NDJSON event stream
        ▼
VS Code chat stream + Plan + Tool Activity views
```

The extension does not import Foundry code. Every turn is one subprocess, one JSON payload in, one NDJSON stream out. The wire protocol lives in `cli/headless.py`; the TypeScript side in `src/agent.ts` parses it into `FoundryEvent` discriminated-union cases.

## Configuration

| Setting | Default | Description |
|---|---|---|
| `croweLogic.pythonPath` | *(auto-detect)* | Python interpreter that runs the headless agent. |
| `croweLogic.foundryPath` | *(auto-detect)* | Filesystem path to the `crowe-logic-foundry` checkout. |
| `croweLogic.model` | `auto` | CroweLM model tier. `auto` selects the first reachable model in the chain. |
| `croweLogic.maxToolRounds` | `20` | Maximum tool-call rounds per chat turn (1 to 100). |
| `croweLogic.apiBaseUrl` | `https://api.crowelogic.com` | Control-plane endpoint for billing, auth, and IDE launch. |
| `croweLogic.ideUrl` | `https://ide.crowelogic.com` | Remote IDE origin used when `Open in Remote IDE` is invoked. |

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Cmd+Shift+L` (macOS) / `Ctrl+Shift+L` | Open Crowe Logic chat |

## Slash commands

| Command | Description |
|---|---|
| `/plan` | Draft a plan before running tools. |
| `/run` | Execute the current plan end to end. |
| `/explain` | Explain the selected file or symbol. |
| `/dataset` | Show or set the active CroweLM dataset context. |
| `/steer` | Persist operator steering for this chat session. |
| `/transcript` | Show the last full answer and reasoning transcript. |

## Themes

**Crowe Logic Dark.** Deep graphite (`#0e0f12`, `#0a0b0e`) with a warm gold accent (`#bfa669`) and a toned-down palette for strings, numbers, and comments. Tuned for long sessions.

**Crowe Logic Light.** Warm cream (`#faf8f5`) with amber gold (`#8c7a3e`). Readable in daylight without losing the brand accent.

Both themes cover the full surface: borders, menus, notifications, scrollbars, and git decorations are tuned deliberately rather than inherited.

## Development

```bash
cd deploy/ide/extensions/crowe-logic
npm install
npm run compile
npm run package
```

Produces `crowe-logic.vsix`. Install locally with `code --install-extension` or deploy into a code-server image via `code-server --install-extension`.

## License

See LICENSE in the repository root.
