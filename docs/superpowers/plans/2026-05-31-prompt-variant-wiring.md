# Prompt-Variant Wiring Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every *chat* model in `MODEL_CHAIN` resolve to a real system-prompt file so routed turns stop silently falling back to a generic inline prompt.

**Architecture:** Three layers. (1) `slug_for` in `config/prompt_loader.py` derives a filesystem slug from a model config — already hardened to reject label-shaped aliases. (2) A new slug-normalization step collapses tier-suffixed duplicates (`-premium`, `-managed`, `-legacy`, `-super`, `-nano`, `-raw`, `titan-premium`→`titan`, etc.) onto their base persona so duplicates share one prompt. (3) Genuinely distinct chat tiers get authored `.md` files. A guardrail test enumerates every chat model and fails if any lacks a resolvable prompt, with an explicit non-chat exclusion set (embeddings, image/video, meta-router) so it never silently regresses.

**Tech Stack:** Python 3.13, pytest, `config/prompt_loader.py`, `config/system_prompts/*.md`, `config/agent_config.py` (`MODEL_CHAIN`, 72 entries).

**Baseline (measured 2026-05-31):** 72 models. After the `slug_for` fix (Task 0, already applied + verified): **21 resolve, 47 chat models missing, 4 non-chat excluded.** Goal end-state: all 68 chat models resolve; 4 non-chat excluded; guardrail test green.

---

## File Structure

- `config/prompt_loader.py` — add `_BASE_ALIAS` collapse map + `normalize_slug()`; `slug_for` returns the normalized slug. (modify)
- `config/system_prompts/<slug>.md` — author the missing base-persona files. (create, ~24 files)
- `tests/test_prompt_coverage.py` — the guardrail test: every chat model resolves; non-chat set excluded. (create)
- `config/agent_config.py` — read-only reference for `MODEL_CHAIN`; not modified unless a remap is cleaner as an alias edit (avoid; prefer loader-side normalization).

**Non-chat exclusion set (4, by backend `name`):** `Cohere-embed-v4` (Filament Pro), `text-embedding-3-large` (Embed Large), `sora-2` (Reel), `model-router` (Model Router). These are embeddings / video / meta-routing — no chat persona.

---

### Task 0: Harden `slug_for` (ALREADY APPLIED — verify only)

**Files:**
- Modify: `config/prompt_loader.py:32-52` (done)

- [ ] **Step 1: Verify the applied fix and coverage**

Run:
```bash
cd ~/Projects/crowe-logic-foundry
.venv/bin/python - <<'PY'
from config import agent_config as ac
from config.prompt_loader import slug_for, variant_prompt_text
chain = ac.MODEL_CHAIN
n = sum(1 for c in chain if variant_prompt_text(slug_for(c)))
print(f"coverage={n}/{len(chain)}")
assert n == 21, f"expected 21, got {n}"
print("OK")
PY
.venv/bin/ruff check config/prompt_loader.py
.venv/bin/python -m pytest tests/ -q -k "prompt or loader or slug" -p no:cacheprovider
```
Expected: `coverage=21/72`, `OK`, ruff `All checks passed!`, existing prompt tests pass.

- [ ] **Step 2: Commit the slug_for fix on its own**

```bash
git add config/prompt_loader.py
git commit -m "fix(prompt_loader): reject label-shaped aliases in slug_for

Aliases like 'CroweLM Frontier' (space + caps) were returned verbatim as
the slug, missing the existing frontier.md. Require filesystem-shaped
aliases (lowercase, no spaces, no provider separators); fall through to
the normalized-label path otherwise. Recovers 14 models (7->21), zero
regressions."
```

---

### Task 1: Guardrail test (the driver — RED first)

**Files:**
- Create: `tests/test_prompt_coverage.py`

- [ ] **Step 1: Write the failing test**

```python
"""Every chat model in MODEL_CHAIN must resolve to a real system-prompt file.

This is the contract that keeps routing wired: when a model is added or
rebranded, this test fails until a prompt file (or a base-alias collapse)
exists for it. Non-chat models (embeddings, image/video, meta-router) are
explicitly excluded — they have no chat persona.
"""
from config import agent_config as ac
from config.prompt_loader import slug_for, variant_prompt_text

# Backend names that are not chat personas and need no system prompt.
NONCHAT_BACKENDS = {
    "Cohere-embed-v4",          # CroweLM Filament Pro (embeddings)
    "text-embedding-3-large",   # CroweLM Embed Large (embeddings)
    "sora-2",                   # CroweLM Reel (video)
    "model-router",             # CroweLM Model Router (meta-routing)
}


def _chat_models():
    return [c for c in ac.MODEL_CHAIN if c.get("name") not in NONCHAT_BACKENDS]


def test_every_chat_model_resolves_to_a_prompt_file():
    missing = []
    for cfg in _chat_models():
        slug = slug_for(cfg)
        if not variant_prompt_text(slug):
            missing.append(f"{cfg.get('label')} (slug={slug!r}, name={cfg.get('name')})")
    assert not missing, "Chat models with no resolvable prompt:\n  " + "\n  ".join(missing)


def test_nonchat_backends_are_present_in_chain():
    # Guards the exclusion set against drift: if a name is removed from the
    # chain, drop it from NONCHAT_BACKENDS too.
    names = {c.get("name") for c in ac.MODEL_CHAIN}
    stale = NONCHAT_BACKENDS - names
    assert not stale, f"NONCHAT_BACKENDS lists models no longer in the chain: {stale}"
```

- [ ] **Step 2: Run it to confirm it fails listing the 47**

Run: `.venv/bin/python -m pytest tests/test_prompt_coverage.py -q -p no:cacheprovider`
Expected: FAIL — `test_every_chat_model_resolves_to_a_prompt_file` lists ~47 missing models. `test_nonchat_backends_are_present_in_chain` PASSES.

- [ ] **Step 3: Commit the failing guardrail (red)**

```bash
git add tests/test_prompt_coverage.py
git commit -m "test(prompts): guardrail that every chat model resolves to a prompt (red)"
```

---

### Task 2: Base-alias collapse for tier-suffixed duplicates

Variants that are the *same persona* differing only by tier/managed/legacy/raw/size should share their base prompt, not get bespoke files. Add a normalization layer to `prompt_loader`.

**Files:**
- Modify: `config/prompt_loader.py` (add `_BASE_ALIAS`, `normalize_slug`; call it in `slug_for`)
- Test: `tests/test_prompt_coverage.py` (add normalization unit test)

- [ ] **Step 1: Write the failing normalization test**

Add to `tests/test_prompt_coverage.py`:

```python
from config.prompt_loader import normalize_slug


def test_normalize_collapses_tier_suffixed_duplicates():
    assert normalize_slug("titan-premium") == "titan"
    assert normalize_slug("apex-premium") == "apex"
    assert normalize_slug("sovereign-premium") == "sovereign"
    assert normalize_slug("prime-premium") == "prime"
    assert normalize_slug("dense-managed") == "dense"
    assert normalize_slug("dense-legacy") == "dense"
    assert normalize_slug("talon-super") == "talon"
    assert normalize_slug("talon-nano") == "talon"
    assert normalize_slug("vanguard-super") == "vanguard"
    assert normalize_slug("vanguard-nano") == "vanguard"
    # base slugs pass through unchanged
    assert normalize_slug("titan") == "titan"
    assert normalize_slug("frontier") == "frontier"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_prompt_coverage.py::test_normalize_collapses_tier_suffixed_duplicates -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'normalize_slug'`.

- [ ] **Step 3: Implement `normalize_slug` and call it from `slug_for`**

In `config/prompt_loader.py`, after the module constants (around line 29), add:

```python
# Tier/size/lineage suffixes that denote the SAME persona as a base slug.
# Collapsed so e.g. titan-premium and titan share one prompt file.
_BASE_ALIAS = {
    "titan-premium": "titan",
    "apex-premium": "apex",
    "sovereign-premium": "sovereign",
    "prime-premium": "prime",
    "dense-managed": "dense",
    "dense-legacy": "dense",
    "talon-super": "talon",
    "talon-nano": "talon",
    "talon-vision": "talon",
    "crowelm-talon-nemoclaw": "talon",
    "vanguard-super": "vanguard",
    "vanguard-nano": "vanguard",
}


def normalize_slug(slug: str) -> str:
    """Collapse tier/size/lineage-suffixed slugs onto their base persona."""
    return _BASE_ALIAS.get(slug, slug)
```

Then change the end of `slug_for` so every return path is normalized. Replace the body's three `return` sites by wrapping the final result. Simplest: rename the existing function to `_raw_slug_for` and add:

```python
def slug_for(model_cfg: dict) -> str:
    """Canonical filesystem slug, with tier-suffixed duplicates collapsed."""
    return normalize_slug(_raw_slug_for(model_cfg))
```

(Rename the current `def slug_for(model_cfg: dict) -> str:` to `def _raw_slug_for(model_cfg: dict) -> str:` and keep its body unchanged.)

- [ ] **Step 4: Run normalization + coverage**

Run:
```bash
.venv/bin/python -m pytest tests/test_prompt_coverage.py::test_normalize_collapses_tier_suffixed_duplicates -q -p no:cacheprovider
.venv/bin/python - <<'PY'
from config import agent_config as ac
from config.prompt_loader import slug_for, variant_prompt_text
chain=ac.MODEL_CHAIN
n=sum(1 for c in chain if variant_prompt_text(slug_for(c)))
print(f"coverage={n}/{len(chain)}")
PY
.venv/bin/ruff check config/prompt_loader.py
```
Expected: normalization test PASS; coverage rises (talon/dense duplicates now resolve via existing files); ruff clean.

- [ ] **Step 5: Commit**

```bash
git add config/prompt_loader.py tests/test_prompt_coverage.py
git commit -m "feat(prompt_loader): collapse tier-suffixed variants onto base persona"
```

---

### Task 3: Author base-persona prompt files

Author one `.md` per genuinely distinct chat persona that still lacks a file. Each follows the house format of the existing files (read `config/system_prompts/frontier.md` and `eclipse.md` first as exemplars: a short identity paragraph, then behavioral guidance; `_base.md` is prepended automatically, so do NOT repeat base policy).

**Files (create each):**

Base personas (unlock their `-premium`/`-managed` duplicates via Task 2):
- `config/system_prompts/titan.md` — CroweLM Helio (gpt-5.4) flagship general tier
- `config/system_prompts/apex.md` — CroweLM Helio Pro (gpt-5.4-pro) deep-reasoning tier
- `config/system_prompts/sovereign.md` — CroweLM Sovereign (claude-opus-4-6-2)
- `config/system_prompts/prime.md` — CroweLM Prime (claude-opus-4-6)
- `config/system_prompts/vanguard.md` — CroweLM Vanguard family

Distinct singletons:
- `auto.md`, `talon.md` already? (talon exists via Task 2 only if a talon.md exists — it does NOT; author it), `nexus.md` (Hyphae Legacy / Kimi-K2.5), `reason.md` (DeepSeek-R1), `oracle.md` (Spire / grok-4-20-reasoning), `vector.md` (Cipher Legacy), `flux.md` (Flash), `helix.md` (MiniMax), `edge.md`, `atlas.md`, `forge.md` (Bastion), `nano.md` (Cinder, fast tier — terse), `kernel.md`, `grower.md` (cultivation persona), `classic.md`, `mycelium.md` (Gemma 4 Mycelium), `mike.md` (Mike Local voice persona), `unified.md`, `deepcore.md`, `kimi_k2_6.md` (Hyphae), `grok_4_3.md` (Crest), `swift.md`? (exists), `llama_4_maverick.md` (Maverick Raw), `llama_4_scout.md` (Scout), `codestral_2501.md` (Anvil — coding), `cohere_command_a.md` (Lattice), `deepseek_r1_0528.md` (Cipher), `gpt_5_4_mini.md` (Helio Mini), `gpt_chat_latest.md` (Chat), `grok_4_1_fast_non_r.md` + `grok_4_1_fast_reasoning.md` (Swift Raw/Reason — or remap both to `swift` via Task 2's map; PREFER remap), `talon-vision` (remapped to talon via Task 2).

> **Authoring rule:** For each missing chat model, FIRST decide remap vs bespoke. If it is a clear tier/lineage twin of an existing persona, add it to `_BASE_ALIAS` (Task 2) instead of writing a file. Only write a `.md` when the persona is genuinely distinct. Re-run the guardrail after each batch.

- [ ] **Step 1: Read the two exemplar files**

Run: `cat config/system_prompts/frontier.md config/system_prompts/eclipse.md`
Expected: see the house format (identity paragraph + guidance, no base-policy repetition).

- [ ] **Step 2: Author files in batches of ~8, re-running the guardrail after each batch**

For each batch, create the `.md` files, then:
```bash
.venv/bin/python -m pytest tests/test_prompt_coverage.py::test_every_chat_model_resolves_to_a_prompt_file -q -p no:cacheprovider
```
Expected: the `missing` list shrinks each batch.

- [ ] **Step 3: Final guardrail run — must be green**

Run: `.venv/bin/python -m pytest tests/test_prompt_coverage.py -q -p no:cacheprovider`
Expected: PASS (0 missing chat models).

- [ ] **Step 4: Full coverage assertion**

Run:
```bash
.venv/bin/python - <<'PY'
from config import agent_config as ac
from config.prompt_loader import slug_for, variant_prompt_text
NONCHAT={"Cohere-embed-v4","text-embedding-3-large","sora-2","model-router"}
chain=[c for c in ac.MODEL_CHAIN if c.get("name") not in NONCHAT]
miss=[c.get("label") for c in chain if not variant_prompt_text(slug_for(c))]
print("missing:", miss or "NONE")
assert not miss
print(f"ALL {len(chain)} chat models resolve")
PY
```
Expected: `missing: NONE`, all chat models resolve.

- [ ] **Step 5: Commit the prompt files**

```bash
git add config/system_prompts/*.md config/prompt_loader.py tests/test_prompt_coverage.py
git commit -m "feat(prompts): author missing chat-variant system prompts; guardrail green"
```

---

### Task 4: Reinstall and live-verify

The running `crowe-logic` is the **pipx install** (`~/.local/pipx/venvs/crowe-logic/`), not the repo. Changes only take effect after reinstall.

**Files:** none (deployment)

- [ ] **Step 1: Reinstall from the repo**

Run: `uv tool install --force --python 3.13 ~/Projects/crowe-logic-foundry` (or the project's documented install command if pipx-managed: `pipx install --force ~/Projects/crowe-logic-foundry`)
Expected: `Installed ... crowe-logic`.

- [ ] **Step 2: Confirm no fallback warnings on the default route**

Run: `printf 'hello\n/quit\n' | crowe-logic 2>&1 | grep -c 'no file for variant' || true`
Expected: `0` — no `[prompt_loader] no file for variant ...` lines.

- [ ] **Step 3: Spot-check a previously-broken variant**

Run: `printf '/model titan\nhello\n/quit\n' | crowe-logic 2>&1 | grep -c 'no file for variant' || true`
Expected: `0`.

---

## Self-Review Notes

- **Spec coverage:** Task 0 = slug_for (logic bug, ~14 models). Task 1 = guardrail driver. Task 2 = duplicate collapse (DRY, ~12 variants). Task 3 = bespoke authoring (remaining distinct personas). Task 4 = the install-vs-repo gap that caused the original confusion. All three requested remediation layers (1 logic, 2 active tiers, 3 full coverage + guardrail) are covered.
- **Non-chat exclusion** is explicit and self-guarding (Task 1 step 2 test catches drift).
- **Remap-vs-bespoke** is a judgment call per variant; Task 3's authoring rule makes remap the default for twins, keeping the file count down.
- **Known ambiguity to resolve during execution:** whether the `-premium`/`-managed` tiers should eventually carry *distinct* prompts (e.g. stricter guardrails for managed). For now they share the base; revisit if product wants tier-differentiated behavior.
