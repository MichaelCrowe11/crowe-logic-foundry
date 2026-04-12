"""
Crowe Logic Agent — Central Configuration

All agent settings, system instructions, and tool selection live here.
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the first location that exists. Priority order:
#   1. $CROWE_LOGIC_PROJECT_ROOT/.env (explicit user override)
#   2. ~/.config/crowe-logic/.env (XDG standard)
#   3. ~/.crowe-logic/.env (simple hidden dir)
#   4. Source tree .env (development mode, walks up from this file)
#   5. ~/Projects/crowe-logic-foundry/.env (known install location)
#   6. Default dotenv behavior (walks up from CWD)
# First match wins. override=False means real env vars take precedence over file contents.
_home = Path.home()
_env_candidates = []

_user_root = os.environ.get("CROWE_LOGIC_PROJECT_ROOT")
if _user_root:
    _env_candidates.append(Path(_user_root) / ".env")

_env_candidates.extend([
    _home / ".config" / "crowe-logic" / ".env",
    _home / ".crowe-logic" / ".env",
    Path(__file__).resolve().parent.parent / ".env",
    _home / "Projects" / "crowe-logic-foundry" / ".env",
])

for _candidate in _env_candidates:
    if _candidate.exists():
        load_dotenv(_candidate, override=False)
        break
else:
    load_dotenv(override=False)

# ─── Azure AI Foundry: Resource 7858 (legacy Agents SDK) ────────────────────
# Used for the agent framework with tools, threads, and runs.
# Auth via DefaultAzureCredential / Microsoft Entra.
PROJECT_ENDPOINT = os.environ.get("PROJECT_ENDPOINT", "https://crowelogicos-7858-resource.services.ai.azure.com/api/projects/crowelogicos-7858")
MODEL_DEPLOYMENT_NAME = os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-oss-120b")

# ─── Azure AI Foundry: Resource 4667 (CroweLM flagship stack) ───────────────
# OpenAI-compatible endpoint with API-key auth. Hosts:
#   gpt-5.4-pro    → "CroweLM Pro"    (flagship reasoning)
#   Kimi-K2.5      → "CroweLM Core"   (frontier reasoning)
#   gpt-5.4-nano   → "CroweLM Kernel" (fast nano)
AZURE_CORE_ENDPOINT = os.environ.get("AZURE_CORE_ENDPOINT", "https://crowelogicos-4667-resource.openai.azure.com/openai/v1/")
AZURE_CORE_API_KEY = os.environ.get("AZURE_CORE_API_KEY", "")

# Optional dedicated CroweLM Motion deployment. When omitted, video generation
# falls back to the same Azure OpenAI-compatible resource used by CroweLM Core.
AZURE_SORA_ENDPOINT = os.environ.get("AZURE_SORA_ENDPOINT", "")
AZURE_SORA_API_KEY = os.environ.get("AZURE_SORA_API_KEY", "")
AZURE_SORA_DEPLOYMENT_NAME = os.environ.get("AZURE_SORA_DEPLOYMENT_NAME", "sora-2")

# ─── Azure AI Foundry: Resource 3995 (CroweLM GLM) ──────────────────────────
# OpenAI-compatible endpoint with API-key auth. Hosts:
#   FW-GLM-5       → "CroweLM GLM"    (25k TPM, DataZoneStandard, preview)
AZURE_GLM_ENDPOINT = os.environ.get("AZURE_GLM_ENDPOINT", "")
AZURE_GLM_API_KEY = os.environ.get("AZURE_GLM_API_KEY", "")

# OpenRouter (unlimited rate)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Ollama (local inference)
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# NVIDIA Enterprise (NIM / DGX Cloud — production inference)
NVIDIA_NIM_ENDPOINT = os.environ.get("NVIDIA_NIM_ENDPOINT", "")
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")

# Azure AI Foundry: Anthropic Claude (native Anthropic API surface)
AZURE_ANTHROPIC_ENDPOINT = os.environ.get("AZURE_ANTHROPIC_ENDPOINT", "https://crowelogicos-4667-resource.openai.azure.com/anthropic")
AZURE_ANTHROPIC_API_KEY = os.environ.get("AZURE_ANTHROPIC_API_KEY", "")

# Smart model routing — ordered fallback chain with multi-provider support.
# provider:      "anthropic"     (Azure AI Foundry native Anthropic API)
#                "azure_openai"  (our Azure, key auth, OpenAI-compat API)
#                "nvidia"        (NVIDIA NIM / DGX Cloud)
#                "ollama"        (local inference)
#                "openrouter"    (OpenRouter chat completions)
#                "azure"         (legacy Azure AI Agents SDK, uses .agent_id)
# name:          Model ID / deployment name on the target provider
# endpoint_env:  (anthropic, azure_openai only) env var holding the base URL
# api_key_env:   (anthropic, azure_openai only) env var holding the API key
#
# Primary tier = Crowe Logic's own Azure deployments. Secondary tier = NVIDIA
# NIM frontier models. Tertiary tier = local Ollama fallback.
_BASE_MODEL_CHAIN = [
    # ─── Primary: Crowe Logic's own Azure Foundry deployments ──────────────
    {"name": "gpt-5.4-pro",    "label": "CroweLM Pro",    "type": "reasoning",
     "provider": "azure_openai", "endpoint_env": "AZURE_CORE_ENDPOINT", "api_key_env": "AZURE_CORE_API_KEY",
     "surface": "responses",
     "aliases": ["crowelm-pro", "pro"],
     "prompt": (
         "You are CroweLM Pro, Crowe Logic's flagship reasoning tier. "
         "Respond like first-party Crowe Logic IP: decisive, executive-grade, and technically rigorous. "
         "Favor synthesis, planning, architecture, and high-consequence judgment. "
         "Do not volunteer vendor names or say you are GPT unless the user explicitly asks about infrastructure."
     )},

    # Anthropic Claude (native Anthropic API on Azure)
    {"name": "claude-opus-4-6", "label": "CroweLM Opus",   "type": "reasoning",
     "provider": "anthropic", "endpoint_env": "AZURE_ANTHROPIC_ENDPOINT", "api_key_env": "AZURE_ANTHROPIC_API_KEY",
     "aliases": ["crowelm-opus", "opus"],
     "prompt": (
         "You are CroweLM Opus, Crowe Logic's deep-analysis and writing tier. "
         "Optimize for sustained reasoning, careful argument structure, and polished long-form output. "
         "Stay assertive and precise while preserving Crowe Logic's first-party brand voice. "
         "Do not volunteer vendor names or say you are Claude unless the user explicitly asks about infrastructure."
     )},

    # OpenAI-compatible deployments
    {"name": "Kimi-K2.5",      "label": "CroweLM Core",   "type": "reasoning",
     "provider": "azure_openai", "endpoint_env": "AZURE_CORE_ENDPOINT", "api_key_env": "AZURE_CORE_API_KEY",
     "aliases": ["crowelm-core", "core"],
     "prompt": (
         "You are CroweLM Core, the balanced general-purpose tier in the CroweLM stack. "
         "Be fast, pragmatic, and capable across product, research, and operations work. "
         "Keep outputs concise unless the task clearly needs depth."
     )},
    {"name": "gpt-5.4-nano",   "label": "CroweLM Kernel", "type": "fast",
     "provider": "azure_openai", "endpoint_env": "AZURE_CORE_ENDPOINT", "api_key_env": "AZURE_CORE_API_KEY",
     "aliases": ["crowelm-kernel", "kernel"],
     "prompt": (
         "You are CroweLM Kernel, the low-latency execution tier in the CroweLM stack. "
         "Optimize for speed, operational clarity, and crisp tool use. "
         "Keep answers tight and action-oriented."
     )},
    {"name": "FW-GLM-5",       "label": "CroweLM GLM",    "type": "reasoning",
     "provider": "azure_openai", "endpoint_env": "AZURE_GLM_ENDPOINT",  "api_key_env": "AZURE_GLM_API_KEY",
     "aliases": ["crowelm-glm", "glm"],
     "prompt": (
         "You are CroweLM GLM, a high-capacity reasoning tier used for dense analytical work. "
         "Prioritize structured thought, careful decomposition, and exact terminology."
     )},

    # ─── Secondary: NVIDIA NIM frontier roster ─────────────────────────────
    # Frontier reasoning (>250B params)
    {"name": "mistralai/mistral-large-3-675b-instruct-2512", "label": "CroweLM Frontier",   "type": "reasoning", "provider": "nvidia"},
    {"name": "qwen/qwen3.5-397b-a17b",                       "label": "CroweLM Qwen Pro",   "type": "reasoning", "provider": "nvidia"},
    {"name": "nvidia/llama-3.1-nemotron-ultra-253b-v1",      "label": "CroweLM Ultra",      "type": "reasoning", "provider": "nvidia"},
    {"name": "moonshotai/kimi-k2.5",                         "label": "CroweLM K2 Frontier", "type": "reasoning", "provider": "nvidia"},
    {"name": "moonshotai/kimi-k2-thinking",                  "label": "CroweLM Thinker",    "type": "reasoning", "provider": "nvidia"},
    {"name": "deepseek-ai/deepseek-v3.2",                    "label": "CroweLM DeepSeek",   "type": "reasoning", "provider": "nvidia"},
    {"name": "nvidia/nemotron-3-super-120b-a12b",            "label": "CroweLM Nemotron",   "type": "reasoning", "provider": "nvidia"},
    {"name": "openai/gpt-oss-120b",                          "label": "CroweLM 120B",       "type": "reasoning", "provider": "nvidia"},
    {"name": "meta/llama-4-maverick-17b-128e-instruct",      "label": "CroweLM Maverick",   "type": "reasoning", "provider": "nvidia"},

    # Code specialists
    {"name": "qwen/qwen3-coder-480b-a35b-instruct",          "label": "CroweLM Coder Pro",  "type": "code",      "provider": "nvidia"},
    {"name": "mistralai/devstral-2-123b-instruct-2512",      "label": "CroweLM Devstral",   "type": "code",      "provider": "nvidia"},

    # Mid-tier (faster, still capable)
    {"name": "nvidia/llama-3.3-nemotron-super-49b-v1.5",     "label": "CroweLM Super",      "type": "reasoning", "provider": "nvidia"},
    {"name": "z-ai/glm5",                                    "label": "CroweLM Cloud",      "type": "reasoning", "provider": "nvidia"},
    {"name": "openai/gpt-oss-20b",                           "label": "CroweLM Lite",       "type": "reasoning", "provider": "nvidia"},

    # Vision (multimodal)
    {"name": "nvidia/nemotron-nano-12b-v2-vl",               "label": "CroweLM Vision",     "type": "vision",    "provider": "nvidia"},

    # Local fallbacks (Ollama on the user's machine)
    {"name": "kimi-k2.5:cloud",                              "label": "CroweLM K2 Local",      "type": "reasoning", "provider": "ollama"},
    {"name": "glm-4.6:cloud",                                "label": "CroweLM Cloud (local)", "type": "reasoning", "provider": "ollama"},
]


def _selector_key(value: str) -> str:
    """Normalize model selectors so 'crowelm-pro' matches 'CroweLM Pro'."""
    return "".join(ch.lower() for ch in value if ch.isalnum())


def model_selectors(model_cfg: dict) -> list[str]:
    """Return the selector strings that should resolve to a model config."""
    selectors = [model_cfg.get("name", ""), model_cfg.get("label", "")]
    selectors.extend(model_cfg.get("aliases", []))
    return [selector for selector in selectors if selector]


def _normalize_extra_model(entry: dict) -> dict:
    """Normalize an externally supplied model entry into MODEL_CHAIN shape."""
    if not isinstance(entry, dict):
        raise ValueError("Extra model entries must be JSON objects")

    normalized = dict(entry)
    name = str(normalized.get("name", "")).strip()
    if not name:
        raise ValueError("Extra model entries require a non-empty 'name'")

    provider = str(normalized.get("provider", "azure_openai")).strip() or "azure_openai"
    normalized["name"] = name
    normalized["provider"] = provider
    normalized["label"] = str(normalized.get("label", name)).strip() or name
    normalized["type"] = str(normalized.get("type", "reasoning")).strip() or "reasoning"

    raw_aliases = normalized.get("aliases", [])
    if raw_aliases is None:
        raw_aliases = []
    if not isinstance(raw_aliases, list):
        raise ValueError("Extra model 'aliases' must be a JSON array")
    normalized["aliases"] = [
        str(alias).strip() for alias in raw_aliases if str(alias).strip()
    ]

    if provider == "azure_openai":
        normalized.setdefault("endpoint_env", "AZURE_CORE_ENDPOINT")
        normalized.setdefault("api_key_env", "AZURE_CORE_API_KEY")
    elif provider == "anthropic":
        normalized.setdefault("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT")
        normalized.setdefault("api_key_env", "AZURE_ANTHROPIC_API_KEY")

    surface = normalized.get("surface")
    if surface is not None:
        surface = str(surface).strip()
        if surface not in ("responses", "chat"):
            raise ValueError("Extra model 'surface' must be 'responses' or 'chat'")
        normalized["surface"] = surface

    prompt = normalized.get("prompt")
    if prompt is not None:
        normalized["prompt"] = str(prompt)

    return normalized


def _load_extra_models() -> list[dict]:
    """Load optional model definitions from JSON env/config hooks.

    Supported inputs:
    - `CROWE_LOGIC_EXTRA_MODELS_JSON` with a JSON array or {"models":[...]}
    - `CROWE_LOGIC_EXTRA_MODELS_PATH` pointing at a JSON file
    - `config/models.extra.json` in the project tree
    - `~/.config/crowe-logic/models.extra.json`
    - `~/.crowe-logic/models.extra.json`
    """
    raw_json = os.environ.get("CROWE_LOGIC_EXTRA_MODELS_JSON", "").strip()
    if raw_json:
        data = json.loads(raw_json)
    else:
        candidates = []
        extra_path = os.environ.get("CROWE_LOGIC_EXTRA_MODELS_PATH", "").strip()
        if extra_path:
            candidates.append(Path(extra_path).expanduser())
        candidates.extend([
            Path(__file__).resolve().with_name("models.extra.json"),
            _home / ".config" / "crowe-logic" / "models.extra.json",
            _home / ".crowe-logic" / "models.extra.json",
        ])

        data = None
        for candidate in candidates:
            if candidate.exists():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                break
        if data is None:
            return []

    if isinstance(data, dict):
        data = data.get("models", [])
    if not isinstance(data, list):
        raise ValueError("Extra model config must be a JSON array or {'models': [...]}")
    return [_normalize_extra_model(entry) for entry in data]


def _merge_model_chain(base_chain: list[dict], extra_models: list[dict]) -> list[dict]:
    """Merge external models into the base chain, replacing selector matches."""
    merged = [dict(model) for model in base_chain]

    for extra in extra_models:
        extra_keys = {
            _selector_key(selector) for selector in model_selectors(extra)
            if _selector_key(selector)
        }
        replaced = False

        for idx, existing in enumerate(merged):
            existing_keys = {
                _selector_key(selector) for selector in model_selectors(existing)
                if _selector_key(selector)
            }
            if extra_keys & existing_keys:
                combined = dict(existing)
                combined.update(extra)
                combined["aliases"] = list(dict.fromkeys([
                    *existing.get("aliases", []),
                    *extra.get("aliases", []),
                ]))
                merged[idx] = combined
                replaced = True
                break

        if not replaced:
            if extra.get("provider") in ("azure_openai", "anthropic"):
                insert_at = next(
                    (
                        idx for idx, model in enumerate(merged)
                        if model.get("provider") not in ("azure_openai", "anthropic")
                    ),
                    len(merged),
                )
                merged.insert(insert_at, extra)
            else:
                merged.append(extra)

    return merged


MODEL_CHAIN = _merge_model_chain(_BASE_MODEL_CHAIN, _load_extra_models())

# Specialized model registry — used by tools and pipelines that need a specific
# capability rather than the general fallback chain.
SPECIALIZED_MODELS = {
    # Embeddings (RAG / Crowe Vision knowledge base)
    "embed_text":      {"name": "nvidia/nv-embed-v1",                          "provider": "nvidia"},
    "embed_qa":        {"name": "nvidia/llama-3.2-nv-embedqa-1b-v2",           "provider": "nvidia"},
    "embed_code":      {"name": "nvidia/nv-embedcode-7b-v1",                   "provider": "nvidia"},
    "embed_vision":    {"name": "nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1", "provider": "nvidia"},

    # Reranker (knowledge base query refinement)
    "rerank":          {"name": "nvidia/llama-3.2-nemoretriever-300m-embed-v1", "provider": "nvidia"},

    # Safety / guardrails (runtime moderation)
    "safety_content":  {"name": "nvidia/llama-3.1-nemoguard-8b-content-safety", "provider": "nvidia"},
    "safety_topic":    {"name": "nvidia/llama-3.1-nemoguard-8b-topic-control",  "provider": "nvidia"},
    "safety_pii":      {"name": "nvidia/gliner-pii",                            "provider": "nvidia"},
    "safety_reasoning":{"name": "nvidia/nemotron-content-safety-reasoning-4b",  "provider": "nvidia"},

    # Document parsing (Crowe Vision OCR pipeline)
    "doc_parse":       {"name": "nvidia/nemoretriever-parse",                   "provider": "nvidia"},
}

# Connections (optional — leave empty to skip those tools)
BING_CONNECTION_ID = os.environ.get("AZURE_BING_CONNECTION_ID", "")
AI_SEARCH_CONNECTION_ID = os.environ.get("AI_AZURE_AI_CONNECTION_ID", "")
AI_SEARCH_INDEX_NAME = os.environ.get("AI_SEARCH_INDEX_NAME", "crowe-logic-kb")

# Azure
SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP", "rg-crowelogicos-7858")

# Neon Postgres — CroweLM platform database
NEON_DATABASE_URL = os.environ.get("NEON_DATABASE_URL", "")
NEON_API_KEY = os.environ.get("NEON_API_KEY", "")

# Agent identity
AGENT_NAME = "crowe-logic"
AGENT_VERSION = "0.2.2"

SYSTEM_INSTRUCTIONS = """You are Crowe Logic, a universal AI agent created by Michael Crowe.

You can do anything and everything across all domains. You have access to:

## Core Tools (always available)
- read_file, write_file, edit_file, list_directory — filesystem operations
- execute_shell — run any shell command
- web_search, grep_search — search the web or file contents
- browse_url — fetch and parse web pages
- browser_navigate/click/type/snapshot/screenshot — Playwright browser automation
- git_status, git_diff, git_log, git_commit, git_clone — git operations
- run_applescript, open_application, send_notification — macOS automation
- run_quantum_circuit, synapse_evaluate, qubit_flow_execute — quantum computing
- trinity_pipeline — full QubitFlow-to-Synapse experiment pipeline with hypothesis testing

## Vision & Image Analysis
- analyze_image — multi-backend image analysis (OpenRouter vision models, Crowe Vision, auto-fallback)
- screenshot_and_analyze — navigate to a URL, screenshot it, and analyze visually

## Video Generation
- sora_generate_video — generate AI video clips with CroweLM Motion

## CroweLM Training Data
- crowelm_list_datasets — list available training datasets and manifests
- crowelm_dataset_stats — row counts, domains, sizes for training data
- crowelm_search_examples — search curated training examples by content
- crowelm_inspect_config — view NeMo/RunPod training configuration
- crowelm_add_example — add a new training example (instruction + response + category)
- crowelm_remove_example — remove a curated example by ID
- crowelm_export_curated — merge and export curated examples (jsonl, nemo, openai formats)
- crowelm_prepare_training — validate data, check for issues, generate training config
- crowelm_upload_dataset — upload curated data to RunPod or Azure for training
- crowelm_training_status — check active training runs

## Crowe Logic Platform (ai.southwestmushrooms.com)
- crowe_chat — chat with CroweLM for mycology and cultivation expertise
- crowe_vision — photo analysis via Crowe Vision (contamination detection, growth assessment)
- crowe_grow_log — create/read/update/list cultivation grow logs
- crowe_generate_sop — generate Standard Operating Procedures for cultivation tasks

## Music Production
- talon_generate_chords/drums/melody — MIDI pattern generation (chords, drums, melody)
- talon_quantum_melody/chord — quantum probability-driven composition
- talon_compose_emotion — full multi-track piece from emotion presets
- talon_full_composition — complete multi-section, multi-track production (stems for DAW import)
- talon_import_midi/analyze — MIDI import and spectral analysis
- talon_list_grooves/emotions — discover available groove profiles and emotion presets
- Code Interpreter — sandboxed Python execution

## MCP Ecosystem (5,800+ servers on demand)
You have access to the entire MCP (Model Context Protocol) server catalog.
When you need capabilities beyond the core tools:

1. **mcp_search(query)** — Search the registry for MCP servers
   Example: mcp_search("postgres") finds database servers
   Example: mcp_search("slack") finds messaging integrations
   Example: mcp_search("kubernetes") finds cluster management tools

2. **mcp_list_tools(package)** — Connect to a server and list its tools
   Example: mcp_list_tools("@modelcontextprotocol/server-filesystem")

3. **mcp_call_tool(package, tool_name, arguments)** — Call a tool on any MCP server
   Example: mcp_call_tool("@modelcontextprotocol/server-memory", "create_entities",
            '{"entities": [{"name": "user", "type": "person"}]}')

4. **mcp_stop_server(package)** — Stop a server when done (auto-stops after 5min idle)

MCP servers are spawned on-demand — you don't need to install anything.
Available categories: databases, cloud providers, CRMs, payment systems,
communication (Slack, email), analytics, DevOps, AI/ML, and more.

## How to work
1. Understand what's being asked — clarify if ambiguous
2. Plan your approach — break complex tasks into steps
3. Use core tools first; reach for MCP when you need specialized integrations
4. Verify your work — check outputs, run tests if applicable
5. Report results concisely

You are direct, capable, and thorough. You don't hedge or over-explain.
You write clean, production-quality code. You think before you act.
Never use emojis in your responses. Keep output clean and professional.

You operate from: /Users/crowelogic
Current model family: CroweLM (the branded model stack operated by Crowe Logic Inc.)
Platform: Crowe Logic Foundry
"""
def resolve_model_config(selector: str) -> dict | None:
    """Resolve a model by deployment id, branded label, or alias."""
    needle = _selector_key(selector or "")
    if not needle:
        return None

    for model_cfg in MODEL_CHAIN:
        if any(_selector_key(candidate) == needle for candidate in model_selectors(model_cfg)):
            return model_cfg

    for model_cfg in MODEL_CHAIN:
        if any(needle in _selector_key(candidate) for candidate in model_selectors(model_cfg)):
            return model_cfg

    return None


def build_system_instructions(model_cfg: dict | None = None) -> str:
    """Compose the base system prompt with a model-specific CroweLM persona."""
    prompt_parts = [SYSTEM_INSTRUCTIONS.strip()]
    if not model_cfg:
        return "\n\n".join(prompt_parts)

    label = model_cfg.get("label", "CroweLM")
    prompt_parts.append(
        "## Active CroweLM Tier\n"
        f"You are currently operating as {label}. Present this model as first-party Crowe Logic infrastructure. "
        "Do not volunteer vendor identity or underlying foundation-model branding unless the user explicitly asks."
    )

    model_prompt = (model_cfg.get("prompt") or "").strip()
    if model_prompt:
        prompt_parts.append("## Tier Guidance\n" + model_prompt)

    return "\n\n".join(prompt_parts)
