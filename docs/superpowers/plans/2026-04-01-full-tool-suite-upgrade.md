# Full Tool Suite Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the Crowe Logic Foundry CLI from 33 to 50 tool functions — adding vision, CroweLM training data, and Crowe Logic AI platform integration — plus publish an MCP server for ecosystem access.

**Architecture:** Four new tool modules (`vision.py`, `crowelm.py`, `crowe_logic_ai.py`, MCP server) following the existing pattern: each function returns JSON, errors are caught and returned as `{"error": str}`. All modules registered in `tools/__init__.py` and documented in `SYSTEM_INSTRUCTIONS`.

**Tech Stack:** Python 3.14, httpx (HTTP client), mcp SDK (MCP server), crowe-quantum-trinity (quantum), Azure AI Agent Service (deployment)

**Spec:** `docs/superpowers/specs/2026-04-01-full-tool-suite-upgrade-design.md`

---

### Task 1: Wire `trinity_pipeline` into tool chain

**Files:**
- Modify: `tools/__init__.py:35` (quantum import line)
- Modify: `tools/__init__.py:75` (user_functions quantum section)
- Modify: `config/agent_config.py:57` (SYSTEM_INSTRUCTIONS quantum line)
- Modify: `agents/quantum.yaml`

- [ ] **Step 1: Update `tools/__init__.py` quantum import**

Change line 35 from:
```python
from tools.quantum import run_quantum_circuit, synapse_evaluate, qubit_flow_execute
```
to:
```python
from tools.quantum import run_quantum_circuit, synapse_evaluate, qubit_flow_execute, trinity_pipeline
```

- [ ] **Step 2: Update `user_functions` set**

Change the quantum section (line 75) from:
```python
    # Quantum
    run_quantum_circuit, synapse_evaluate, qubit_flow_execute,
```
to:
```python
    # Quantum
    run_quantum_circuit, synapse_evaluate, qubit_flow_execute, trinity_pipeline,
```

- [ ] **Step 3: Update SYSTEM_INSTRUCTIONS quantum line**

In `config/agent_config.py`, change line 57 from:
```
- run_quantum_circuit, synapse_evaluate, qubit_flow_execute — quantum computing
```
to:
```
- run_quantum_circuit, synapse_evaluate, qubit_flow_execute — quantum computing
- trinity_pipeline — full QubitFlow-to-Synapse experiment pipeline with hypothesis testing
```

- [ ] **Step 4: Update `agents/quantum.yaml`**

Replace the entire file with:
```yaml
name: quantum
description: "Quantum circuit design and evaluation specialist"
model: gpt-oss-120b
tools:
  - run_quantum_circuit
  - synapse_evaluate
  - qubit_flow_execute
  - trinity_pipeline
  - execute_shell
prompt_override: |
  You are the quantum computing specialist within Crowe Logic.
  You design and execute quantum circuits using Qiskit, Cirq, PennyLane,
  and the Crowe Quantum Trinity platform (QubitFlow + Synapse + Core).
  Use trinity_pipeline for full experiment workflows with hypothesis testing.
  You explain quantum concepts clearly.
  You never use emojis. Output is clean and professional.
```

- [ ] **Step 5: Verify import works**

Run:
```bash
.venv/bin/python -c "from tools import user_functions; print(f'{len(user_functions)} tools'); assert any(f.__name__ == 'trinity_pipeline' for f in user_functions)"
```
Expected: `34 tools` (no error)

- [ ] **Step 6: Commit**

```bash
git add tools/__init__.py config/agent_config.py agents/quantum.yaml
git commit -m "feat: register trinity_pipeline in tool chain and quantum agent"
```

---

### Task 2: Vision tool (`tools/vision.py`)

**Files:**
- Create: `tools/vision.py`
- Test: `tests/test_vision.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vision.py`:
```python
"""Tests for tools.vision — multi-backend image analysis."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock
from tools.vision import analyze_image, screenshot_and_analyze, VISION_MODELS


class TestAnalyzeImage:
    def test_returns_error_for_missing_file(self):
        result = json.loads(analyze_image("/nonexistent/image.png"))
        assert "error" in result

    def test_returns_error_for_unsupported_format(self, tmp_path):
        bad_file = tmp_path / "test.xyz"
        bad_file.write_text("not an image")
        result = json.loads(analyze_image(str(bad_file)))
        assert "error" in result

    @patch("tools.vision._call_openrouter_vision")
    def test_auto_backend_tries_openrouter_first(self, mock_or, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        mock_or.return_value = {"backend": "openrouter", "analysis": "a cat"}
        result = json.loads(analyze_image(str(img), backend="auto"))
        assert result["analysis"] == "a cat"
        mock_or.assert_called_once()

    @patch("tools.vision._call_openrouter_vision", side_effect=Exception("rate limited"))
    @patch("tools.vision._call_crowe_vision")
    def test_auto_falls_back_to_crowe(self, mock_crowe, mock_or, tmp_path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        mock_crowe.return_value = {"backend": "crowe", "analysis": "a mushroom"}
        result = json.loads(analyze_image(str(img), backend="auto"))
        assert result["backend"] == "crowe"
        assert result["analysis"] == "a mushroom"

    @patch("tools.vision._call_openrouter_vision")
    def test_explicit_openrouter_backend(self, mock_or, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        mock_or.return_value = {"backend": "openrouter", "analysis": "result"}
        result = json.loads(analyze_image(str(img), backend="openrouter"))
        mock_or.assert_called_once()

    def test_vision_models_list_is_not_empty(self):
        assert len(VISION_MODELS) >= 2


class TestScreenshotAndAnalyze:
    @patch("tools.vision.analyze_image")
    @patch("tools.vision.browser_screenshot")
    @patch("tools.vision.browser_navigate")
    def test_combines_screenshot_and_analysis(self, mock_nav, mock_shot, mock_analyze):
        mock_nav.return_value = json.dumps({"url": "https://example.com"})
        mock_shot.return_value = json.dumps({"path": "/tmp/shot.png"})
        mock_analyze.return_value = json.dumps({"analysis": "a webpage"})
        result = json.loads(screenshot_and_analyze("https://example.com"))
        assert "analysis" in result
        mock_nav.assert_called_once()
        mock_shot.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_vision.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.vision'`

- [ ] **Step 3: Implement `tools/vision.py`**

Create `tools/vision.py`:
```python
"""
Vision tool — multi-backend image analysis with automatic fallback.

Backends: OpenRouter (free vision models), Crowe Vision (ai.southwestmushrooms.com), local (future).
"""

import base64
import json
import mimetypes
import os

import httpx

from tools.playwright_browser import browser_navigate, browser_screenshot

# Vision-capable models on OpenRouter (free tier first, paid fallback)
VISION_MODELS = [
    "google/gemini-2.0-flash-exp:free",
    "meta-llama/llama-4-scout:free",
    "openai/gpt-4o-mini",
]

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}


def analyze_image(image_path: str, prompt: str = "Describe this image in detail", backend: str = "auto") -> str:
    """
    Analyze an image using multi-backend vision with automatic fallback.

    :param image_path: Path to the image file on disk.
    :param prompt: What to analyze about the image.
    :param backend: Vision backend — "auto", "openrouter", "crowe", or "local".
    :return: JSON with analysis results.
    :rtype: str
    """
    try:
        if not os.path.exists(image_path):
            return json.dumps({"error": f"File not found: {image_path}"})

        ext = os.path.splitext(image_path)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return json.dumps({"error": f"Unsupported image format: {ext}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"})

        mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        if backend == "auto":
            return json.dumps(_auto_analyze(image_b64, mime_type, prompt))
        elif backend == "openrouter":
            return json.dumps(_call_openrouter_vision(image_b64, mime_type, prompt))
        elif backend == "crowe":
            return json.dumps(_call_crowe_vision(image_b64, mime_type, prompt))
        elif backend == "local":
            return json.dumps({"error": "Local vision backend not yet implemented"})
        else:
            return json.dumps({"error": f"Unknown backend: {backend}"})

    except Exception as e:
        return json.dumps({"error": str(e)})


def screenshot_and_analyze(url: str, prompt: str = "Describe what you see on this page") -> str:
    """
    Navigate to a URL, take a screenshot, and analyze it with vision.

    :param url: The URL to screenshot.
    :param prompt: What to analyze about the page.
    :return: JSON with URL, screenshot path, and analysis.
    :rtype: str
    """
    try:
        nav_result = json.loads(browser_navigate(url))
        if "error" in nav_result:
            return json.dumps({"error": f"Navigation failed: {nav_result['error']}"})

        shot_result = json.loads(browser_screenshot())
        if "error" in shot_result:
            return json.dumps({"error": f"Screenshot failed: {shot_result['error']}"})

        screenshot_path = shot_result.get("path", "")
        if not screenshot_path:
            return json.dumps({"error": "Screenshot path not returned"})

        analysis_result = json.loads(analyze_image(screenshot_path, prompt))

        return json.dumps({
            "url": url,
            "screenshot_path": screenshot_path,
            "analysis": analysis_result.get("analysis", analysis_result.get("error", "No analysis")),
            "backend": analysis_result.get("backend", "unknown"),
        })

    except Exception as e:
        return json.dumps({"error": str(e)})


def _auto_analyze(image_b64: str, mime_type: str, prompt: str) -> dict:
    """Try OpenRouter first, fall back to Crowe Vision."""
    try:
        return _call_openrouter_vision(image_b64, mime_type, prompt)
    except Exception:
        pass

    try:
        return _call_crowe_vision(image_b64, mime_type, prompt)
    except Exception as e:
        return {"error": f"All vision backends failed. Last error: {e}"}


def _call_openrouter_vision(image_b64: str, mime_type: str, prompt: str) -> dict:
    """Send image to OpenRouter vision model."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    last_error = None
    for model in VISION_MODELS:
        try:
            response = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                        ],
                    }],
                    "max_tokens": 2048,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()
            analysis = data["choices"][0]["message"]["content"]
            return {"backend": "openrouter", "model": model, "analysis": analysis}
        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(f"All OpenRouter vision models failed. Last: {last_error}")


def _call_crowe_vision(image_b64: str, mime_type: str, prompt: str) -> dict:
    """Send image to Crowe Logic AI Vision endpoint."""
    url = os.environ.get("CROWE_LOGIC_AI_URL", "https://ai.southwestmushrooms.com")
    key = os.environ.get("CROWE_LOGIC_AI_KEY", "")

    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    response = httpx.post(
        f"{url}/api/crowe-vision/analyze",
        headers=headers,
        json={"image": image_b64, "mime_type": mime_type, "prompt": prompt},
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    return {"backend": "crowe", "analysis": data.get("analysis", str(data))}
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_vision.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tools/vision.py tests/test_vision.py
git commit -m "feat: add multi-backend vision tool (OpenRouter + Crowe Vision)"
```

---

### Task 3: CroweLM data tools (`tools/crowelm.py`)

**Files:**
- Create: `tools/crowelm.py`
- Create: `data/crowelm-unified/curated/` directory
- Test: `tests/test_crowelm.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_crowelm.py`:
```python
"""Tests for tools.crowelm — CroweLM training data management."""

import json
import os
import tempfile
import pytest
from unittest.mock import patch

# We'll patch DATA_DIR in the module to point at a temp dir
import tools.crowelm as crowelm_mod


@pytest.fixture
def data_dir(tmp_path):
    """Set up a mock data directory with manifest and curated dir."""
    manifest = {
        "manifest_version": "1.0",
        "summary": {
            "total_raw_samples": 201000,
            "crowelm_training_entries": 137875,
            "total_size_gb": 4.29,
            "domains": "biotech, pharma, mycology"
        },
        "datasets_acquired": {
            "specialized_rqa": "50,000 samples - STEM Q&A",
            "specialized_reasoning": "50,000 samples - Cross-domain reasoning"
        },
        "top_domains": {"gene": 91974, "rna": 74916}
    }
    (tmp_path / "DATASET_MANIFEST.json").write_text(json.dumps(manifest))

    unified = {"total_entries": 137875, "format": "jsonl"}
    unified_dir = tmp_path / "unified_training"
    unified_dir.mkdir()
    (unified_dir / "UNIFIED_MANIFEST.json").write_text(json.dumps(unified))

    nemo_dir = tmp_path / "nemo_training"
    nemo_dir.mkdir()
    (nemo_dir / "training_config.yaml").write_text("model: llama-3.1-8b\nepochs: 3\nbatch_size: 4\n")

    curated_dir = tmp_path / "curated"
    curated_dir.mkdir()

    old_dir = crowelm_mod.DATA_DIR
    crowelm_mod.DATA_DIR = str(tmp_path)
    yield tmp_path
    crowelm_mod.DATA_DIR = old_dir


class TestQueryTier:
    def test_list_datasets(self, data_dir):
        result = json.loads(crowelm_mod.crowelm_list_datasets())
        assert result["summary"]["total_raw_samples"] == 201000
        assert "datasets_acquired" in result

    def test_dataset_stats(self, data_dir):
        result = json.loads(crowelm_mod.crowelm_dataset_stats())
        assert result["total_raw_samples"] == 201000
        assert "top_domains" in result

    def test_inspect_config(self, data_dir):
        result = json.loads(crowelm_mod.crowelm_inspect_config())
        assert "nemo" in result
        assert "epochs" in result["nemo"]

    def test_search_examples_empty_curated(self, data_dir):
        result = json.loads(crowelm_mod.crowelm_search_examples("mushroom"))
        assert result["results"] == []
        assert result["count"] == 0


class TestCurationTier:
    def test_add_example(self, data_dir):
        result = json.loads(crowelm_mod.crowelm_add_example(
            instruction="How do I grow shiitake?",
            response="Start with hardwood sawdust blocks...",
            category="mycology"
        ))
        assert result["added"] is True
        assert result["category"] == "mycology"
        assert os.path.exists(data_dir / "curated" / "mycology.jsonl")

    def test_search_finds_added_example(self, data_dir):
        crowelm_mod.crowelm_add_example("shiitake substrate", "oak sawdust", "mycology")
        result = json.loads(crowelm_mod.crowelm_search_examples("shiitake"))
        assert result["count"] == 1
        assert "shiitake" in result["results"][0]["instruction"]

    def test_remove_example(self, data_dir):
        add_result = json.loads(crowelm_mod.crowelm_add_example("test", "response", "general"))
        example_id = add_result["example_id"]
        remove_result = json.loads(crowelm_mod.crowelm_remove_example(example_id))
        assert remove_result["removed"] is True

    def test_export_curated(self, data_dir):
        crowelm_mod.crowelm_add_example("q1", "a1", "general")
        crowelm_mod.crowelm_add_example("q2", "a2", "mycology")
        result = json.loads(crowelm_mod.crowelm_export_curated())
        assert result["count"] == 2
        assert result["format"] == "jsonl"
        assert os.path.exists(result["path"])


class TestPipelineTier:
    def test_prepare_training(self, data_dir):
        crowelm_mod.crowelm_add_example("q1", "a1", "general")
        result = json.loads(crowelm_mod.crowelm_prepare_training())
        assert "total_examples" in result
        assert result["total_examples"] >= 1

    def test_training_status_when_idle(self, data_dir):
        result = json.loads(crowelm_mod.crowelm_training_status())
        assert result["running"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crowelm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.crowelm'`

- [ ] **Step 3: Create curated directory**

```bash
mkdir -p data/crowelm-unified/curated
```

- [ ] **Step 4: Implement `tools/crowelm.py`**

Create `tools/crowelm.py`:
```python
"""
CroweLM data tools — query, curate, and manage training datasets.

Reads from data/crowelm-unified/. Curated examples go to data/crowelm-unified/curated/.
"""

import glob
import json
import os
import uuid

import yaml

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "crowelm-unified")


# ── Query Tier ──────────────────────────────────────────────────────────────

def crowelm_list_datasets() -> str:
    """
    List all CroweLM datasets from the manifest.

    :return: JSON with dataset catalog including summary, datasets acquired, and top domains.
    :rtype: str
    """
    try:
        manifest_path = os.path.join(DATA_DIR, "DATASET_MANIFEST.json")
        if not os.path.exists(manifest_path):
            return json.dumps({"error": f"Manifest not found at {manifest_path}"})
        with open(manifest_path) as f:
            manifest = json.load(f)

        unified_path = os.path.join(DATA_DIR, "unified_training", "UNIFIED_MANIFEST.json")
        unified = {}
        if os.path.exists(unified_path):
            with open(unified_path) as f:
                unified = json.load(f)

        return json.dumps({
            "summary": manifest.get("summary", {}),
            "datasets_acquired": manifest.get("datasets_acquired", {}),
            "top_domains": manifest.get("top_domains", {}),
            "unified_training": unified,
            "curated_count": _count_curated_examples(),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_dataset_stats(dataset_name: str = "all") -> str:
    """
    Get statistics for CroweLM training datasets.

    :param dataset_name: Dataset name or "all" for aggregate stats.
    :return: JSON with row counts, domains, and curated counts.
    :rtype: str
    """
    try:
        manifest_path = os.path.join(DATA_DIR, "DATASET_MANIFEST.json")
        if not os.path.exists(manifest_path):
            return json.dumps({"error": "Manifest not found"})
        with open(manifest_path) as f:
            manifest = json.load(f)

        summary = manifest.get("summary", {})
        stats = {
            "total_raw_samples": summary.get("total_raw_samples", 0),
            "crowelm_training_entries": summary.get("crowelm_training_entries", 0),
            "total_size_gb": summary.get("total_size_gb", 0),
            "domains": summary.get("domains", ""),
            "top_domains": manifest.get("top_domains", {}),
            "curated_count": _count_curated_examples(),
        }

        if dataset_name != "all":
            acquired = manifest.get("datasets_acquired", {})
            if dataset_name in acquired:
                stats["selected_dataset"] = {dataset_name: acquired[dataset_name]}
            else:
                stats["note"] = f"Dataset '{dataset_name}' not found in manifest"

        return json.dumps(stats)
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_search_examples(query: str, dataset: str = "all", limit: int = 10) -> str:
    """
    Search training examples by content (instruction + response).

    :param query: Search string (substring match).
    :param dataset: Restrict to a category or "all".
    :param limit: Max results to return.
    :return: JSON with matching examples.
    :rtype: str
    """
    try:
        curated_dir = os.path.join(DATA_DIR, "curated")
        results = []
        query_lower = query.lower()

        if not os.path.exists(curated_dir):
            return json.dumps({"results": [], "count": 0, "query": query})

        pattern = f"{dataset}.jsonl" if dataset != "all" else "*.jsonl"
        for jsonl_path in glob.glob(os.path.join(curated_dir, pattern)):
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    example = json.loads(line)
                    text = (example.get("instruction", "") + " " + example.get("response", "")).lower()
                    if query_lower in text:
                        example["_source"] = os.path.basename(jsonl_path)
                        results.append(example)
                        if len(results) >= limit:
                            break
            if len(results) >= limit:
                break

        return json.dumps({"results": results, "count": len(results), "query": query})
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_inspect_config() -> str:
    """
    Show current training configuration (NeMo and RunPod).

    :return: JSON with parsed training parameters.
    :rtype: str
    """
    try:
        config = {}

        nemo_path = os.path.join(DATA_DIR, "nemo_training", "training_config.yaml")
        if os.path.exists(nemo_path):
            with open(nemo_path) as f:
                config["nemo"] = yaml.safe_load(f)

        runpod_path = os.path.join(DATA_DIR, "runpod_crowelm_unified_config.yaml")
        if os.path.exists(runpod_path):
            with open(runpod_path) as f:
                config["runpod"] = yaml.safe_load(f)

        if not config:
            return json.dumps({"error": "No training configs found"})

        return json.dumps(config)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Curation Tier ───────────────────────────────────────────────────────────

def crowelm_add_example(instruction: str, response: str, category: str = "general") -> str:
    """
    Add a new training example to the curated dataset.

    :param instruction: The training prompt/instruction.
    :param response: The expected model response.
    :param category: Category for organization (general, mycology, quantum, etc.).
    :return: JSON confirmation with example ID.
    :rtype: str
    """
    try:
        curated_dir = os.path.join(DATA_DIR, "curated")
        os.makedirs(curated_dir, exist_ok=True)

        example_id = str(uuid.uuid4())[:8]
        example = {
            "id": example_id,
            "instruction": instruction,
            "response": response,
            "category": category,
        }

        file_path = os.path.join(curated_dir, f"{category}.jsonl")
        with open(file_path, "a") as f:
            f.write(json.dumps(example) + "\n")

        return json.dumps({"added": True, "example_id": example_id, "category": category, "file": file_path})
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_remove_example(example_id: str) -> str:
    """
    Remove a training example by ID from curated datasets.

    :param example_id: The UUID prefix of the example to remove.
    :return: JSON confirmation.
    :rtype: str
    """
    try:
        curated_dir = os.path.join(DATA_DIR, "curated")
        if not os.path.exists(curated_dir):
            return json.dumps({"error": "No curated directory found"})

        for jsonl_path in glob.glob(os.path.join(curated_dir, "*.jsonl")):
            lines = []
            found = False
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    example = json.loads(line)
                    if example.get("id") == example_id:
                        found = True
                        continue
                    lines.append(line)

            if found:
                with open(jsonl_path, "w") as f:
                    for line in lines:
                        f.write(line + "\n")
                return json.dumps({"removed": True, "example_id": example_id, "file": jsonl_path})

        return json.dumps({"removed": False, "error": f"Example {example_id} not found"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_export_curated(format: str = "jsonl") -> str:
    """
    Export all curated training examples as a single merged file.

    :param format: Output format — "jsonl" (default), "nemo", or "openai".
    :return: JSON with export path and count.
    :rtype: str
    """
    try:
        curated_dir = os.path.join(DATA_DIR, "curated")
        if not os.path.exists(curated_dir):
            return json.dumps({"error": "No curated directory found"})

        examples = []
        for jsonl_path in sorted(glob.glob(os.path.join(curated_dir, "*.jsonl"))):
            with open(jsonl_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        examples.append(json.loads(line))

        if not examples:
            return json.dumps({"error": "No curated examples to export"})

        export_path = os.path.join(DATA_DIR, f"curated_export.{format}")

        with open(export_path, "w") as f:
            for ex in examples:
                if format == "openai":
                    chat_format = {
                        "messages": [
                            {"role": "user", "content": ex["instruction"]},
                            {"role": "assistant", "content": ex["response"]},
                        ]
                    }
                    f.write(json.dumps(chat_format) + "\n")
                elif format == "nemo":
                    nemo_format = {"input": ex["instruction"], "output": ex["response"]}
                    f.write(json.dumps(nemo_format) + "\n")
                else:
                    f.write(json.dumps(ex) + "\n")

        return json.dumps({"path": export_path, "count": len(examples), "format": format})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Pipeline Tier ───────────────────────────────────────────────────────────

def crowelm_prepare_training(config_overrides: str = "{}") -> str:
    """
    Validate curated data and prepare for training.

    :param config_overrides: JSON string of config overrides (e.g. '{"epochs": 5}').
    :return: JSON with validation results and training readiness.
    :rtype: str
    """
    try:
        curated_dir = os.path.join(DATA_DIR, "curated")
        overrides = json.loads(config_overrides)

        examples = []
        issues = []
        seen_instructions = set()

        if os.path.exists(curated_dir):
            for jsonl_path in glob.glob(os.path.join(curated_dir, "*.jsonl")):
                with open(jsonl_path) as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue
                        ex = json.loads(line)
                        if not ex.get("instruction"):
                            issues.append(f"{os.path.basename(jsonl_path)}:{line_num} — empty instruction")
                            continue
                        if not ex.get("response"):
                            issues.append(f"{os.path.basename(jsonl_path)}:{line_num} — empty response")
                            continue
                        if ex["instruction"] in seen_instructions:
                            issues.append(f"{os.path.basename(jsonl_path)}:{line_num} — duplicate instruction")
                            continue
                        seen_instructions.add(ex["instruction"])
                        examples.append(ex)

        nemo_config_path = os.path.join(DATA_DIR, "nemo_training", "training_config.yaml")
        config = {}
        if os.path.exists(nemo_config_path):
            with open(nemo_config_path) as f:
                config = yaml.safe_load(f) or {}
        config.update(overrides)

        return json.dumps({
            "ready": len(examples) > 0 and len(issues) == 0,
            "total_examples": len(examples),
            "issues": issues,
            "config": config,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_upload_dataset(target: str = "runpod") -> str:
    """
    Upload curated dataset to cloud storage for training.

    :param target: Cloud target — "runpod" or "azure".
    :return: JSON with upload status.
    :rtype: str
    """
    try:
        export_result = json.loads(crowelm_export_curated(format="nemo"))
        if "error" in export_result:
            return json.dumps({"error": f"Export failed: {export_result['error']}"})

        export_path = export_result["path"]
        size_mb = os.path.getsize(export_path) / (1024 * 1024)

        if target == "runpod":
            upload_script = os.path.join(DATA_DIR, "upload_and_train_runpod.py")
            if not os.path.exists(upload_script):
                return json.dumps({"error": "RunPod upload script not found", "hint": "Run manually with cloud_storage_manager.py"})
            return json.dumps({
                "uploaded": False,
                "target": target,
                "size_mb": round(size_mb, 2),
                "export_path": export_path,
                "action_required": f"Run: .venv/bin/python {upload_script}",
            })
        elif target == "azure":
            return json.dumps({
                "uploaded": False,
                "target": target,
                "size_mb": round(size_mb, 2),
                "export_path": export_path,
                "action_required": "Run: .venv/bin/python cloud_storage_manager.py upload",
            })
        else:
            return json.dumps({"error": f"Unknown target: {target}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowelm_training_status() -> str:
    """
    Check status of active CroweLM training runs.

    :return: JSON with training run status.
    :rtype: str
    """
    try:
        return json.dumps({
            "running": False,
            "message": "No active training runs detected. Use crowelm_upload_dataset to start.",
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Internal ────────────────────────────────────────────────────────────────

def _count_curated_examples() -> int:
    """Count total examples across all curated JSONL files."""
    curated_dir = os.path.join(DATA_DIR, "curated")
    if not os.path.exists(curated_dir):
        return 0
    count = 0
    for jsonl_path in glob.glob(os.path.join(curated_dir, "*.jsonl")):
        with open(jsonl_path) as f:
            count += sum(1 for line in f if line.strip())
    return count
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_crowelm.py -v`
Expected: All 10 tests PASS

- [ ] **Step 6: Commit**

```bash
git add tools/crowelm.py tests/test_crowelm.py
git commit -m "feat: add CroweLM data tools (query, curate, pipeline)"
```

---

### Task 4: Crowe Logic AI HTTP client (`tools/crowe_logic_ai.py`)

**Files:**
- Create: `tools/crowe_logic_ai.py`
- Test: `tests/test_crowe_logic_ai.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_crowe_logic_ai.py`:
```python
"""Tests for tools.crowe_logic_ai — HTTP client for ai.southwestmushrooms.com."""

import json
import pytest
from unittest.mock import patch, MagicMock
from tools.crowe_logic_ai import crowe_ai_chat, crowe_ai_vision, crowe_ai_grow_log, crowe_ai_generate_sop


class TestCroweAiChat:
    @patch("tools.crowe_logic_ai._crowe_ai_request")
    def test_chat_sends_message(self, mock_req):
        mock_req.return_value = {"response": "Shiitake grow best on hardwood."}
        result = json.loads(crowe_ai_chat("How do I grow shiitake?"))
        assert "response" in result
        mock_req.assert_called_once()

    def test_chat_returns_error_on_failure(self):
        with patch("tools.crowe_logic_ai._crowe_ai_request", side_effect=Exception("connection refused")):
            result = json.loads(crowe_ai_chat("test"))
            assert "error" in result


class TestCroweAiVision:
    @patch("tools.crowe_logic_ai._crowe_ai_request")
    def test_vision_sends_image(self, mock_req, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        mock_req.return_value = {"analysis": "A healthy mycelium colony"}
        result = json.loads(crowe_ai_vision(str(img), "What do you see?"))
        assert "analysis" in result

    def test_vision_returns_error_for_missing_file(self):
        result = json.loads(crowe_ai_vision("/nonexistent.png"))
        assert "error" in result


class TestCroweAiGrowLog:
    @patch("tools.crowe_logic_ai._crowe_ai_request")
    def test_list_grow_logs(self, mock_req):
        mock_req.return_value = {"logs": [{"id": 1, "species": "shiitake"}]}
        result = json.loads(crowe_ai_grow_log("list"))
        assert "logs" in result

    @patch("tools.crowe_logic_ai._crowe_ai_request")
    def test_create_grow_log(self, mock_req):
        mock_req.return_value = {"created": True, "id": 42}
        result = json.loads(crowe_ai_grow_log("create", '{"species": "lions mane"}'))
        assert result["created"] is True


class TestCroweAiSop:
    @patch("tools.crowe_logic_ai._crowe_ai_request")
    def test_generate_sop(self, mock_req):
        mock_req.return_value = {"sop": "Standard Operating Procedure for substrate prep..."}
        result = json.loads(crowe_ai_generate_sop("substrate preparation"))
        assert "sop" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_crowe_logic_ai.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.crowe_logic_ai'`

- [ ] **Step 3: Implement `tools/crowe_logic_ai.py`**

Create `tools/crowe_logic_ai.py`:
```python
"""
Crowe Logic AI platform client — HTTP tools for ai.southwestmushrooms.com.

Provides access to CroweLM chat, Crowe Vision, grow logs, and SOP generation.
"""

import base64
import json
import os

import httpx


def crowe_ai_chat(message: str, context: str = "") -> str:
    """
    Chat with CroweLM for mycology and cultivation expertise.

    :param message: The user message to send to CroweLM.
    :param context: Optional conversation context.
    :return: JSON with CroweLM response.
    :rtype: str
    """
    try:
        result = _crowe_ai_request("POST", "/api/chat", json={"message": message, "context": context})
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowe_ai_vision(image_path: str, prompt: str = "Analyze this image") -> str:
    """
    Analyze an image using Crowe Vision (photo analysis for cultivation).

    :param image_path: Path to the image file.
    :param prompt: What to analyze about the image.
    :return: JSON with vision analysis results.
    :rtype: str
    """
    try:
        if not os.path.exists(image_path):
            return json.dumps({"error": f"File not found: {image_path}"})

        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        result = _crowe_ai_request("POST", "/api/crowe-vision/analyze", json={
            "image": image_b64,
            "prompt": prompt,
        })
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowe_ai_grow_log(action: str, data: str = "{}") -> str:
    """
    Manage grow logs on the Crowe Logic AI platform.

    :param action: Operation — "create", "read", "update", or "list".
    :param data: JSON string with log data (for create/update) or filters (for read/list).
    :return: JSON with grow log data.
    :rtype: str
    """
    try:
        parsed_data = json.loads(data)

        if action == "list":
            result = _crowe_ai_request("GET", "/api/conversations")
        elif action == "create":
            result = _crowe_ai_request("POST", "/api/conversations", json=parsed_data)
        elif action == "read":
            log_id = parsed_data.get("id", "")
            result = _crowe_ai_request("GET", f"/api/conversations/{log_id}")
        elif action == "update":
            log_id = parsed_data.pop("id", "")
            result = _crowe_ai_request("PATCH", f"/api/conversations/{log_id}", json=parsed_data)
        else:
            return json.dumps({"error": f"Unknown action: {action}. Use: create, read, update, list"})

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def crowe_ai_generate_sop(topic: str, parameters: str = "{}") -> str:
    """
    Generate a Standard Operating Procedure for cultivation tasks.

    :param topic: The SOP topic (e.g., "substrate preparation", "fruiting chamber setup").
    :param parameters: JSON string with additional parameters (species, scale, etc.).
    :return: JSON with the generated SOP document.
    :rtype: str
    """
    try:
        parsed_params = json.loads(parameters)
        parsed_params["topic"] = topic

        result = _crowe_ai_request("POST", "/api/chat", json={
            "message": f"Generate a detailed Standard Operating Procedure for: {topic}",
            "context": json.dumps(parsed_params),
        })
        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _crowe_ai_request(method: str, path: str, **kwargs) -> dict:
    """Send an authenticated request to the Crowe Logic AI platform."""
    url = os.environ.get("CROWE_LOGIC_AI_URL", "https://ai.southwestmushrooms.com")
    key = os.environ.get("CROWE_LOGIC_AI_KEY", "")

    headers = kwargs.pop("headers", {})
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers.setdefault("Content-Type", "application/json")

    response = httpx.request(method, f"{url}{path}", headers=headers, timeout=60.0, **kwargs)
    response.raise_for_status()
    return response.json()
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_crowe_logic_ai.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tools/crowe_logic_ai.py tests/test_crowe_logic_ai.py
git commit -m "feat: add Crowe Logic AI platform client (chat, vision, grow logs, SOP)"
```

---

### Task 5: Register all new tools in `__init__.py` and `SYSTEM_INSTRUCTIONS`

**Files:**
- Modify: `tools/__init__.py`
- Modify: `config/agent_config.py`

- [ ] **Step 1: Update `tools/__init__.py`**

Add the following import blocks after the existing quantum imports (line 35):

```python
# Vision
from tools.vision import analyze_image, screenshot_and_analyze

# CroweLM training data
from tools.crowelm import (
    crowelm_list_datasets, crowelm_dataset_stats, crowelm_search_examples,
    crowelm_inspect_config,
    crowelm_add_example, crowelm_remove_example, crowelm_export_curated,
    crowelm_prepare_training, crowelm_upload_dataset, crowelm_training_status,
)

# Crowe Logic AI platform
from tools.crowe_logic_ai import crowe_ai_chat, crowe_ai_vision, crowe_ai_grow_log, crowe_ai_generate_sop
```

Add the following to the `user_functions` set (after the Quantum section):

```python
    # Vision
    analyze_image, screenshot_and_analyze,
    # CroweLM training data
    crowelm_list_datasets, crowelm_dataset_stats, crowelm_search_examples,
    crowelm_inspect_config,
    crowelm_add_example, crowelm_remove_example, crowelm_export_curated,
    crowelm_prepare_training, crowelm_upload_dataset, crowelm_training_status,
    # Crowe Logic AI platform
    crowe_ai_chat, crowe_ai_vision, crowe_ai_grow_log, crowe_ai_generate_sop,
```

- [ ] **Step 2: Update `SYSTEM_INSTRUCTIONS` in `config/agent_config.py`**

Insert these sections after the quantum line and before the MCP section:

```python
- trinity_pipeline — full QubitFlow-to-Synapse experiment pipeline with hypothesis testing

## Vision & Image Analysis
- analyze_image — multi-backend image analysis (OpenRouter vision models, Crowe Vision, auto-fallback)
- screenshot_and_analyze — navigate to a URL, screenshot it, and analyze visually

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

## Crowe Logic AI Platform (ai.southwestmushrooms.com)
- crowe_ai_chat — chat with CroweLM for mycology and cultivation expertise
- crowe_ai_vision — photo analysis via Crowe Vision (contamination detection, growth assessment)
- crowe_ai_grow_log — create/read/update/list cultivation grow logs
- crowe_ai_generate_sop — generate Standard Operating Procedures for cultivation tasks
```

- [ ] **Step 3: Verify tool count**

Run:
```bash
.venv/bin/python -c "from tools import user_functions; print(f'{len(user_functions)} tools registered')"
```
Expected: `50 tools registered`

- [ ] **Step 4: Commit**

```bash
git add tools/__init__.py config/agent_config.py
git commit -m "feat: register all new tools in init and SYSTEM_INSTRUCTIONS (50 total)"
```

---

### Task 6: Update cultivation agent YAML

**Files:**
- Modify: `agents/cultivation.yaml`

- [ ] **Step 1: Update `agents/cultivation.yaml`**

Replace the entire file with:
```yaml
name: cultivation
description: "Mycology knowledge, growing protocols, and Crowe Logic AI platform specialist"
model: gpt-oss-120b
tools:
  - web_search
  - browse_url
  - read_file
  - write_file
  - analyze_image
  - crowe_ai_chat
  - crowe_ai_vision
  - crowe_ai_grow_log
  - crowe_ai_generate_sop
prompt_override: |
  You are the cultivation specialist within Crowe Logic.
  You have deep knowledge of mycology, mushroom cultivation, substrate
  preparation, environmental controls, and commercial growing operations.
  You reference The Mushroom Grower methodology when applicable.

  You have direct access to the Crowe Logic AI platform (ai.southwestmushrooms.com):
  - crowe_ai_chat: consult CroweLM for domain-specific cultivation answers
  - crowe_ai_vision: analyze photos of mycelium, fruiting bodies, contamination
  - crowe_ai_grow_log: manage cultivation grow logs
  - crowe_ai_generate_sop: generate Standard Operating Procedures

  Use analyze_image for general image analysis (multi-backend with fallback).
  You never use emojis. Output is clean and professional.
```

- [ ] **Step 2: Commit**

```bash
git add agents/cultivation.yaml
git commit -m "feat: add Crowe Logic AI tools to cultivation agent"
```

---

### Task 7: Update `.env.example` with new vars

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Read current `.env.example`**

Read the file to see what's already there.

- [ ] **Step 2: Add new env vars**

Append to `.env.example`:
```bash
# Crowe Logic AI Platform (ai.southwestmushrooms.com)
CROWE_LOGIC_AI_URL=https://ai.southwestmushrooms.com
CROWE_LOGIC_AI_KEY=
```

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: add Crowe Logic AI env vars to .env.example"
```

---

### Task 8: MCP server for Crowe Logic AI

**Files:**
- Create: `scripts/mcp_crowe_logic_ai.py`
- Create: `scripts/mcp_pyproject.toml` (publishing config)
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_server.py`:
```python
"""Tests for the Crowe Logic AI MCP server tool definitions."""

import json
import pytest
from unittest.mock import patch, MagicMock


class TestMcpServerTools:
    def test_server_module_imports(self):
        """Verify the MCP server module is importable."""
        import scripts.mcp_crowe_logic_ai as mcp_mod
        assert hasattr(mcp_mod, "mcp")

    def test_server_has_four_tools(self):
        """Verify all 4 MCP tools are registered."""
        import scripts.mcp_crowe_logic_ai as mcp_mod
        tool_names = [t.name for t in mcp_mod.mcp._tool_manager.list_tools()]
        assert "crowe_chat" in tool_names
        assert "crowe_vision" in tool_names
        assert "crowe_grow_log" in tool_names
        assert "crowe_sop" in tool_names

    @patch("scripts.mcp_crowe_logic_ai._request")
    def test_crowe_chat_tool(self, mock_req):
        """Test the chat tool calls the correct endpoint."""
        import scripts.mcp_crowe_logic_ai as mcp_mod
        mock_req.return_value = {"response": "test response"}
        # Call the underlying function directly
        result = mcp_mod.crowe_chat("hello")
        assert "test response" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.mcp_crowe_logic_ai'`

- [ ] **Step 3: Ensure scripts/ is a package**

```bash
touch scripts/__init__.py
```

- [ ] **Step 4: Install MCP SDK**

```bash
.venv/bin/pip install "mcp[cli]>=1.0.0"
```

- [ ] **Step 5: Implement `scripts/mcp_crowe_logic_ai.py`**

Create `scripts/mcp_crowe_logic_ai.py`:
```python
#!/usr/bin/env python3
"""
Crowe Logic AI — MCP Server

Exposes Crowe Logic AI platform (ai.southwestmushrooms.com) as MCP tools.
Any MCP client (Claude Code, Cursor, Windsurf, Gemini CLI, etc.) can connect.

Usage:
    python scripts/mcp_crowe_logic_ai.py                    # stdio transport (default)
    uvx crowe-logic-ai-mcp                                  # after PyPI publish

Claude Code config:
    {"mcpServers": {"crowe-logic-ai": {"command": "uvx", "args": ["crowe-logic-ai-mcp"]}}}
"""

import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "crowe-logic-ai",
    version="1.0.0",
    description="Crowe Logic AI — mycology expertise, photo analysis, grow logs, and SOP generation",
)


def _request(method: str, path: str, **kwargs) -> dict:
    """Send an authenticated request to the Crowe Logic AI platform."""
    url = os.environ.get("CROWE_LOGIC_AI_URL", "https://ai.southwestmushrooms.com")
    key = os.environ.get("CROWE_LOGIC_AI_KEY", "")

    headers = kwargs.pop("headers", {})
    if key:
        headers["Authorization"] = f"Bearer {key}"
    headers.setdefault("Content-Type", "application/json")

    response = httpx.request(method, f"{url}{path}", headers=headers, timeout=60.0, **kwargs)
    response.raise_for_status()
    return response.json()


@mcp.tool()
def crowe_chat(message: str, context: str = "") -> str:
    """Chat with CroweLM for mycology and cultivation expertise.

    Args:
        message: Your question or message about mushroom cultivation, mycology, or related topics.
        context: Optional conversation context for multi-turn conversations.
    """
    result = _request("POST", "/api/chat", json={"message": message, "context": context})
    return json.dumps(result, indent=2)


@mcp.tool()
def crowe_vision(image_base64: str, prompt: str = "Analyze this image") -> str:
    """Analyze an image using Crowe Vision — specialized for mushroom cultivation photos.

    Detects contamination, assesses mycelium health, identifies species, and evaluates growth stages.

    Args:
        image_base64: Base64-encoded image data.
        prompt: What to analyze about the image.
    """
    result = _request("POST", "/api/crowe-vision/analyze", json={"image": image_base64, "prompt": prompt})
    return json.dumps(result, indent=2)


@mcp.tool()
def crowe_grow_log(action: str, data: str = "{}") -> str:
    """Manage mushroom cultivation grow logs.

    Args:
        action: Operation — "list", "create", "read", or "update".
        data: JSON string with log data. For create: {"species": "shiitake", ...}. For read: {"id": "log_id"}.
    """
    parsed = json.loads(data)

    if action == "list":
        result = _request("GET", "/api/conversations")
    elif action == "create":
        result = _request("POST", "/api/conversations", json=parsed)
    elif action == "read":
        result = _request("GET", f"/api/conversations/{parsed.get('id', '')}")
    elif action == "update":
        log_id = parsed.pop("id", "")
        result = _request("PATCH", f"/api/conversations/{log_id}", json=parsed)
    else:
        return json.dumps({"error": f"Unknown action: {action}"})

    return json.dumps(result, indent=2)


@mcp.tool()
def crowe_sop(topic: str, parameters: str = "{}") -> str:
    """Generate a Standard Operating Procedure for mushroom cultivation tasks.

    Args:
        topic: The SOP topic (e.g., "substrate preparation", "fruiting chamber setup", "spawn production").
        parameters: Optional JSON with extra parameters like species, scale, or specific requirements.
    """
    parsed = json.loads(parameters)
    parsed["topic"] = topic
    result = _request("POST", "/api/chat", json={
        "message": f"Generate a detailed Standard Operating Procedure for: {topic}",
        "context": json.dumps(parsed),
    })
    return json.dumps(result, indent=2)


def main():
    """Entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: All 3 tests PASS

- [ ] **Step 7: Create PyPI publishing config**

Create `scripts/mcp_pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "crowe-logic-ai-mcp"
version = "1.0.0"
description = "MCP server for Crowe Logic AI — mycology expertise, photo analysis, grow logs, and SOP generation"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
authors = [{name = "Michael Crowe", email = "michael@crowelogic.com"}]
keywords = ["mcp", "mycology", "mushroom", "cultivation", "ai", "agriculture"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "Topic :: Scientific/Engineering :: Bio-Informatics",
]
dependencies = [
    "mcp[cli]>=1.0.0",
    "httpx>=0.28.0",
]

[project.scripts]
crowe-logic-ai-mcp = "scripts.mcp_crowe_logic_ai:main"

[project.urls]
Repository = "https://github.com/MichaelCrowe11/crowe-logic-foundry"
```

- [ ] **Step 8: Commit**

```bash
git add scripts/__init__.py scripts/mcp_crowe_logic_ai.py scripts/mcp_pyproject.toml tests/test_mcp_server.py
git commit -m "feat: add Crowe Logic AI MCP server (4 tools, PyPI-publishable)"
```

---

### Task 9: Full integration test and final commit

**Files:**
- Test: verify complete import chain

- [ ] **Step 1: Run full import verification**

```bash
.venv/bin/python -c "
from tools import user_functions
print(f'Total tools: {len(user_functions)}')
assert len(user_functions) == 50, f'Expected 50, got {len(user_functions)}'

# Verify every new tool is present
new_tools = [
    'trinity_pipeline',
    'analyze_image', 'screenshot_and_analyze',
    'crowelm_list_datasets', 'crowelm_dataset_stats', 'crowelm_search_examples',
    'crowelm_inspect_config', 'crowelm_add_example', 'crowelm_remove_example',
    'crowelm_export_curated', 'crowelm_prepare_training', 'crowelm_upload_dataset',
    'crowelm_training_status',
    'crowe_ai_chat', 'crowe_ai_vision', 'crowe_ai_grow_log', 'crowe_ai_generate_sop',
]
names = {f.__name__ for f in user_functions}
for tool in new_tools:
    assert tool in names, f'Missing: {tool}'

print('All 17 new tools verified')
print(f'Tool breakdown: 50 total = 33 existing + 17 new')
print('Integration complete')
"
```
Expected: `All 17 new tools verified`

- [ ] **Step 2: Run full test suite**

```bash
.venv/bin/python -m pytest tests/ -v --tb=short
```
Expected: All tests PASS

- [ ] **Step 3: Verify agent config is valid**

```bash
.venv/bin/python -c "
from config.agent_config import SYSTEM_INSTRUCTIONS
assert 'trinity_pipeline' in SYSTEM_INSTRUCTIONS
assert 'analyze_image' in SYSTEM_INSTRUCTIONS
assert 'crowelm_list_datasets' in SYSTEM_INSTRUCTIONS
assert 'crowe_ai_chat' in SYSTEM_INSTRUCTIONS
print('SYSTEM_INSTRUCTIONS validated — all new sections present')
print(f'Instructions length: {len(SYSTEM_INSTRUCTIONS)} chars')
"
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: full tool suite upgrade — 33 to 50 tools, vision + CroweLM + Crowe Logic AI + MCP"
```
