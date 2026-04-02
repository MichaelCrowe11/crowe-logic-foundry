# Crowe Logic Foundry -- Full Tool Suite Upgrade

**Date:** 2026-04-01
**Scope:** 7 integration tasks across wiring, vision, data, and platform connectivity
**New files:** 4 (vision.py, crowelm.py, crowe_logic_ai.py, mcp server)
**Modified files:** 4 (tools/__init__.py, config/agent_config.py, quantum.yaml, cultivation.yaml)

---

## 1. Quick Wiring (Category A)

Mechanical changes connecting existing pieces. No new dependencies.

### 1a. Register `trinity_pipeline` in tool chain

**`tools/__init__.py`:**
- Add `trinity_pipeline` to quantum import line
- Add `trinity_pipeline` to `user_functions` set

**`config/agent_config.py` (SYSTEM_INSTRUCTIONS):**
- Change quantum line from:
  `run_quantum_circuit, synapse_evaluate, qubit_flow_execute`
  to:
  `run_quantum_circuit, synapse_evaluate, qubit_flow_execute, trinity_pipeline`
- Add description: `trinity_pipeline — full QubitFlow-to-Synapse experiment pipeline with hypothesis testing`

### 1b. Update quantum agent

**`agents/quantum.yaml`:**
- Add `trinity_pipeline` to tools list
- Update prompt_override to mention Trinity bridge and the unified pipeline

### 1c. Pipeline templates

No changes needed. The existing `refactor.yaml`, `compose.yaml`, and `research.yaml` templates reference tools that exist and work correctly.

### 1d. Deploy script

No changes needed. `scripts/create_agent.py` imports `user_functions` from `tools/__init__.py`, so new tools are automatically included when the agent is redeployed.

---

## 2. Vision Tool (`tools/vision.py`)

Multi-backend image analysis with automatic fallback.

### Architecture

```
analyze_image(image_path, prompt, backend="auto")
    |
    +-- "auto" --> try OpenRouter vision model
    |              --> fall back to Crowe Vision API
    |              --> fall back to error
    |
    +-- "openrouter" --> vision-capable model via OpenRouter Chat Completions
    |                    Model: google/gemini-2.0-flash-exp:free (free, fast)
    |                    Fallback: meta-llama/llama-4-scout:free
    |
    +-- "crowe" --> POST to ai.southwestmushrooms.com Crowe Vision endpoint
    |
    +-- "local" --> reserved for future (Ollama LLaVA, etc.)
```

### Functions

**`analyze_image(image_path: str, prompt: str = "Describe this image in detail", backend: str = "auto") -> str`**
- Read image from disk, base64-encode
- Detect MIME type from extension
- Send to selected backend as multimodal chat completion
- Return JSON: `{"backend": str, "analysis": str}`
- On error: `{"error": str, "backend": str}`

**`screenshot_and_analyze(url: str, prompt: str = "Describe what you see on this page") -> str`**
- Call `browser_navigate(url)` + `browser_screenshot()`
- Pipe screenshot path into `analyze_image()`
- Return combined JSON: `{"url": str, "screenshot_path": str, "analysis": str}`

### OpenRouter Vision Implementation

Uses existing `OPENROUTER_API_KEY` from env. No new API keys needed.

```python
# Vision model selection (free tier first, paid fallback)
VISION_MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-4-scout:free",
    "openai/gpt-4o-mini",
]
```

Request format:
```python
{
    "model": model,
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        ]
    }]
}
```

### Crowe Vision Implementation

POST to `{CROWE_LOGIC_AI_URL}/api/vision` with multipart form:
- `image`: base64 or file upload
- `prompt`: analysis prompt
- Auth: `Authorization: Bearer {CROWE_LOGIC_AI_KEY}`

### Env Vars

- `OPENROUTER_API_KEY` -- already configured, reused
- `CROWE_LOGIC_AI_URL` -- default `https://ai.southwestmushrooms.com` (shared with Section 4)
- `CROWE_LOGIC_AI_KEY` -- API key for Crowe Logic AI platform (shared with Section 4)

---

## 3. CroweLM Data Tools (`tools/crowelm.py`)

Full dataset management: query, curate, and trigger training runs.

### Data Layout

```
data/crowelm-unified/
    DATASET_MANIFEST.json        -- master dataset catalog
    UNIFIED_MANIFEST.json        -- merged training manifest
    unified_training/
        UNIFIED_MANIFEST.json    -- training-ready data
    crowe_integrated/
        nvidia_biotech_stats.json
        nvidia_stats.json
    curated/                     -- NEW: agent-curated examples (JSONL)
        general.jsonl
        mycology.jsonl
        quantum.jsonl
        ...
    nemo_training/
        training_config.yaml     -- NeMo training config
        train.sh
    runpod_crowelm_unified_config.yaml
```

### Functions -- Query Tier

**`crowelm_list_datasets() -> str`**
- Read DATASET_MANIFEST.json and UNIFIED_MANIFEST.json
- Return JSON: list of datasets with names, sizes, categories

**`crowelm_dataset_stats(dataset_name: str = "all") -> str`**
- Parse manifest for row counts, token estimates, categories, timestamps
- If "all", return aggregate stats across all datasets
- Return JSON with stats

**`crowelm_search_examples(query: str, dataset: str = "all", limit: int = 10) -> str`**
- Search JSONL training files for examples matching query (substring match on instruction + response)
- Return JSON: list of matching examples with IDs

**`crowelm_inspect_config() -> str`**
- Read `nemo_training/training_config.yaml` and `runpod_crowelm_unified_config.yaml`
- Return JSON with parsed training parameters (model, epochs, batch size, learning rate, etc.)

### Functions -- Curation Tier

**`crowelm_add_example(instruction: str, response: str, category: str = "general") -> str`**
- Append JSONL line to `curated/{category}.jsonl`
- Auto-generate UUID for example_id
- Return JSON: `{"added": true, "example_id": str, "category": str, "file": str}`

**`crowelm_remove_example(example_id: str) -> str`**
- Search curated/ JSONL files for matching ID
- Remove the line (rewrite file without it)
- Return JSON: `{"removed": true, "example_id": str}`

**`crowelm_export_curated(format: str = "jsonl") -> str`**
- Merge all curated/*.jsonl into a single export file
- Support formats: jsonl (default), nemo (NeMo-compatible), openai (chat format)
- Return JSON: `{"exported": str, "count": int, "format": str}`

### Functions -- Pipeline Tier

**`crowelm_prepare_training(config_overrides: str = "{}") -> str`**
- Validate curated data (check for duplicates, empty fields, format issues)
- Merge with existing training data
- Generate updated NeMo config with any overrides
- Return JSON: `{"ready": bool, "total_examples": int, "config_path": str, "issues": list}`

**`crowelm_upload_dataset(target: str = "runpod") -> str`**
- Calls existing `cloud_storage_manager.py` or `upload_and_train_runpod.py`
- Targets: "runpod", "azure"
- Return JSON: `{"uploaded": bool, "target": str, "size_mb": float}`

**`crowelm_training_status() -> str`**
- Check active training runs (RunPod API or local process)
- Return JSON: `{"running": bool, "progress": str, "eta": str}` or `{"running": false}`

---

## 4. Crowe Logic AI Integration

### 4a. HTTP Client Tool (`tools/crowe_logic_ai.py`)

Direct API client for the Crowe Logic AI platform at ai.southwestmushrooms.com.

**`crowe_ai_chat(message: str, context: str = "") -> str`**
- POST `{CROWE_LOGIC_AI_URL}/api/chat`
- Body: `{"message": message, "context": context}`
- Returns CroweLM response as JSON

**`crowe_ai_vision(image_path: str, prompt: str = "") -> str`**
- POST `{CROWE_LOGIC_AI_URL}/api/vision`
- Multipart: image file + prompt
- Returns analysis JSON (this is also used by vision.py's "crowe" backend)

**`crowe_ai_grow_log(action: str, data: str = "{}") -> str`**
- action: "create", "read", "update", "list"
- POST/GET `{CROWE_LOGIC_AI_URL}/api/grow-logs`
- Returns grow log data as JSON

**`crowe_ai_generate_sop(topic: str, parameters: str = "{}") -> str`**
- POST `{CROWE_LOGIC_AI_URL}/api/sop`
- Body: `{"topic": topic, ...parsed parameters}`
- Returns generated SOP document as JSON

**Shared auth pattern:**
```python
def _crowe_ai_request(method, path, **kwargs):
    url = os.environ.get("CROWE_LOGIC_AI_URL", "https://ai.southwestmushrooms.com")
    key = os.environ.get("CROWE_LOGIC_AI_KEY", "")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    response = httpx.request(method, f"{url}{path}", headers=headers, **kwargs)
    return response.json()
```

### 4b. MCP Server (`scripts/mcp_crowe_logic_ai.py`)

Standalone MCP server that exposes Crowe Logic AI to any MCP client.

**Tools exposed:**
- `crowe_chat` -- CroweLM conversation
- `crowe_vision` -- Photo analysis
- `crowe_grow_log` -- Grow log CRUD
- `crowe_sop` -- SOP generation

**Implementation:** Uses `mcp` Python SDK (FastMCP pattern):
```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("crowe-logic-ai")

@mcp.tool()
def crowe_chat(message: str, context: str = "") -> str:
    """Chat with CroweLM for mycology and cultivation expertise."""
    ...
```

### 4c. MCP Server Publishing

**PyPI (primary):**
- Package name: `crowe-logic-ai-mcp`
- Entry point: `crowe-logic-ai-mcp = scripts.mcp_crowe_logic_ai:main`
- Install + run: `uvx crowe-logic-ai-mcp`
- Requires: `mcp>=1.0.0`, `httpx>=0.28.0`

**Claude Code config (example):**
```json
{
  "mcpServers": {
    "crowe-logic-ai": {
      "command": "uvx",
      "args": ["crowe-logic-ai-mcp"],
      "env": {
        "CROWE_LOGIC_AI_URL": "https://ai.southwestmushrooms.com",
        "CROWE_LOGIC_AI_KEY": "your-key"
      }
    }
  }
}
```

**npm (secondary, for JS ecosystem clients):**
- Thin Node.js wrapper that spawns the Python MCP server
- Package name: `@crowelogic/mcp-crowe-ai`
- Or skip npm entirely and just publish PyPI -- `uvx` works in all major MCP clients

**Registry listing:**
- Submit to mcp.run / Smithery.ai catalog
- Category: "Science & Research", "Agriculture"
- Tags: mycology, cultivation, mushroom, agriculture, ai

---

## 5. Updated Agent Config

### SYSTEM_INSTRUCTIONS additions

Add to `config/agent_config.py` SYSTEM_INSTRUCTIONS:

```
## Quantum Computing (updated)
- run_quantum_circuit — execute circuits via Qiskit, Cirq, PennyLane, Crowe Trinity
- synapse_evaluate — uncertainty propagation and symbolic math
- qubit_flow_execute — QubitFlow DSL execution via Trinity bridge
- trinity_pipeline — full experiment pipeline: execute + sample + analyze + hypothesis test

## Vision & Image Analysis (NEW)
- analyze_image — multi-backend image analysis (OpenRouter vision, Crowe Vision, local)
- screenshot_and_analyze — capture a webpage and analyze it visually

## CroweLM Training Data (NEW)
- crowelm_list_datasets, crowelm_dataset_stats, crowelm_search_examples — query training data
- crowelm_inspect_config — view current training configuration
- crowelm_add_example, crowelm_remove_example, crowelm_export_curated — curate training data
- crowelm_prepare_training, crowelm_upload_dataset, crowelm_training_status — training pipeline

## Crowe Logic AI Platform (NEW)
- crowe_ai_chat — CroweLM conversation (mycology expertise)
- crowe_ai_vision — photo analysis via Crowe Vision
- crowe_ai_grow_log — create/read/update grow logs
- crowe_ai_generate_sop — generate cultivation SOPs
```

### Updated agent YAMLs

**`agents/quantum.yaml`** -- add `trinity_pipeline`

**`agents/cultivation.yaml`** -- add `crowe_ai_chat`, `crowe_ai_vision`, `crowe_ai_grow_log`, `crowe_ai_generate_sop`, `analyze_image`. Update prompt to reference the Crowe Logic AI platform.

---

## 6. New Dependencies

**No new PyPI dependencies for the foundry itself.** All tools use `httpx` (already installed) and stdlib.

**For the MCP server package (separate):**
- `mcp>=1.0.0`
- `httpx>=0.28.0`

---

## 7. Env Vars Summary

Existing (no changes):
- `OPENROUTER_API_KEY` -- reused for vision

New:
- `CROWE_LOGIC_AI_URL` -- default `https://ai.southwestmushrooms.com`
- `CROWE_LOGIC_AI_KEY` -- API key for platform auth

---

## 8. File Inventory

### New files (4)
| File | Purpose | Functions |
|------|---------|-----------|
| `tools/vision.py` | Multi-backend image analysis | 2 functions |
| `tools/crowelm.py` | Training data query/curate/pipeline | 10 functions |
| `tools/crowe_logic_ai.py` | Crowe Logic AI HTTP client | 4 functions |
| `scripts/mcp_crowe_logic_ai.py` | MCP server for ecosystem | 4 MCP tools |

### Modified files (4)
| File | Changes |
|------|---------|
| `tools/__init__.py` | Import + register all new functions in `user_functions` |
| `config/agent_config.py` | Add new tool sections to SYSTEM_INSTRUCTIONS |
| `agents/quantum.yaml` | Add `trinity_pipeline` |
| `agents/cultivation.yaml` | Add Crowe Logic AI tools |

### Total tool count after upgrade
- Current: 33 functions
- Adding: 17 functions (2 vision + 10 crowelm + 4 crowe_ai + 1 trinity_pipeline)
- New total: **50 tool functions** + 4 MCP server tools
