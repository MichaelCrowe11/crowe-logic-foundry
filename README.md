# Crowe Logic

Universal AI agent powered by gpt-oss-120b on Azure AI Foundry. 75 built-in tools across cultivation, quantum computing, vision analysis, video generation, music composition, and more.

## Install

```bash
pip install crowe-logic
```

```bash
npm i @michaelcrowe11/crowe-logic
```

## Setup

1. Deploy gpt-oss-120b on [Azure AI Foundry](https://ai.azure.com)
2. Configure credentials:
```bash
cp .env.example .env
# Fill in Azure project endpoint, deployment name, and optional keys
```
3. Authenticate with Azure:
```bash
az login
```
4. Deploy the agent:
```bash
crowe-logic deploy
```
5. Start chatting:
```bash
crowe-logic
```

## Usage

```bash
crowe-logic              # Interactive chat (default)
crowe-logic chat         # Interactive chat session
crowe-logic run "prompt" # Single prompt, get response
crowe-logic headless     # Crowe Logic Command JSON event stream
crowe-logic deploy       # Create/recreate the agent
crowe-logic status       # Show agent status
crowe-logic tools        # List available tools
```

## Crowe Logic Command

For external hosts that need a structured agent runtime, Foundry now
ships a headless JSON-streaming entrypoint branded as Crowe Logic
Command:

```bash
crowe-logic headless --input request.json
```

It emits newline-delimited JSON events for tokens, reasoning, tool
execution, and completion so Studio or other hosts can render the run in
their own UI without parsing terminal output.

## Tools (75)

| Category | Tools | Description |
|----------|-------|-------------|
| Filesystem | 6 | Read, write, list, search, move, delete files |
| Shell | 2 | Execute commands, manage processes |
| Web & Search | 5 | HTTP requests, web search, scraping |
| Git | 4 | Status, diff, log, commit operations |
| Browser Automation | 5 | Playwright-based navigation, screenshots, interaction |
| macOS / AppleScript | 2 | System automation, app control |
| iTerm2 | 20 | Terminal profiles, status bar, title, theme, split panes |
| Quantum Computing | 7 | QubitFlow circuits, Synapse pipelines, Trinity bridge |
| Music Composition | 5 | Talon engine -- scales, chords, MIDI, Ableton integration |
| Vision & Image | 2 | Multi-backend photo analysis (OpenRouter, Crowe Vision) |
| Video Generation | 1 | Azure-hosted Sora 2 text/image-to-video generation |
| CroweLM Training | 10 | Dataset management, curation, fine-tuning pipeline |
| Crowe Logic Platform | 4 | Chat, vision, grow logs, SOP generation via ai.southwestmushrooms.com |
| MCP Client | 2 | Connect to 5,800+ MCP servers on demand |

## MCP Server

The Crowe Logic platform is also available as a standalone MCP server for any MCP client:

```bash
uvx crowe-logic-mcp
```

See [crowe-logic-mcp](https://github.com/MichaelCrowe11/crowe-logic-mcp) for configuration details.

## Specialized Agents

| Agent | Focus |
|-------|-------|
| `cultivation` | Mushroom cultivation with Crowe Logic platform integration |
| `quantum` | Quantum computing with QubitFlow + Synapse + Trinity bridge |
| `music` | Music composition with Talon engine |
| `code` | General software development |
| `research` | Web research and analysis |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `AZURE_AI_PROJECT_ENDPOINT` | Azure AI Foundry project endpoint |
| `AZURE_AI_DEPLOYMENT` | Model deployment name |
| `AZURE_SORA_ENDPOINT` | Optional dedicated Sora endpoint; falls back to `AZURE_CORE_ENDPOINT` |
| `AZURE_SORA_API_KEY` | Optional dedicated Sora API key; falls back to `AZURE_CORE_API_KEY` |
| `AZURE_SORA_DEPLOYMENT_NAME` | Azure Sora deployment name, usually `sora-2` |
| `OPENROUTER_API_KEY` | OpenRouter API key (vision fallback) |
| `CROWE_LOGIC_URL` | Crowe Logic platform URL |
| `CROWE_LOGIC_KEY` | Crowe Logic API key |

## Docker

```bash
docker pull michaelcrowe1111/crowe-logic:latest
docker run -it --env-file .env michaelcrowe1111/crowe-logic
```

GPU variant for fine-tuning:

```bash
docker build --target gpu -t crowe-logic:gpu .
```

## Requirements

- Python 3.10+
- Azure AI Foundry account with gpt-oss-120b deployment
- Azure CLI (`az login`) for authentication

## Author

Michael Crowe -- [Crowe Logic, Inc.](https://crowelogic.com)

## License

MIT
