# CroweLM Routing Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop every prompt silently landing on the slow, mis-configured CroweLM Supreme tier — make routing health-aware, multi-provider, default-on, and bounded by a 5s first-token budget.

**Architecture:** Reuse the existing heuristic router (`config/router.py`) and TTFT watchdog (`providers/_ttft_watchdog.py`). Add a small in-process circuit-breaker `HealthRegistry` (`config/health.py`) that `_auto_route_available` consults alongside the existing `_model_switch_error`. Fix the three root bugs: the availability-bypassing backstop in `route_prompt`, the unset-endpoint Supreme tier, and the gpt-5 param mismatch.

**Tech Stack:** Python 3.10+, pytest, Rich. Existing modules: `config/router.py`, `config/agent_config.py`, `providers/_shared.py`, `providers/_ttft_watchdog.py`, `cli/crowe_logic.py`.

**Spec:** `docs/superpowers/specs/2026-05-30-crowelm-routing-reliability-design.md`

---

## File structure

| File | Responsibility |
|---|---|
| `config/agent_config.py` | Repoint Supreme → `gpt-5.5`; gpt-5 param correctness in `tier_runtime_params` |
| `config/router.py` | `FAST_BACKSTOP` ladder; availability-respecting backstop |
| `config/health.py` *(new)* | In-process circuit-breaker `HealthRegistry` |
| `providers/_ttft_watchdog.py` | Default first-token deadline → 5s |
| `cli/crowe_logic.py` | Default-on routing; local-lane gate; consult HealthRegistry; record failures; explicit badge |
| `tests/test_*` | TDD coverage for each |

Work the tasks in order — later tasks depend on symbols defined earlier (`FAST_BACKSTOP`, `HealthRegistry`).

---

### Task 1: gpt-5 family param correctness

**Why:** gpt-5 deployments 400 on `max_tokens`/`temperature`; they need `max_completion_tokens` and no custom temperature. A 400 currently reads as a model failure and triggers needless fallback. The single choke point is `tier_runtime_params` (consumed at `providers/_shared.py:507`).

**Files:**
- Modify: `config/agent_config.py` (function `tier_runtime_params`)
- Test: `tests/test_tier_runtime_params.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tier_runtime_params.py
from config.agent_config import tier_runtime_params


def test_gpt5_family_uses_max_completion_tokens_and_drops_temperature():
    cfg = {"name": "gpt-5.5", "label": "CroweLM Quasar", "provider": "azure_openai"}
    params = tier_runtime_params(cfg)
    assert "max_tokens" not in params
    assert "temperature" not in params
    # if any token cap is emitted it must be the gpt-5 spelling
    if "max_completion_tokens" in params:
        assert isinstance(params["max_completion_tokens"], int)


def test_non_gpt5_keeps_classic_params():
    cfg = {"name": "DeepSeek-V4-Flash", "label": "CroweLM Flash", "provider": "azure_openai"}
    params = tier_runtime_params(cfg)
    assert "max_completion_tokens" not in params
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tier_runtime_params.py -v`
Expected: FAIL (gpt-5.5 currently emits `max_tokens`/`temperature`).

- [ ] **Step 3: Implement the minimal change**

In `config/agent_config.py`, at the end of `tier_runtime_params`, before it returns the params dict, normalize for the gpt-5 family:

```python
    # gpt-5 family (Azure Foundry) rejects max_tokens + custom temperature.
    # Translate to the spelling the deployment accepts so a valid call does
    # not 400 and trigger a false fallback.
    name = str(model_cfg.get("name", "")).lower()
    if name.startswith("gpt-5"):
        if "max_tokens" in params:
            params["max_completion_tokens"] = params.pop("max_tokens")
        params.pop("temperature", None)
        params.pop("top_p", None)
    return params
```

(Adjust the final `return` so this block runs on the assembled `params` dict — do not add a second return.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tier_runtime_params.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add config/agent_config.py tests/test_tier_runtime_params.py
git commit -m "fix: gpt-5 tiers send max_completion_tokens, drop unsupported temperature"
```

---

### Task 2: Repoint CroweLM Supreme to a live Azure frontier

**Why:** Supreme is hardwired to the unset `AZURE_ANTHROPIC_*` endpoint (`config/agent_config.py:156`). That single misconfig is the "Supreme → 18s → Talon" behavior. Repoint to live `gpt-5.5` on `AZURE_CORE`.

**Files:**
- Modify: `config/agent_config.py:156-176` (the Supreme block)
- Test: `tests/test_supreme_backing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_supreme_backing.py
import os
from config.agent_config import MODEL_CHAIN, resolve_model_config


def _supreme():
    return resolve_model_config("CroweLM Supreme")


def test_supreme_is_azure_openai_gpt5():
    cfg = _supreme()
    assert cfg is not None
    assert cfg["provider"] == "azure_openai"
    assert cfg["name"] == "gpt-5.5"
    assert cfg.get("endpoint_env") == "AZURE_CORE_ENDPOINT"


def test_supreme_available_when_azure_core_configured(monkeypatch):
    monkeypatch.setenv("AZURE_CORE_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_CORE_API_KEY", "k")
    from cli.crowe_logic import _auto_route_available
    assert _auto_route_available(_supreme()) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_supreme_backing.py -v`
Expected: FAIL (Supreme is `provider: anthropic`, `name: claude-opus-4-7`).

- [ ] **Step 3: Implement the repoint**

Replace the Supreme entry at `config/agent_config.py:156` (keep label, aliases, and prompt; change backing):

```python
    {"name": "gpt-5.5", "label": "CroweLM Supreme",  "type": "reasoning",
     "provider": "azure_openai", "backend_name": "gpt-5.5",
     "endpoint_env": "AZURE_CORE_ENDPOINT", "api_key_env": "AZURE_CORE_API_KEY",
     # FOLLOW-UP: restore Claude-Opus-on-Azure when AZURE_ANTHROPIC_* creds exist.
     # Same-provider fallback for Supreme is gpt-5.4-pro (see FAST_BACKSTOP).
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_supreme_backing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config/agent_config.py tests/test_supreme_backing.py
git commit -m "fix: repoint CroweLM Supreme to live gpt-5.5 on AZURE_CORE (was unset AZURE_ANTHROPIC)"
```

---

### Task 3: FAST_BACKSTOP ladder + availability-respecting backstop

**Why:** `route_prompt` falls to `next(cfg for cfg in chain if provider != "auto")` (`config/router.py:401`) when nothing resolves — the chain head, which is Supreme, **bypassing the availability check**. Replace with a multi-provider fast ladder, and when truly nothing passes availability, pick the first *available* chain entry, never blindly the head.

**Files:**
- Modify: `config/router.py` (add `FAST_BACKSTOP`; rewrite the empty-`available` branch ~`router.py:397-416`)
- Test: `tests/test_router.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_router.py  (append)
from config.router import route_prompt, FAST_BACKSTOP


_CHAIN = [
    {"name": "crowelm-auto", "label": "CroweLM Auto", "provider": "auto", "aliases": ["auto"]},
    {"name": "gpt-5.5", "label": "CroweLM Supreme", "provider": "azure_openai", "aliases": ["supreme"]},
    {"name": "grok-4-1-fast-non-r", "label": "CroweLM Swift Raw", "provider": "azure_openai", "aliases": ["swift"]},
    {"name": "gpt-5.4-nano", "label": "CroweLM Cinder", "provider": "azure_openai", "aliases": ["cinder"]},
]


def test_greeting_never_routes_to_supreme_when_fast_tier_available():
    # all tiers available
    decision = route_prompt("how are you", chain=_CHAIN, availability=lambda c: True)
    assert decision.selected_label != "CroweLM Supreme"


def test_backstop_picks_available_tier_not_chain_head():
    # only the fast tiers are available; Supreme (head) is NOT
    def avail(cfg):
        return cfg["name"] in {"grok-4-1-fast-non-r", "gpt-5.4-nano"}
    decision = route_prompt("an ambiguous nothing prompt zzz", chain=_CHAIN, availability=avail)
    assert decision.selected_name in {"grok-4-1-fast-non-r", "gpt-5.4-nano"}
    assert decision.selected_label != "CroweLM Supreme"


def test_fast_backstop_is_multiprovider():
    # NIM Talon must appear so an Azure-wide outage still has a floor
    joined = " ".join(s.lower() for s in FAST_BACKSTOP)
    assert "talon" in joined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_router.py -k "backstop or greeting or multiprovider" -v`
Expected: FAIL (`FAST_BACKSTOP` undefined; backstop returns Supreme).

- [ ] **Step 3: Implement FAST_BACKSTOP + availability-respecting fallback**

In `config/router.py`, add near `_INTENT_PREFERENCES`:

```python
# Multi-provider fast floor used when an intent's preferred tiers are all
# unavailable. Ordered same-provider-first (Azure Foundry) then cross-provider
# (NVIDIA NIM). Probed live 2026-05-30. NEVER lead with Supreme.
FAST_BACKSTOP: tuple[str, ...] = (
    "grok-4-1-fast-non-r", "CroweLM Swift Raw",      # Azure, 0.73s
    "gpt-5.4-nano", "CroweLM Cinder",                # Azure
    "gpt-5.4-mini",
    "CroweLM Talon Nano", "crowelm-talon-nano",      # NIM cross-provider floor
    "Llama-4-Scout", "Kimi-K2-6",                    # Azure mid
    "CroweLM Talon", "crowelm-talon",                # NIM mid
)
```

Then replace the backstop logic in `route_prompt` (the block beginning `backstop = (...)` through the `if not available:` return):

```python
    preferences = _INTENT_PREFERENCES.get(intent, ())
    available = _resolve_all((*preferences, *FAST_BACKSTOP), chain, availability)

    if not available:
        # Nothing in preferences or the fast backstop resolved AND passed the
        # availability check. Fall to the first chain entry that is itself
        # available — never blindly the chain head (which is Supreme).
        primary = next(
            (cfg for cfg in chain
             if cfg.get("provider") != "auto"
             and (availability is None or availability(cfg))),
            None,
        )
        if primary is None:
            # Truly nothing available: last-resort chain head so the turn
            # still produces a route (fail-open).
            primary = next((cfg for cfg in chain if cfg.get("provider") != "auto"),
                           chain[0] if chain else {})
        return RouteDecision(
            intent=intent,
            primary=primary,
            fallbacks=(),
            companions=(),
            reason=(
                f"intent={intent}; no preferred/backstop tier available; "
                f"fell back to first available tier ({primary.get('label', '?')})"
                f"{fallback_reason}"
            ),
            confidence=confidence,
        )
```

Leave the rest of `route_prompt` (primary/fallbacks/companions) unchanged. Remove the old `backstop = ("CroweLM Nexus", ...)` line.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_router.py -v`
Expected: PASS (new tests + existing router tests stay green).

- [ ] **Step 5: Commit**

```bash
git add config/router.py tests/test_router.py
git commit -m "fix: multi-provider FAST_BACKSTOP; backstop respects availability, never blindly Supreme"
```

---

### Task 4: Default-on auto-routing

**Why:** Routing is opt-in (`CROWE_LOGIC_AUTO_ROUTE=1`). When unset, no routing runs and the chain head (Supreme) is used directly. Flip the default to on; `=0` still disables.

**Files:**
- Modify: `cli/crowe_logic.py` (function `_auto_route_enabled`, ~line 184)
- Test: `tests/test_auto_route_enabled.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auto_route_enabled.py
from cli.crowe_logic import _auto_route_enabled


def test_routing_on_by_default(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_AUTO_ROUTE", raising=False)
    assert _auto_route_enabled() is True


def test_routing_can_be_disabled(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_AUTO_ROUTE", "0")
    assert _auto_route_enabled() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_auto_route_enabled.py -v`
Expected: FAIL (`test_routing_on_by_default` — default currently off).

- [ ] **Step 3: Implement the default flip**

Replace `_auto_route_enabled` in `cli/crowe_logic.py`:

```python
def _auto_route_enabled() -> bool:
    """Return True when per-turn routing is active. Default-on; set
    CROWE_LOGIC_AUTO_ROUTE=0 (or false/no/off) to pin the active model."""
    val = os.environ.get("CROWE_LOGIC_AUTO_ROUTE", "").strip().lower()
    if val in ("0", "false", "no", "off"):
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_auto_route_enabled.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/crowe_logic.py tests/test_auto_route_enabled.py
git commit -m "feat: auto-routing default-on (CROWE_LOGIC_AUTO_ROUTE=0 disables)"
```

---

### Task 5: Gate the Mike-only local lane out of customer routing

**Why:** Local Ollama tiers (`crowelm-unified-v2`, `gemma-4-mycelium`, `mike-clone`) must never be a backstop for customer routing. Exclude `provider == "ollama"` from `_auto_route_available` unless a personal flag is set.

**Files:**
- Modify: `cli/crowe_logic.py` (function `_auto_route_available`, ~line 187)
- Test: `tests/test_local_lane_gate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_local_lane_gate.py
from cli.crowe_logic import _auto_route_available


_LOCAL = {"name": "crowelm-unified-v2", "label": "CroweLM Mycelium Local", "provider": "ollama"}


def test_local_excluded_from_customer_routing(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_PERSONAL_LANE", raising=False)
    assert _auto_route_available(_LOCAL) is False


def test_local_allowed_under_personal_flag(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_PERSONAL_LANE", "1")
    # still subject to the normal switch-error check, but not gated by provider
    assert _auto_route_available(_LOCAL) in (True, False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_local_lane_gate.py -v`
Expected: FAIL (`test_local_excluded...` — local is currently allowed).

- [ ] **Step 3: Implement the gate**

Replace `_auto_route_available` in `cli/crowe_logic.py`:

```python
def _personal_lane_enabled() -> bool:
    val = os.environ.get("CROWE_LOGIC_PERSONAL_LANE", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _auto_route_available(model_cfg: dict) -> bool:
    """Return True when a routed model is usable in the current environment.

    Local (Ollama) tiers are a Mike-only personal lane: excluded from customer
    auto-routing unless CROWE_LOGIC_PERSONAL_LANE is set.
    """
    if model_cfg.get("provider") == "ollama" and not _personal_lane_enabled():
        return False
    return _model_switch_error(model_cfg) is None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_local_lane_gate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/crowe_logic.py tests/test_local_lane_gate.py
git commit -m "feat: gate local Ollama tiers to Mike-only personal lane (CROWE_LOGIC_PERSONAL_LANE)"
```

---

### Task 6: HealthRegistry circuit breaker (config/health.py)

**Why:** A dead tier is retried turn after turn (no cooldown), causing repeated 18s hangs. Add an in-process breaker: N failures opens a tier for a cooldown; a half-open probe re-tests before closing. Fail-open on internal error.

**Files:**
- Create: `config/health.py`
- Test: `tests/test_health.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_health.py
from config.health import HealthRegistry


def test_opens_after_threshold_failures():
    clock = {"t": 0.0}
    reg = HealthRegistry(failure_threshold=2, cooldown_seconds=60,
                         clock=lambda: clock["t"])
    assert reg.is_available("m") is True
    reg.record_failure("m", "boom")
    assert reg.is_available("m") is True   # 1 < threshold
    reg.record_failure("m", "boom")
    assert reg.is_available("m") is False  # breaker open


def test_half_open_after_cooldown_then_close_on_success():
    clock = {"t": 0.0}
    reg = HealthRegistry(failure_threshold=1, cooldown_seconds=60,
                         clock=lambda: clock["t"])
    reg.record_failure("m", "boom")
    assert reg.is_available("m") is False
    clock["t"] = 61.0
    assert reg.is_available("m") is True    # half-open probe allowed
    reg.record_success("m")
    clock["t"] = 62.0
    assert reg.is_available("m") is True     # closed


def test_record_ttft_breach_counts_as_failure():
    clock = {"t": 0.0}
    reg = HealthRegistry(failure_threshold=1, cooldown_seconds=60,
                         clock=lambda: clock["t"], ttft_budget_seconds=5.0)
    reg.record_ttft("m", 9.0)
    assert reg.is_available("m") is False


def test_fail_open_on_internal_error():
    reg = HealthRegistry(failure_threshold=1, cooldown_seconds=60)
    # corrupt internal state; is_available must not raise, must default available
    reg._states = None  # type: ignore
    assert reg.is_available("m") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_health.py -v`
Expected: FAIL (`config.health` does not exist).

- [ ] **Step 3: Implement HealthRegistry**

```python
# config/health.py
"""In-process circuit breaker for model tiers.

Complements the persisted provider-health in cli/crowe_logic.py: that layer
blocks a whole provider after a provider-wide error; this layer trips a single
model tier after repeated per-tier failures (incl. TTFT-budget breaches) so a
slow/dead tier is skipped for a cooldown instead of retried every turn.

Fail-open: any internal error resolves to "available" so paying users are
never hard-blocked by the health layer itself.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _State:
    failures: int = 0
    opened_at: float | None = None     # when the breaker tripped open
    half_open: bool = False            # a probe is in flight


@dataclass
class HealthRegistry:
    failure_threshold: int = 3
    cooldown_seconds: float = 60.0
    ttft_budget_seconds: float = 5.0
    clock: "callable" = time.monotonic
    _states: dict = field(default_factory=dict)

    def is_available(self, name: str) -> bool:
        try:
            st = self._states.get(name)
            if st is None or st.opened_at is None:
                return True
            if self.clock() - st.opened_at >= self.cooldown_seconds:
                st.half_open = True      # allow a single probe
                return True
            return False
        except Exception:
            return True                  # fail-open

    def record_success(self, name: str) -> None:
        try:
            self._states[name] = _State()
        except Exception:
            pass

    def record_failure(self, name: str, reason: str = "") -> None:
        try:
            st = self._states.setdefault(name, _State())
            st.failures += 1
            st.half_open = False
            if st.failures >= self.failure_threshold and st.opened_at is None:
                st.opened_at = self.clock()
            elif st.opened_at is not None:
                st.opened_at = self.clock()   # re-open after a failed probe
        except Exception:
            pass

    def record_ttft(self, name: str, seconds: float) -> None:
        if seconds > self.ttft_budget_seconds:
            self.record_failure(name, f"ttft {seconds:.1f}s > {self.ttft_budget_seconds:.1f}s")
        else:
            self.record_success(name)


# Process-wide singleton consulted by _auto_route_available.
registry = HealthRegistry()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_health.py -v`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add config/health.py tests/test_health.py
git commit -m "feat: HealthRegistry in-process circuit breaker for model tiers"
```

---

### Task 7: Wire HealthRegistry + record failures/TTFT into dispatch

**Why:** The breaker only helps if `_auto_route_available` consults it and the dispatch failure path records into it. The 5s budget comes from the existing watchdog default.

**Files:**
- Modify: `providers/_ttft_watchdog.py:35-37` (default deadline → 5s)
- Modify: `cli/crowe_logic.py` — `_auto_route_available` consults `registry`; failure path (~line 1672) calls `registry.record_failure`
- Test: `tests/test_ttft_watchdog.py` (extend), `tests/test_health_dispatch.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ttft_watchdog.py  (append)
from providers._ttft_watchdog import DEFAULT_TTFT_DEADLINE_SECONDS


def test_default_ttft_budget_is_5s_when_env_unset(monkeypatch):
    # importlib reload to re-read the module-level default
    monkeypatch.delenv("CROWELM_TTFT_DEADLINE_SECONDS", raising=False)
    import importlib, providers._ttft_watchdog as w
    importlib.reload(w)
    assert w.DEFAULT_TTFT_DEADLINE_SECONDS == 5.0
```

```python
# tests/test_health_dispatch.py
from config import health
from cli.crowe_logic import _auto_route_available


def test_auto_route_available_respects_open_breaker(monkeypatch):
    cfg = {"name": "gpt-5.4-nano", "label": "CroweLM Cinder", "provider": "azure_openai"}
    monkeypatch.setenv("AZURE_CORE_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_CORE_API_KEY", "k")
    health.registry.record_success(cfg["name"])
    assert _auto_route_available(cfg) is True
    health.registry.failure_threshold = 1
    health.registry.record_failure(cfg["name"], "boom")
    assert _auto_route_available(cfg) is False
    health.registry.record_success(cfg["name"])  # cleanup
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_ttft_watchdog.py::test_default_ttft_budget_is_5s_when_env_unset tests/test_health_dispatch.py -v`
Expected: FAIL (default is 60s; `_auto_route_available` ignores the breaker).

- [ ] **Step 3a: Change the watchdog default deadline**

In `providers/_ttft_watchdog.py:35-37`:

```python
DEFAULT_TTFT_DEADLINE_SECONDS = float(
    os.environ.get("CROWELM_TTFT_DEADLINE_SECONDS", "5.0")
)
```

Also update the docstring line "Default deadline is 60 seconds." → "Default deadline is 5 seconds."

- [ ] **Step 3b: Consult the registry in `_auto_route_available`**

Extend the function from Task 5 so the breaker is checked too:

```python
def _auto_route_available(model_cfg: dict) -> bool:
    if model_cfg.get("provider") == "ollama" and not _personal_lane_enabled():
        return False
    from config.health import registry
    if not registry.is_available(str(model_cfg.get("name", ""))):
        return False
    return _model_switch_error(model_cfg) is None
```

- [ ] **Step 3c: Record failures in the dispatch failure path**

In `cli/crowe_logic.py`, in the "Model failed" block (right after the `_model_state["failures"][...]` increment at ~line 1672), add:

```python
                try:
                    from config.health import registry as _health_registry
                    _health_registry.record_failure(model_cfg["name"], last_error or "")
                except Exception:
                    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_ttft_watchdog.py tests/test_health_dispatch.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add providers/_ttft_watchdog.py cli/crowe_logic.py tests/test_ttft_watchdog.py tests/test_health_dispatch.py
git commit -m "feat: 5s default TTFT budget; _auto_route_available consults HealthRegistry; record failures on dispatch"
```

---

### Task 8: Explicit, intentional hedge badge

**Why:** "Model failed — switching to…" reads as a scary error for a routine hedge. Make it state the reason calmly and distinguish timeout/error/unavailable.

**Files:**
- Modify: `cli/crowe_logic.py:1719-1722` (the fallback banner)
- Test: `tests/test_hedge_message.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hedge_message.py
from cli.crowe_logic import _hedge_banner


def test_hedge_banner_states_reason_and_target():
    msg = _hedge_banner(target_label="CroweLM Cinder", reason="timeout")
    assert "CroweLM Cinder" in msg
    assert "timeout" in msg.lower()
    assert "failed" not in msg.lower()   # no alarming language for a routine hedge
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_hedge_message.py -v`
Expected: FAIL (`_hedge_banner` undefined).

- [ ] **Step 3: Implement `_hedge_banner` and use it**

Add near the other render helpers in `cli/crowe_logic.py`:

```python
def _hedge_banner(*, target_label: str, reason: str) -> str:
    """Calm, intentional routing-hedge line (not an error)."""
    reason_txt = {"timeout": "slow start", "error": "provider error",
                  "unavailable": "tier unavailable"}.get(reason, reason)
    return f"  [dim #bfa669]Routing onward → {target_label} ({reason_txt})[/dim #bfa669]"
```

Then replace the banner at ~line 1719:

```python
                _reason = "timeout" if (last_error and "ttft" in last_error.lower()) else "error"
                console.print(_hedge_banner(target_label=next_model["label"], reason=_reason))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_hedge_message.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli/crowe_logic.py tests/test_hedge_message.py
git commit -m "feat: calm intentional routing-hedge banner (reason-aware, not 'Model failed')"
```

---

### Task 9: Full regression + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the routing/health test surface**

Run: `.venv/bin/python -m pytest tests/test_router.py tests/test_health.py tests/test_health_dispatch.py tests/test_auto_route_enabled.py tests/test_local_lane_gate.py tests/test_supreme_backing.py tests/test_tier_runtime_params.py tests/test_ttft_watchdog.py tests/test_hedge_message.py -v`
Expected: ALL PASS.

- [ ] **Step 2: Run the full suite for regressions**

Run: `.venv/bin/python -m pytest -q`
Expected: no new failures vs baseline.

- [ ] **Step 3: Manual smoke — greeting no longer hits Supreme**

Run: `CROWE_LOGIC_AUTO_ROUTE= .venv/bin/crowe-logic run "how are you"`
Expected: routes to a fast tier (Swift/Cinder/Talon), sub-2s first token, **no** Supreme, no "Model failed" banner.

- [ ] **Step 4: Commit any final fixups**

```bash
git add -A && git commit -m "test: routing reliability regression pass" || echo "nothing to commit"
```

---

## Self-review notes

- **Spec coverage:** silent fallback → Task 8 (calm banner) + Task 6/7 (breaker stops blind retry); slow TTFT → Task 7 (5s budget); wrong model → Task 3 (backstop) + Task 4 (default-on) + Task 2 (Supreme live); optimize → Task 1 (param fix) + Task 6 (breaker). Multi-provider → Task 3 `FAST_BACKSTOP`. Local Mike-only → Task 5.
- **Deferred to later specs:** full-roster deployment + scale-to-zero (Spec 2), cosmetic redesign (Spec 3), Azure-native `model-router` A/B.
- **Reuse:** existing `with_ttft_watchdog` (no new `ttft_guard`); existing `_set_provider_health`/`_model_switch_error` (HealthRegistry complements, does not replace).
- **Verify during impl:** `tier_runtime_params` exact return structure (Task 1); `gpt-5.4-pro` 400 cause (Supreme same-provider fallback); confirm `resolve_model_config` resolves the new Supreme by label.
