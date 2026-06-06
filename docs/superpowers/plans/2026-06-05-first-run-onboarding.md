# First-Run Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fresh zero-cred `crowe-logic` gets a clean onboarding card and (Phase 2) a real free-tier model response via an anonymous device token, instead of today's provider-error wall.

**Architecture:** Gateway-first thin client. All policy (caps, tier mapping, upsell copy) lives in the control plane behind `api.crowelogic.com`. New CLI logic lives in `cli/first_run.py` + small extensions to `cli/gateway_client.py`; `cli/crowe_logic.py` (4,142-line monolith) gains only thin hooks. Control plane gains an anonymous-device principal beside the existing Crowe ID / PAT principals.

**Tech Stack:** Python 3.11+, click, rich, httpx (CLI); FastAPI, asyncpg-style `Database` helper, HMAC-SHA256 tokens (control plane); pytest.

**Scope:** Spec Phases 0–2. Phase 3 (sign-in upsell + usage merge) gets its own plan after Phase 2 ships — it depends on observing real anonymous usage shape.

**Working directory:** `~/Projects/crowe-logic-foundry-onboarding` (worktree, branch `feat/first-run-onboarding`). Do NOT touch `~/Projects/crowe-logic-foundry` (dirty, owned by another lane).

**Commit note:** if `git commit` fails with a gpg keydb lock, use `git -c commit.gpgsign=false commit ...`.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `cli/first_run.py` | Create | Credential-state detection, first-run card, anonymous bootstrap |
| `cli/gateway_client.py` | Modify | Default URL → api.crowelogic.com; `register_device()`; device-token bearer support; `FreeTierCapped` |
| `cli/crowe_logic.py` | Modify | 3-line hook in `chat()` and `run()`; `init` click command (thin wrapper) |
| `config/agent_config.py` | Modify | `crowelm-mycelium` model entry |
| `control_plane/tokens.py` | Modify | `make_device_token()` / `verify_device_token()` (HMAC) |
| `control_plane/plans.py` | Modify | `ANON_PLAN_ID`, `ANON_DAILY_TURN_CAP`, rank −1 |
| `control_plane/anonymous.py` | Create | `POST /v1/anonymous/register` + per-IP rate limit |
| `control_plane/gateway.py` | Modify | Anonymous principal branch, `_is_metered` fix, daily cap → structured 402 |
| `control_plane/main.py` | Modify | Register anonymous router |
| `migrations/010_anon_devices.sql` | Create | `anon_usage` table |
| `tests/test_first_run.py` | Create | State matrix, card, init scaffold |
| `tests/test_gateway_client_device.py` | Create | Device store + register + 402 handling |
| `tests/test_anon_tokens.py` | Create | Token mint/verify |
| `tests/test_anonymous_gateway.py` | Create | Register endpoint, principal resolution, daily cap |

---

## Phase 0 — Default gateway URL + domain revival

### Task 1: Change the baked-in gateway default

**Files:**
- Modify: `cli/gateway_client.py:18-20`
- Test: `tests/test_gateway_client_device.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for gateway_client device-token support and defaults."""
import importlib


def test_default_gateway_is_api_crowelogic(monkeypatch):
    monkeypatch.delenv("CROWE_LOGIC_GATEWAY_URL", raising=False)
    import cli.gateway_client as gc
    importlib.reload(gc)
    assert gc.GATEWAY_BASE == "https://api.crowelogic.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/crowe-logic-foundry-onboarding && python -m pytest tests/test_gateway_client_device.py::test_default_gateway_is_api_crowelogic -v`
Expected: FAIL — `assert 'https://chat.crowelogic.com' == 'https://api.crowelogic.com'`

- [ ] **Step 3: Change the default**

In `cli/gateway_client.py` replace:

```python
GATEWAY_BASE = os.environ.get(
    "CROWE_LOGIC_GATEWAY_URL", "https://chat.crowelogic.com"
).rstrip("/")
```

with:

```python
GATEWAY_BASE = os.environ.get(
    "CROWE_LOGIC_GATEWAY_URL", "https://api.crowelogic.com"
).rstrip("/")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_gateway_client_device.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cli/gateway_client.py tests/test_gateway_client_device.py
git commit -m "fix(cli): default gateway URL is api.crowelogic.com (chat. is dead)"
```

### Task 2: Wire api.crowelogic.com to the Azure ACA control plane (infra, partly manual)

**Files:** none in repo (operator task)

- [ ] **Step 1: Get the ACA app FQDN**

```bash
az containerapp show -g <foundry-rg> -n <foundry-app> --query properties.configuration.ingress.fqdn -o tsv
```

(Resource group/app name: whatever serves the Azure control plane today — `az containerapp list -o table` to find it.)

- [ ] **Step 2 (MANUAL — Michael): Squarespace DNS**

Add CNAME: host `api` → the FQDN from Step 1. Also add the `asuid.api` TXT record Azure requires:

```bash
az containerapp show -g <foundry-rg> -n <foundry-app> --query properties.customDomainVerificationId -o tsv
```

TXT host `asuid.api`, value = that verification id.

- [ ] **Step 3: Bind domain + managed cert**

```bash
az containerapp hostname add -g <foundry-rg> -n <foundry-app> --hostname api.crowelogic.com
az containerapp hostname bind -g <foundry-rg> -n <foundry-app> --hostname api.crowelogic.com --environment <aca-env>
```

- [ ] **Step 4: Verify**

Run: `curl -s -o /dev/null -w '%{http_code}' https://api.crowelogic.com/health`
Expected: `200`

---

## Phase 1 — Kill the error wall

### Task 3: `detect_credential_state()`

**Files:**
- Create: `cli/first_run.py`
- Test: `tests/test_first_run.py`

- [ ] **Step 1: Write the failing tests**

```python
"""First-run credential-state detection and onboarding card."""
import pytest

from cli import first_run
from cli.first_run import CredState


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # Simulate a machine with no creds: strip every api_key_env in the chain.
    from config.agent_config import MODEL_CHAIN
    for entry in MODEL_CHAIN:
        for key in ("api_key_env", "endpoint_env"):
            env = entry.get(key)
            if env:
                monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv("CROWE_LOGIC_GATEWAY_URL", raising=False)
    # No Crowe ID session on disk.
    from cli import auth
    monkeypatch.setattr(
        first_run, "_load_creds",
        lambda: (_ for _ in ()).throw(auth.NotLoggedIn("no store")),
    )


def test_none_when_nothing_present():
    assert first_run.detect_credential_state() is CredState.NONE


def test_signed_in_wins(monkeypatch):
    monkeypatch.setattr(first_run, "_load_creds", lambda: {"access_token": "x"})
    assert first_run.detect_credential_state() is CredState.SIGNED_IN


def test_env_creds(monkeypatch):
    from config.agent_config import MODEL_CHAIN
    env = next(e["api_key_env"] for e in MODEL_CHAIN if e.get("api_key_env"))
    monkeypatch.setenv(env, "test-key")
    assert first_run.detect_credential_state() is CredState.ENV_CREDS


def test_gateway_only(monkeypatch):
    monkeypatch.setenv("CROWE_LOGIC_GATEWAY_URL", "https://example.test")
    assert first_run.detect_credential_state() is CredState.GATEWAY_ONLY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_first_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cli.first_run'`

- [ ] **Step 3: Implement**

Create `cli/first_run.py`:

```python
"""First-run credential detection and onboarding for the crowe-logic CLI.

This module is the ONLY place that decides what a zero-credential start does.
Policy (caps, free-tier copy) lives server-side; this is protocol + rendering.
"""

from __future__ import annotations

import os
from enum import Enum


class CredState(str, Enum):
    SIGNED_IN = "signed_in"      # Crowe ID session on disk -> PR #45 gateway routing
    ENV_CREDS = "env_creds"      # provider env keys -> existing local routing
    GATEWAY_ONLY = "gateway_only"  # explicit gateway URL, no local keys
    NONE = "none"                # fresh install -> first-run flow


def _load_creds() -> dict:
    """Indirection point so tests can stub the auth store without touching disk."""
    from cli import auth

    return auth.load_creds()


def detect_credential_state() -> CredState:
    from cli import auth

    try:
        _load_creds()
        return CredState.SIGNED_IN
    except auth.NotLoggedIn:
        pass

    from config.agent_config import MODEL_CHAIN

    for entry in MODEL_CHAIN:
        key_env = entry.get("api_key_env")
        if key_env and os.environ.get(key_env):
            return CredState.ENV_CREDS

    if os.environ.get("CROWE_LOGIC_GATEWAY_URL"):
        return CredState.GATEWAY_ONLY

    return CredState.NONE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_first_run.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add cli/first_run.py tests/test_first_run.py
git commit -m "feat(cli): detect_credential_state - four-state credential detection"
```

### Task 4: First-run card + `ensure_first_run()` gate

**Files:**
- Modify: `cli/first_run.py`
- Test: `tests/test_first_run.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_first_run.py`)

```python
def test_ensure_first_run_passes_with_creds(monkeypatch):
    monkeypatch.setattr(first_run, "_load_creds", lambda: {"access_token": "x"})
    from rich.console import Console
    assert first_run.ensure_first_run(Console(file=None, quiet=True)) is True


def test_ensure_first_run_blocks_on_none(monkeypatch):
    from rich.console import Console
    from io import StringIO
    buf = StringIO()
    console = Console(file=buf, width=100)
    assert first_run.ensure_first_run(console) is False
    out = buf.getvalue()
    assert "crowe-logic login" in out
    assert "crowe-logic init --node" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_first_run.py -k ensure -v`
Expected: FAIL — `AttributeError: module 'cli.first_run' has no attribute 'ensure_first_run'`

- [ ] **Step 3: Implement** (append to `cli/first_run.py`)

```python
def render_first_run_card(console) -> None:
    """One clean card instead of the provider-error wall. No emojis (Crowe design)."""
    from rich.panel import Panel

    body = (
        "[bold]No credentials found.[/bold]\n\n"
        "Pick a path:\n\n"
        "  [bold #bfa669]crowe-logic login[/bold #bfa669]"
        "        Sign in with Crowe ID (recommended)\n"
        "  [bold #bfa669]crowe-logic init --node[/bold #bfa669]"
        "  Scaffold env-file credentials for a self-managed node\n\n"
        "Docs: https://crowelogic.com/docs/cli/getting-started"
    )
    console.print(Panel(body, title="Welcome to Crowe Logic", border_style="#bfa669"))


def ensure_first_run(console) -> bool:
    """Gate a session start. Returns True to proceed, False to exit cleanly.

    Phase 2 turns the NONE branch into anonymous free-tier bootstrap; until
    then NONE shows the card and exits.
    """
    state = detect_credential_state()
    if state is not CredState.NONE:
        return True
    render_first_run_card(console)
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_first_run.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add cli/first_run.py tests/test_first_run.py
git commit -m "feat(cli): first-run card replaces the zero-cred error wall"
```

### Task 5: `crowe-logic init --node` scaffold

**Files:**
- Modify: `cli/first_run.py`
- Modify: `cli/crowe_logic.py` (new click command, after the `chat` command around line 1436 block — exact anchor: after the function that `grep -n "^def chat" cli/crowe_logic.py` locates ends; any position inside the `@main` group is fine)
- Test: `tests/test_first_run.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_init_node_writes_template(tmp_path, monkeypatch):
    target = tmp_path / ".crowe-logic.env"
    path = first_run.scaffold_node_env(str(target))
    assert path == str(target)
    text = target.read_text()
    assert "CROWE_LOGIC_AUTO_ROUTE=1" in text
    assert "CROWE_OPEN_API_KEY=" in text
    assert "set -a" in text  # sourcing instructions present
    # Key NAMES only - never values.
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            assert line.endswith("=") or line.endswith("=1")
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_init_node_refuses_overwrite(tmp_path):
    target = tmp_path / ".crowe-logic.env"
    target.write_text("existing")
    with pytest.raises(FileExistsError):
        first_run.scaffold_node_env(str(target))
    assert target.read_text() == "existing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_first_run.py -k init_node -v`
Expected: FAIL — no attribute `scaffold_node_env`

- [ ] **Step 3: Implement** (append to `cli/first_run.py`)

```python
_NODE_ENV_TEMPLATE = """\
# Crowe Logic node credentials - fill in values, then load into your shell:
#   set -a; . ~/.crowe-logic.env; set +a
# (add that line to ~/.bashrc or ~/.zshrc for persistence)

# Required: resolve CroweLM Auto to a live tier per turn.
CROWE_LOGIC_AUTO_ROUTE=1

# Option A - direct Azure tiers (one key serves every alias on crowelm-prod-eastus2):
CROWE_OPEN_API_KEY=
CROWE_OPEN_ENDPOINT=
AZURE_CORE_API_KEY=
AZURE_CORE_ENDPOINT=

# Option B - route through the gateway instead of local keys:
# CROWE_LOGIC_GATEWAY_URL=https://api.crowelogic.com
"""


def scaffold_node_env(path: str | None = None) -> str:
    """Write the self-managed-node env template (key names only). 0600.

    Raises FileExistsError rather than clobbering an existing file.
    """
    target = path or os.path.expanduser("~/.crowe-logic.env")
    if os.path.exists(target):
        raise FileExistsError(target)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(_NODE_ENV_TEMPLATE)
    return target
```

- [ ] **Step 4: Add the click command** in `cli/crowe_logic.py` (inside the `@main` group, near the other commands):

```python
@main.command()
@click.option("--node", is_flag=True, help="Scaffold ~/.crowe-logic.env for a self-managed node.")
def init(node):
    """Set up credentials for this machine."""
    from cli.first_run import render_first_run_card, scaffold_node_env

    if not node:
        render_first_run_card(console)
        return
    try:
        path = scaffold_node_env()
    except FileExistsError as exc:
        _render_error(f"{exc} already exists - edit it directly or remove it first.")
        raise SystemExit(1)
    console.print(
        f"  Wrote [bold #bfa669]{path}[/bold #bfa669] (0600).\n"
        "  Fill in values, then:  set -a; . ~/.crowe-logic.env; set +a"
    )
```

- [ ] **Step 5: Run tests + smoke the command**

Run: `python -m pytest tests/test_first_run.py -v` — Expected: 8 PASS
Run: `python -c "from click.testing import CliRunner; from cli.crowe_logic import main; r = CliRunner().invoke(main, ['init']); print(r.output); assert r.exit_code == 0"`
Expected: prints the first-run card, exit 0

- [ ] **Step 6: Commit**

```bash
git add cli/first_run.py cli/crowe_logic.py tests/test_first_run.py
git commit -m "feat(cli): crowe-logic init --node scaffolds the thin-node env template"
```

### Task 6: Hook `ensure_first_run` into `chat()` and `run()`

**Files:**
- Modify: `cli/crowe_logic.py` — top of `chat()` body (immediately after its imports, before `orch = _get_orchestrator()` at ~line 1461) and top of the `run` command body (`grep -n '^def run' cli/crowe_logic.py` to locate)

- [ ] **Step 1: Insert the hook in `chat()`**

```python
    from cli.first_run import ensure_first_run
    if not ensure_first_run(console):
        return
```

- [ ] **Step 2: Insert the same 3 lines at the top of the `run` command body**

(Identical code; `run` executes single prompts and hits the same cascade.)

- [ ] **Step 3: Verify the error wall is dead with a clean env**

Run:
```bash
env -i HOME="$HOME" PATH="$PATH" TERM=xterm python -m cli.crowe_logic run "hello" 2>&1 | head -25
```
Expected: the first-run card (login / init --node / docs), exit without provider errors. NOTE: if `~/.config/crowe-logic/auth.json` exists on this Mac, move it aside first (`mv ~/.config/crowe-logic/auth.json /tmp/auth.json.bak`) and restore after.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest tests/ -x -q`
Expected: PASS (existing suite unaffected)

- [ ] **Step 5: Commit**

```bash
git add cli/crowe_logic.py
git commit -m "feat(cli): gate chat/run session start on first-run credential detection"
```

---

## Phase 2 — Anonymous free tier

### Task 7: Device tokens in `control_plane/tokens.py`

**Files:**
- Modify: `control_plane/tokens.py`
- Test: `tests/test_anon_tokens.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Anonymous device token mint/verify."""
import pytest

from control_plane import tokens


@pytest.fixture(autouse=True)
def signing_secret(monkeypatch):
    monkeypatch.setenv("CROWE_ANON_SIGNING_SECRET", "test-secret")


def test_mint_and_verify_roundtrip():
    device_id, raw = tokens.make_device_token()
    assert raw.startswith(tokens.ANON_PREFIX)
    assert tokens.verify_device_token(raw) == device_id


def test_verify_rejects_tampered_sig():
    _, raw = tokens.make_device_token()
    assert tokens.verify_device_token(raw[:-4] + "0000") is None


def test_verify_rejects_foreign_prefixes():
    assert tokens.verify_device_token("crowe_pat_abc") is None
    assert tokens.verify_device_token("") is None
    assert tokens.verify_device_token("crowe_anon_nosig") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_anon_tokens.py -v`
Expected: FAIL — no attribute `ANON_PREFIX`

- [ ] **Step 3: Implement** — in `control_plane/tokens.py` add `import hmac`, `import os` to the imports, then append:

```python
ANON_PREFIX = "crowe_anon_"


def _anon_sig(device_id: str) -> str:
    secret = os.environ["CROWE_ANON_SIGNING_SECRET"]
    return hmac.new(secret.encode(), device_id.encode(), hashlib.sha256).hexdigest()[:32]


def make_device_token() -> tuple[str, str]:
    """Mint an anonymous device token. Returns (device_id, raw_token).

    Stateless HMAC: the gateway verifies without a DB row, so registration is
    cheap and revocation is by daily cap rather than by token.
    """
    device_id = secrets.token_hex(12)
    return device_id, f"{ANON_PREFIX}{device_id}.{_anon_sig(device_id)}"


def verify_device_token(raw: str) -> str | None:
    """Return the device_id for a valid anonymous token, else None."""
    if not raw or not raw.startswith(ANON_PREFIX):
        return None
    device_id, _, sig = raw[len(ANON_PREFIX):].partition(".")
    if not device_id or not sig:
        return None
    if not hmac.compare_digest(sig, _anon_sig(device_id)):
        return None
    return device_id
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_anon_tokens.py -v` — Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add control_plane/tokens.py tests/test_anon_tokens.py
git commit -m "feat(gateway): HMAC anonymous device tokens (crowe_anon_ prefix)"
```

### Task 8: `free-anonymous` plan + migration

**Files:**
- Modify: `control_plane/plans.py`
- Create: `migrations/010_anon_devices.sql`
- Test: `tests/test_anonymous_gateway.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Anonymous plan semantics + register endpoint + gateway principal/cap."""
import pytest

from control_plane import plans


def test_anon_plan_ranks_below_everything():
    assert plans.plan_rank(plans.ANON_PLAN_ID) == -1
    assert plans.plan_rank("byok") > plans.plan_rank(plans.ANON_PLAN_ID)


def test_anon_plan_not_in_launch_plans():
    # Stripe/pricing surfaces iterate LAUNCH_PLAN_IDS; anon must stay out.
    assert plans.ANON_PLAN_ID not in plans.LAUNCH_PLAN_IDS


def test_anon_cap_constant():
    assert plans.ANON_DAILY_TURN_CAP == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_anonymous_gateway.py -v`
Expected: FAIL — no attribute `ANON_PLAN_ID`

- [ ] **Step 3: Implement** — in `control_plane/plans.py`, after the `PLAN_RANK` definition (line ~25) add:

```python
# Anonymous free tier: deliberately NOT in LAUNCH_PLAN_IDS (Stripe surfaces
# iterate that tuple). Rank -1 sits below every paid plan.
ANON_PLAN_ID = "free-anonymous"
ANON_DAILY_TURN_CAP = 20  # server-side policy; tune without a client release
```

Then change `plan_rank` (line ~87) to:

```python
def plan_rank(plan_id: str | None) -> int:
    canonical = canonical_plan_id(plan_id)
    if canonical == ANON_PLAN_ID:
        return -1
    return PLAN_RANK.get(canonical, PLAN_RANK["personal"])
```

And add a display name to `PLAN_DISPLAY_NAMES` (line ~27 dict): `"free-anonymous": "Free",`

- [ ] **Step 4: Create `migrations/010_anon_devices.sql`**

```sql
-- Anonymous free-tier daily usage. Device ids are HMAC-verified, not FK-backed.
CREATE TABLE IF NOT EXISTS anon_usage (
    device_id TEXT NOT NULL,
    day DATE NOT NULL,
    turns INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, day)
);
```

- [ ] **Step 5: Run tests** — `python -m pytest tests/test_anonymous_gateway.py -v` — Expected: 3 PASS. Also `python -m pytest tests/ -q -k plan` for regressions.

- [ ] **Step 6: Commit**

```bash
git add control_plane/plans.py migrations/010_anon_devices.sql tests/test_anonymous_gateway.py
git commit -m "feat(gateway): free-anonymous plan (rank -1, 20 turns/day) + anon_usage table"
```

### Task 9: Mycelium model entry

**Files:**
- Modify: `config/agent_config.py` (`_BASE_MODEL_CHAIN`, line ~171) and `control_plane/gateway.py` (`MODEL_PLAN_ACCESS`, line ~39)
- Test: `tests/test_anonymous_gateway.py`

- [ ] **Step 1: Write the failing test** (append)

```python
def test_mycelium_resolves_and_is_anon_accessible():
    from config.agent_config import resolve_model_config
    from control_plane.gateway import MODEL_PLAN_ACCESS

    cfg = resolve_model_config("crowelm-mycelium")
    assert cfg is not None
    assert cfg["api_key_env"] == "CROWELM_MYCELIUM_API_KEY"
    assert MODEL_PLAN_ACCESS["crowelm-mycelium"] == plans.ANON_PLAN_ID
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_anonymous_gateway.py::test_mycelium_resolves_and_is_anon_accessible -v`
Expected: FAIL — `cfg is None`

- [ ] **Step 3: Add the model entry.** First inspect an existing OpenAI-compat entry to mirror its exact field set:

```bash
grep -n '"provider": "openai_compat"' config/agent_config.py | head -3
```

Read one full entry, then append to `_BASE_MODEL_CHAIN` (matching any additional required fields you find — e.g. a `model_env`/deployment field — set to `CROWELM_MYCELIUM_MODEL`):

```python
    # ─── Free tier: CroweLM Mycelium — anonymous gateway-only tier (Modal proxy) ──
    {
        "name": "crowelm-mycelium",
        "label": "CroweLM Mycelium",
        "provider": "openai_compat",
        "aliases": ["mycelium", "free"],
        "endpoint_env": "CROWELM_MYCELIUM_ENDPOINT",
        "api_key_env": "CROWELM_MYCELIUM_API_KEY",
        "prompt": (
            "You are CroweLM Mycelium, Crowe Logic's free community model. "
            "Be helpful and concise."
        ),
    },
```

- [ ] **Step 4: Map it in `MODEL_PLAN_ACCESS`** (`control_plane/gateway.py` line ~39 dict). Add the import `from .plans import ANON_PLAN_ID` next to the existing plans imports, then add the dict entry:

```python
    "crowelm-mycelium": ANON_PLAN_ID,
```

- [ ] **Step 5: Run tests** — `python -m pytest tests/test_anonymous_gateway.py -v` — Expected: 4 PASS. Then full suite: `python -m pytest tests/ -q` (model-chain tests may assert chain length/ordering — fix any that hardcode counts).

- [ ] **Step 6: Commit**

```bash
git add config/agent_config.py control_plane/gateway.py tests/test_anonymous_gateway.py
git commit -m "feat(models): CroweLM Mycelium free tier entry, anon-plan gated"
```

### Task 10: `POST /v1/anonymous/register`

**Files:**
- Create: `control_plane/anonymous.py`
- Modify: `control_plane/main.py` (router registration, line ~34 block)
- Test: `tests/test_anonymous_gateway.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_register_mints_token(monkeypatch):
    monkeypatch.setenv("CROWE_ANON_SIGNING_SECRET", "test-secret")
    from control_plane import anonymous, tokens
    import asyncio

    class FakeClient:
        host = "203.0.113.7"

    class FakeRequest:
        client = FakeClient()

    anonymous._register_log.clear()
    out = asyncio.run(anonymous.register_device(FakeRequest()))
    assert tokens.verify_device_token(out["token"]) == out["device_id"]
    assert out["free_model"] == "crowelm-mycelium"
    assert out["daily_turn_cap"] == plans.ANON_DAILY_TURN_CAP


def test_register_rate_limits_per_ip(monkeypatch):
    monkeypatch.setenv("CROWE_ANON_SIGNING_SECRET", "test-secret")
    from fastapi import HTTPException
    from control_plane import anonymous
    import asyncio

    class FakeClient:
        host = "203.0.113.8"

    class FakeRequest:
        client = FakeClient()

    anonymous._register_log.clear()
    for _ in range(anonymous._REGISTER_MAX_PER_IP):
        asyncio.run(anonymous.register_device(FakeRequest()))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(anonymous.register_device(FakeRequest()))
    assert exc.value.status_code == 429
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_anonymous_gateway.py -k register -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'control_plane.anonymous'`

- [ ] **Step 3: Implement** — create `control_plane/anonymous.py`:

```python
"""Anonymous device registration for the free tier.

Stateless HMAC tokens (see tokens.make_device_token) + a per-IP in-process
rate limit. NOTE: the limiter is per-replica; at >1 ACA replica the effective
ceiling is N x _REGISTER_MAX_PER_IP. Acceptable for launch - the daily turn
cap is the real spend bound.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from .plans import ANON_DAILY_TURN_CAP
from .tokens import make_device_token

router = APIRouter(prefix="/v1/anonymous", tags=["anonymous"])

_REGISTER_WINDOW = 3600.0  # seconds
_REGISTER_MAX_PER_IP = 5
_register_log: dict[str, list[float]] = {}

FREE_MODEL = "crowelm-mycelium"


@router.post("/register")
async def register_device(request: Request) -> dict:
    """Mint an anonymous device token. No PII; rate limited per source IP."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    hits = [t for t in _register_log.get(ip, []) if now - t < _REGISTER_WINDOW]
    if len(hits) >= _REGISTER_MAX_PER_IP:
        raise HTTPException(
            status_code=429,
            detail="Too many device registrations from this address; try again later.",
        )
    hits.append(now)
    _register_log[ip] = hits

    device_id, token = make_device_token()
    return {
        "device_id": device_id,
        "token": token,
        "free_model": FREE_MODEL,
        "daily_turn_cap": ANON_DAILY_TURN_CAP,
    }
```

- [ ] **Step 4: Register the router** — in `control_plane/main.py`, next to the existing imports/includes (lines ~34-37):

```python
from .anonymous import router as anonymous_router
app.include_router(anonymous_router)
```

(Match the existing import style at the top of the file — the other routers show the exact pattern.)

- [ ] **Step 5: Run tests** — `python -m pytest tests/test_anonymous_gateway.py -v` — Expected: 6 PASS

- [ ] **Step 6: Commit**

```bash
git add control_plane/anonymous.py control_plane/main.py tests/test_anonymous_gateway.py
git commit -m "feat(gateway): POST /v1/anonymous/register - rate-limited device token mint"
```

### Task 11: Anonymous principal + daily cap in the gateway

**Files:**
- Modify: `control_plane/gateway.py` — `_resolve_principal` (line ~341), `_is_metered` (line ~403), `gateway_chat` (line ~413)
- Test: `tests/test_anonymous_gateway.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
def _anon_key_info(device_id="dev123"):
    return {
        "plan_id": plans.ANON_PLAN_ID,
        "workspace_id": device_id,
        "user_id": device_id,
        "principal": "anonymous",
        "subject": f"anon:{device_id}",
    }


def test_resolve_principal_accepts_device_token(monkeypatch):
    monkeypatch.setenv("CROWE_ANON_SIGNING_SECRET", "test-secret")
    import asyncio
    from control_plane import gateway, tokens

    device_id, raw = tokens.make_device_token()
    info = asyncio.run(
        gateway._resolve_principal(authorization=f"Bearer {raw}", x_api_key=None, db=None)
    )
    assert info["principal"] == "anonymous"
    assert info["plan_id"] == plans.ANON_PLAN_ID
    assert info["user_id"] == device_id


def test_anonymous_is_not_workspace_metered():
    from control_plane.gateway import _is_metered
    assert _is_metered(_anon_key_info()) is False
    assert _is_metered({"principal": "crowe-id"}) is False
    assert _is_metered({"principal": "workspace"}) is True


class FakeDb:
    """Stub for the asyncpg-style Database helper used by gateway_chat."""

    def __init__(self, turns_today=0):
        self.turns_today = turns_today
        self.executed = []

    async def fetchrow(self, query, *args):
        if "anon_usage" in query:
            return {"turns": self.turns_today}
        return None

    async def execute(self, query, *args):
        self.executed.append((query, args))


def test_anon_chat_under_cap_calls_provider(monkeypatch):
    import asyncio
    from control_plane import gateway

    async def fake_provider(**kwargs):
        return ("hello from mycelium", 5, 7)

    monkeypatch.setattr(gateway, "_call_provider", lambda **kw: fake_provider(**kw))
    req = gateway.GatewayRequest(model="crowelm-mycelium", messages=[{"role": "user", "content": "hi"}])
    resp = asyncio.run(gateway.gateway_chat(req, key_info=_anon_key_info(), db=FakeDb(turns_today=3)))
    assert resp.content == "hello from mycelium"


def test_anon_chat_cap_hit_returns_structured_402():
    import asyncio
    from fastapi import HTTPException
    from control_plane import gateway

    req = gateway.GatewayRequest(model="crowelm-mycelium", messages=[{"role": "user", "content": "hi"}])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            gateway.gateway_chat(
                req, key_info=_anon_key_info(), db=FakeDb(turns_today=plans.ANON_DAILY_TURN_CAP)
            )
        )
    assert exc.value.status_code == 402
    detail = exc.value.detail
    assert detail["code"] == "anon_daily_cap"
    assert "message" in detail and "upsell" in detail


def test_anon_cannot_reach_paid_models():
    import asyncio
    from fastapi import HTTPException
    from control_plane import gateway

    req = gateway.GatewayRequest(model="gpt-5.5", messages=[{"role": "user", "content": "hi"}])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(gateway.gateway_chat(req, key_info=_anon_key_info(), db=FakeDb()))
    assert exc.value.status_code == 403
```

(If `GatewayRequest` requires extra fields, check its definition at `control_plane/gateway.py:319` and supply them.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_anonymous_gateway.py -v`
Expected: new tests FAIL (no anon branch in `_resolve_principal`; `_is_metered` returns True for anonymous; no cap logic)

- [ ] **Step 3: Implement.** In `control_plane/gateway.py`:

(a) Add to the tokens import line: `verify_device_token` (the file already imports from `.tokens`), and to the plans imports: `ANON_PLAN_ID, ANON_DAILY_TURN_CAP`.

(b) In `_resolve_principal`, FIRST branch inside the function (before the Crowe ID JWT branch — anon tokens are not JWTs and must not fall through to the 401):

```python
    # ── Anonymous device token path (free tier) ──
    if authorization and authorization.startswith("Bearer "):
        device_id = verify_device_token(authorization[7:])
        if device_id:
            return {
                "plan_id": ANON_PLAN_ID,
                "workspace_id": device_id,
                "user_id": device_id,
                "principal": "anonymous",
                "subject": f"anon:{device_id}",
            }
```

(c) Fix `_is_metered` so anonymous principals skip the workspace plans-table lookup (it would crash — no `free-anonymous` row in `plans`):

```python
    return key_info.get("principal") not in ("crowe-id", "anonymous")
```

(Update its docstring accordingly.)

(d) In `gateway_chat`, immediately after the plan-access check and before the token-budget block, add the daily cap (increment-before-call: deny-by-default, failed provider calls count):

```python
    # ── Anonymous daily turn cap ──
    if key_info.get("principal") == "anonymous":
        from datetime import date

        today = date.today()
        row = await db.fetchrow(
            "SELECT turns FROM anon_usage WHERE device_id = $1 AND day = $2",
            user_id,
            today,
        )
        if row and row["turns"] >= ANON_DAILY_TURN_CAP:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "anon_daily_cap",
                    "message": f"Free daily limit reached ({ANON_DAILY_TURN_CAP} turns).",
                    "upsell": "Sign in for full CroweLM tiers: run `crowe-logic login` or visit https://crowelogic.com/pricing",
                },
            )
        await db.execute(
            """INSERT INTO anon_usage (device_id, day, turns) VALUES ($1, $2, 1)
               ON CONFLICT (device_id, day) DO UPDATE SET turns = anon_usage.turns + 1""",
            user_id,
            today,
        )
```

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_anonymous_gateway.py -v` — Expected: 11 PASS. Full suite: `python -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add control_plane/gateway.py tests/test_anonymous_gateway.py
git commit -m "feat(gateway): anonymous principal, metering exemption, 20/day cap with structured 402"
```

### Task 12: CLI device store + anonymous turns

**Files:**
- Modify: `cli/gateway_client.py`
- Test: `tests/test_gateway_client_device.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
import json


def test_device_store_roundtrip(tmp_path, monkeypatch):
    import cli.gateway_client as gc

    store = tmp_path / "device.json"
    monkeypatch.setattr(gc, "DEVICE_STORE", str(store))
    gc.save_device({"device_id": "d1", "token": "crowe_anon_d1.sig"})
    assert oct(store.stat().st_mode & 0o777) == "0o600"
    assert gc.load_device()["device_id"] == "d1"


def test_load_device_missing_returns_none(tmp_path, monkeypatch):
    import cli.gateway_client as gc

    monkeypatch.setattr(gc, "DEVICE_STORE", str(tmp_path / "nope.json"))
    assert gc.load_device() is None


def test_load_device_corrupt_returns_none(tmp_path, monkeypatch):
    import cli.gateway_client as gc

    store = tmp_path / "device.json"
    store.write_text("{not json")
    monkeypatch.setattr(gc, "DEVICE_STORE", str(store))
    assert gc.load_device() is None


def test_chat_402_raises_free_tier_capped(monkeypatch):
    import cli.gateway_client as gc

    class FakeResp:
        status_code = 402

        def json(self):
            return {"detail": {"code": "anon_daily_cap", "message": "capped", "upsell": "login"}}

    monkeypatch.setattr(gc.httpx, "post", lambda *a, **kw: FakeResp())
    with pytest.raises(gc.FreeTierCapped) as exc:
        gc.chat("crowelm-mycelium", [{"role": "user", "content": "hi"}], bearer="crowe_anon_x.y")
    assert exc.value.detail["code"] == "anon_daily_cap"
```

(Add `import pytest` at the top of the file if not present from Task 1.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gateway_client_device.py -v`
Expected: FAIL — no `DEVICE_STORE` / `save_device` / `FreeTierCapped`

- [ ] **Step 3: Implement** — in `cli/gateway_client.py`:

(a) Add `import json` to imports. After `GATEWAY_BASE` add:

```python
DEVICE_STORE = os.path.expanduser("~/.config/crowe-logic/device.json")


class FreeTierCapped(Exception):
    """Raised on a structured 402 from the gateway (anonymous daily cap)."""

    def __init__(self, detail: dict):
        self.detail = detail if isinstance(detail, dict) else {"message": str(detail)}
        super().__init__(self.detail.get("message", "free tier capped"))


def save_device(device: dict) -> None:
    os.makedirs(os.path.dirname(DEVICE_STORE), exist_ok=True)
    fd = os.open(DEVICE_STORE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(device, fh)


def load_device() -> dict | None:
    try:
        with open(DEVICE_STORE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def register_device() -> dict:
    """Mint + persist an anonymous device token. Returns the register payload."""
    resp = httpx.post(f"{GATEWAY_BASE}/v1/anonymous/register", timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    save_device(payload)
    return payload
```

(b) Extend `chat()` to accept a `bearer` override and handle 402. Change the signature to:

```python
def chat(
    model: str,
    messages: list[dict],
    max_tokens: int | None = None,
    temperature: float | None = None,
    bearer: str | None = None,
) -> dict:
```

Inside the retry loop, replace the headers line with:

```python
            headers={"Authorization": f"Bearer {bearer or _token()}"},
```

After the `403` branch add:

```python
        if resp.status_code == 402:
            raise FreeTierCapped(resp.json().get("detail", {}))
```

And guard the 401-refresh branch so anonymous calls don't try a Crowe ID refresh: change `if resp.status_code == 401 and attempt == 0:` to `if resp.status_code == 401 and attempt == 0 and bearer is None:`.

- [ ] **Step 4: Run tests** — `python -m pytest tests/test_gateway_client_device.py -v` — Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add cli/gateway_client.py tests/test_gateway_client_device.py
git commit -m "feat(cli): anonymous device store, register_device, 402 FreeTierCapped"
```

### Task 13: Wire NONE state to the free tier

**Files:**
- Modify: `cli/first_run.py` (`ensure_first_run` NONE branch)
- Modify: `cli/crowe_logic.py` (anonymous turn path)
- Test: `tests/test_first_run.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_first_run.py`)

```python
def test_none_state_bootstraps_anonymous(monkeypatch):
    from io import StringIO
    from rich.console import Console

    calls = {}

    monkeypatch.setattr(
        first_run, "_bootstrap_anonymous",
        lambda: calls.setdefault("registered", {"token": "crowe_anon_x.y", "free_model": "crowelm-mycelium", "daily_turn_cap": 20}),
    )
    state = {}
    console = Console(file=StringIO(), width=100)
    assert first_run.ensure_first_run(console, session_state=state) is True
    assert state["anon_device_token"] == "crowe_anon_x.y"
    assert state["anon_free_model"] == "crowelm-mycelium"


def test_none_state_degrades_to_card_when_gateway_down(monkeypatch):
    from io import StringIO
    from rich.console import Console

    def boom():
        raise OSError("network down")

    monkeypatch.setattr(first_run, "_bootstrap_anonymous", boom)
    buf = StringIO()
    console = Console(file=buf, width=100)
    assert first_run.ensure_first_run(console, session_state={}) is False
    assert "crowe-logic login" in buf.getvalue()
```

Also update the two existing `ensure_first_run` tests from Task 4 to pass `session_state={}`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_first_run.py -v`
Expected: FAIL — `ensure_first_run` has no `session_state` param / no `_bootstrap_anonymous`

- [ ] **Step 3: Implement** — in `cli/first_run.py` replace `ensure_first_run` with:

```python
def _bootstrap_anonymous() -> dict:
    """Register (or reuse) an anonymous device token. Raises on network failure."""
    from cli import gateway_client

    device = gateway_client.load_device()
    if device and device.get("token"):
        return device
    return gateway_client.register_device()


def ensure_first_run(console, session_state: dict | None = None) -> bool:
    """Gate a session start. Returns True to proceed, False to exit cleanly.

    NONE -> anonymous free-tier bootstrap (device token via the gateway); on
    any failure, degrade to the setup card - never a stack trace, and never
    free inference without a server-verified token (deny-by-default).
    """
    state = detect_credential_state()
    if state is not CredState.NONE:
        return True

    try:
        device = _bootstrap_anonymous()
    except Exception:
        render_first_run_card(console)
        return False

    if session_state is not None:
        session_state["anon_device_token"] = device["token"]
        session_state["anon_free_model"] = device.get("free_model", "crowelm-mycelium")
    console.print(
        "  [dim]Free tier active - CroweLM Mycelium, "
        f"{device.get('daily_turn_cap', 20)} turns/day. "
        "Run [/dim][bold #bfa669]crowe-logic login[/bold #bfa669][dim] for full tiers.[/dim]"
    )
    return True
```

- [ ] **Step 4: Update the hooks** from Task 6 in `cli/crowe_logic.py` to pass state:

```python
    from cli.first_run import ensure_first_run
    if not ensure_first_run(console, session_state=session_state):
        return
```

- [ ] **Step 5: Route anonymous turns through the gateway.** Locate the signed-in gateway turn branch: `grep -n 'gateway_client' cli/crowe_logic.py`. In the turn-execution path where the CLI decides gateway-vs-local (the PR #45 branch), extend the condition so it also fires when `session_state.get("anon_device_token")` is set, and pass the bearer + pinned model:

```python
        anon_token = session_state.get("anon_device_token")
        if anon_token:
            from cli.gateway_client import FreeTierCapped

            try:
                result = gateway_client.chat(
                    session_state.get("anon_free_model", "crowelm-mycelium"),
                    turn_messages,
                    bearer=anon_token,
                )
            except FreeTierCapped as capped:
                _render_error(
                    f"{capped.detail.get('message', 'Free daily limit reached.')}\n"
                    f"{capped.detail.get('upsell', 'Run `crowe-logic login` for full tiers.')}",
                    title="Free tier",
                )
                continue
```

(Adapt `turn_messages` / `result` handling to match the surrounding signed-in branch exactly — render `result["content"]` the same way it does. The gateway's `message`/`upsell` strings are rendered verbatim: policy copy is server-owned.)

- [ ] **Step 6: Run tests** — `python -m pytest tests/test_first_run.py tests/ -q` — Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add cli/first_run.py cli/crowe_logic.py tests/test_first_run.py
git commit -m "feat(cli): zero-cred sessions bootstrap the anonymous free tier"
```

### Task 14: Deploy + end-to-end verification

**Files:** none in repo (operator task)

- [ ] **Step 1: Control-plane env.** Set on the ACA app (and Railway if it stays live):
  - `CROWE_ANON_SIGNING_SECRET` — `openssl rand -hex 32`
  - `CROWELM_MYCELIUM_ENDPOINT` / `CROWELM_MYCELIUM_API_KEY` — the Modal proxy values (MANUAL — Michael: confirm the Modal proxy accepts control-plane-originated traffic; spec prerequisite)

- [ ] **Step 2: Apply migration 010** against the control-plane Postgres (same channel previous migrations used).

- [ ] **Step 3: Deploy the control plane** (existing deploy path; Azure needs DIGEST-PIN, not `:latest`).

- [ ] **Step 4: Live register probe**

```bash
curl -s -X POST https://api.crowelogic.com/v1/anonymous/register | python3 -m json.tool
```
Expected: JSON with `token` starting `crowe_anon_`, `daily_turn_cap: 20`.

- [ ] **Step 5: Clean-machine CLI walk (the parallel-session loop).** On this Mac:

```bash
mv ~/.config/crowe-logic/auth.json /tmp/auth.json.bak 2>/dev/null
mv ~/.config/crowe-logic/device.json /tmp/device.json.bak 2>/dev/null
env -i HOME="$HOME" PATH="$PATH" TERM=xterm python -m cli.crowe_logic run "hello"
```
Expected: "Free tier active" notice + a real Mycelium response. Restore the moved files after.

- [ ] **Step 6: Cap-out walk.** Loop 21 turns against one device token; turn 21 must render the gateway's upsell copy, not an error wall.

- [ ] **Step 7: Push + PR**

```bash
git push -u origin feat/first-run-onboarding
gh pr create --title "First-run onboarding: anonymous free tier + api.crowelogic.com default" --body "Implements docs/superpowers/specs/2026-06-05-first-run-onboarding-design.md (Phases 0-2).

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Out of scope (deliberate)

- Phase 3 (sign-in upsell flow polish + anonymous-usage merge into Crowe ID accounts) — separate plan.
- Device-code auth for headless sign-in.
- Streaming for anonymous turns (`/chat/stream` stays principal-gated as-is; anonymous uses non-streaming `/chat`).
- PyPI release of the updated wheel (cut after the PR merges and the control plane is live, so the new default URL never points at a 404).
