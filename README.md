# Crowe Logic

Universal AI agent powered by gpt-oss-120b on Azure AI Foundry. 74 built-in tools across cultivation, quantum computing, vision analysis, music composition, and more.

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
crowe-logic deploy       # Create/recreate the agent
crowe-logic status       # Show agent status
crowe-logic tools        # List available tools
```

## Tools (74)

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
