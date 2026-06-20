# Onboarding Phase 3 — Sign-In Upsell + Anti-Abuse Usage Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an anonymous user signs in, their free-tier daily-turn count follows them to their Crowe ID account (account-keyed, cross-device), so signing up cannot reset the 20/day free counter.

**Architecture:** Add a `free` signed-in plan tier metered by the daily *turn* cap (not the monthly token budget). Generalize the anonymous daily counter from `device_id` to a `principal_id` (`device:<id>` or `user:<sub>`) in a new `free_usage` table. Add a `/v1/anonymous/link` endpoint that merges a device's turns onto the account with `min(cap, account+device)` math, called by `crowe-logic login` after PKCE. Update the cap-wall upsell copy to be honest (sign-in = continuity, upgrade = more turns).

**Tech Stack:** Python 3.13, FastAPI, asyncpg/Postgres, pytest. Server in `control_plane/`, CLI in `cli/`. Tests use the existing `FakeDb` pattern.

**Spec:** `docs/superpowers/specs/2026-06-07-onboarding-phase3-signin-merge-design.md`

**Branch:** `feat/onboarding-phase3-signin-merge` (cut from `main`).

**Run tests with:** `.venv/bin/python -m pytest <path> -q` from the repo root. Baseline pre-existing failures: **18 failed / 819 passed** — never chase those 18 (apex prompt drift, multipart/email-validator collection errors, asyncio markers). A task is green if it does not increase the failed count.

---

## File Structure

- `control_plane/plans.py` — add the `free` plan to ranking + display (Task 1).
- `control_plane/oidc.py` — `tier_to_plan`: safe additive mapping + `free` default (Task 1).
- `migrations/011_free_usage.sql` / `.down.sql` — new principal-keyed counter table, rekey-copy from `anon_usage` (Task 2).
- `control_plane/gateway.py` — `_free_principal_id` helper + generalized turn-cap block over `free_usage` (Task 3); honest cap-hit copy (Task 6).
- `control_plane/anonymous.py` — `POST /v1/anonymous/link` merge endpoint + `_merged_turns` pure helper (Task 4).
- `cli/gateway_client.py` — `link_device()` (Task 5).
- `cli/crowe_logic.py` — call `link_device()` from `login_cmd` (Task 5).
- Tests: `tests/test_phase3_free_plan.py`, `tests/test_phase3_migration.py`, `tests/test_phase3_link.py`, `tests/test_phase3_link_client.py`, plus additions to `tests/test_anonymous_gateway.py`.

---

## Task 1: `free` signed-in plan tier (plans.py + oidc.tier_to_plan)

**Why:** A signed-in unpaid user must be metered by the daily turn cap, like anon — not the monthly token budget, like paid. Today `tier_to_plan` sends `"free"`/unknown tiers to `personal` (a 750k-token paid allowance). Phase 3 routes them to a new `free` plan. The change is additive and least-privilege-defaulting so genuine paid tiers are preserved.

**Files:**
- Modify: `control_plane/plans.py`
- Modify: `control_plane/oidc.py`
- Test: `tests/test_phase3_free_plan.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase3_free_plan.py`:

```python
"""Phase 3: the `free` signed-in plan tier."""

from control_plane import plans
from control_plane import oidc


def test_free_plan_ranks_below_personal_above_anon():
    assert plans.plan_rank("free") == 0
    assert plans.plan_rank("free") < plans.plan_rank("personal")
    assert plans.plan_rank("free") > plans.plan_rank(plans.ANON_PLAN_ID)


def test_free_plan_is_canonical_passthrough():
    # `free` must not be aliased away to another plan id.
    assert plans.canonical_plan_id("free") == "free"


def test_free_plan_has_display_name():
    assert plans.display_plan_name("free") == "Free"


def test_tier_to_plan_unknown_and_free_resolve_to_free():
    # No subscription / unknown tier -> least privilege = the free plan.
    assert oidc.tier_to_plan(None) == "free"
    assert oidc.tier_to_plan("") == "free"
    assert oidc.tier_to_plan("free") == "free"
    assert oidc.tier_to_plan("totally-unknown") == "free"


def test_tier_to_plan_preserves_paid_tiers():
    # Genuine paid tiers are untouched (no downgrade).
    assert oidc.tier_to_plan("personal") == "personal"
    assert oidc.tier_to_plan("pro") == "pro"
    assert oidc.tier_to_plan("studio") == "team"
    assert oidc.tier_to_plan("enterprise") == "enterprise"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase3_free_plan.py -q`
Expected: FAIL — `plan_rank("free")` returns the personal default (1), `tier_to_plan(None)` returns `"personal"`.

- [ ] **Step 3: Add the `free` plan to `control_plane/plans.py`**

In `PLAN_DISPLAY_NAMES` (the dict starting near line 32) add a `"free"` entry alongside the existing `"free-anonymous": "Free"`:

```python
PLAN_DISPLAY_NAMES = {
    "personal": "Personal",
    "pro": "Pro",
    "team": "Team",
    "enterprise": "Enterprise",
    "free": "Free",
    "free-anonymous": "Free",
}
```

In `plan_rank` (near line 65), add a `free` special-case beside the existing anon one:

```python
def plan_rank(plan_id: str | None) -> int:
    canonical = canonical_plan_id(plan_id)
    if canonical == ANON_PLAN_ID:
        return -1
    if canonical == "free":
        return 0
    return PLAN_RANK.get(canonical, PLAN_RANK["personal"])
```

(`canonical_plan_id("free")` already returns `"free"` — it is not in `LEGACY_PLAN_ALIASES` — so no change is needed there. The test `test_free_plan_is_canonical_passthrough` guards this.)

- [ ] **Step 4: Make `tier_to_plan` safe + free-defaulting in `control_plane/oidc.py`**

Replace the `_TIER_PLAN` dict and `tier_to_plan` default (lines ~20-34):

```python
_TIER_PLAN = {
    "personal": "personal",   # preserve any genuine personal-tier subscriber
    "pro": "pro",
    "studio": "team",
    "enterprise": "enterprise",
    # NOTE: "free"/unknown/missing fall through to the default below ("free"),
    # the new signed-in free tier (20/day Mycelium). This intentionally stops
    # giving no-subscription Crowe IDs the paid `personal` allowance.
}


def tier_to_plan(crowe_tier: str | None) -> str:
    """Map a Crowe ID tier to a gateway plan id. Unknown/missing -> least privilege (free)."""
    return _TIER_PLAN.get((crowe_tier or "").lower(), "free")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase3_free_plan.py -q`
Expected: PASS (6 passed).

- [ ] **Step 6: Run the broader plan/oidc suites for regressions**

Run: `.venv/bin/python -m pytest tests/ -q -k "plan or oidc or tier or gateway" --continue-on-collection-errors`
Expected: no NEW failures vs the 18-failure baseline. If a test asserted `tier_to_plan(None) == "personal"`, update it to `"free"` (that was the old default; the new least-privilege default is intended).

- [ ] **Step 7: Commit**

```bash
git add control_plane/plans.py control_plane/oidc.py tests/test_phase3_free_plan.py
git commit -m "feat(plans): add free signed-in plan tier; tier_to_plan defaults to free

Co-Authored-By: Claude <noreply@anthropic.com>"
```

> **VERIFICATION NOTE for the implementer / Michael:** This flips the default plan for a no-/unknown-tier Crowe ID from `personal` (paid allowance) to `free` (20/day). The additive `"personal": "personal"` mapping preserves anyone whose Keycloak `crowe_tier` is literally `"personal"`. The one residual risk: a paying customer whose `crowe_tier` is empty or `"free"` would drop to 20/day. Before deploying, confirm in Keycloak that paid Personal subscribers carry `crowe_tier="personal"` (or pro/studio/enterprise), not empty/`"free"`. If they carry empty/`"free"`, do NOT deploy Task 1 until billing sets a real tier claim.

---

## Task 2: Migration 011 — principal-keyed `free_usage` table

**Why:** Generalize the daily counter from `device_id` to a `principal_id` so one mechanism serves both anonymous devices (`device:<id>`) and signed-in free accounts (`user:<sub>`). Rekey existing live `anon_usage` rows in; never drop them.

**Files:**
- Create: `migrations/011_free_usage.sql`
- Create: `migrations/011_free_usage.down.sql`
- Test: `tests/test_phase3_migration.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase3_migration.py`:

```python
"""Phase 3: migration 011 creates the principal-keyed free_usage table."""

from pathlib import Path

MIG = Path(__file__).resolve().parent.parent / "migrations"


def test_migration_011_creates_free_usage_and_rekeys():
    up = (MIG / "011_free_usage.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS free_usage" in up
    assert "principal_id" in up
    assert "PRIMARY KEY (principal_id, day)" in up
    # Rekey existing anon rows in place, prefixed device:<id> — not drop/recreate.
    assert "INSERT INTO free_usage" in up
    assert "'device:'" in up
    assert "FROM anon_usage" in up


def test_migration_011_has_reversible_down():
    down = (MIG / "011_free_usage.down.sql").read_text()
    assert "DROP TABLE IF EXISTS free_usage" in down
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase3_migration.py -q`
Expected: FAIL — the migration files do not exist yet.

- [ ] **Step 3: Write the up migration**

Create `migrations/011_free_usage.sql`:

```sql
-- Principal-keyed free-tier daily counter. Generalizes anon_usage (device-only)
-- so the same mechanism meters anonymous devices (device:<id>) and signed-in
-- free accounts (user:<sub>). anon_usage is left intact (frozen) so a rollback
-- to the prior image still has its data; the gateway now reads/writes free_usage.
CREATE TABLE IF NOT EXISTS free_usage (
    principal_id TEXT NOT NULL,
    day          DATE NOT NULL,
    turns        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (principal_id, day)
);

-- Rekey existing live anonymous rows in place (device:<device_id>). Idempotent:
-- ON CONFLICT DO NOTHING means re-running the migration will not double-insert.
INSERT INTO free_usage (principal_id, day, turns)
SELECT 'device:' || device_id, day, turns
FROM anon_usage
ON CONFLICT (principal_id, day) DO NOTHING;
```

- [ ] **Step 4: Write the down migration**

Create `migrations/011_free_usage.down.sql`:

```sql
-- Reverse 011: project free_usage's device rows back onto anon_usage, then drop.
-- anon_usage was left intact by the up migration, so this restores any turns the
-- gateway recorded under device:<id> while free_usage was live.
INSERT INTO anon_usage (device_id, day, turns)
SELECT substring(principal_id FROM 8), day, turns
FROM free_usage
WHERE principal_id LIKE 'device:%'
ON CONFLICT (device_id, day) DO UPDATE SET turns = EXCLUDED.turns;

DROP TABLE IF EXISTS free_usage;
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase3_migration.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add migrations/011_free_usage.sql migrations/011_free_usage.down.sql tests/test_phase3_migration.py
git commit -m "feat(db): migration 011 principal-keyed free_usage, rekey from anon_usage

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: Generalized turn-cap block in the gateway

**Why:** The turn-cap currently runs only for `principal == "anonymous"` against `anon_usage`. It must run for anonymous devices AND signed-in `free`-plan accounts, against `free_usage`, keyed by the right `principal_id`.

**Files:**
- Modify: `control_plane/gateway.py` (the `gateway_chat` cap block near lines 477-501; add a helper above it)
- Test: `tests/test_anonymous_gateway.py` (append)

- [ ] **Step 1: Write the failing test**

First add this `FakeFreeDb` near the top of `tests/test_anonymous_gateway.py`'s helper section (after the existing `FakeDb`):

```python
class FakeFreeDb:
    """Stub Database for the free_usage path: records the principal_id read."""

    def __init__(self, turns_today=0):
        self.turns_today = turns_today
        self.read_principal = None
        self.executed = []

    async def fetchrow(self, query, *args):
        if "free_usage" in query:
            self.read_principal = args[0]
            return {"turns": self.turns_today}
        if "plans" in query:
            return {"token_budget_month": -1}
        return None

    async def execute(self, query, *args):
        self.executed.append((query, args))
```

Then append these tests:

```python
def test_free_principal_id_classifies_anon_free_and_paid():
    from control_plane import gateway

    anon = {"principal": "anonymous", "user_id": "devABC", "plan_id": "free-anonymous"}
    free = {"principal": "crowe-id", "user_id": "sub-123", "plan_id": "free"}
    paid = {"principal": "crowe-id", "user_id": "sub-999", "plan_id": "pro"}

    assert gateway._free_principal_id(anon) == "device:devABC"
    assert gateway._free_principal_id(free) == "user:sub-123"
    assert gateway._free_principal_id(paid) is None


def test_free_signed_in_user_is_turn_capped(monkeypatch):
    import asyncio
    import pytest as _pytest
    from control_plane import gateway

    async def fake_provider(**kwargs):
        return ("ok", 1, 1)

    monkeypatch.setattr(gateway, "_call_provider", lambda **kw: fake_provider(**kw))

    key_info = {
        "principal": "crowe-id",
        "user_id": "sub-123",
        "workspace_id": "sub-123",
        "plan_id": "free",
        "subject": "user@example.com",
    }
    req = gateway.GatewayRequest(
        model="crowelm-mycelium", messages=[{"role": "user", "content": "hi"}]
    )
    db = FakeFreeDb(turns_today=gateway.ANON_DAILY_TURN_CAP)
    with _pytest.raises(gateway.HTTPException) as exc:
        asyncio.run(gateway.gateway_chat(req, key_info=key_info, db=db))
    assert exc.value.status_code == 402
    assert db.read_principal == "user:sub-123"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_anonymous_gateway.py -q -k "free_principal or free_signed_in"`
Expected: FAIL — `_free_principal_id` does not exist; the cap block still reads `anon_usage` and only fires for anonymous.

- [ ] **Step 3: Add the `_free_principal_id` helper**

In `control_plane/gateway.py`, add above `gateway_chat` (near the `_is_metered` helper, ~line 391):

```python
def _free_principal_id(key_info: dict) -> str | None:
    """Return the free-tier counter key for a turn-capped principal, else None.

    Anonymous devices and signed-in `free`-plan accounts share one daily turn
    cap, keyed by `device:<id>` / `user:<sub>`. Paid principals return None
    (they are token-budget metered instead — see `_is_metered`).
    """
    from .plans import canonical_plan_id

    principal = key_info.get("principal")
    if principal == "anonymous":
        return f"device:{key_info['user_id']}"
    if principal == "crowe-id" and canonical_plan_id(key_info.get("plan_id")) == "free":
        return f"user:{key_info['user_id']}"
    return None
```

- [ ] **Step 4: Replace the cap block to use `free_usage` + the helper**

In `gateway_chat`, replace the existing anonymous cap block (the `if key_info.get("principal") == "anonymous":` block, ~lines 477-501) with:

```python
    free_pid = _free_principal_id(key_info)
    if free_pid is not None:
        from datetime import date

        today = date.today()
        row = await db.fetchrow(
            "SELECT turns FROM free_usage WHERE principal_id = $1 AND day = $2",
            free_pid,
            today,
        )
        if row and row["turns"] >= ANON_DAILY_TURN_CAP:
            is_anon = key_info.get("principal") == "anonymous"
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "free_daily_cap",
                    "message": f"Free daily limit reached ({ANON_DAILY_TURN_CAP} turns).",
                    "upsell": (
                        "Sign in to sync your free usage across devices and save your "
                        "history: run `crowe-logic login`. For higher limits, upgrade at "
                        "https://crowelogic.com/pricing"
                        if is_anon
                        else "You've used today's free turns. Upgrade for higher limits: "
                        "https://crowelogic.com/pricing"
                    ),
                },
            )
        await db.execute(
            """INSERT INTO free_usage (principal_id, day, turns) VALUES ($1, $2, 1)
               ON CONFLICT (principal_id, day) DO UPDATE SET turns = free_usage.turns + 1""",
            free_pid,
            today,
        )
```

(Task 6 finalizes the copy; this lands the working honest default so the task is self-contained.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_anonymous_gateway.py -q`
Expected: PASS. The pre-existing `test_anon_chat_under_cap_calls_provider` may reference `anon_usage`/`FakeDb`; if it asserts the old `anon_usage` SQL string, update that assertion to `free_usage` and the principal `device:<id>` (same behavior, new key). Do not weaken what it checks.

- [ ] **Step 6: Commit**

```bash
git add control_plane/gateway.py tests/test_anonymous_gateway.py
git commit -m "feat(gateway): turn-cap free signed-in accounts via principal-keyed free_usage

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: `/v1/anonymous/link` merge endpoint

**Why:** The merge itself — re-key a device's daily turns onto the signed-in account with `min(cap, account+device)` math, then delete the device rows. Idempotent; invalid token is a no-op that never blocks login.

**Files:**
- Modify: `control_plane/anonymous.py` (add `_merged_turns` pure helper + `POST /link`)
- Test: `tests/test_phase3_link.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase3_link.py`:

```python
"""Phase 3: /v1/anonymous/link device->account usage merge."""

import asyncio
import pytest

from control_plane import anonymous
from control_plane.plans import ANON_DAILY_TURN_CAP


def test_merged_turns_caps_the_sum():
    # min(cap, account + device): never resets, never over-caps into lockout.
    assert anonymous._merged_turns(0, 20, ANON_DAILY_TURN_CAP) == 20   # fresh acct + maxed device
    assert anonymous._merged_turns(18, 17, ANON_DAILY_TURN_CAP) == 20  # two devices, capped not 35
    assert anonymous._merged_turns(3, 4, ANON_DAILY_TURN_CAP) == 7     # legit small carry
    assert anonymous._merged_turns(20, 20, ANON_DAILY_TURN_CAP) == 20  # already maxed


class _LinkDb:
    """Stub: device rows to merge + account rows already present."""

    def __init__(self, device_rows, account_rows=None):
        self.device_rows = device_rows                   # [{"day": d, "turns": n}]
        self.account = dict(account_rows or {})           # {day: turns}
        self.deleted = []
        self.upserts = []

    async def fetch(self, query, *args):
        if "WHERE principal_id = $1" in query:
            return [dict(r) for r in self.device_rows]
        return []

    async def fetchval(self, query, *args):
        # account existing turns for a given (principal, day)
        return self.account.get(args[1])

    async def execute(self, query, *args):
        if query.strip().startswith("DELETE"):
            self.deleted.append(args[0])
        else:
            self.upserts.append(args)  # (account_pid, day, turns)


def test_link_merges_and_clears(monkeypatch):
    monkeypatch.setattr(anonymous, "verify_device_token", lambda raw: "devX")
    monkeypatch.setattr(anonymous.oidc, "verify_token", lambda tok: {"sub": "sub-1"})
    from datetime import date

    d = date.today()
    db = _LinkDb(device_rows=[{"day": d, "turns": 18}], account_rows={d: 17})

    body = anonymous.LinkRequest(device_token="crowe_anon_x")
    resp = asyncio.run(anonymous.link_device(body, authorization="Bearer jwt", db=db))

    assert resp["merged_days"] == 1
    assert resp["today_turns"] == 20            # min(20, 17+18)
    assert db.deleted == ["device:devX"]        # device rows cleared
    assert db.upserts[0][0] == "user:sub-1"     # merged onto the account


def test_link_invalid_token_is_noop(monkeypatch):
    monkeypatch.setattr(anonymous, "verify_device_token", lambda raw: None)
    monkeypatch.setattr(anonymous.oidc, "verify_token", lambda tok: {"sub": "sub-1"})
    db = _LinkDb(device_rows=[])
    body = anonymous.LinkRequest(device_token="garbage")
    resp = asyncio.run(anonymous.link_device(body, authorization="Bearer jwt", db=db))
    assert resp["merged_days"] == 0
    assert db.deleted == []


def test_link_requires_auth():
    db = _LinkDb(device_rows=[])
    body = anonymous.LinkRequest(device_token="crowe_anon_x")
    with pytest.raises(anonymous.HTTPException) as exc:
        asyncio.run(anonymous.link_device(body, authorization=None, db=db))
    assert exc.value.status_code == 401
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase3_link.py -q`
Expected: FAIL — `_merged_turns`, `LinkRequest`, and `link_device` do not exist.

- [ ] **Step 3: Implement the helper + endpoint**

In `control_plane/anonymous.py`, ensure these imports exist near the top (add only the missing ones — the file already has `router`, `verify_device_token`-adjacent imports, and `ANON_DAILY_TURN_CAP`):

```python
from datetime import date

from fastapi import Depends, HTTPException, Header
from pydantic import BaseModel

from . import oidc
from .db import get_db
from .tokens import verify_device_token
from .plans import ANON_DAILY_TURN_CAP
```

Add the pure helper and the endpoint (after the existing `register_device`):

```python
def _merged_turns(account_turns: int, device_turns: int, cap: int) -> int:
    """Anti-abuse merge math: never reset, never over-cap into a lockout."""
    return min(cap, account_turns + device_turns)


class LinkRequest(BaseModel):
    device_token: str


@router.post("/link")
async def link_device(
    body: LinkRequest,
    authorization: str | None = Header(default=None),
    db=Depends(get_db),
) -> dict:
    """Merge an anonymous device's daily turns onto the signed-in account.

    Auth: Crowe ID bearer (required). Invalid/expired device token -> no-op
    (never blocks login). Idempotent: device rows are deleted after merge.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Crowe ID token required")
    try:
        claims = oidc.verify_token(authorization[7:])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail=f"Invalid Crowe ID token: {exc}")

    account_pid = f"user:{claims['sub']}"

    device_id = verify_device_token(body.device_token)
    if not device_id:
        return {"merged_days": 0, "today_turns": None, "cap": ANON_DAILY_TURN_CAP}
    device_pid = f"device:{device_id}"

    rows = await db.fetch(
        "SELECT day, turns FROM free_usage WHERE principal_id = $1",
        device_pid,
    )

    today = date.today()
    today_turns = None
    for r in rows:
        existing = await db.fetchval(
            "SELECT turns FROM free_usage WHERE principal_id = $1 AND day = $2",
            account_pid,
            r["day"],
        ) or 0
        merged = _merged_turns(existing, r["turns"], ANON_DAILY_TURN_CAP)
        await db.execute(
            """INSERT INTO free_usage (principal_id, day, turns) VALUES ($1, $2, $3)
               ON CONFLICT (principal_id, day) DO UPDATE SET turns = EXCLUDED.turns""",
            account_pid,
            r["day"],
            merged,
        )
        if r["day"] == today:
            today_turns = merged

    await db.execute("DELETE FROM free_usage WHERE principal_id = $1", device_pid)

    return {"merged_days": len(rows), "today_turns": today_turns, "cap": ANON_DAILY_TURN_CAP}
```

> If `control_plane/anonymous.py` already imports some of these names, do not duplicate — add only the missing ones (`oidc`, `Header`, `BaseModel`, `date`, `get_db`).

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_phase3_link.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add control_plane/anonymous.py tests/test_phase3_link.py
git commit -m "feat(gateway): /v1/anonymous/link merges device turns onto account (min-cap)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: CLI — call `/link` on login

**Why:** Wire the client side: after a successful `crowe-logic login`, send the on-disk device token to `/v1/anonymous/link`, then delete `device.json`. A link failure must not fail the login.

**Files:**
- Modify: `cli/gateway_client.py` (add `link_device`)
- Modify: `cli/crowe_logic.py` (`login_cmd` calls it)
- Test: `tests/test_phase3_link_client.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase3_link_client.py`:

```python
"""Phase 3: CLI link_device posts the device token and clears the local store."""

import json
import httpx
import pytest

from cli import gateway_client


def test_link_device_posts_and_clears(tmp_path, monkeypatch):
    store = tmp_path / "device.json"
    store.write_text(json.dumps({"token": "crowe_anon_x", "device_id": "devX"}))
    monkeypatch.setattr(gateway_client, "DEVICE_STORE", str(store))

    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent["url"] = url
        sent["json"] = json
        sent["auth"] = headers.get("Authorization")
        return httpx.Response(200, json={"merged_days": 1, "today_turns": 5, "cap": 20})

    monkeypatch.setattr(gateway_client.httpx, "post", fake_post)

    result = gateway_client.link_device(bearer="jwt-abc")

    assert result["merged_days"] == 1
    assert sent["url"].endswith("/v1/anonymous/link")
    assert sent["json"] == {"device_token": "crowe_anon_x"}
    assert sent["auth"] == "Bearer jwt-abc"
    assert not store.exists()  # device.json deleted after a successful merge


def test_link_device_no_store_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_client, "DEVICE_STORE", str(tmp_path / "nope.json"))
    assert gateway_client.link_device(bearer="jwt") is None


def test_link_device_failure_keeps_store(tmp_path, monkeypatch):
    store = tmp_path / "device.json"
    store.write_text(json.dumps({"token": "crowe_anon_x"}))
    monkeypatch.setattr(gateway_client, "DEVICE_STORE", str(store))

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(gateway_client.httpx, "post", boom)
    # Must not raise; device.json retained so a later login retries.
    assert gateway_client.link_device(bearer="jwt") is None
    assert store.exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_phase3_link_client.py -q`
Expected: FAIL — `gateway_client.link_device` does not exist.

- [ ] **Step 3: Implement `link_device` in `cli/gateway_client.py`**

Add after `register_device` (which already defines `GATEWAY_BASE`, `DEVICE_STORE`, `load_device`, `_TIMEOUT`):

```python
def link_device(bearer: str) -> dict | None:
    """Merge a stored anonymous device token onto the signed-in account.

    No-op when no device store exists. Never raises: a merge failure leaves
    device.json in place so the next login can retry. Returns the merge summary
    on success, else None.
    """
    device = load_device()
    if not device or not device.get("token"):
        return None
    try:
        resp = httpx.post(
            f"{GATEWAY_BASE}/v1/anonymous/link",
            json={"device_token": device["token"]},
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception:
        return None  # keep device.json; retry on next login
    try:
        os.remove(DEVICE_STORE)
    except OSError:
        pass
    return result
```

> Confirm `os` and `_TIMEOUT` are imported at the top of `cli/gateway_client.py` (they back `save_device`/`chat`). If `os` is missing, add `import os`.

- [ ] **Step 4: Call it from `login_cmd` in `cli/crowe_logic.py`**

`login_cmd` (near line 2768) currently does `who = auth.login_pkce()`. Add the link call after a successful login and surface a one-line note. The new body:

```python
def login_cmd():
    """Sign in to Crowe ID in the browser (PKCE)."""
    from cli import auth, gateway_client

    try:
        who = auth.login_pkce()
    except Exception as exc:  # noqa: BLE001
        _render_error(str(exc), "Sign-in failed")
        return

    # Merge any anonymous free-tier usage onto the account (anti-abuse continuity).
    merged = gateway_client.link_device(bearer=auth.current_access_token())
    console.print(f"  [#bfa669]Signed in[/] as {who.get('username', 'Crowe ID')}.")
    if merged and merged.get("merged_days"):
        console.print("  [dim]Your free usage now syncs across devices.[/dim]")
```

> If `login_cmd` already prints tier/welcome detail, preserve it — only add the `link_device` call and its note. `auth.current_access_token()` is the existing accessor used by `gateway_client._token`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_phase3_link_client.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add cli/gateway_client.py cli/crowe_logic.py tests/test_phase3_link_client.py
git commit -m "feat(cli): link anonymous device usage onto the account on login

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: Finalize honest cap-wall copy + full-suite check

**Why:** Lock the upsell copy (Task 3 landed a working default; this is the explicit copy review) and confirm the whole change sits at the test baseline before shipping.

**Files:**
- Modify: `control_plane/gateway.py` (only if the Task 3 copy needs wording tweaks)
- Test: `tests/test_anonymous_gateway.py` (append a copy assertion)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_anonymous_gateway.py`:

```python
def test_anon_cap_upsell_promotes_login_free_cap_promotes_upgrade(monkeypatch):
    import asyncio
    import pytest as _pytest
    from control_plane import gateway

    async def fake_provider(**kwargs):
        return ("ok", 1, 1)

    monkeypatch.setattr(gateway, "_call_provider", lambda **kw: fake_provider(**kw))
    req = gateway.GatewayRequest(
        model="crowelm-mycelium", messages=[{"role": "user", "content": "hi"}]
    )

    # Anonymous at cap -> upsell mentions `crowe-logic login`.
    anon = {"principal": "anonymous", "user_id": "devX", "workspace_id": "devX",
            "plan_id": "free-anonymous", "subject": "anon:devX"}
    with _pytest.raises(gateway.HTTPException) as e1:
        asyncio.run(gateway.gateway_chat(req, key_info=anon,
                    db=FakeFreeDb(turns_today=gateway.ANON_DAILY_TURN_CAP)))
    assert "crowe-logic login" in e1.value.detail["upsell"]

    # Signed-in free at cap -> upsell promotes upgrade, not login.
    free = {"principal": "crowe-id", "user_id": "sub-1", "workspace_id": "sub-1",
            "plan_id": "free", "subject": "u@example.com"}
    with _pytest.raises(gateway.HTTPException) as e2:
        asyncio.run(gateway.gateway_chat(req, key_info=free,
                    db=FakeFreeDb(turns_today=gateway.ANON_DAILY_TURN_CAP)))
    assert "crowe-logic login" not in e2.value.detail["upsell"]
    assert "pricing" in e2.value.detail["upsell"]
```

- [ ] **Step 2: Run the test**

Run: `.venv/bin/python -m pytest tests/test_anonymous_gateway.py -q -k upsell`
Expected: PASS if Task 3's copy already differentiates anon vs free (it does). If wording differs, adjust the `detail["upsell"]` strings in `gateway.py` to satisfy the assertions, keeping the honest split (anon → login; free → upgrade).

- [ ] **Step 3: Run the full suite for the baseline check**

Run: `.venv/bin/python -m pytest tests/ -q --continue-on-collection-errors 2>&1 | tail -1`
Expected: failed count == 18 (baseline). New passing tests added by Phase 3 raise only the passed count.

- [ ] **Step 4: Commit**

```bash
git add control_plane/gateway.py tests/test_anonymous_gateway.py
git commit -m "feat(gateway): honest cap-wall copy (anon->login, free->upgrade)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: Deploy + live integration walk (manual, gated)

**Why:** Phase 3 is server-side first. The endpoint must be live before the CLI wheel that calls it ships — the same ordering discipline as Phase 2.

**This task is operational, not code. Do not run it as part of TDD.** It is the runbook for shipping after Tasks 1-6 merge.

- [ ] **Step 1: Pre-deploy verification gate (Task 1 note)**

Confirm in Keycloak that paid Personal subscribers carry `crowe_tier="personal"` (or pro/studio/enterprise), not empty/`"free"`. If any paid customer relies on the old empty→personal default, hold deployment until billing sets a real tier claim. (See the Task 1 VERIFICATION NOTE.)

- [ ] **Step 2: Apply migration 011 to prod**

Apply via the established in-container exec pattern (local psql is blocked by the PG firewall; the classifier blocks firewall edits). Same approach used to apply migration 010 on 2026-06-06: base64-encode `migrations/011_free_usage.sql`, then `script -q /tmp/out.txt az containerapp exec -g rg-foundry-prod -n crowe-foundry --command "python -c 'import base64,asyncio,asyncpg,os; ...execute(base64.b64decode(B64))...'"`. Verify: `SELECT count(*) FROM free_usage;` returns the rekeyed anon rows.

- [ ] **Step 3: Build + digest-pin deploy the gateway image**

```bash
git worktree add /tmp/p3 origin/main
cd /tmp/p3
az acr build -r crowefoundryreg2026 -t foundry-control-plane:phase3 -f Dockerfile.control-plane .
az containerapp update -g rg-foundry-prod -n crowe-foundry --image "crowefoundryreg2026.azurecr.io/foundry-control-plane@sha256:<digest-from-build>"
```

Wait for the new revision to report `Healthy Running`; `curl -s https://api.crowelogic.com/health`.

- [ ] **Step 4: Live integration walk (the real proof)**

From the OrbStack `ubuntu` machine (fresh egress, not rate-limited):

```
# 1. Burn the anon free tier to the cap (loop crowe-logic run until the 402);
#    note the device's turn count.
# 2. crowe-logic login  -> completes PKCE, prints "free usage now syncs across devices".
# 3. Confirm device.json is gone:   ls ~/.config/crowe-logic/device.json  (absent)
# 4. crowe-logic run "..."  -> STILL capped (no reset). The account inherited the count.
# 5. Inspect free_usage: the user:<sub> row holds the merged (min-capped) turns;
#    the device:<id> rows are gone.
```

- [ ] **Step 5: Cut the CLI wheel (after the endpoint is confirmed live)**

Bump version, build, `twine upload` — same as the 0.4.x releases. Never publish the wheel before Step 3 confirms `/v1/anonymous/link` answers.

---

## Self-Review

**Spec coverage:**
- `free` plan tier → Task 1. Principal-keyed counter → Tasks 2-3. `/v1/anonymous/link` with `min(cap, …)` → Task 4. Login link call → Task 5. Honest upsell copy → Tasks 3+6. Migration safety (rekey, `.down`) → Task 2. Abuse boundary + error handling (no-op on bad token, login never blocked, deny-by-default) → Tasks 4-5. Deploy ordering → Task 7. Every spec section maps to a task.

**Placeholder scan:** No TBD/TODO. Every code step shows full code; `<digest-from-build>` in Task 7 is an operational value produced by the build command, not a code placeholder.

**Type/name consistency:** `free_usage(principal_id, day, turns)` used identically in Tasks 2/3/4. `_free_principal_id` (Task 3) and `_merged_turns`/`LinkRequest`/`link_device` (Task 4) referenced consistently. `gateway_client.link_device(bearer=...)` signature matches between the Task 5 implementation and the `login_cmd` call site. `ANON_DAILY_TURN_CAP` is the single source of the cap throughout.

**Known soft spot (flagged, not hidden):** the migration test asserts SQL *content*, not a live apply (the control plane has no in-process Postgres in CI); the real apply is verified manually in Task 7 Step 2. The `_LinkDb`/`FakeFreeDb` stubs model the queries the endpoints actually run; the live SQL is exercised in the Task 7 walk.
