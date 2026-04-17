# Copilot Instructions — Crowe Logic Foundry

## What This Repo Is

Crowe Logic is a universal AI agent framework powered by the CroweLM model stack on Azure AI Foundry. It exposes an intelligent CLI (`crowe-logic`) and MCP server that orchestrates multi-provider inference (Azure OpenAI, Anthropic Claude, Crowe-hosted open models), dynamic model fallback, specialized agents for quantum/music/biotech domains, and 79 tools spanning filesystem, shell, web search, git, browser automation, iTerm2 control, and domain-specific capabilities like CroweLM fine-tuning pipelines and Arizona public records.

## Architecture

**Three entry points:**
- **CLI** (`cli/crowe_logic.py`): Interactive chat and single-prompt execution; smart routing through the model chain with fallback on failure.
- **Headless mode** (`cli/headless.py`): Streaming JSON event loop for external consumers (reasoning tokens, answer tokens, tool calls).
- **MCP Server** (`tools/mcp_client.py`): Exposes agent as an MCP server for Cursor, Claude, or other clients.

**Model chain & provider abstraction:**
- Config-driven model chain defined in `config/agent_config.py` (e.g., CroweLM Titan → Apex → Prime, with fallback to specialized tiers).
- **Provider layer** (`providers/`) abstracts Azure OpenAI, Anthropic, self-hosted OpenAI-compatible, NVIDIA NIM, and Ollama.
- **Azure AI Agent Service** (`azure-ai-agents` SDK) handles tool registration, function calling, and streaming.
- Shared utilities in `providers/_shared.py` (token counting, streaming setup, error handling).

**Tool registry & execution:**
- Tools live in `tools/` (79 functions grouped by domain: filesystem, shell, quantum, music, vision, training pipelines, public records, iTerm2).
- Each tool is a Python function decorated with docstrings; Azure AI Agent Service introspects them to build the schema and invoke them.
- Session-scoped state in `cli/session_runtime.py` persists steering instructions, datasets, and transcripts.

**Specialized agents:**
- Five YAML agent specs in `agents/` (code, cultivation, music, quantum, research) with custom system instructions and tool subsets.

## Build / Run / Test

**Setup:**
```bash
pip install -r requirements.txt  # Python 3.10+
cp .env.example .env             # Fill in Azure endpoints and API keys
az login                          # Authenticate with Azure
```

**Run:**
```bash
crowe-logic              # Interactive chat (default)
crowe-logic run "prompt" # Single prompt
crowe-logic deploy       # Verify model health
crowe-logic models sync  # Sync extra models from Azure
```

**Tests:**
```bash
pytest                                    # All tests
pytest tests/test_azure_openai.py         # Single file
pytest tests/test_azure_openai.py::TestClass::test_name  # Single test
```

**Docker:**
```bash
docker build -t crowe-logic .
docker run -it --env-file .env crowe-logic
```

## Key Conventions

1. **Model chain and fallback**: When a model fails, `_advance_model()` moves to the next entry in `MODEL_CHAIN`. The primary tier is Crowe-hosted open models; fallbacks are Azure/Anthropic/NVIDIA/Ollama. Reset with `_reset_model_chain()` at session start.

2. **Tool schema generation**: Tools must have clear docstrings with `:param`, `:return`, and `:rtype:` lines—Azure AI Agent Service parses these to build function schemas. No type hints in docstring syntax; they come from the function signature.

3. **Provider detection**: Determine which provider to instantiate based on `MODEL_CHAIN` entries. Each model has `provider` (azure_openai, anthropic, openai_compat, etc.) and corresponding `endpoint_env`, `api_key_env` fields.

4. **Session state**: `load_session_runtime(session_id)` and `handle_local_control_command()` manage session-scoped steering, datasets, and transcripts in `~/.crowe-logic/runtime/`.

5. **Rendering**: Terminal UI lives in `cli/branding.py`. Use `Renderer` class to handle streaming output, spinner updates, and markdown rendering.

6. **MCP server ecosystem**: `tools/mcp_client.py` calls out to 5,800+ MCP servers on demand via the registry. Only load when explicitly invoked by the agent.

## Gotchas

1. **Environment precedence**: `.env` loading follows a strict priority order (see `config/agent_config.py`). Set `CROWE_LOGIC_PROJECT_ROOT` if you have a custom install location.

2. **Azure credentials**: Requires `az login` first; CLI falls back to interactive browser auth. Ensure your Azure account has AI Foundry deployments for the CroweLM lineup (gpt-5.4, gpt-5.4-pro, claude-opus-4-6).

3. **Streaming vs. buffering**: `azure-ai-agents` SDK defaults to streaming. Renderer expects token callbacks; if you change provider, ensure streaming hooks are wired correctly.

4. **Optional dependencies**: Quantum, music, and nvidia tiers require `pip install crowe-logic[quantum]` or `[nvidia]`. Standard install covers Azure/Anthropic/open-model tiers.

5. **Test file structure**: Tests use `monkeypatch` for path setup (see `test_session_runtime.py`). Use tmp_path fixtures; don't rely on disk state.

6. **Tool docstring parsing**: Malformed docstrings (missing colons, wrong param names) will break Azure schema generation. Always test tool schema with `crowe-logic tools list` after changes.
