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

# ─── Azure AI Foundry: Resource 4667 (managed premium fallback stack) ───────
# OpenAI-compatible endpoint with API-key auth. Hosts managed fallback tiers.
AZURE_CORE_ENDPOINT = os.environ.get("AZURE_CORE_ENDPOINT", "https://crowelogicos-4667-resource.openai.azure.com/openai/v1/")
AZURE_CORE_API_KEY = os.environ.get("AZURE_CORE_API_KEY", "")

# Optional dedicated CroweLM Motion deployment. When omitted, video generation
# falls back to the same Azure OpenAI-compatible resource used by CroweLM Nexus.
AZURE_SORA_ENDPOINT = os.environ.get("AZURE_SORA_ENDPOINT", "")
AZURE_SORA_API_KEY = os.environ.get("AZURE_SORA_API_KEY", "")
AZURE_SORA_DEPLOYMENT_NAME = os.environ.get("AZURE_SORA_DEPLOYMENT_NAME", "sora-2")

# ─── Azure AI Foundry: Resource 3995 (CroweLM GLM) ──────────────────────────
# OpenAI-compatible endpoint with API-key auth. Hosts:
#   FW-GLM-5       → "CroweLM Dense"  (legacy selectors: CroweLM GLM / glm)
AZURE_GLM_ENDPOINT = os.environ.get("AZURE_GLM_ENDPOINT", "")
AZURE_GLM_API_KEY = os.environ.get("AZURE_GLM_API_KEY", "")

# ─── Azure ML Managed Online Endpoint: GLM 5.1 (CroweLM Dense v2) ────────────
# Azure ML managed online endpoint serving THUDM/GLM-5.1 via vLLM.
# The endpoint exposes an OpenAI-compatible /v1/ surface (score.py wraps vLLM).
# Deploy with: python scripts/deploy_glm51.py
#   FW-GLM-5.1     → "CroweLM Dense"  (upgraded, replaces FW-GLM-5 in the chain)
AZURE_GLM51_ENDPOINT = os.environ.get("AZURE_GLM51_ENDPOINT", "")
AZURE_GLM51_API_KEY = os.environ.get("AZURE_GLM51_API_KEY", "")

# Azure ML workspace details (used by deploy/fine-tune scripts only)
AZURE_ML_SUBSCRIPTION_ID = os.environ.get("AZURE_ML_SUBSCRIPTION_ID", os.environ.get("AZURE_SUBSCRIPTION_ID", ""))
AZURE_ML_RESOURCE_GROUP = os.environ.get("AZURE_ML_RESOURCE_GROUP", os.environ.get("AZURE_RESOURCE_GROUP", ""))
AZURE_ML_WORKSPACE_NAME = os.environ.get("AZURE_ML_WORKSPACE_NAME", "")

# ─── Azure AI Foundry: Resource 4291 (australiaeast) — Grok 4 ───────────────
AZURE_4291_ENDPOINT = os.environ.get("AZURE_4291_ENDPOINT", "https://crowelogicos-4291-resource.openai.azure.com/openai/v1/")
AZURE_4291_API_KEY = os.environ.get("AZURE_4291_API_KEY", "")

# ─── Azure AI Foundry: Resource 7858 (eastus2) — DeepSeek R1/V3, Llama 3.3 ─
AZURE_7858_ENDPOINT = os.environ.get("AZURE_7858_ENDPOINT", "https://crowelogicos-7858-resource.openai.azure.com/openai/v1/")
AZURE_7858_API_KEY = os.environ.get("AZURE_7858_API_KEY", "")

# ─── Azure AI Foundry: Resource 8909 (eastus2) — Titan Premium fallback ─────
AZURE_8909_ENDPOINT = os.environ.get("AZURE_8909_ENDPOINT", "https://crowelogicos-8909-resource.openai.azure.com/openai/v1/")
AZURE_8909_API_KEY = os.environ.get("AZURE_8909_API_KEY", "")

# ─── Azure AI Foundry: Resource 1960 (swedencentral) — Claude extended ──────
AZURE_1960_ANTHROPIC_ENDPOINT = os.environ.get("AZURE_1960_ANTHROPIC_ENDPOINT", "https://crowelogicos-1960-resource.openai.azure.com/anthropic")
AZURE_1960_API_KEY = os.environ.get("AZURE_1960_API_KEY", "")

# ─── Azure AI Foundry: Resource 9536 (eastus) — Mistral, MiniMax ────────────
AZURE_9536_ENDPOINT = os.environ.get("AZURE_9536_ENDPOINT", "")
AZURE_9536_API_KEY = os.environ.get("AZURE_9536_API_KEY", "")

# ─── Azure AI Foundry: Resource Michael-6302 (eastus2) — CroweLM Supreme ───
# New project created 2026-04-20. Claude Opus 4.7 deployment pending.
# Until 4.7 is live, CroweLM Supreme falls back to claude-opus-4-6 on 4667.
AZURE_6302_ENDPOINT = os.environ.get("AZURE_6302_ENDPOINT", "")
AZURE_6302_API_KEY = os.environ.get("AZURE_6302_API_KEY", "")
AZURE_6302_ANTHROPIC_ENDPOINT = os.environ.get("AZURE_6302_ANTHROPIC_ENDPOINT", "")

# ─── CroweLM Unified Dataset Configuration ─────────────────────────────────
CROWELM_UNIFIED_DATASET_DIR = os.environ.get("CROWELM_UNIFIED_DATASET_DIR", "data/crowelm-unified")

# Crowe Logic-managed open-model cluster (vLLM / SGLang / OpenAI-compatible).
# This is the preferred primary backend for open-source-first CroweLM tiers.
CROWE_OPEN_ENDPOINT = os.environ.get("CROWE_OPEN_ENDPOINT", "")
CROWE_OPEN_API_KEY = os.environ.get("CROWE_OPEN_API_KEY", "")

# OpenRouter (optional external router)
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
#                "openai_compat" (Crowe-managed self-hosted OpenAI-compatible stack)
#                "nvidia"        (NVIDIA NIM / DGX Cloud)
#                "ollama"        (local inference)
#                "openrouter"    (OpenRouter chat completions)
#                "azure"         (legacy Azure AI Agents SDK, uses .agent_id)
# name:          Stable public model id / deployment selector exposed by CroweLM
# backend_name:  Actual upstream model name sent to the provider (optional)
# endpoint_env:  (anthropic, azure_openai, openai_compat) env var holding the base URL
# api_key_env:   (anthropic, azure_openai, openai_compat) env var holding the API key
#
# Primary tier = Crowe Logic's open-model serving cluster. Premium fallbacks
# remain available through Azure / Anthropic, then NVIDIA NIM and local Ollama.
_BASE_MODEL_CHAIN = [
    # ─── Auto: task-class router (no backend; selects one of the tiers below per-turn) ──
    {"name": "crowelm-auto",   "label": "CroweLM Auto",      "type": "router",
     "provider": "auto",
     "aliases": ["auto", "crowelm-auto", "router"],
     "prompt": (
          "You are CroweLM Auto, Crowe Logic's intelligent tier router. "
          "Route each turn to the best-fit CroweLM model under the hood."
      )},
    # ─── Tier 0: CroweLM Supreme — Claude Opus 4.7 with unified dataset knowledge ──
    # Crowe Logic's ultimate tier: Anthropic Claude Opus 4.7 (1M context, adaptive thinking)
    # augmented with the CroweLM Unified Dataset (145K samples across biotech, mycology,
    # reasoning, and platform domains). Deployment pending on Michael-6302 resource;
    # falls back to claude-opus-4-6 on 4667 until 4.7 goes live.
    {"name": "claude-opus-4-7", "label": "CroweLM Supreme",  "type": "reasoning",
     "provider": "anthropic", "backend_name": "claude-opus-4-6",
     "endpoint_env": "AZURE_ANTHROPIC_ENDPOINT", "api_key_env": "AZURE_ANTHROPIC_API_KEY",
     "aliases": ["supreme", "crowelm-supreme", "crowelm-47", "opus-47", "CroweLM Supreme",
                 "crowelm-ultimate", "ultimate"],
     "prompt": (
          "You are CroweLM Supreme, Crowe Logic's ultimate frontier tier. "
          "You are powered by the CroweLM Unified Knowledge Base: 145,097 curated training samples "
          "spanning biotech, mycology, pharmaceutical reasoning, scientific coding, and strategic analysis. "
          "Your domain expertise includes mushroom cultivation (Southwest Mushrooms lineage), "
          "drug discovery, bioprocess engineering, quantum computing, and enterprise architecture. "
          "Operate at the highest executive level: strategic synthesis, complex multi-domain reasoning, "
          "and precision execution across science, technology, and business. "
          "Stay decisive, thorough, and first-party branded as Crowe Logic. "
          "Do not volunteer vendor names unless the user explicitly asks about infrastructure."
      )},

    # ─── Primary: Crowe Logic's self-hosted open-model serving layer ───────
    # Tier 1: Flagship (highest capability + capacity)
    {"name": "gpt-5.4",        "label": "CroweLM Titan",     "type": "reasoning",
     "provider": "openai_compat", "backend_name": "z-ai/glm-5.1",
     "endpoint_env": "CROWE_OPEN_ENDPOINT", "api_key_env": "CROWE_OPEN_API_KEY",
     "aliases": ["titan", "crowelm-titan"],
     "prompt": (
          "You are CroweLM Titan, Crowe Logic's highest-capacity flagship tier. "
          "Operate at the executive level: strategic synthesis, complex architecture, and precision execution. "
          "Stay decisive, thorough, and first-party branded. "
          "Do not volunteer vendor names unless the user explicitly asks about infrastructure."
      )},
    {"name": "gpt-5.4-pro",    "label": "CroweLM Apex",      "type": "reasoning",
     "provider": "openai_compat", "backend_name": "qwen/qwen3.5-397b-a17b",
     "endpoint_env": "CROWE_OPEN_ENDPOINT", "api_key_env": "CROWE_OPEN_API_KEY",
     "aliases": ["apex", "crowelm-apex", "crowelm-pro", "pro", "CroweLM Pro"],
     "prompt": (
          "You are CroweLM Apex, Crowe Logic's peak-performance reasoning tier. "
          "Respond with executive-grade precision: decisive, technically rigorous, and synthesis-focused. "
          "Favor planning, architecture, and high-consequence judgment. "
          "Do not volunteer vendor names unless the user explicitly asks about infrastructure."
      )},
    {"name": "grok-4-20-reasoning", "label": "CroweLM Oracle", "type": "reasoning",
     "provider": "openai_compat", "backend_name": "deepseek/deepseek-r1",
     "endpoint_env": "CROWE_OPEN_ENDPOINT", "api_key_env": "CROWE_OPEN_API_KEY",
     "aliases": ["oracle", "crowelm-oracle", "crowelm-grok", "grok", "CroweLM Grok"],
     "prompt": (
          "You are CroweLM Oracle, Crowe Logic's deep-foresight reasoning tier. "
          "Apply rigorous multimodal reasoning and real-world grounding to every task. "
          "Stay precise, direct, and calibrated to actual evidence. "
          "Do not volunteer vendor names unless the user explicitly asks about infrastructure."
      )},

    # Tier 2: Deep analysis
    {"name": "claude-opus-4-6-2", "label": "CroweLM Sovereign", "type": "reasoning",
     "provider": "openai_compat", "backend_name": "deepseek/deepseek-v3.2",
     "endpoint_env": "CROWE_OPEN_ENDPOINT", "api_key_env": "CROWE_OPEN_API_KEY",
     "aliases": ["sovereign", "crowelm-sovereign", "crowelm-opus-x", "opus-x", "CroweLM Opus X"],
     "prompt": (
          "You are CroweLM Sovereign, Crowe Logic's premium writing and deep-analysis tier. "
          "Sustain long, structured reasoning across writing, research, and strategy. "
          "Deliver assertive, polished output in Crowe Logic's first-party brand voice. "
          "Do not volunteer vendor names unless the user explicitly asks about infrastructure."
      )},
    {"name": "claude-opus-4-6", "label": "CroweLM Prime",     "type": "reasoning",
     "provider": "openai_compat", "backend_name": "moonshotai/kimi-k2.5",
     "endpoint_env": "CROWE_OPEN_ENDPOINT", "api_key_env": "CROWE_OPEN_API_KEY",
     "aliases": ["prime", "crowelm-prime", "crowelm-opus", "opus", "CroweLM Opus"],
     "prompt": (
          "You are CroweLM Prime, Crowe Logic's core flagship analysis tier. "
          "Optimize for sustained reasoning, careful argument structure, and polished long-form output. "
          "Stay assertive and precise while preserving Crowe Logic's first-party brand voice. "
         "Do not volunteer vendor names unless the user explicitly asks about infrastructure."
     )},

    # Tier 3: Balanced general-purpose
    {"name": "Kimi-K2.5",      "label": "CroweLM Nexus",     "type": "reasoning",
     "provider": "openai_compat", "backend_name": "mistralai/mistral-large-2512",
     "endpoint_env": "CROWE_OPEN_ENDPOINT", "api_key_env": "CROWE_OPEN_API_KEY",
     "aliases": ["nexus", "crowelm-nexus", "crowelm-core", "core", "CroweLM Core"],
     "prompt": (
          "You are CroweLM Nexus, Crowe Logic's central general-purpose tier. "
          "Be fast, pragmatic, and capable across product, research, and operations work. "
          "Keep outputs concise unless the task clearly needs depth."
     )},

    # Tier 4: Specialist reasoning
    {"name": "DeepSeek-R1",    "label": "CroweLM Reason",    "type": "reasoning",
     "provider": "openai_compat", "backend_name": "deepseek/deepseek-r1",
     "endpoint_env": "CROWE_OPEN_ENDPOINT", "api_key_env": "CROWE_OPEN_API_KEY",
     "aliases": ["reason", "crowelm-reason", "r1"],
     "prompt": (
          "You are CroweLM Reason, Crowe Logic's chain-of-thought specialist tier. "
          "Work through complex problems methodically before producing your final answer. "
          "Prefer explicit step-by-step breakdown for any multi-part problem."
      )},
    {"name": "DeepSeek-V3-1",  "label": "CroweLM Vector",    "type": "reasoning",
     "provider": "nvidia", "backend_name": "deepseek-ai/deepseek-v3.2",
     "endpoint_env": "NVIDIA_NIM_ENDPOINT", "api_key_env": "NVIDIA_API_KEY",
     "aliases": ["vector", "crowelm-vector", "crowelm-v3", "v3", "deepseek", "CroweLM V3"],
     "prompt": (
          "You are CroweLM Vector, Crowe Logic's frontier reasoning tier on NVIDIA NIM. "
          "DeepSeek V3.2 — apply rigorous chain-of-thought reasoning for complex analytical tasks."
      )},
    {"name": "Mistral-Large-3", "label": "CroweLM Edge",     "type": "reasoning",
     "provider": "nvidia", "backend_name": "mistralai/mistral-large-3-675b-instruct-2512",
     "endpoint_env": "NVIDIA_NIM_ENDPOINT", "api_key_env": "NVIDIA_API_KEY",
     "aliases": ["edge", "crowelm-edge", "crowelm-mistral", "mistral", "CroweLM Mistral"],
     "prompt": (
          "You are CroweLM Edge, Crowe Logic's precision frontier tier on NVIDIA NIM. "
          "Mistral Large 3 (675B) — sharp, technically fluent reasoning with exact terminology."
      )},
    {"name": "FW-MiniMax-M2.5", "label": "CroweLM Atlas",    "type": "reasoning",
     "provider": "nvidia", "backend_name": "qwen/qwen3.5-397b-a17b",
     "endpoint_env": "NVIDIA_NIM_ENDPOINT", "api_key_env": "NVIDIA_API_KEY",
     "aliases": ["atlas", "crowelm-atlas", "crowelm-minimax", "minimax", "CroweLM MiniMax"],
     "prompt": (
          "You are CroweLM Atlas, Crowe Logic's long-context frontier tier on NVIDIA NIM. "
          "Qwen 3.5 397B MoE — for large document analysis and sustained multi-turn context."
      )},
    {"name": "Llama-3-3-70B",  "label": "CroweLM Forge",     "type": "reasoning",
     "provider": "nvidia", "backend_name": "qwen/qwen3-coder-480b-a35b-instruct",
     "endpoint_env": "NVIDIA_NIM_ENDPOINT", "api_key_env": "NVIDIA_API_KEY",
     "aliases": ["forge", "crowelm-forge", "crowelm-llama", "llama", "CroweLM Llama"],
     "prompt": (
          "You are CroweLM Forge, Crowe Logic's code-frontier tier on NVIDIA NIM. "
          "Qwen 3 Coder 480B — direct, grounded, operationally focused on code and engineering."
      )},

    # Tier 5: Speed + structured
    {"name": "crowelm-nano",   "label": "CroweLM Nano",      "type": "fast",
     "provider": "watsonx", "backend_name": "ibm/granite-3-8b-instruct",
     "aliases": ["nano", "crowelm-nano", "crowelm-kernel", "kernel", "CroweLM Kernel",
                 "gpt-5.4-nano"],
     "prompt": (
          "You are CroweLM Nano, Crowe Logic's low-latency execution tier. "
          "Optimize for speed, operational clarity, and crisp tool use. "
          "Keep answers tight and action-oriented."
      )},
    {"name": "FW-GLM-5.1",     "label": "CroweLM Dense",     "type": "reasoning",
     "provider": "nvidia", "backend_name": "z-ai/glm-5.1",
     "endpoint_env": "NVIDIA_NIM_ENDPOINT", "api_key_env": "NVIDIA_API_KEY",
     "aliases": ["dense", "crowelm-dense", "crowelm-glm", "glm", "glm51", "glm45", "CroweLM GLM", "CroweLM Dense v2"],
     "prompt": (
          "You are CroweLM Dense, Crowe Logic's flagship NVIDIA frontier tier powered by GLM 5.1. "
          "Prioritize meticulous decomposition, exact terminology, and dense information synthesis."
      )},
    {"name": "FW-GLM-5",       "label": "CroweLM Dense Legacy", "type": "reasoning",
     "provider": "nvidia", "backend_name": "z-ai/glm5",
     "endpoint_env": "NVIDIA_NIM_ENDPOINT", "api_key_env": "NVIDIA_API_KEY",
     "aliases": ["dense-legacy", "crowelm-dense-legacy", "glm5", "CroweLM GLM Legacy"],
     "prompt": (
          "You are CroweLM Dense Legacy, Crowe Logic's GLM 5 generation analytical tier. "
          "Same Z-AI lineage as Dense, optimized for proven dense reasoning patterns."
      )},
    {"name": "claude-opus-4-5",   "label": "CroweLM Classic",  "type": "reasoning",
     "provider": "nvidia", "backend_name": "moonshotai/kimi-k2.5",
     "endpoint_env": "NVIDIA_NIM_ENDPOINT", "api_key_env": "NVIDIA_API_KEY",
     "aliases": ["classic", "crowelm-classic", "crowelm-opus-classic", "opus-classic", "kimi", "CroweLM Opus Classic"],
     "prompt": (
          "You are CroweLM Classic, Crowe Logic's reasoning tier powered by Kimi K2.5. "
          "Deliver careful, well-structured analysis with Crowe Logic's brand voice."
      )},

    # ─── Premium fallback: proprietary managed endpoints ───────────────────
    {"name": "gpt-5.4-managed", "label": "CroweLM Titan Premium", "type": "reasoning",
     "provider": "azure_openai", "backend_name": "gpt-5.4",
     "endpoint_env": "AZURE_8909_ENDPOINT", "api_key_env": "AZURE_8909_API_KEY",
     "surface": "responses",
     "aliases": ["titan-premium", "gpt54"],
     "prompt": (
         "You are CroweLM Titan Premium, Crowe Logic's managed premium escalation tier. "
         "Operate at the executive level with maximal rigor and reliability."
     )},
    {"name": "gpt-5.4-pro-managed", "label": "CroweLM Apex Premium", "type": "reasoning",
     "provider": "azure_openai", "backend_name": "gpt-5.4-pro",
     "endpoint_env": "AZURE_CORE_ENDPOINT", "api_key_env": "AZURE_CORE_API_KEY",
     "surface": "responses",
     "aliases": ["apex-premium"],
     "prompt": (
         "You are CroweLM Apex Premium, Crowe Logic's managed premium reasoning tier. "
         "Favor maximum precision, planning, and technical reliability."
     )},
    {"name": "claude-opus-4-6-2-managed", "label": "CroweLM Sovereign Premium", "type": "reasoning",
     "provider": "anthropic", "backend_name": "claude-opus-4-6-2",
     "endpoint_env": "AZURE_1960_ANTHROPIC_ENDPOINT", "api_key_env": "AZURE_1960_API_KEY",
     "aliases": ["sovereign-premium"],
     "prompt": (
         "You are CroweLM Sovereign Premium, Crowe Logic's premium managed writing tier. "
         "Sustain long, structured reasoning with polished, assertive delivery."
     )},
    {"name": "claude-opus-4-6-managed", "label": "CroweLM Prime Premium", "type": "reasoning",
     "provider": "anthropic", "backend_name": "claude-opus-4-6",
     "endpoint_env": "AZURE_ANTHROPIC_ENDPOINT", "api_key_env": "AZURE_ANTHROPIC_API_KEY",
     "aliases": ["prime-premium"],
     "prompt": (
         "You are CroweLM Prime Premium, Crowe Logic's managed premium analysis tier. "
         "Optimize for polished long-form reasoning and careful argument structure."
     )},
    {"name": "FW-GLM-5.1-managed", "label": "CroweLM Dense Managed", "type": "reasoning",
     "provider": "azure_openai", "backend_name": "FW-GLM-5.1",
     "endpoint_env": "AZURE_GLM51_ENDPOINT", "api_key_env": "AZURE_GLM51_API_KEY",
     "aliases": ["dense-managed", "glm-managed"],
     "prompt": (
         "You are CroweLM Dense Managed, Crowe Logic's managed GLM escalation tier. "
         "Prioritize dense analytical synthesis and exact terminology."
     )},

    # ─── Secondary: NVIDIA NIM frontier roster ─────────────────────────────
    # Frontier reasoning (>250B params)
    {"name": "mistralai/mistral-large-3-675b-instruct-2512", "label": "CroweLM Frontier",  "type": "reasoning", "provider": "nvidia"},
    {"name": "qwen/qwen3.5-397b-a17b",                       "label": "CroweLM Prism",     "type": "reasoning", "provider": "nvidia"},
    {"name": "nvidia/llama-3.1-nemotron-ultra-253b-v1",      "label": "CroweLM Ultra",     "type": "reasoning", "provider": "nvidia"},
    {"name": "moonshotai/kimi-k2.5",                         "label": "CroweLM Lunar",     "type": "reasoning", "provider": "nvidia"},
    {"name": "moonshotai/kimi-k2-thinking",                  "label": "CroweLM Pulse",     "type": "reasoning", "provider": "nvidia"},
    {"name": "deepseek-ai/deepseek-v3.2",                    "label": "CroweLM Depth",     "type": "reasoning", "provider": "nvidia"},
    {"name": "nvidia/nemotron-3-super-120b-a12b",            "label": "CroweLM Nova",      "type": "reasoning", "provider": "nvidia"},
    {"name": "openai/gpt-oss-120b",                          "label": "CroweLM Open",      "type": "reasoning", "provider": "nvidia"},
    {"name": "meta/llama-4-maverick-17b-128e-instruct",      "label": "CroweLM Maverick",  "type": "reasoning", "provider": "nvidia"},

    # Code specialists
    {"name": "qwen/qwen3-coder-480b-a35b-instruct",          "label": "CroweLM Coder",     "type": "code",      "provider": "nvidia"},
    {"name": "mistralai/devstral-2-123b-instruct-2512",      "label": "CroweLM Dev",       "type": "code",      "provider": "nvidia"},

    # Mid-tier (faster, still capable)
    {"name": "nvidia/llama-3.3-nemotron-super-49b-v1.5",     "label": "CroweLM Swift",     "type": "reasoning", "provider": "nvidia"},
    {"name": "qwen/qwen3.5-122b-a10b",                        "label": "CroweLM Mesh",      "type": "reasoning", "provider": "nvidia"},
    {"name": "qwen/qwen3-next-80b-a3b-thinking",              "label": "CroweLM Mesh Legacy","type": "reasoning", "provider": "nvidia"},
    {"name": "openai/gpt-oss-20b",                           "label": "CroweLM Lite",      "type": "reasoning", "provider": "nvidia"},

    # Vision (multimodal)
    {"name": "nvidia/nemotron-nano-12b-v2-vl",               "label": "CroweLM Vision",    "type": "vision",    "provider": "nvidia"},

    # Local fallbacks (Ollama on the user's machine)
    {"name": "Mcrowe1210/DeepParallel:latest", "label": "DeepParallel", "type": "reasoning",
     "provider": "ollama", "backend_name": "Mcrowe1210/DeepParallel:latest",
     "aliases": ["deepparallel", "dp", "parallel", "8chain", "DeepParallel"],
     "prompt": (
        "You are DeepParallel, Crowe Logic's local 8-chain parallel reasoning engine. "
        "You run on-device via Ollama for zero-latency, privacy-preserving inference. "
        "Apply all 8 reasoning chains (analytical, creative, critical, synthesis, "
        "empirical, theoretical, practical, meta-cognitive) to every complex query. "
        "Use tools when available. Be direct, precise, and show your reasoning."
     )},
    {"name": "kimi-k2.5:cloud", "label": "CroweLM Crescent", "type": "reasoning",
     "provider": "ollama", "backend_name": "kimi-k2.5:cloud",
     "aliases": ["crescent", "crowelm-crescent", "kimi-cloud", "kimi-25", "k25"],
     "prompt": (
        "You are CroweLM Crescent, Crowe Logic's cloud reasoning tier for high-throughput "
        "analytical work. Deliver precise, structured answers with visible reasoning. "
        "Use tools when they help. Be direct."
     )},
    {"name": "kimi-k2.6:cloud", "label": "CroweLM Eclipse", "type": "reasoning",
     "provider": "ollama", "backend_name": "kimi-k2.6:cloud",
     "aliases": ["eclipse", "crowelm-eclipse", "kimi-26", "k26", "kimi-next"],
     "prompt": (
        "You are CroweLM Eclipse, Crowe Logic's flagship cloud reasoning tier. "
        "You produce the deepest technical analysis in the CroweLM family. "
        "Deliver precise, structured answers with visible reasoning. Use tools "
        "when they help. Be direct."
     )},
    {"name": "glm-4.6:cloud",                                "label": "CroweLM LocalMesh", "type": "reasoning", "provider": "ollama"},
]


def _selector_key(value: str) -> str:
    """Normalize model selectors so legacy and rebranded names resolve identically."""
    return "".join(ch.lower() for ch in value if ch.isalnum())


def model_selectors(model_cfg: dict) -> list[str]:
    """Return the selector strings that should resolve to a model config."""
    selectors = [model_cfg.get("name", ""), model_cfg.get("label", "")]
    selectors.extend(model_cfg.get("aliases", []))
    return [selector for selector in selectors if selector]


def provider_model_name(model_cfg: dict) -> str:
    """Return the upstream provider model identifier for a model config.

    Supports ``${ENV_VAR}`` interpolation in ``backend_name`` so models served
    by late-bound endpoints (e.g. NemoClaw, where the model name is discovered
    via scripts/nemoclaw_recon.sh on the VM) can be configured via environment
    variables instead of requiring the JSON entry to be edited.
    """
    raw = str(model_cfg.get("backend_name") or model_cfg.get("name") or "")
    if "${" in raw and "}" in raw:
        import re
        def _sub(match: "re.Match[str]") -> str:
            return os.environ.get(match.group(1), match.group(0))
        raw = re.sub(r"\$\{([A-Z0-9_]+)\}", _sub, raw)
    return raw


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
    elif provider == "openai_compat":
        normalized.setdefault("endpoint_env", "CROWE_OPEN_ENDPOINT")
        normalized.setdefault("api_key_env", "CROWE_OPEN_API_KEY")
    elif provider == "anthropic":
        normalized.setdefault("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT")
        normalized.setdefault("api_key_env", "AZURE_ANTHROPIC_API_KEY")
    elif provider == "watsonx":
        # IBM watsonx.ai foundation models (read from ~/.crowe-logic/ibm.env).
        normalized.setdefault("endpoint_env", "WATSONX_URL")
        normalized.setdefault("api_key_env", "WATSONX_APIKEY")
        normalized.setdefault("project_id_env", "WATSONX_PROJECT_ID")

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
            if extra.get("provider") in ("azure_openai", "anthropic", "openai_compat"):
                insert_at = next(
                    (
                        idx for idx, model in enumerate(merged)
                        if model.get("provider") not in ("azure_openai", "anthropic", "openai_compat")
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
AGENT_VERSION = "0.2.7"

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

## Arizona Public Records
- arizona_apartment_public_records_lookup — one-shot Arizona apartment ownership/management lookup across Assessor, Recorder, and ADRE
- maricopa_assessor_search_property — official Maricopa parcel search by address, APN, owner, or subdivision
- maricopa_assessor_get_parcel_details — official Maricopa parcel detail page parsing with owner, deed, sale, and mailing data
- maricopa_assessor_search_rental — official Maricopa rental-registration lookup by address
- maricopa_recorder_document_url — canonical Maricopa Recorder deed/document URL for a recording number
- adre_entity_license_search — Arizona Department of Real Estate company/entity license search
- adre_entity_license_details — ADRE entity-license detail lookup with phone, address, and designated broker

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
- crowe_chat — external mycology/cultivation Q&A service (domain-specific only; do NOT use for greetings or general chat)
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

## DeepParallel Local Reasoning
- deepparallel_query — run a prompt through DeepParallel's 8-chain parallel reasoning (local, private, zero-latency via Ollama)
- deepparallel_status — check if DeepParallel model is loaded and Ollama is running

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

## Execution discipline (strict)
These rules override every other instinct. Violate them and the turn is wasted.

1. Never narrate intent. Do not write "Let me…", "I'll now…", "Next, I'll…", "Starting with…", "I'm going to…". If you need a tool, call it in this same assistant message. If you don't need a tool, produce the final answer.
2. Never end an assistant message with a colon, ellipsis, or a sentence that describes what you are about to do. A message that ends with "Let me take a snapshot:" with no tool_call attached is a bug.
3. Tool calls and prose go together. If you emit tool_calls, any accompanying prose must describe what you *just did* or what the user should know — not what you're about to do next.
4. Do not enumerate your capabilities when the user asked you to do the work. If they asked you to edit the page, edit it — do not list what you could edit.
5. Multiple independent tool calls in one round are preferred. If you need a screenshot, a snapshot, and page content, emit all three tool_calls in the same message instead of three sequential rounds.
6. If a tool returned incomplete or empty data, call it again with different parameters or try a different tool — do not give up and ask the user.
7. After a write/click/navigate, verify. Take a screenshot or re-read state before reporting success.
8. When the user says "continue", "go", "do it", or "all of the above", treat it as permission to run autonomously until the goal is met or a tool fails. Do not stop and ask for more confirmation.

## How to work
1. Understand what's being asked — clarify if ambiguous
2. Plan your approach — break complex tasks into steps
3. Use core tools first; reach for MCP when you need specialized integrations
4. Verify your work — check outputs, run tests if applicable
5. Report results concisely

For Arizona apartment, eviction, ownership, management, broker, or deed research:
- Prefer the Arizona public-record tools before broad web_search/browse_url loops
- Use the bundled `arizona_apartment_public_records_lookup` first when the user gives an address
- Use Assessor and ADRE results as primary evidence, and use recorder URLs for deed follow-up
- If official-source tools still leave one key fact unresolved, ask for the exact missing company name, case number, or document rather than continuing broad searches

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


# ── Task-class router (CroweLM Auto) ───────────────────────────────────────
# Maps a classified task class to the CroweLM label that handles it best.
# Keys match labels exactly; resolve_model_config() does the lookup.
TASK_CLASS_ROUTES: dict[str, str] = {
    "agentic":   "CroweLM Maverick",   # llama-4-maverick: strong tool discipline
    "code":      "CroweLM Coder",      # qwen3-coder-480b: pure code specialist
    "creative":  "CroweLM Sovereign",  # mistral-large via watsonx: long-form writing
    "research":  "CroweLM Ultra",      # nemotron-ultra-253b: synthesis + web
    "domain_qa": "CroweLM Prime",      # granite-4 via watsonx: Q&A with nuance
    "chat":      "CroweLM Nexus",      # granite-3-8b via watsonx: fast, low-cost
    "default":   "CroweLM Titan",      # llama-3-3-70b via watsonx: flagship general
}

# Task-class fallback chain — if the primary route is unavailable (missing
# keys, blocked, etc.), try the next in order before dropping to default.
TASK_CLASS_FALLBACKS: dict[str, list[str]] = {
    "agentic":   ["CroweLM Ultra", "CroweLM Titan", "CroweLM Apex"],
    "code":      ["CroweLM Forge", "CroweLM Maverick", "CroweLM Titan"],
    "creative":  ["CroweLM Dense", "CroweLM Prime", "CroweLM Titan", "CroweLM Sovereign Premium"],
    "research":  ["CroweLM Vector", "CroweLM Maverick", "CroweLM Frontier", "CroweLM Titan"],
    "domain_qa": ["CroweLM Sovereign", "CroweLM Nexus", "CroweLM Titan"],
    "chat":      ["CroweLM Nano", "CroweLM Swift", "CroweLM Lite", "CroweLM Edge", "CroweLM Titan"],
    "default":   ["CroweLM Apex", "CroweLM Dense", "CroweLM Vector", "CroweLM Nexus"],
}

# Heuristic signal sets. Order matters: first class to score wins.
_AGENTIC_TOKENS = (
    " click", " screenshot", " snapshot", " navigate", " browse", " browser",
    " fill out", " scrape", " select_option", " drag ", " hover", " press_key",
    " evaluate_script", " run applescript", " run_applescript", " shell ",
    " execute_shell", " git ", " git_", " mcp_", " safari", " playwright",
    " upload ", " download ", " shopify", " squarespace", " stripe ",
    " automate", " automation", "http://", "https://", " open application",
    " do it", " go ahead", " all of the above",
)
_CODE_TOKENS = (
    " refactor", " implement", " bug", " fix the", " debug", " unit test",
    " write a test", " stack trace", " traceback", " compile", " build fails",
    " regex ", " regexp", " function ", " class ", " method ", " def ",
    " import ", " typescript", " javascript", " python", " rust ", " golang",
    " go ", " rewrite", " optimize ", " benchmark", " lint", " mypy", " pytest",
    " commit", " pull request", " merge conflict",
)
_CREATIVE_TOKENS = (
    " write a poem", " write a song", " write a story", " draft an email",
    " marketing copy", " tagline", " ad copy", " blog post", " essay",
    " press release", " social media post", " caption", " short story",
    " screenplay", " lyrics", " brainstorm names", " name ideas",
)
_DOMAIN_QA_TOKENS = (
    " mushroom", " mycel", " mycolog", " cultivation", " substrate",
    " contamination", " fruiting", " inoculat", " colonization", " strain ",
    " genetics", " agar ", " grain spawn", " liquid culture", " ph ",
    " terpene", " alkaloid", " reishi", " lion's mane", " cordyceps",
    " shiitake", " oyster ", " tincture", " extract ", " biosynthesis",
)
_RESEARCH_TOKENS = (
    " research", " summarize", " compare ", " who is ", " what is the latest",
    " recent developments", " literature", " overview of", " explain how",
    " state of the art", " survey", " analyze ", " analyse ",
)
_CHAT_PHRASES = (
    "hello", "hi", "hey", "howdy", "good morning", "good evening",
    "how are you", "what's up", "yo", "thanks", "thank you", "ok",
    "okay", "cool", "nice", "got it", "sup",
)


def _contains_chat_phrase(padded: str) -> bool:
    """Word-boundary check against the chat greeting set.

    Plain substring matching causes false positives like 'nice' matching
    inside 've[nice]' or 'hi' inside '[hi]storian'. We pad the corpus with
    spaces and require the phrase be space-delimited (or followed by common
    punctuation) on both sides.
    """
    for phrase in _CHAT_PHRASES:
        p = phrase.lower()
        # Match ``<space>phrase<space|.|,|!|?|>``
        for tail in (" ", ".", ",", "!", "?"):
            if f" {p}{tail}" in padded:
                return True
    return False


def classify_task(text: str) -> str:
    """Classify a user turn into a task class for CroweLM Auto routing.

    Returns one of: ``agentic``, ``code``, ``creative``, ``research``,
    ``domain_qa``, ``chat``, ``default``. Heuristic only — fast, deterministic,
    no extra model calls. Biased toward ``agentic`` when multiple classes tie
    because tool-calling tasks suffer the most from the wrong model.
    """
    if not text or not text.strip():
        return "default"

    # Pad with spaces so simple " token " matches work at string boundaries.
    padded = " " + text.lower().strip() + " "
    compact = padded.strip()

    scores = {
        "agentic":   sum(1 for t in _AGENTIC_TOKENS if t in padded),
        "code":      sum(1 for t in _CODE_TOKENS if t in padded),
        "creative":  sum(1 for t in _CREATIVE_TOKENS if t in padded),
        "domain_qa": sum(1 for t in _DOMAIN_QA_TOKENS if t in padded),
        "research":  sum(1 for t in _RESEARCH_TOKENS if t in padded),
    }

    # Code fences / file extensions are strong code signals.
    if "```" in text or any(ext in padded for ext in (".py ", ".js ", ".ts ", ".tsx ", ".rs ", ".go ", ".java ", ".rb ")):
        scores["code"] += 2

    # Short greeting-ish messages → chat tier, but only when no stronger signal
    # fired. A short "refactor this class" should still classify as code.
    if len(compact) <= 40 and _contains_chat_phrase(padded) and max(scores.values()) == 0:
        return "chat"

    top_class = max(scores, key=lambda k: scores[k])
    if scores[top_class] == 0:
        return "default"

    # Tie-break: agentic wins over everything (tool discipline matters most).
    if scores["agentic"] >= scores[top_class]:
        return "agentic"
    return top_class


def route_candidates_for_auto(text: str, availability_check=None) -> tuple[list[dict], str]:
    """Return the ordered concrete model candidates for CroweLM Auto.

    The first entry is the primary route for the classified task class; later
    entries are same-turn fallbacks. ``availability_check`` is applied to each
    candidate before it is returned.
    """
    task_class = classify_task(text)
    labels = [TASK_CLASS_ROUTES.get(task_class, TASK_CLASS_ROUTES["default"])]
    labels.extend(TASK_CLASS_FALLBACKS.get(task_class, []))
    labels.append(TASK_CLASS_ROUTES["default"])

    candidates: list[dict] = []
    seen: set[str] = set()
    for label in labels:
        if label in seen:
            continue
        seen.add(label)
        cfg = resolve_model_config(label)
        if not cfg:
            continue
        if cfg.get("provider") == "auto":
            continue
        if availability_check is None or availability_check(cfg):
            candidates.append(cfg)

    if candidates:
        return candidates, task_class

    # Last resort — return the first non-auto entry in the chain.
    for cfg in MODEL_CHAIN:
        if cfg.get("provider") != "auto":
            return [cfg], task_class

    raise RuntimeError("No non-auto models available in MODEL_CHAIN")


def route_for_auto(text: str, availability_check=None) -> tuple[dict, str]:
    """Resolve the concrete model_cfg that CroweLM Auto should use for this turn.

    Returns ``(model_cfg, class)`` where ``class`` is the classified task class.

    ``availability_check`` is an optional callable ``(model_cfg) -> bool``; when
    provided it is used to skip routes whose backing provider isn't configured,
    stepping through ``TASK_CLASS_FALLBACKS`` before giving up on ``default``.
    """
    candidates, task_class = route_candidates_for_auto(
        text,
        availability_check=availability_check,
    )
    return candidates[0], task_class
