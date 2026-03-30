# Crowe Logic

Universal AI agent powered by gpt-oss-120b on Azure AI Foundry.

## Install

```bash
pip install crowe-logic
```

## Setup

1. Deploy gpt-oss-120b on [Azure AI Foundry](https://ai.azure.com)
2. Configure credentials:
```bash
cp .env.example .env
# Fill in your Azure project endpoint and deployment name
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

## Tools

33 built-in tools across filesystem, shell, web, git, browser automation, macOS, quantum computing, and music composition. Plus access to 5,800+ MCP servers on demand.

## Requirements

- Python 3.10+
- Azure AI Foundry account with gpt-oss-120b deployment
- Azure CLI (`az login`) for authentication

## Author

Michael Crowe — [Crowe Logic, Inc.](https://crowelogic.com)
