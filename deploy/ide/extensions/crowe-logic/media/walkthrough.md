# Crowe Logic

The Foundry agent is your chat participant in this IDE. Press `⌃⌘I` and type `@crowe`.

It reads and edits files in your workspace, runs shell commands, queries the model chain (Azure Foundry, NVIDIA NIM, Ollama), and streams each tool call into the **Crowe Logic** activity-bar pane on the left.

## Slash commands

- `/plan` drafts a plan before running anything
- `/run` executes the current plan end to end
- `/explain` walks the selected file or symbol

## Where things live

- **Chat** sits on the right. The Crowe Logic mark is its avatar.
- **Plan** and **Tool Activity** sit in the left activity bar.
- **Remote IDE** (when enabled) hands you off to a cloud session at ide.crowelogic.com.

## Getting started

1. Run `Crowe Logic: Sign In` from the command palette to connect to the platform.
2. Open the chat and ask `@crowe plan a PR that updates the README`.
3. Approve tool calls as they stream, or hit `/run` to let it proceed.
