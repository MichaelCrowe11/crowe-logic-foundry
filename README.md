# Crowe Logic

Universal AI agent powered by the CroweLM model stack on Azure AI Foundry. The default lineup now fronts `gpt-5.4-pro` as `CroweLM Pro` and `claude-opus-4-6` as `CroweLM Opus`, alongside the existing CroweLM Core, Kernel, and GLM tiers.

## Install

```bash
pip install crowe-logic
```

```bash
npm i @michaelcrowe11/crowe-logic
```

## Setup

1. Deploy the CroweLM-backed Foundry models on [Azure AI Foundry](https://ai.azure.com)
2. Configure credentials:
```bash
cp .env.example .env
# Fill in the Azure endpoints and API keys for the CroweLM resources
```
3. Authenticate with Azure:
```bash
az login
```
4. Verify the model stack:
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
crowe-logic deploy       # Verify provider health across the CroweLM stack
crowe-logic status       # Show agent status
crowe-logic tools        # List available tools
```

`CroweLM Pro` is now wired to stream reasoning summaries through both the terminal UI and `crowe-logic headless` when the Azure Responses API emits them, so hosts that consume the JSON event stream can render `reasoning` deltas before answer tokens.

## Tools (79)

| Category | Tools | Description |
|----------|-------|-------------|
| Filesystem | 4 | Read, write, edit, list files and directories |
| Shell | 1 | Execute commands with timeout and working directory |
| Web & Search | 3 | Web search, grep/ripgrep, URL fetching |
| Git | 5 | Status, diff, log, commit, clone |
| Browser Automation | 5 | Playwright-based navigation, screenshots, interaction |
| macOS / AppleScript | 3 | System automation, app control, notifications |
| iTerm2 | 18 | Windows, tabs, panes, broadcast, badges, colors, variables |
| Quantum Computing | 4 | QubitFlow circuits, Synapse evaluation, Trinity pipeline |
| Music Composition | 11 | Talon engine -- chords, drums, melody, emotion, quantum composition |
| Vision & Image | 2 | Multi-backend photo analysis (OpenRouter, Crowe Vision) |
| Video Generation | 1 | CroweLM Motion text/image-to-video on Azure AI Foundry |
| CroweLM Training | 10 | Dataset management, curation, fine-tuning pipeline |
| CroweLM Pipeline | 4 | Staging, promotion, agent runner, audit logs |
| Crowe Logic Platform | 4 | Chat, vision, grow logs, SOP generation |
| MCP Client | 4 | Search registry, list tools, call tools, stop servers |

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
| `PROJECT_ENDPOINT` | Legacy Azure AI Foundry project endpoint |
| `MODEL_DEPLOYMENT_NAME` | Legacy Azure Agents deployment name |
| `AZURE_CORE_ENDPOINT` | Core CroweLM Azure OpenAI endpoint (`gpt-5.4-pro`, `gpt-5.4-nano`, `Kimi-K2.5`) |
| `AZURE_CORE_API_KEY` | API key for the core CroweLM Azure OpenAI endpoint |
| `AZURE_ANTHROPIC_ENDPOINT` | Azure Anthropic endpoint for `claude-opus-4-6` |
| `AZURE_ANTHROPIC_API_KEY` | API key for the Azure Anthropic endpoint |
| `AZURE_GLM_ENDPOINT` | Optional GLM endpoint (`FW-GLM-5`) |
| `AZURE_GLM_API_KEY` | API key for the GLM endpoint |
| `AZURE_SORA_ENDPOINT` | Optional dedicated CroweLM Motion endpoint; falls back to `AZURE_CORE_ENDPOINT` |
| `AZURE_SORA_API_KEY` | Optional dedicated CroweLM Motion API key; falls back to `AZURE_CORE_API_KEY` |
| `AZURE_SORA_DEPLOYMENT_NAME` | CroweLM Motion deployment name, usually `sora-2` |
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
- Azure AI Foundry account with `gpt-5.4-pro` and `claude-opus-4-6` deployments
- Azure CLI (`az login`) for authentication

## Author

Michael Crowe -- [Crowe Logic, Inc.](https://crowelogic.com)

## License

MIT
