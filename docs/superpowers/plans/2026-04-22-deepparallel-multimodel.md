# DeepParallel Multimodel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `multimodel_parallel_query`, a sibling tool to the existing `deepparallel_query`, that runs eight genuinely parallel reasoning chains across Kimi K2.6 (Ollama Cloud), GLM 5.1 (CROWE_OPEN_ENDPOINT), and local DeepParallel, with Claude Opus 4.7 as judge for synthesis.

**Architecture:** Transport-adapter pattern (Ollama / OpenAI-compat / Anthropic) behind an async dispatcher. Dispatcher plans 8 (backend, persona) chains from named presets, enforces a pre-dispatch budget ceiling, fires via `asyncio.gather` with per-chain timeouts, drops failed chains, then hands surviving outputs to a synthesis layer (judge / vote / debate). Every call is logged to a JSONL ledger that phase 7 valuation draws from.

**Tech Stack:** Python 3.11, httpx (async HTTP), anthropic SDK, click (CLI), pytest + pytest-asyncio + pytest-httpx (testing).

**Spec reference:** `docs/superpowers/specs/2026-04-22-deepparallel-multimodel-design.md`

**Implementation notes:**
- Existing CLI binary is `crowe-logic` (not `lfcli` as originally written in the spec). CLI subcommands will be `crowe-logic parallel query|ledger|show`.
- CLI file lives at `cli/parallel.py` (flat layout, matches existing codebase convention).
- Tests live at `tests/test_*.py` (flat).
- Existing `tools/deepparallel.py` is NOT modified.

---

## File Structure

**Created:**
- `tools/multimodel_parallel.py` (public API)
- `tools/parallel/__init__.py`
- `tools/parallel/backends.py` (transport adapters)
- `tools/parallel/personas.py` (8 persona fragments)
- `tools/parallel/costs.py` (price table, budget estimator)
- `tools/parallel/configs.py` (preset loader)
- `tools/parallel/presets.json` (user-editable preset overrides)
- `tools/parallel/dispatcher.py` (chain planning + budget gate)
- `tools/parallel/collector.py` (async gather + survivor filter)
- `tools/parallel/synthesis.py` (judge / vote / debate)
- `tools/parallel/ledger.py` (JSONL writer + reader)
- `cli/parallel.py` (click command group)
- `tests/test_parallel_backends.py`
- `tests/test_parallel_costs.py`
- `tests/test_parallel_configs.py`
- `tests/test_parallel_dispatcher.py`
- `tests/test_parallel_collector.py`
- `tests/test_parallel_synthesis.py`
- `tests/test_parallel_ledger.py`
- `tests/test_multimodel_parallel.py`

**Modified:**
- `cli/crowe_logic.py` (register the new `parallel` click group)
- `pyproject.toml` (add `pytest-asyncio` and `pytest-httpx` to dev deps if absent)
- `tools/__init__.py` (export `multimodel_parallel_query`)

**Not modified (by contract):**
- `tools/deepparallel.py`

---

## Phase 1: Scaffold and Transport Adapters

### Task 1: Create module skeleton

**Files:**
- Create: `tools/parallel/__init__.py`
- Create: `tools/multimodel_parallel.py`

- [ ] **Step 1: Create the directory and package init**

```bash
mkdir -p tools/parallel
```

Write `tools/parallel/__init__.py`:

```python
"""Multimodel parallel reasoning: heterogeneous ensembling with judge synthesis.

Public API is exposed through ``tools.multimodel_parallel``. Submodules here
are implementation details and may change without notice.
"""
```

- [ ] **Step 2: Create the public module stub**

Write `tools/multimodel_parallel.py`:

```python
"""multimodel_parallel_query: 8-chain parallel reasoning across heterogeneous backends."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChainResult:
    backend: str
    persona: str
    text: str
    cost_usd: float
    latency_ms: int
    error: str | None = None


@dataclass(frozen=True)
class ParallelResult:
    synthesized_answer: str
    chains: tuple[ChainResult, ...]
    synthesis_metadata: dict
    total_cost_usd: float
    total_latency_ms: int
    dropped_chains: tuple[str, ...]
    ledger_id: str
```

- [ ] **Step 3: Verify module imports cleanly**

Run: `.venv/bin/python -c "from tools.multimodel_parallel import ChainResult, ParallelResult; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add tools/parallel/__init__.py tools/multimodel_parallel.py
git commit -m "feat(multimodel): scaffold package and public dataclasses"
```

---

### Task 2: Personas module

**Files:**
- Create: `tools/parallel/personas.py`
- Create: `tests/test_parallel_personas.py`

- [ ] **Step 1: Write the failing test**

Write `tests/test_parallel_personas.py`:

```python
from tools.parallel.personas import PERSONAS, PERSONA_ORDER, persona_prompt


def test_eight_personas_defined():
    assert len(PERSONAS) == 8
    assert set(PERSONA_ORDER) == set(PERSONAS.keys())


def test_persona_names_canonical():
    assert PERSONA_ORDER == [
        "analytical", "creative", "critical", "synthesis",
        "empirical", "theoretical", "practical", "meta-cognitive",
    ]


def test_persona_prompt_returns_instruction():
    text = persona_prompt("analytical")
    assert "analytical" in text.lower()
    assert len(text) > 40


def test_persona_prompt_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        persona_prompt("nonexistent")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_parallel_personas.py -v`
Expected: FAIL with `ModuleNotFoundError` on `tools.parallel.personas`.

- [ ] **Step 3: Implement personas**

Write `tools/parallel/personas.py`:

```python
"""Canonical 8 reasoning personas, mapped one per chain in a parallel run."""

PERSONAS: dict[str, str] = {
    "analytical": (
        "Apply rigorous analytical reasoning. Decompose the problem into "
        "components, examine each systematically, and draw conclusions from "
        "the evidence you can support."
    ),
    "creative": (
        "Approach the problem with divergent thinking. Generate unexpected "
        "angles, novel connections, and imaginative reframings."
    ),
    "critical": (
        "Apply skeptical scrutiny. Identify assumptions, probe weaknesses, "
        "stress-test claims, and surface counterexamples."
    ),
    "synthesis": (
        "Integrate across perspectives. Identify common patterns, reconcile "
        "tensions, and produce a coherent unified view."
    ),
    "empirical": (
        "Ground reasoning in observable evidence. Cite data, mechanisms, "
        "and verifiable facts. Avoid speculation beyond what the evidence "
        "supports."
    ),
    "theoretical": (
        "Reason from first principles and formal frameworks. Apply relevant "
        "theory, models, and abstract structures."
    ),
    "practical": (
        "Focus on actionable steps and real-world constraints. Prioritize "
        "implementability, cost, and concrete outcomes."
    ),
    "meta-cognitive": (
        "Reflect on the reasoning process itself. Identify blind spots, "
        "flag uncertainty, and call out where the current approach may fail."
    ),
}

PERSONA_ORDER: list[str] = [
    "analytical", "creative", "critical", "synthesis",
    "empirical", "theoretical", "practical", "meta-cognitive",
]


def persona_prompt(persona: str) -> str:
    """Return the instruction text for one persona.

    :raises KeyError: if ``persona`` is not in ``PERSONAS``.
    """
    return PERSONAS[persona]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_parallel_personas.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add tools/parallel/personas.py tests/test_parallel_personas.py
git commit -m "feat(multimodel): eight canonical reasoning personas"
```

---

### Task 3: Costs module with budget estimator

**Files:**
- Create: `tools/parallel/costs.py`
- Create: `tests/test_parallel_costs.py`

- [ ] **Step 1: Write the failing test**

Write `tests/test_parallel_costs.py`:

```python
import pytest

from tools.parallel.costs import (
    BackendPrice, PRICES, estimate_cost, BudgetError, assert_within_budget,
)


def test_prices_are_populated_for_core_backends():
    for backend in [
        "kimi-k2.6:cloud",
        "z-ai/glm-5.1",
        "Mcrowe1210/DeepParallel:latest",
        "claude-opus-4-7",
    ]:
        assert backend in PRICES


def test_estimate_cost_zero_for_local():
    cost = estimate_cost("Mcrowe1210/DeepParallel:latest", 1000, 1000)
    assert cost == 0.0


def test_estimate_cost_scales_linearly():
    small = estimate_cost("kimi-k2.6:cloud", 100, 100)
    big = estimate_cost("kimi-k2.6:cloud", 1000, 1000)
    assert big == pytest.approx(small * 10, rel=1e-6)


def test_estimate_cost_unknown_backend_returns_zero():
    assert estimate_cost("bogus/model", 1000, 1000) == 0.0


def test_assert_within_budget_passes_when_under():
    assert_within_budget(planned_cost_usd=0.10, budget_usd=0.50)


def test_assert_within_budget_raises_when_over():
    with pytest.raises(BudgetError) as exc:
        assert_within_budget(planned_cost_usd=1.00, budget_usd=0.50)
    assert "1.00" in str(exc.value) and "0.50" in str(exc.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_parallel_costs.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement costs module**

Write `tools/parallel/costs.py`:

```python
"""Backend price table and budget gate.

Prices are placeholders until phase 6 calibrates against real ledger data.
All values are USD per 1,000 tokens.
"""

from __future__ import annotations

from dataclasses import dataclass


class BudgetError(Exception):
    """Raised when planned cost exceeds the caller's budget ceiling."""


@dataclass(frozen=True)
class BackendPrice:
    input_per_1k_usd: float
    output_per_1k_usd: float


PRICES: dict[str, BackendPrice] = {
    "kimi-k2.6:cloud": BackendPrice(input_per_1k_usd=0.0015, output_per_1k_usd=0.0025),
    "kimi-k2.5:cloud": BackendPrice(input_per_1k_usd=0.0012, output_per_1k_usd=0.0020),
    "z-ai/glm-5.1": BackendPrice(input_per_1k_usd=0.0005, output_per_1k_usd=0.0015),
    "deepseek/deepseek-r1": BackendPrice(input_per_1k_usd=0.0010, output_per_1k_usd=0.0022),
    "Mcrowe1210/DeepParallel:latest": BackendPrice(0.0, 0.0),
    "claude-opus-4-7": BackendPrice(input_per_1k_usd=0.015, output_per_1k_usd=0.075),
    "claude-opus-4-6": BackendPrice(input_per_1k_usd=0.015, output_per_1k_usd=0.075),
}


def estimate_cost(backend: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for one call to ``backend``.

    Unknown backends return 0.0 (do not block), so a missing price entry
    cannot trip the budget gate. Unknown backends are still logged via the
    ledger for later calibration.
    """
    price = PRICES.get(backend)
    if price is None:
        return 0.0
    return (
        (input_tokens / 1000.0) * price.input_per_1k_usd
        + (output_tokens / 1000.0) * price.output_per_1k_usd
    )


def assert_within_budget(planned_cost_usd: float, budget_usd: float) -> None:
    """Raise :class:`BudgetError` if ``planned_cost_usd`` exceeds ``budget_usd``."""
    if planned_cost_usd > budget_usd:
        raise BudgetError(
            f"Planned cost ${planned_cost_usd:.2f} exceeds budget ${budget_usd:.2f}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_parallel_costs.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add tools/parallel/costs.py tests/test_parallel_costs.py
git commit -m "feat(multimodel): price table and budget gate"
```

---

### Task 4: Configs module with preset loader

**Files:**
- Create: `tools/parallel/configs.py`
- Create: `tools/parallel/presets.json`
- Create: `tests/test_parallel_configs.py`

- [ ] **Step 1: Write the failing test**

Write `tests/test_parallel_configs.py`:

```python
import json

import pytest

from tools.parallel.configs import (
    ChainSpec, Preset, load_preset, list_presets, total_chains,
)


def test_fast_preset_delegates_to_deepparallel():
    preset = load_preset("fast")
    assert preset.delegate_to_deepparallel is True
    assert preset.chains == ()


def test_cloud_balanced_has_eight_chains():
    preset = load_preset("cloud-balanced")
    assert total_chains(preset) == 8
    backends = {c.backend for c in preset.chains}
    assert "kimi-k2.6:cloud" in backends
    assert "z-ai/glm-5.1" in backends


def test_deep_preset_is_four_kimi_four_glm():
    preset = load_preset("deep")
    assert total_chains(preset) == 8
    by_backend = {c.backend: c.count for c in preset.chains}
    assert by_backend["kimi-k2.6:cloud"] == 4
    assert by_backend["z-ai/glm-5.1"] == 4


def test_max_preset_includes_deepseek_and_local():
    preset = load_preset("max")
    by_backend = {c.backend: c.count for c in preset.chains}
    assert "deepseek/deepseek-r1" in by_backend
    assert "Mcrowe1210/DeepParallel:latest" in by_backend


def test_unknown_preset_raises():
    with pytest.raises(ValueError, match="Unknown preset"):
        load_preset("does-not-exist")


def test_list_presets_returns_all_names():
    names = list_presets()
    assert {"fast", "cloud-balanced", "deep", "max"} <= set(names)


def test_user_override_takes_precedence(tmp_path, monkeypatch):
    override_path = tmp_path / "presets.json"
    override_path.write_text(json.dumps({
        "cloud-balanced": {
            "chains": [{"backend": "kimi-k2.6:cloud", "count": 5}],
        },
    }))
    monkeypatch.setattr(
        "tools.parallel.configs._presets_file", lambda: override_path,
    )
    preset = load_preset("cloud-balanced")
    assert total_chains(preset) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_parallel_configs.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement configs module and presets file**

Write `tools/parallel/presets.json`:

```json
{}
```

Write `tools/parallel/configs.py`:

```python
"""Named presets for multimodel parallel runs.

Defaults are defined in this file. User-editable overrides live in
``presets.json`` next to this module and are merged on top of defaults
at load time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChainSpec:
    backend: str
    count: int


@dataclass(frozen=True)
class Preset:
    name: str
    chains: tuple[ChainSpec, ...]
    delegate_to_deepparallel: bool = False


_DEFAULTS: dict[str, Preset] = {
    "fast": Preset(
        name="fast",
        chains=(),
        delegate_to_deepparallel=True,
    ),
    "cloud-balanced": Preset(
        name="cloud-balanced",
        chains=(
            ChainSpec("kimi-k2.6:cloud", 3),
            ChainSpec("z-ai/glm-5.1", 3),
            ChainSpec("Mcrowe1210/DeepParallel:latest", 2),
        ),
    ),
    "deep": Preset(
        name="deep",
        chains=(
            ChainSpec("kimi-k2.6:cloud", 4),
            ChainSpec("z-ai/glm-5.1", 4),
        ),
    ),
    "max": Preset(
        name="max",
        chains=(
            ChainSpec("kimi-k2.6:cloud", 2),
            ChainSpec("z-ai/glm-5.1", 2),
            ChainSpec("deepseek/deepseek-r1", 2),
            ChainSpec("Mcrowe1210/DeepParallel:latest", 2),
        ),
    ),
}


def _presets_file() -> Path:
    return Path(__file__).parent / "presets.json"


def _load_overrides() -> dict:
    path = _presets_file()
    if not path.exists():
        return {}
    text = path.read_text().strip()
    if not text:
        return {}
    return json.loads(text)


def load_preset(name: str) -> Preset:
    """Load a preset by name, applying user overrides from ``presets.json``."""
    overrides = _load_overrides()
    if name in overrides:
        override = overrides[name]
        chains = tuple(
            ChainSpec(c["backend"], int(c["count"])) for c in override.get("chains", [])
        )
        return Preset(
            name=name,
            chains=chains,
            delegate_to_deepparallel=bool(override.get("delegate_to_deepparallel", False)),
        )
    if name not in _DEFAULTS:
        raise ValueError(f"Unknown preset: {name}")
    return _DEFAULTS[name]


def list_presets() -> list[str]:
    """Return all preset names (defaults plus overrides)."""
    names = set(_DEFAULTS.keys())
    names.update(_load_overrides().keys())
    return sorted(names)


def total_chains(preset: Preset) -> int:
    return sum(c.count for c in preset.chains)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_parallel_configs.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add tools/parallel/configs.py tools/parallel/presets.json tests/test_parallel_configs.py
git commit -m "feat(multimodel): named presets with user override support"
```

---

### Task 5: Backend adapter base + OllamaAdapter

**Files:**
- Create: `tools/parallel/backends.py`
- Create: `tests/test_parallel_backends.py`
- Modify: `pyproject.toml` (confirm `pytest-httpx` and `pytest-asyncio` in dev deps)

- [ ] **Step 1: Confirm test dependencies**

Run: `grep -E "pytest-asyncio|pytest-httpx" pyproject.toml requirements*.txt`
Expected: both present. If missing, add to the `[project.optional-dependencies]` dev array in `pyproject.toml`:

```toml
"pytest-asyncio>=0.24.0",
"pytest-httpx>=0.35.0",
```

Install: `.venv/bin/pip install pytest-asyncio pytest-httpx`

- [ ] **Step 2: Write the failing test**

Write `tests/test_parallel_backends.py`:

```python
import pytest
from pytest_httpx import HTTPXMock

from tools.parallel.backends import (
    Adapter, BackendCall, BackendResponse, OllamaAdapter,
)


def _ok_response(text: str = "ok") -> dict:
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }


@pytest.mark.asyncio
async def test_ollama_adapter_happy_path(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://localhost:11434/v1/chat/completions",
        json=_ok_response("hello world"),
    )
    adapter = OllamaAdapter(model="test-model", base_url="http://localhost:11434")
    call = BackendCall(prompt="hi", system="sys", max_tokens=128, temperature=0.5)
    resp = await adapter.send(call)
    assert resp.text == "hello world"
    assert resp.input_tokens == 10
    assert resp.output_tokens == 3
    assert resp.latency_ms >= 0


@pytest.mark.asyncio
async def test_ollama_adapter_strips_v1_suffix_on_base_url(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://localhost:11434/v1/chat/completions",
        json=_ok_response(),
    )
    adapter = OllamaAdapter(model="test-model", base_url="http://localhost:11434/v1")
    await adapter.send(BackendCall("p", "s", 10, 0.1))


@pytest.mark.asyncio
async def test_ollama_adapter_raises_on_4xx(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="http://localhost:11434/v1/chat/completions",
        status_code=400,
        json={"error": "bad request"},
    )
    adapter = OllamaAdapter(model="test-model", base_url="http://localhost:11434")
    import httpx as _httpx
    with pytest.raises(_httpx.HTTPStatusError):
        await adapter.send(BackendCall("p", "s", 10, 0.1))
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_parallel_backends.py -v`
Expected: FAIL with import error on `tools.parallel.backends`.

- [ ] **Step 4: Implement base adapter and OllamaAdapter**

Write `tools/parallel/backends.py`:

```python
"""Transport adapters for parallel backends.

Each adapter wraps one HTTP surface (Ollama, OpenAI-compat, Anthropic)
behind the same async ``send`` interface. Adapters do not handle retries
or synthesis; those are the collector and synthesis layer's concerns.
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class BackendCall:
    prompt: str
    system: str
    max_tokens: int
    temperature: float


@dataclass(frozen=True)
class BackendResponse:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class Adapter(ABC):
    """Minimal async interface that every transport adapter implements."""

    backend_id: str

    @abstractmethod
    async def send(self, call: BackendCall) -> BackendResponse:
        """Send one request and return the parsed response."""


class OllamaAdapter(Adapter):
    """Talks to the Ollama /v1/chat/completions endpoint (local or cloud)."""

    def __init__(
        self,
        model: str,
        base_url: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        raw_url = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        self._base_url = raw_url.replace("/v1", "").rstrip("/")
        self._model = model
        self._timeout_s = timeout_s
        self.backend_id = model

    async def send(self, call: BackendCall) -> BackendResponse:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": call.system},
                {"role": "user", "content": call.prompt},
            ],
            "temperature": call.temperature,
            "max_tokens": call.max_tokens,
            "stream": False,
        }
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        latency_ms = int((time.monotonic() - t0) * 1000)
        usage = data.get("usage") or {}
        return BackendResponse(
            text=data["choices"][0]["message"]["content"],
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=latency_ms,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_parallel_backends.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add tools/parallel/backends.py tests/test_parallel_backends.py pyproject.toml
git commit -m "feat(multimodel): Ollama adapter with async httpx"
```

---

### Task 6: OpenAICompatAdapter + AnthropicAdapter

**Files:**
- Modify: `tools/parallel/backends.py`
- Modify: `tests/test_parallel_backends.py`

- [ ] **Step 1: Add failing tests for both adapters**

Append to `tests/test_parallel_backends.py`:

```python
from tools.parallel.backends import OpenAICompatAdapter, AnthropicAdapter


@pytest.mark.asyncio
async def test_openai_compat_adapter_uses_bearer_auth(httpx_mock: HTTPXMock):
    def _match(request):
        assert request.headers["authorization"] == "Bearer sk-test"
        return True
    httpx_mock.add_response(
        url="https://api.example.com/chat/completions",
        json=_ok_response("glm says hi"),
        match_headers={"Authorization": "Bearer sk-test"},
    )
    adapter = OpenAICompatAdapter(
        model="z-ai/glm-5.1",
        endpoint="https://api.example.com",
        api_key="sk-test",
    )
    resp = await adapter.send(BackendCall("p", "s", 10, 0.1))
    assert resp.text == "glm says hi"


@pytest.mark.asyncio
async def test_anthropic_adapter_posts_to_messages_endpoint(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url="https://api.anthropic.com/v1/messages",
        json={
            "content": [{"type": "text", "text": "claude says hi"}],
            "usage": {"input_tokens": 12, "output_tokens": 5},
        },
    )
    adapter = AnthropicAdapter(
        model="claude-opus-4-7",
        api_key="ant-test",
    )
    resp = await adapter.send(BackendCall("p", "sys", 100, 0.2))
    assert resp.text == "claude says hi"
    assert resp.input_tokens == 12
    assert resp.output_tokens == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_parallel_backends.py -v`
Expected: 2 new failures on missing class imports.

- [ ] **Step 3: Add the two adapters**

Append to `tools/parallel/backends.py`:

```python
class OpenAICompatAdapter(Adapter):
    """Talks to any OpenAI-compatible endpoint (CROWE_OPEN_ENDPOINT, NVIDIA NIM, etc.)."""

    def __init__(
        self,
        model: str,
        endpoint: str | None = None,
        api_key: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._endpoint = (endpoint or os.environ["CROWE_OPEN_ENDPOINT"]).rstrip("/")
        self._api_key = api_key or os.environ["CROWE_OPEN_API_KEY"]
        self._model = model
        self._timeout_s = timeout_s
        self.backend_id = model

    async def send(self, call: BackendCall) -> BackendResponse:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": call.system},
                {"role": "user", "content": call.prompt},
            ],
            "temperature": call.temperature,
            "max_tokens": call.max_tokens,
            "stream": False,
        }
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.post(
                f"{self._endpoint}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        latency_ms = int((time.monotonic() - t0) * 1000)
        usage = data.get("usage") or {}
        return BackendResponse(
            text=data["choices"][0]["message"]["content"],
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=latency_ms,
        )


class AnthropicAdapter(Adapter):
    """Talks to the Anthropic Messages API. Used primarily by the judge."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str = "https://api.anthropic.com",
        timeout_s: float = 90.0,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self.backend_id = model

    async def send(self, call: BackendCall) -> BackendResponse:
        payload = {
            "model": self._model,
            "max_tokens": call.max_tokens,
            "temperature": call.temperature,
            "system": call.system,
            "messages": [{"role": "user", "content": call.prompt}],
        }
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.post(
                f"{self._base_url}/v1/messages",
                json=payload,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        latency_ms = int((time.monotonic() - t0) * 1000)
        content_blocks = data.get("content", [])
        text = "".join(block.get("text", "") for block in content_blocks if block.get("type") == "text")
        usage = data.get("usage") or {}
        return BackendResponse(
            text=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            latency_ms=latency_ms,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_parallel_backends.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add tools/parallel/backends.py tests/test_parallel_backends.py
git commit -m "feat(multimodel): OpenAI-compat and Anthropic adapters"
```

---

## Phase 2: Dispatcher, Persona Rotation, Budget Gate

### Task 7: Chain planner (persona rotation + budget estimate)

**Files:**
- Create: `tools/parallel/dispatcher.py`
- Create: `tests/test_parallel_dispatcher.py`

- [ ] **Step 1: Write the failing test**

Write `tests/test_parallel_dispatcher.py`:

```python
import pytest

from tools.parallel.configs import load_preset
from tools.parallel.dispatcher import (
    PlannedChain, plan_chains, estimate_total_cost,
)
from tools.parallel.costs import BudgetError


def test_plan_chains_matches_preset_count():
    preset = load_preset("cloud-balanced")
    plan = plan_chains(preset)
    assert len(plan) == 8


def test_plan_chains_rotates_personas():
    preset = load_preset("cloud-balanced")
    plan = plan_chains(preset)
    personas = [p.persona for p in plan]
    assert len(set(personas)) == 8  # all 8 personas present


def test_plan_chains_preserves_backend_distribution():
    preset = load_preset("cloud-balanced")
    plan = plan_chains(preset)
    counts: dict[str, int] = {}
    for p in plan:
        counts[p.backend] = counts.get(p.backend, 0) + 1
    assert counts == {
        "kimi-k2.6:cloud": 3,
        "z-ai/glm-5.1": 3,
        "Mcrowe1210/DeepParallel:latest": 2,
    }


def test_estimate_total_cost_sums_planned_chains():
    plan = [
        PlannedChain(backend="kimi-k2.6:cloud", persona="analytical"),
        PlannedChain(backend="Mcrowe1210/DeepParallel:latest", persona="creative"),
    ]
    cost = estimate_total_cost(plan, max_input_tokens=500, max_output_tokens=500)
    assert cost > 0.0  # kimi contributes
    # local leg contributes 0; kimi at 500+500 tokens:
    # 0.5*0.0015 + 0.5*0.0025 = 0.00075 + 0.00125 = 0.002
    assert cost == pytest.approx(0.002, rel=1e-6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_parallel_dispatcher.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement dispatcher**

Write `tools/parallel/dispatcher.py`:

```python
"""Chain planning: map a preset onto concrete (backend, persona) tuples."""

from __future__ import annotations

from dataclasses import dataclass

from tools.parallel.configs import Preset
from tools.parallel.costs import estimate_cost
from tools.parallel.personas import PERSONA_ORDER


@dataclass(frozen=True)
class PlannedChain:
    backend: str
    persona: str


def plan_chains(preset: Preset) -> list[PlannedChain]:
    """Expand a preset into a concrete chain plan.

    Personas are assigned by rotating through :data:`PERSONA_ORDER`, so for
    an 8-chain preset every persona appears exactly once. For presets with
    fewer than 8 chains, personas are taken from the front of the order.
    """
    chains: list[PlannedChain] = []
    persona_idx = 0
    for spec in preset.chains:
        for _ in range(spec.count):
            persona = PERSONA_ORDER[persona_idx % len(PERSONA_ORDER)]
            chains.append(PlannedChain(backend=spec.backend, persona=persona))
            persona_idx += 1
    return chains


def estimate_total_cost(
    plan: list[PlannedChain],
    max_input_tokens: int,
    max_output_tokens: int,
) -> float:
    """Sum estimated cost across every planned chain."""
    return sum(
        estimate_cost(c.backend, max_input_tokens, max_output_tokens) for c in plan
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_parallel_dispatcher.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add tools/parallel/dispatcher.py tests/test_parallel_dispatcher.py
git commit -m "feat(multimodel): chain planner with persona rotation and cost estimate"
```

---

### Task 8: Adapter factory (backend string to Adapter instance)

**Files:**
- Modify: `tools/parallel/dispatcher.py`
- Modify: `tests/test_parallel_dispatcher.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_parallel_dispatcher.py`:

```python
from tools.parallel.dispatcher import build_adapter
from tools.parallel.backends import OllamaAdapter, OpenAICompatAdapter, AnthropicAdapter


def test_build_adapter_routes_ollama():
    adapter = build_adapter("kimi-k2.6:cloud")
    assert isinstance(adapter, OllamaAdapter)


def test_build_adapter_routes_local_deepparallel():
    adapter = build_adapter("Mcrowe1210/DeepParallel:latest")
    assert isinstance(adapter, OllamaAdapter)


def test_build_adapter_routes_openai_compat(monkeypatch):
    monkeypatch.setenv("CROWE_OPEN_ENDPOINT", "https://fake.example/v1")
    monkeypatch.setenv("CROWE_OPEN_API_KEY", "fake")
    adapter = build_adapter("z-ai/glm-5.1")
    assert isinstance(adapter, OpenAICompatAdapter)


def test_build_adapter_routes_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    adapter = build_adapter("claude-opus-4-7")
    assert isinstance(adapter, AnthropicAdapter)


def test_build_adapter_unknown_backend_raises():
    with pytest.raises(ValueError, match="no adapter"):
        build_adapter("totally/made-up-model")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_parallel_dispatcher.py -v`
Expected: 5 new failures on missing `build_adapter`.

- [ ] **Step 3: Implement factory**

Append to `tools/parallel/dispatcher.py`:

```python
from tools.parallel.backends import (
    Adapter, OllamaAdapter, OpenAICompatAdapter, AnthropicAdapter,
)


_OLLAMA_SUFFIXES = (":cloud", ":latest")
_OLLAMA_PREFIXES = ("Mcrowe1210/",)
_ANTHROPIC_PREFIXES = ("claude-",)


def build_adapter(backend: str, timeout_s: float = 60.0) -> Adapter:
    """Return the transport adapter for a backend identifier.

    Routing rules:
    - Names ending in ``:cloud`` or ``:latest``, or beginning with
      ``Mcrowe1210/``, go through Ollama.
    - Names beginning with ``claude-`` go through Anthropic.
    - Everything else goes through the OpenAI-compat endpoint.
    """
    if backend.startswith(_ANTHROPIC_PREFIXES):
        return AnthropicAdapter(model=backend, timeout_s=timeout_s)
    if backend.endswith(_OLLAMA_SUFFIXES) or backend.startswith(_OLLAMA_PREFIXES):
        return OllamaAdapter(model=backend, timeout_s=timeout_s)
    # OpenAI-compat as default for "vendor/model" style identifiers
    if "/" in backend:
        return OpenAICompatAdapter(model=backend, timeout_s=timeout_s)
    raise ValueError(f"no adapter for backend: {backend!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_parallel_dispatcher.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add tools/parallel/dispatcher.py tests/test_parallel_dispatcher.py
git commit -m "feat(multimodel): adapter factory routing by backend id"
```

---

## Phase 3: Collector

### Task 9: Async collector with timeouts and survivor filter

**Files:**
- Create: `tools/parallel/collector.py`
- Create: `tests/test_parallel_collector.py`

- [ ] **Step 1: Write the failing test**

Write `tests/test_parallel_collector.py`:

```python
import asyncio

import pytest

from tools.parallel.backends import Adapter, BackendCall, BackendResponse
from tools.parallel.collector import CollectorResult, collect_chains
from tools.parallel.dispatcher import PlannedChain
from tools.parallel.personas import persona_prompt


class _FakeAdapter(Adapter):
    def __init__(self, backend_id: str, text: str, delay_s: float = 0.0, raise_exc: Exception | None = None):
        self.backend_id = backend_id
        self._text = text
        self._delay = delay_s
        self._exc = raise_exc

    async def send(self, call: BackendCall) -> BackendResponse:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._exc:
            raise self._exc
        return BackendResponse(text=self._text, input_tokens=5, output_tokens=5, latency_ms=1)


@pytest.mark.asyncio
async def test_collector_returns_results_for_all_successful_chains():
    plan = [PlannedChain("bk1", "analytical"), PlannedChain("bk2", "creative")]
    adapters = {
        "bk1": _FakeAdapter("bk1", "text-1"),
        "bk2": _FakeAdapter("bk2", "text-2"),
    }
    result = await collect_chains(
        plan=plan, adapters=adapters,
        prompt="hi", system="sys", max_tokens=100, temperature=0.5,
        per_chain_timeout_s=5.0,
    )
    assert len(result.chains) == 2
    assert {c.text for c in result.chains} == {"text-1", "text-2"}
    assert result.dropped_chains == ()


@pytest.mark.asyncio
async def test_collector_drops_failing_chain_but_keeps_survivors():
    plan = [PlannedChain("ok", "analytical"), PlannedChain("bad", "creative")]
    adapters = {
        "ok": _FakeAdapter("ok", "fine"),
        "bad": _FakeAdapter("bad", "unused", raise_exc=RuntimeError("boom")),
    }
    result = await collect_chains(
        plan=plan, adapters=adapters,
        prompt="hi", system="sys", max_tokens=100, temperature=0.5,
        per_chain_timeout_s=5.0,
    )
    assert len(result.chains) == 1
    assert result.chains[0].text == "fine"
    assert result.dropped_chains == ("bad:creative",)


@pytest.mark.asyncio
async def test_collector_times_out_slow_chain():
    plan = [PlannedChain("slow", "analytical"), PlannedChain("fast", "creative")]
    adapters = {
        "slow": _FakeAdapter("slow", "never", delay_s=2.0),
        "fast": _FakeAdapter("fast", "quick"),
    }
    result = await collect_chains(
        plan=plan, adapters=adapters,
        prompt="hi", system="sys", max_tokens=100, temperature=0.5,
        per_chain_timeout_s=0.1,
    )
    assert len(result.chains) == 1
    assert result.chains[0].text == "quick"
    assert result.dropped_chains == ("slow:analytical",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_parallel_collector.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement collector**

Write `tools/parallel/collector.py`:

```python
"""Async dispatch and survivor filter for planned chains."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from tools.multimodel_parallel import ChainResult
from tools.parallel.backends import Adapter, BackendCall
from tools.parallel.costs import estimate_cost
from tools.parallel.dispatcher import PlannedChain
from tools.parallel.personas import persona_prompt


@dataclass(frozen=True)
class CollectorResult:
    chains: tuple[ChainResult, ...]
    dropped_chains: tuple[str, ...]


async def _run_one_chain(
    chain: PlannedChain,
    adapter: Adapter,
    prompt: str,
    system_base: str,
    max_tokens: int,
    temperature: float,
    per_chain_timeout_s: float,
) -> ChainResult:
    persona_instruction = persona_prompt(chain.persona)
    combined_system = (
        f"{system_base}\n\n{persona_instruction}" if system_base else persona_instruction
    )
    call = BackendCall(
        prompt=prompt,
        system=combined_system,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    try:
        response = await asyncio.wait_for(adapter.send(call), timeout=per_chain_timeout_s)
    except asyncio.TimeoutError:
        return ChainResult(
            backend=chain.backend, persona=chain.persona,
            text="", cost_usd=0.0, latency_ms=int(per_chain_timeout_s * 1000),
            error=f"timeout after {per_chain_timeout_s}s",
        )
    except Exception as exc:  # noqa: BLE001 - surfaced through ChainResult
        return ChainResult(
            backend=chain.backend, persona=chain.persona,
            text="", cost_usd=0.0, latency_ms=0,
            error=f"{type(exc).__name__}: {exc}",
        )
    cost = estimate_cost(chain.backend, response.input_tokens, response.output_tokens)
    return ChainResult(
        backend=chain.backend, persona=chain.persona,
        text=response.text, cost_usd=cost, latency_ms=response.latency_ms,
        error=None,
    )


async def collect_chains(
    plan: list[PlannedChain],
    adapters: dict[str, Adapter],
    prompt: str,
    system: str,
    max_tokens: int,
    temperature: float,
    per_chain_timeout_s: float,
) -> CollectorResult:
    """Run every planned chain concurrently, drop failures, return survivors."""
    tasks = [
        _run_one_chain(
            chain=c,
            adapter=adapters[c.backend],
            prompt=prompt,
            system_base=system,
            max_tokens=max_tokens,
            temperature=temperature,
            per_chain_timeout_s=per_chain_timeout_s,
        )
        for c in plan
    ]
    results: list[ChainResult] = await asyncio.gather(*tasks)
    surviving: list[ChainResult] = []
    dropped: list[str] = []
    for r in results:
        if r.error is None and r.text:
            surviving.append(r)
        else:
            dropped.append(f"{r.backend}:{r.persona}")
    return CollectorResult(
        chains=tuple(surviving), dropped_chains=tuple(dropped),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_parallel_collector.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add tools/parallel/collector.py tests/test_parallel_collector.py
git commit -m "feat(multimodel): async collector with timeout and survivor filter"
```

---

## Phase 4: Synthesis Layer

### Task 10: Judge synthesis (Claude Opus 4.7)

**Files:**
- Create: `tools/parallel/synthesis.py`
- Create: `tests/test_parallel_synthesis.py`

- [ ] **Step 1: Write the failing test**

Write `tests/test_parallel_synthesis.py`:

```python
import pytest

from tools.multimodel_parallel import ChainResult
from tools.parallel.backends import Adapter, BackendCall, BackendResponse
from tools.parallel.synthesis import synthesize


class _StubJudge(Adapter):
    def __init__(self, reply: str):
        self.backend_id = "claude-opus-4-7"
        self._reply = reply
        self.last_prompt: str | None = None

    async def send(self, call: BackendCall) -> BackendResponse:
        self.last_prompt = call.prompt
        return BackendResponse(
            text=self._reply, input_tokens=100, output_tokens=50, latency_ms=200,
        )


@pytest.mark.asyncio
async def test_judge_receives_all_chain_outputs_labeled():
    chains = (
        ChainResult("kimi-k2.6:cloud", "analytical", "kimi view", 0.01, 100),
        ChainResult("z-ai/glm-5.1", "creative", "glm view", 0.005, 150),
    )
    judge = _StubJudge(reply="SYNTHESIZED_ANSWER")
    result = await synthesize(
        mode="judge", chains=chains, original_prompt="what is 2+2?",
        judge_adapter=judge,
    )
    assert result.synthesized_answer == "SYNTHESIZED_ANSWER"
    assert "kimi-k2.6:cloud" in judge.last_prompt
    assert "analytical" in judge.last_prompt
    assert "kimi view" in judge.last_prompt
    assert "glm view" in judge.last_prompt
    assert "what is 2+2?" in judge.last_prompt


@pytest.mark.asyncio
async def test_judge_fallback_on_judge_failure():
    chains = (
        ChainResult("bk1", "analytical", "answer one", 0.01, 100),
        ChainResult("bk2", "creative", "answer two", 0.005, 150),
    )

    class _FailingJudge(Adapter):
        backend_id = "claude-opus-4-7"
        async def send(self, call):
            raise RuntimeError("judge offline")

    result = await synthesize(
        mode="judge", chains=chains, original_prompt="q",
        judge_adapter=_FailingJudge(),
    )
    assert result.metadata.get("fallback") is True
    assert "answer one" in result.synthesized_answer
    assert "answer two" in result.synthesized_answer
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_parallel_synthesis.py -v`
Expected: FAIL on import error.

- [ ] **Step 3: Implement synthesis**

Write `tools/parallel/synthesis.py`:

```python
"""Synthesis layer: judge / vote / debate.

The judge mode is the default for phase 1. Vote and debate ship in the
same module behind the same :func:`synthesize` interface.
"""

from __future__ import annotations

from dataclasses import dataclass

from tools.multimodel_parallel import ChainResult
from tools.parallel.backends import Adapter, BackendCall


_JUDGE_SYSTEM = (
    "You are the synthesis judge for DeepParallel Multimodel. Multiple "
    "independent reasoning chains have produced answers to the same "
    "question. Your job is to integrate them into the single best answer. "
    "Identify points of agreement, surface meaningful disagreements, and "
    "resolve contradictions by weighing evidence rather than by popularity. "
    "If the chains disagree in ways you cannot resolve, say so explicitly "
    "and produce a best-effort answer that flags the uncertainty."
)


@dataclass(frozen=True)
class SynthesisOutput:
    synthesized_answer: str
    metadata: dict


def _format_chains_for_judge(chains: tuple[ChainResult, ...]) -> str:
    lines: list[str] = []
    for i, c in enumerate(chains, start=1):
        lines.append(
            f"### Chain {i} (backend={c.backend}, persona={c.persona})\n{c.text}\n"
        )
    return "\n".join(lines)


def _fallback_concatenation(chains: tuple[ChainResult, ...]) -> str:
    return "\n\n---\n\n".join(
        f"[{c.backend} / {c.persona}]\n{c.text}" for c in chains
    )


async def _synthesize_judge(
    chains: tuple[ChainResult, ...],
    original_prompt: str,
    judge_adapter: Adapter,
) -> SynthesisOutput:
    chain_block = _format_chains_for_judge(chains)
    judge_prompt = (
        f"Original question:\n{original_prompt}\n\n"
        f"Independent reasoning chains:\n{chain_block}\n\n"
        "Produce the integrated best answer."
    )
    call = BackendCall(
        prompt=judge_prompt, system=_JUDGE_SYSTEM,
        max_tokens=2048, temperature=0.3,
    )
    try:
        resp = await judge_adapter.send(call)
    except Exception as exc:  # noqa: BLE001 - surfaced as fallback
        return SynthesisOutput(
            synthesized_answer=_fallback_concatenation(chains),
            metadata={
                "judge_model": judge_adapter.backend_id,
                "fallback": True,
                "fallback_reason": f"{type(exc).__name__}: {exc}",
            },
        )
    return SynthesisOutput(
        synthesized_answer=resp.text,
        metadata={
            "judge_model": judge_adapter.backend_id,
            "judge_input_tokens": resp.input_tokens,
            "judge_output_tokens": resp.output_tokens,
            "fallback": False,
        },
    )


async def synthesize(
    mode: str,
    chains: tuple[ChainResult, ...],
    original_prompt: str,
    judge_adapter: Adapter | None = None,
) -> SynthesisOutput:
    """Run synthesis in one of: judge, vote, debate."""
    if mode == "judge":
        if judge_adapter is None:
            raise ValueError("judge mode requires judge_adapter")
        return await _synthesize_judge(chains, original_prompt, judge_adapter)
    if mode == "vote":
        return _synthesize_vote(chains)
    if mode == "debate":
        if judge_adapter is None:
            raise ValueError("debate mode requires judge_adapter")
        return await _synthesize_debate(chains, original_prompt, judge_adapter)
    raise ValueError(f"unknown synthesis mode: {mode!r}")


def _synthesize_vote(chains: tuple[ChainResult, ...]) -> SynthesisOutput:
    """Trivial vote: return the longest response (proxy for 'most considered')."""
    if not chains:
        return SynthesisOutput(synthesized_answer="", metadata={"mode": "vote", "empty": True})
    winner = max(chains, key=lambda c: len(c.text))
    return SynthesisOutput(
        synthesized_answer=winner.text,
        metadata={
            "mode": "vote", "winner_backend": winner.backend,
            "winner_persona": winner.persona,
        },
    )


async def _synthesize_debate(
    chains: tuple[ChainResult, ...],
    original_prompt: str,
    judge_adapter: Adapter,
) -> SynthesisOutput:
    """Single-round debate: judge is shown chains framed as competing answers
    and asked to pick a winner, then to produce a refined synthesis."""
    chain_block = _format_chains_for_judge(chains)
    debate_prompt = (
        f"Original question:\n{original_prompt}\n\n"
        f"Competing answers:\n{chain_block}\n\n"
        "Step 1: identify which single chain is most correct and why. "
        "Step 2: produce a refined answer that incorporates valid points "
        "from other chains but is grounded in the chosen chain."
    )
    call = BackendCall(
        prompt=debate_prompt, system=_JUDGE_SYSTEM,
        max_tokens=2048, temperature=0.2,
    )
    try:
        resp = await judge_adapter.send(call)
    except Exception as exc:  # noqa: BLE001
        return SynthesisOutput(
            synthesized_answer=_fallback_concatenation(chains),
            metadata={
                "mode": "debate", "fallback": True,
                "fallback_reason": f"{type(exc).__name__}: {exc}",
            },
        )
    return SynthesisOutput(
        synthesized_answer=resp.text,
        metadata={
            "mode": "debate", "judge_model": judge_adapter.backend_id,
            "fallback": False,
        },
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_parallel_synthesis.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add tools/parallel/synthesis.py tests/test_parallel_synthesis.py
git commit -m "feat(multimodel): judge synthesis with fallback and debate stub"
```

---

### Task 11: Vote and debate synthesis tests

**Files:**
- Modify: `tests/test_parallel_synthesis.py`

- [ ] **Step 1: Add tests for vote and debate**

Append to `tests/test_parallel_synthesis.py`:

```python
@pytest.mark.asyncio
async def test_vote_returns_longest_chain():
    chains = (
        ChainResult("a", "analytical", "short", 0, 0),
        ChainResult("b", "creative", "a much longer and more thoughtful answer", 0, 0),
    )
    result = await synthesize(mode="vote", chains=chains, original_prompt="q")
    assert result.synthesized_answer == "a much longer and more thoughtful answer"
    assert result.metadata["winner_backend"] == "b"


@pytest.mark.asyncio
async def test_debate_calls_judge():
    chains = (
        ChainResult("a", "analytical", "option A", 0, 0),
        ChainResult("b", "creative", "option B", 0, 0),
    )
    judge = _StubJudge(reply="DEBATE_RESULT")
    result = await synthesize(
        mode="debate", chains=chains, original_prompt="q",
        judge_adapter=judge,
    )
    assert result.synthesized_answer == "DEBATE_RESULT"
    assert "Step 1" in judge.last_prompt
    assert "option A" in judge.last_prompt


@pytest.mark.asyncio
async def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown synthesis mode"):
        await synthesize(mode="bogus", chains=(), original_prompt="q")
```

- [ ] **Step 2: Run test to verify passes**

Run: `.venv/bin/pytest tests/test_parallel_synthesis.py -v`
Expected: 5 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_parallel_synthesis.py
git commit -m "test(multimodel): vote and debate synthesis coverage"
```

---

## Phase 5: Ledger, Public API, CLI

### Task 12: JSONL ledger writer and reader

**Files:**
- Create: `tools/parallel/ledger.py`
- Create: `tests/test_parallel_ledger.py`

- [ ] **Step 1: Write the failing test**

Write `tests/test_parallel_ledger.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.multimodel_parallel import ChainResult
from tools.parallel.ledger import LedgerEntry, write_entry, read_entries, summarize


def test_write_entry_appends_single_jsonl_line(tmp_path):
    ledger_dir = tmp_path / "ledger"
    entry = LedgerEntry(
        ledger_id="abc123", timestamp=datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
        prompt_hash="h1", config="cloud-balanced",
        chains=(ChainResult("bk1", "analytical", "t1", 0.01, 100),),
        synthesized_answer="final",
        synthesis_metadata={"judge_model": "claude", "fallback": False},
        total_cost_usd=0.01, total_latency_ms=150, dropped_chains=(),
    )
    path = write_entry(entry, ledger_dir=ledger_dir)
    assert path.exists()
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["ledger_id"] == "abc123"
    assert record["chains"][0]["text"] == "t1"


def test_read_entries_roundtrip(tmp_path):
    ledger_dir = tmp_path / "ledger"
    for i in range(3):
        entry = LedgerEntry(
            ledger_id=f"id-{i}",
            timestamp=datetime(2026, 4, 22, 12, i, tzinfo=timezone.utc),
            prompt_hash=f"h{i}", config="deep",
            chains=(),
            synthesized_answer="x", synthesis_metadata={},
            total_cost_usd=0.0, total_latency_ms=0, dropped_chains=(),
        )
        write_entry(entry, ledger_dir=ledger_dir)
    records = list(read_entries(ledger_dir=ledger_dir))
    assert len(records) == 3
    assert {r["ledger_id"] for r in records} == {"id-0", "id-1", "id-2"}


def test_summarize_aggregates_calls_and_cost(tmp_path):
    ledger_dir = tmp_path / "ledger"
    for i in range(3):
        write_entry(
            LedgerEntry(
                ledger_id=f"id-{i}",
                timestamp=datetime(2026, 4, 22, 12, i, tzinfo=timezone.utc),
                prompt_hash=f"h{i}", config="deep",
                chains=(ChainResult("bk", "analytical", "t", 0.05, 100),),
                synthesized_answer="x", synthesis_metadata={},
                total_cost_usd=0.05, total_latency_ms=100, dropped_chains=(),
            ),
            ledger_dir=ledger_dir,
        )
    summary = summarize(ledger_dir=ledger_dir)
    assert summary["call_count"] == 3
    assert summary["total_cost_usd"] == pytest.approx(0.15, rel=1e-6)


def test_read_entries_tolerates_malformed_line(tmp_path):
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir(parents=True)
    path = ledger_dir / "2026-04-22.jsonl"
    path.write_text(
        '{"ledger_id": "good", "chains": [], "timestamp": "2026-04-22T12:00:00+00:00"}\n'
        'not json at all\n'
        '{"ledger_id": "also-good", "chains": [], "timestamp": "2026-04-22T12:01:00+00:00"}\n'
    )
    records = list(read_entries(ledger_dir=ledger_dir))
    assert [r["ledger_id"] for r in records] == ["good", "also-good"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_parallel_ledger.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement ledger**

Write `tools/parallel/ledger.py`:

```python
"""JSONL ledger for every multimodel_parallel_query call.

One file per UTC day, named ``YYYY-MM-DD.jsonl``, under the ledger root.
Append-only. Reads tolerate malformed lines so one bad write cannot
corrupt the whole log.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from tools.multimodel_parallel import ChainResult


_DEFAULT_LEDGER_ROOT = Path.home() / ".crowe-logic" / "ledger" / "parallel"


def _resolve_root(ledger_dir: Path | None) -> Path:
    if ledger_dir is not None:
        return ledger_dir
    env_override = os.environ.get("CROWE_PARALLEL_LEDGER")
    if env_override:
        return Path(env_override)
    return _DEFAULT_LEDGER_ROOT


@dataclass(frozen=True)
class LedgerEntry:
    ledger_id: str
    timestamp: datetime
    prompt_hash: str
    config: str
    chains: tuple[ChainResult, ...]
    synthesized_answer: str
    synthesis_metadata: dict
    total_cost_usd: float
    total_latency_ms: int
    dropped_chains: tuple[str, ...]


def _entry_to_dict(entry: LedgerEntry) -> dict:
    return {
        "ledger_id": entry.ledger_id,
        "timestamp": entry.timestamp.astimezone(timezone.utc).isoformat(),
        "prompt_hash": entry.prompt_hash,
        "config": entry.config,
        "chains": [asdict(c) for c in entry.chains],
        "synthesized_answer": entry.synthesized_answer,
        "synthesis_metadata": entry.synthesis_metadata,
        "total_cost_usd": entry.total_cost_usd,
        "total_latency_ms": entry.total_latency_ms,
        "dropped_chains": list(entry.dropped_chains),
    }


def write_entry(entry: LedgerEntry, ledger_dir: Path | None = None) -> Path:
    root = _resolve_root(ledger_dir)
    root.mkdir(parents=True, exist_ok=True)
    day = entry.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d")
    path = root / f"{day}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_entry_to_dict(entry), ensure_ascii=False) + "\n")
    return path


def read_entries(ledger_dir: Path | None = None) -> Iterator[dict]:
    root = _resolve_root(ledger_dir)
    if not root.exists():
        return
    for path in sorted(root.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # tolerate corrupt lines


def summarize(ledger_dir: Path | None = None) -> dict:
    call_count = 0
    total_cost = 0.0
    total_latency = 0
    for record in read_entries(ledger_dir=ledger_dir):
        call_count += 1
        total_cost += float(record.get("total_cost_usd", 0))
        total_latency += int(record.get("total_latency_ms", 0))
    return {
        "call_count": call_count,
        "total_cost_usd": total_cost,
        "avg_latency_ms": (total_latency // call_count) if call_count else 0,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_parallel_ledger.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add tools/parallel/ledger.py tests/test_parallel_ledger.py
git commit -m "feat(multimodel): JSONL ledger writer, reader, summarizer"
```

---

### Task 13: Public `multimodel_parallel_query` end-to-end

**Files:**
- Modify: `tools/multimodel_parallel.py`
- Create: `tests/test_multimodel_parallel.py`

- [ ] **Step 1: Write the failing test**

Write `tests/test_multimodel_parallel.py`:

```python
import pytest

from tools.multimodel_parallel import multimodel_parallel_query
from tools.parallel.backends import Adapter, BackendCall, BackendResponse
from tools.parallel import dispatcher as _dispatcher


class _FakeAdapter(Adapter):
    def __init__(self, backend_id: str, text: str):
        self.backend_id = backend_id
        self._text = text

    async def send(self, call: BackendCall) -> BackendResponse:
        return BackendResponse(text=self._text, input_tokens=10, output_tokens=10, latency_ms=5)


@pytest.fixture
def fake_adapters(monkeypatch):
    def _build(backend, timeout_s=60.0):
        return _FakeAdapter(backend, f"response from {backend}")
    monkeypatch.setattr(_dispatcher, "build_adapter", _build)
    return _build


@pytest.mark.asyncio
async def test_end_to_end_cloud_balanced(tmp_path, monkeypatch, fake_adapters):
    monkeypatch.setenv("CROWE_PARALLEL_LEDGER", str(tmp_path / "ledger"))
    result = await multimodel_parallel_query(
        prompt="what is 2+2?",
        config="cloud-balanced",
        synthesis="vote",  # avoid needing a real judge here
        budget_usd=1.00,
        timeout_s=5.0,
    )
    assert len(result.chains) == 8
    assert result.synthesized_answer  # non-empty
    assert result.ledger_id
    assert result.dropped_chains == ()


@pytest.mark.asyncio
async def test_budget_gate_rejects_over_budget(tmp_path, monkeypatch, fake_adapters):
    from tools.parallel.costs import BudgetError
    monkeypatch.setenv("CROWE_PARALLEL_LEDGER", str(tmp_path / "ledger"))
    with pytest.raises(BudgetError):
        await multimodel_parallel_query(
            prompt="x", config="deep", synthesis="vote",
            budget_usd=0.00001,  # absurdly low
            timeout_s=5.0,
        )


@pytest.mark.asyncio
async def test_fast_preset_delegates_to_deepparallel(monkeypatch):
    # Patch deepparallel_query so we do not hit Ollama
    def _stub(prompt, system="", temperature=0.55, max_tokens=4096, reasoning_chains="all"):
        return "DEEPPARALLEL_OUT"
    monkeypatch.setattr("tools.deepparallel.deepparallel_query", _stub)
    result = await multimodel_parallel_query(
        prompt="hi", config="fast", synthesis="judge",
        budget_usd=0.50, timeout_s=5.0,
    )
    assert result.synthesized_answer == "DEEPPARALLEL_OUT"
    assert result.synthesis_metadata.get("mode") == "delegated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_multimodel_parallel.py -v`
Expected: FAIL. `multimodel_parallel_query` is not yet defined.

- [ ] **Step 3: Implement the public entry point**

Replace contents of `tools/multimodel_parallel.py`:

```python
"""multimodel_parallel_query: 8-chain parallel reasoning across heterogeneous backends."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from tools.parallel.configs import load_preset
from tools.parallel.costs import assert_within_budget


@dataclass(frozen=True)
class ChainResult:
    backend: str
    persona: str
    text: str
    cost_usd: float
    latency_ms: int
    error: str | None = None


@dataclass(frozen=True)
class ParallelResult:
    synthesized_answer: str
    chains: tuple[ChainResult, ...]
    synthesis_metadata: dict
    total_cost_usd: float
    total_latency_ms: int
    dropped_chains: tuple[str, ...]
    ledger_id: str


def _prompt_hash(prompt: str) -> str:
    return hashlib.blake2b(prompt.encode("utf-8"), digest_size=8).hexdigest()


async def multimodel_parallel_query(
    prompt: str,
    config: str = "cloud-balanced",
    synthesis: str = "judge",
    budget_usd: float = 0.50,
    timeout_s: float = 60.0,
    system: str = "",
    max_tokens: int = 2048,
    temperature: float = 0.55,
) -> ParallelResult:
    """Run 8 parallel reasoning chains, synthesize, and log to the ledger."""
    # Imports inside the function body to keep ``tools.multimodel_parallel``
    # importable before ``tools.parallel`` submodules are ready on a fresh clone.
    from tools.parallel.collector import collect_chains
    from tools.parallel.dispatcher import (
        build_adapter, estimate_total_cost, plan_chains,
    )
    from tools.parallel.ledger import LedgerEntry, write_entry
    from tools.parallel.synthesis import synthesize

    ledger_id = uuid.uuid4().hex
    preset = load_preset(config)

    # Delegation shortcut for the "fast" preset
    if preset.delegate_to_deepparallel:
        import asyncio
        from tools.deepparallel import deepparallel_query

        def _delegate() -> str:
            return deepparallel_query(
                prompt=prompt, system=system,
                temperature=temperature, max_tokens=max_tokens,
            )
        text = await asyncio.to_thread(_delegate)
        entry = LedgerEntry(
            ledger_id=ledger_id,
            timestamp=datetime.now(timezone.utc),
            prompt_hash=_prompt_hash(prompt),
            config=config,
            chains=(),
            synthesized_answer=text,
            synthesis_metadata={"mode": "delegated", "target": "deepparallel_query"},
            total_cost_usd=0.0, total_latency_ms=0, dropped_chains=(),
        )
        write_entry(entry)
        return ParallelResult(
            synthesized_answer=text,
            chains=(),
            synthesis_metadata=entry.synthesis_metadata,
            total_cost_usd=0.0, total_latency_ms=0,
            dropped_chains=(), ledger_id=ledger_id,
        )

    # Standard parallel path
    plan = plan_chains(preset)
    max_input_tokens = max(len(prompt) // 4, 128)  # rough estimate
    planned_cost = estimate_total_cost(plan, max_input_tokens, max_tokens)
    assert_within_budget(planned_cost_usd=planned_cost, budget_usd=budget_usd)

    per_chain_timeout = timeout_s / 2
    adapters = {
        chain.backend: build_adapter(chain.backend, timeout_s=per_chain_timeout)
        for chain in plan
    }

    t_start = datetime.now(timezone.utc)
    collected = await collect_chains(
        plan=plan, adapters=adapters,
        prompt=prompt, system=system,
        max_tokens=max_tokens, temperature=temperature,
        per_chain_timeout_s=per_chain_timeout,
    )

    if len(collected.chains) < 3:
        synthesized_answer = ""
        synth_metadata: dict = {
            "mode": synthesis,
            "synthesis": "skipped",
            "reason": f"only {len(collected.chains)} surviving chains",
        }
    else:
        judge_adapter = (
            build_adapter("claude-opus-4-7", timeout_s=per_chain_timeout)
            if synthesis in {"judge", "debate"} else None
        )
        synth = await synthesize(
            mode=synthesis, chains=collected.chains,
            original_prompt=prompt, judge_adapter=judge_adapter,
        )
        synthesized_answer = synth.synthesized_answer
        synth_metadata = synth.metadata

    total_latency_ms = int((datetime.now(timezone.utc) - t_start).total_seconds() * 1000)
    total_cost = sum(c.cost_usd for c in collected.chains)

    entry = LedgerEntry(
        ledger_id=ledger_id,
        timestamp=t_start,
        prompt_hash=_prompt_hash(prompt),
        config=config,
        chains=collected.chains,
        synthesized_answer=synthesized_answer,
        synthesis_metadata=synth_metadata,
        total_cost_usd=total_cost,
        total_latency_ms=total_latency_ms,
        dropped_chains=collected.dropped_chains,
    )
    write_entry(entry)

    return ParallelResult(
        synthesized_answer=synthesized_answer,
        chains=collected.chains,
        synthesis_metadata=synth_metadata,
        total_cost_usd=total_cost,
        total_latency_ms=total_latency_ms,
        dropped_chains=collected.dropped_chains,
        ledger_id=ledger_id,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_multimodel_parallel.py -v`
Expected: 3 passed

- [ ] **Step 5: Export from tools package**

Modify `tools/__init__.py` to add (find the exports block near the top):

```python
from tools.multimodel_parallel import (
    multimodel_parallel_query, ChainResult, ParallelResult,
)
```

Verify: `.venv/bin/python -c "from tools import multimodel_parallel_query; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add tools/multimodel_parallel.py tools/__init__.py tests/test_multimodel_parallel.py
git commit -m "feat(multimodel): end-to-end multimodel_parallel_query with ledger"
```

---

### Task 14: CLI subcommands (query, ledger, show)

**Files:**
- Create: `cli/parallel.py`
- Modify: `cli/crowe_logic.py`

- [ ] **Step 1: Write CLI module**

Write `cli/parallel.py`:

```python
"""`crowe-logic parallel` subcommand group.

Subcommands:
- ``query``: run one multimodel_parallel_query from the terminal.
- ``ledger``: print summary of the JSONL ledger.
- ``show``: print one ledger record by id.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from tools.multimodel_parallel import multimodel_parallel_query
from tools.parallel.ledger import read_entries, summarize

_console = Console()


@click.group(name="parallel")
def parallel_group() -> None:
    """Multimodel parallel reasoning (8-chain heterogeneous ensemble)."""


@parallel_group.command("query")
@click.argument("prompt", nargs=-1, required=True)
@click.option("--config", default="cloud-balanced", show_default=True,
              help="Preset name: fast, cloud-balanced, deep, max.")
@click.option("--synthesis", default="judge", type=click.Choice(["judge", "vote", "debate"]))
@click.option("--budget", "budget_usd", default=0.50, show_default=True, type=float)
@click.option("--timeout", "timeout_s", default=60.0, show_default=True, type=float)
@click.option("--json", "json_out", is_flag=True, help="Print full ParallelResult as JSON.")
def query_cmd(prompt, config, synthesis, budget_usd, timeout_s, json_out) -> None:
    full_prompt = " ".join(prompt)
    result = asyncio.run(multimodel_parallel_query(
        prompt=full_prompt, config=config, synthesis=synthesis,
        budget_usd=budget_usd, timeout_s=timeout_s,
    ))
    if json_out:
        payload = {
            "ledger_id": result.ledger_id,
            "synthesized_answer": result.synthesized_answer,
            "total_cost_usd": result.total_cost_usd,
            "total_latency_ms": result.total_latency_ms,
            "dropped_chains": list(result.dropped_chains),
            "synthesis_metadata": result.synthesis_metadata,
        }
        click.echo(json.dumps(payload, indent=2))
        return
    _console.rule(f"[bold]DeepParallel Multimodel ({config}, {synthesis})")
    _console.print(result.synthesized_answer)
    _console.rule()
    _console.print(
        f"[dim]ledger_id={result.ledger_id}  "
        f"cost=${result.total_cost_usd:.4f}  "
        f"latency={result.total_latency_ms}ms  "
        f"dropped={len(result.dropped_chains)}[/dim]"
    )


@parallel_group.command("ledger")
def ledger_cmd() -> None:
    summary = summarize()
    table = Table(title="DeepParallel Multimodel Ledger Summary")
    table.add_column("Metric"); table.add_column("Value")
    table.add_row("Total calls", str(summary["call_count"]))
    table.add_row("Total cost (USD)", f"${summary['total_cost_usd']:.4f}")
    table.add_row("Avg latency (ms)", str(summary["avg_latency_ms"]))
    _console.print(table)


@parallel_group.command("show")
@click.argument("ledger_id")
def show_cmd(ledger_id: str) -> None:
    for record in read_entries():
        if record.get("ledger_id") == ledger_id:
            click.echo(json.dumps(record, indent=2))
            return
    click.echo(f"ledger_id {ledger_id!r} not found", err=True)
    sys.exit(1)
```

- [ ] **Step 2: Register the group in the main CLI**

First, find the click group or dispatch point:

```bash
grep -nE "click\.group|@click\.group|add_command|click\.Group" cli/crowe_logic.py | head -20
```

If there is a `@click.group()` decorator defining the top-level group, add near the top of the file (after other imports):

```python
from cli.parallel import parallel_group
```

And right after the top-level group is defined, add:

```python
<top_level_group_name>.add_command(parallel_group)
```

If the file uses a custom dispatch dict (no `click.group`), find the dispatch map (look for a dict mapping subcommand names to functions) and add `"parallel": parallel_group` to it. Verify by running step 3.

- [ ] **Step 3: Smoke-test the CLI wiring**

Run: `.venv/bin/crowe-logic parallel --help`
Expected: help text listing `query`, `ledger`, `show`.

- [ ] **Step 4: Commit**

```bash
git add cli/parallel.py cli/crowe_logic.py
git commit -m "feat(multimodel): crowe-logic parallel CLI (query, ledger, show)"
```

---

## Phase 6: Live Integration and Calibration

### Task 15: Opt-in live integration test

**Files:**
- Create: `tests/test_parallel_live.py`

- [ ] **Step 1: Write opt-in live test**

Write `tests/test_parallel_live.py`:

```python
"""Live integration against real cloud backends.

Gated on CROWE_RUN_LIVE_TESTS=1 so CI does not pay for these calls.
Run manually with: .venv/bin/pytest tests/test_parallel_live.py -v
"""

import os

import pytest

from tools.multimodel_parallel import multimodel_parallel_query

pytestmark = pytest.mark.skipif(
    os.environ.get("CROWE_RUN_LIVE_TESTS") != "1",
    reason="live tests require CROWE_RUN_LIVE_TESTS=1",
)


@pytest.mark.asyncio
async def test_cloud_balanced_live():
    result = await multimodel_parallel_query(
        prompt="In one sentence, what does 'ensemble diversity' mean?",
        config="cloud-balanced", synthesis="judge",
        budget_usd=0.10, timeout_s=45.0, max_tokens=200,
    )
    assert result.synthesized_answer, "empty synthesis"
    assert len(result.chains) >= 3, f"only {len(result.chains)} surviving chains"
    assert result.total_cost_usd <= 0.10


@pytest.mark.asyncio
async def test_deep_live():
    result = await multimodel_parallel_query(
        prompt="Name one tradeoff of heterogeneous model ensembling.",
        config="deep", synthesis="judge",
        budget_usd=0.25, timeout_s=45.0, max_tokens=200,
    )
    assert result.synthesized_answer
    assert len(result.chains) >= 3
```

- [ ] **Step 2: Verify it skips without the env var**

Run: `.venv/bin/pytest tests/test_parallel_live.py -v`
Expected: 2 skipped.

- [ ] **Step 3: Run live against real endpoints (manual)**

Set the env: `export CROWE_RUN_LIVE_TESTS=1`
Ensure `ANTHROPIC_API_KEY`, `CROWE_OPEN_ENDPOINT`, `CROWE_OPEN_API_KEY`, `OLLAMA_BASE_URL` are set as needed.
Run: `.venv/bin/pytest tests/test_parallel_live.py -v`
Expected: 2 passed (or documented failures if a backend is down).

- [ ] **Step 4: Commit the test file (skipped by default in CI)**

```bash
git add tests/test_parallel_live.py
git commit -m "test(multimodel): opt-in live integration tests"
```

---

### Task 16: Cost calibration from live ledger

**Files:**
- Modify: `tools/parallel/costs.py` (update PRICES to calibrated values)
- Create: `docs/superpowers/notes/2026-04-22-parallel-cost-calibration.md`

- [ ] **Step 1: Collect 20 to 50 live ledger records**

With `CROWE_RUN_LIVE_TESTS=1` and env vars set, run a curated prompt set:

```bash
for i in 1 2 3 4 5; do
  .venv/bin/crowe-logic parallel query --config cloud-balanced --synthesis judge \
    "Explain one tradeoff in $i different words" --json
done
for i in 1 2 3; do
  .venv/bin/crowe-logic parallel query --config deep --synthesis judge \
    "Why use heterogeneous ensembling, attempt $i" --json
done
```

- [ ] **Step 2: Extract real prices from ledger**

Write and run a small one-off analysis (do not commit the script):

```python
from tools.parallel.ledger import read_entries
from collections import defaultdict

totals = defaultdict(lambda: {"cost": 0.0, "in_tok": 0, "out_tok": 0})
for r in read_entries():
    for c in r.get("chains", []):
        bk = c["backend"]
        # backend tokens are not stored directly; infer from cost and current PRICES
        totals[bk]["cost"] += c["cost_usd"]
for bk, t in totals.items():
    print(f"{bk}: ${t['cost']:.4f}")
```

Compare to vendor invoices for the same period. Adjust `PRICES` in `tools/parallel/costs.py` if any row diverges by more than 10 percent.

- [ ] **Step 3: Write calibration note**

Write `docs/superpowers/notes/2026-04-22-parallel-cost-calibration.md`:

```markdown
# DeepParallel Multimodel Cost Calibration (2026-04-22)

## Prompts used
[list the prompts from step 1]

## Observed per-chain costs
[paste table from step 2]

## Adjustments applied to costs.py
[note any PRICES changes]

## Notes for next calibration
[anything surprising]
```

- [ ] **Step 4: Commit**

```bash
git add tools/parallel/costs.py docs/superpowers/notes/2026-04-22-parallel-cost-calibration.md
git commit -m "feat(multimodel): calibrate PRICES from live ledger data"
```

---

## Phase 7: Valuation Report

### Task 17: Write valuation report from ledger data

**Files:**
- Create: `docs/superpowers/specs/2026-04-22-deepparallel-valuation.md`

- [ ] **Step 1: Extract ledger statistics**

Run a one-off analysis (can reuse the script from Task 16 step 2). Capture:

- Total calls per preset
- Median and p95 cost per preset
- Median and p95 latency per preset
- Per-backend failure rate
- Judge fallback rate
- Typical disagreement patterns (sample 5 to 10 records and read their `synthesis_metadata`)

- [ ] **Step 2: Draft the valuation document**

Write `docs/superpowers/specs/2026-04-22-deepparallel-valuation.md` following the structure defined in the design spec section "Valuation report (phase 7 deliverable)":

1. Product definition
2. Competitive landscape (OpenAI o1/o3 thinking, Claude extended thinking, Constitutional AI, Debate Game, Society of Minds)
3. Technical moat (heterogeneous ensembling, judge synthesis, cost-tiered configs, local-leg baseline)
4. Use cases and target customers (internal, license to other agent frameworks, verticals)
5. Cost model (real numbers from ledger step 1)
6. Revenue models (internal efficiency, SaaS per-call, per-seat license)
7. Comparables and valuation range (DCF with conservative usage projections)
8. Risk register (cost runaway, vendor rate limits, synthesis failure modes, model deprecation, GLM 5.1 Ollama gap, Claude pricing changes)

Each section is a short paragraph or short bulleted list. Target length: 1000 to 2000 words total.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-04-22-deepparallel-valuation.md
git commit -m "docs(specs): DeepParallel Multimodel valuation report"
```

---

## Self-Review Checklist (run after writing the plan)

- [ ] **Spec coverage:** every goal and non-goal in the spec is reflected in a task. Budget gate (Task 3), persona preservation (Task 2 and Task 7), judge synthesis default (Task 10), named presets (Task 4), fast-preset delegation (Task 13), ledger (Task 12), CLI (Task 14), calibration (Task 16), valuation (Task 17). Existing `deepparallel_query` is never modified.
- [ ] **Placeholder scan:** no "TBD", "TODO", "add appropriate X", or empty tests. Every step has complete code.
- [ ] **Type consistency:** `ChainResult`, `ParallelResult`, `PlannedChain`, `ChainSpec`, `Preset`, `LedgerEntry`, `BackendCall`, `BackendResponse`, `SynthesisOutput` used consistently across tasks. Function names (`plan_chains`, `estimate_total_cost`, `build_adapter`, `collect_chains`, `synthesize`, `write_entry`, `read_entries`, `summarize`, `multimodel_parallel_query`) stable across tasks.

## Notes for the implementer

- Use `.venv/bin/python` and `.venv/bin/pytest` directly. Do not rely on the `chpwd` hook; it does not fire under the Bash tool.
- Run `.venv/bin/ruff check tools/parallel tests` before every commit. Fix formatting with `.venv/bin/ruff format tools/parallel tests`.
- Keep each commit small. The plan produces roughly 17 commits.
- If any step fails unexpectedly, stop and investigate rather than patching around it. Silent failures here will invalidate the phase 7 valuation data.
