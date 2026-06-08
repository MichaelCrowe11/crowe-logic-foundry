# Agent-Native Payment Rail (x402) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Foundry gateway's existing internal 402 credit wallet into an open, agent-payable **x402** rail: an agent authenticates with a Crowe ID `client_credentials` token, gets a machine-readable 402 with a price, pays (Crowe-credit grant now; USDC-on-Base interface ready), and is served — no human in the loop.

**Architecture:** Five small, single-responsibility modules added to `control_plane/`, each composed in a thin endpoint. Pure protocol units (`x402.py` envelope/parse, `agents.py` identity) carry no I/O and are unit-tested in isolation. DB units (`agent_wallets.py` ledger, mirrors the proven `consume_credits` atomic-debit pattern) and `settlement.py` (scheme verifiers) are tested against the SQLite preview app. Slice 1 ships a **dedicated** `POST /api/agent/v1/chat` so the live human/API-key `/chat` path is untouched.

**Tech Stack:** Python 3.10+, FastAPI, asyncpg/SQLite (`control_plane/db.py` + `preview.py` mock), PyJWT (RS256 Crowe ID verify), `hmac`/`hashlib` (credit grants), pytest + pytest-asyncio. Pricing unit = **integer micro-USD** across both ledgers.

---

## File Structure

- **Create** `control_plane/x402.py` — price catalog + 402 envelope builder + `X-PAYMENT` parser (pure).
- **Create** `control_plane/agents.py` — agent-token detection + agent principal mapping (pure).
- **Create** `control_plane/agent_wallets.py` — `agent_wallets` ledger: ensure / atomic debit / idempotent credit.
- **Create** `control_plane/settlement.py` — scheme verifiers (credit-grant HMAC now, chain interface stubbed).
- **Create** `control_plane/agent_gateway.py` — `POST /api/agent/v1/chat` + `/.well-known/x402` + `/.well-known/agent` router, wired into the app.
- **Create** `migrations/0NN_agent_wallets.sql` — `agent_wallets` + `payment_receipts` tables (NN = next number).
- **Modify** `control_plane/preview.py` — add the two tables to the SQLite mock schema so tests + local boot work.
- **Modify** `control_plane/__init__.py` (or wherever `app.include_router` lives) — mount the new router.
- **Create** tests: `tests/test_x402_envelope.py`, `tests/test_agent_identity.py`, `tests/test_agent_wallets.py`, `tests/test_settlement.py`, `tests/test_agent_gateway.py`, `tests/test_discovery_manifest.py`.

Run all tests from the repo with the repo venv: `.venv/bin/python -m pytest <file> -q` (the `crowe-logic-foundry` PATH hook does not fire in non-interactive shells — invoke `.venv/bin/python` directly).

---

### Task 0: Migration — agent_wallets + payment_receipts tables

**Files:**
- Create: `migrations/0NN_agent_wallets.sql`
- Modify: `control_plane/preview.py` (SQLite mock schema)

- [ ] **Step 1: Determine the next migration number**

Run: `ls migrations/ | sort | tail -3`
Use the next integer (e.g. if last is `009_*.sql`, create `010_agent_wallets.sql`). Substitute that number for `0NN` below.

- [ ] **Step 2: Write the Postgres migration**

Create `migrations/0NN_agent_wallets.sql`:
```sql
-- Agent-native payment rail: per-agent wallet + idempotent payment receipts.
CREATE TABLE IF NOT EXISTS agent_wallets (
    client_id     TEXT PRIMARY KEY,
    balance       BIGINT NOT NULL DEFAULT 0,        -- micro-USD
    funding       TEXT   NOT NULL DEFAULT 'crowe-credit',
    chain_address TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payment_receipts (
    id          TEXT PRIMARY KEY,                   -- payment nonce (idempotency key)
    client_id   TEXT NOT NULL REFERENCES agent_wallets(client_id),
    scheme      TEXT NOT NULL,
    amount      BIGINT NOT NULL,
    resource    TEXT NOT NULL,
    tx_ref      TEXT,
    settled_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 3: Mirror the tables in the SQLite preview mock**

In `control_plane/preview.py`, find the block of `CREATE TABLE IF NOT EXISTS ...` statements (near the `users`/`organizations` definitions) and add:
```python
            CREATE TABLE IF NOT EXISTS agent_wallets (
                client_id     TEXT PRIMARY KEY,
                balance       INTEGER NOT NULL DEFAULT 0,
                funding       TEXT NOT NULL DEFAULT 'crowe-credit',
                chain_address TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS payment_receipts (
                id         TEXT PRIMARY KEY,
                client_id  TEXT NOT NULL,
                scheme     TEXT NOT NULL,
                amount     INTEGER NOT NULL,
                resource   TEXT NOT NULL,
                tx_ref     TEXT,
                settled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
```
(Match the surrounding statements' exact string-literal/execution style — if they are separate `cur.execute("...")` calls, add two separate calls instead.)

- [ ] **Step 4: Boot the preview app to confirm the schema applies**

Run: `.venv/bin/python -c "from control_plane.preview import app; print('preview app import OK')"`
Expected: `preview app import OK` with no schema error.

- [ ] **Step 5: Commit**

```bash
git add migrations/0NN_agent_wallets.sql control_plane/preview.py
git commit -m "feat(x402): agent_wallets + payment_receipts tables (pg migration + sqlite mock)"
```

---

### Task 1: x402 envelope + price catalog (pure)

**Files:**
- Create: `control_plane/x402.py`
- Test: `tests/test_x402_envelope.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_x402_envelope.py`:
```python
import base64
import json

import pytest

from control_plane import x402


def test_price_catalog_has_slice1_endpoint():
    assert x402.price_for("/api/agent/v1/chat") > 0


def test_unknown_resource_raises():
    with pytest.raises(KeyError):
        x402.price_for("/nope")


def test_envelope_advertises_both_schemes():
    env = x402.build_payment_required("/api/agent/v1/chat")
    assert env["x402Version"] == 1
    schemes = {a["scheme"] for a in env["accepts"]}
    assert schemes == {"exact", "crowe-credit"}
    for a in env["accepts"]:
        assert a["resource"] == "/api/agent/v1/chat"
        assert int(a["maxAmountRequired"]) == x402.price_for("/api/agent/v1/chat")


def test_parse_x_payment_roundtrip():
    payload = {"scheme": "crowe-credit", "nonce": "n1", "resource": "/api/agent/v1/chat",
               "amount": 50, "grant": "abc"}
    header = base64.b64encode(json.dumps(payload).encode()).decode()
    assert x402.parse_x_payment(header) == payload


def test_parse_x_payment_rejects_garbage():
    with pytest.raises(ValueError):
        x402.parse_x_payment("!!!not-base64-json!!!")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_x402_envelope.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'control_plane.x402'`.

- [ ] **Step 3: Write minimal implementation**

Create `control_plane/x402.py`:
```python
"""x402 protocol surface: price catalog, 402 envelope builder, X-PAYMENT parser.

Pure functions — no I/O. The price catalog is the single source of truth shared by
the 402 envelope and the discovery manifest, so the quoted price and the charged
price can never drift. Prices are integer micro-USD (1 unit = $0.000001).
"""
from __future__ import annotations

import base64
import json
import os

# Single source of truth. Add an entry here to monetize a new endpoint.
PRICE_CATALOG: dict[str, int] = {
    "/api/agent/v1/chat": 50,  # micro-USD per call (slice-1 default; tune later)
}

CROWE_BASE_PAYTO = os.environ.get("X402_BASE_PAYTO", "0xCROWE_BASE_TREASURY_PLACEHOLDER")
X402_NETWORK = os.environ.get("X402_NETWORK", "base")
X402_ASSET = os.environ.get("X402_ASSET", "USDC")


def price_for(resource: str) -> int:
    """Price in micro-USD for a metered resource. Raises KeyError if unpriced."""
    return PRICE_CATALOG[resource]


def build_payment_required(resource: str, price: int | None = None) -> dict:
    """Build the x402 `402` body advertising both settlement schemes."""
    amount = price if price is not None else price_for(resource)
    amount_s = str(amount)
    return {
        "x402Version": 1,
        "error": "payment required",
        "accepts": [
            {
                "scheme": "exact",
                "network": X402_NETWORK,
                "asset": X402_ASSET,
                "maxAmountRequired": amount_s,
                "payTo": CROWE_BASE_PAYTO,
                "resource": resource,
                "mimeType": "application/json",
            },
            {
                "scheme": "crowe-credit",
                "network": "crowe",
                "asset": "credit",
                "maxAmountRequired": amount_s,
                "payTo": "crowe-ledger",
                "resource": resource,
                "mimeType": "application/json",
            },
        ],
    }


def parse_x_payment(header: str) -> dict:
    """Decode a base64-encoded JSON X-PAYMENT header. Raises ValueError on garbage."""
    try:
        raw = base64.b64decode(header, validate=True)
        obj = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"malformed X-PAYMENT header: {exc}")
    if not isinstance(obj, dict):
        raise ValueError("X-PAYMENT must decode to a JSON object")
    return obj
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_x402_envelope.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add control_plane/x402.py tests/test_x402_envelope.py
git commit -m "feat(x402): price catalog + 402 envelope + X-PAYMENT parser (pure)"
```

---

### Task 2: Agent identity (pure)

**Files:**
- Create: `control_plane/agents.py`
- Test: `tests/test_agent_identity.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_identity.py`:
```python
from control_plane import agents


def test_human_token_is_not_agent():
    claims = {"sub": "kc-sub-1", "preferred_username": "michael@crowelogic.com",
              "email": "michael@crowelogic.com", "crowe_tier": "enterprise"}
    assert agents.is_agent_token(claims) is False


def test_service_account_token_is_agent():
    claims = {"sub": "svc-sub-9", "preferred_username": "service-account-agent-alpha",
              "clientId": "agent-alpha", "azp": "agent-alpha", "crowe_tier": "pro"}
    assert agents.is_agent_token(claims) is True


def test_agent_principal_shape():
    claims = {"sub": "svc-sub-9", "preferred_username": "service-account-agent-alpha",
              "clientId": "agent-alpha", "azp": "agent-alpha", "crowe_tier": "pro"}
    p = agents.agent_principal(claims)
    assert p == {
        "principal": "crowe-agent",
        "client_id": "agent-alpha",
        "workspace_id": "agent-alpha",
        "user_id": "svc-sub-9",
        "plan_id": "pro",
        "subject": "service-account-agent-alpha",
    }


def test_agent_principal_falls_back_to_azp_then_sub():
    claims = {"sub": "svc-sub-7", "preferred_username": "service-account-x",
              "azp": "x", "crowe_tier": "free"}
    assert agents.agent_principal(claims)["client_id"] == "x"
    assert agents.agent_principal({"sub": "only-sub",
                                   "preferred_username": "service-account-only"}
                                  )["client_id"] == "only-sub"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_identity.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'control_plane.agents'`.

- [ ] **Step 3: Write minimal implementation**

Create `control_plane/agents.py`:
```python
"""Agent identity: distinguish Crowe ID `client_credentials` (machine) tokens from
human logins, and map them to a metered agent principal keyed by client_id.

Keycloak stamps service-account (client_credentials) tokens with
`preferred_username = "service-account-<clientId>"` and a `clientId`/`azp` claim,
and carries no human `email`. That is our discriminator.
"""
from __future__ import annotations

from . import oidc


def is_agent_token(claims: dict) -> bool:
    """True if the verified token is a machine (client_credentials) principal."""
    username = claims.get("preferred_username", "") or ""
    return username.startswith("service-account-")


def agent_principal(claims: dict) -> dict:
    """Map verified agent-token claims to a metered principal dict.

    client_id resolution order: clientId -> azp -> sub (always non-empty).
    workspace_id == client_id so the agent's wallet is keyed by its identity.
    """
    client_id = claims.get("clientId") or claims.get("azp") or claims["sub"]
    return {
        "principal": "crowe-agent",
        "client_id": client_id,
        "workspace_id": client_id,
        "user_id": claims["sub"],
        "plan_id": oidc.tier_to_plan(claims.get("crowe_tier")),
        "subject": claims.get("preferred_username"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_identity.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add control_plane/agents.py tests/test_agent_identity.py
git commit -m "feat(x402): agent-token detection + agent principal mapping (pure)"
```

---

### Task 3: Agent wallet ledger (atomic debit + idempotent credit)

**Files:**
- Create: `control_plane/agent_wallets.py`
- Test: `tests/test_agent_wallets.py`

This mirrors the proven `consume_credits` pattern (`control_plane/__init__.py:1145`): a conditional `UPDATE ... WHERE balance >= amount` so concurrent debits can't oversell. Credits are made idempotent by inserting the payment receipt (PK = nonce) in the same transaction — a replayed payment hits the PK and is rejected.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_wallets.py`:
```python
import pytest

from control_plane import agent_wallets as w
from control_plane.db import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path}/wallets.db")
    await database.connect()
    await database.execute(
        "CREATE TABLE agent_wallets (client_id TEXT PRIMARY KEY, balance INTEGER NOT NULL "
        "DEFAULT 0, funding TEXT DEFAULT 'crowe-credit', chain_address TEXT)"
    )
    await database.execute(
        "CREATE TABLE payment_receipts (id TEXT PRIMARY KEY, client_id TEXT, scheme TEXT, "
        "amount INTEGER, resource TEXT, tx_ref TEXT)"
    )
    yield database
    await database.disconnect()


@pytest.mark.asyncio
async def test_new_wallet_starts_at_zero(db):
    row = await w.ensure_wallet(db, "agent-1")
    assert row["balance"] == 0


@pytest.mark.asyncio
async def test_credit_then_debit(db):
    await w.ensure_wallet(db, "agent-1")
    bal = await w.credit(db, "agent-1", 100, receipt_id="r1", scheme="crowe-credit",
                         resource="/api/agent/v1/chat", tx_ref=None)
    assert bal == 100
    bal2 = await w.debit(db, "agent-1", 30)
    assert bal2 == 70


@pytest.mark.asyncio
async def test_debit_insufficient_raises(db):
    await w.ensure_wallet(db, "agent-1")
    with pytest.raises(w.InsufficientFunds):
        await w.debit(db, "agent-1", 5)


@pytest.mark.asyncio
async def test_credit_is_idempotent_on_replayed_receipt(db):
    await w.ensure_wallet(db, "agent-1")
    await w.credit(db, "agent-1", 100, receipt_id="r1", scheme="crowe-credit",
                   resource="/api/agent/v1/chat", tx_ref=None)
    with pytest.raises(w.DuplicatePayment):
        await w.credit(db, "agent-1", 100, receipt_id="r1", scheme="crowe-credit",
                       resource="/api/agent/v1/chat", tx_ref=None)
    # balance only moved once
    row = await w.ensure_wallet(db, "agent-1")
    assert row["balance"] == 100
```

> Note: if `control_plane.db.Database` does not accept a `sqlite:///` URL or lacks `connect/disconnect`, mirror the exact construction used in `tests/test_control_plane.py` instead (read that file's db fixture and copy it verbatim). Keep the table DDL above.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_wallets.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'control_plane.agent_wallets'`.

- [ ] **Step 3: Write minimal implementation**

Create `control_plane/agent_wallets.py`:
```python
"""Per-agent wallet ledger keyed by Crowe ID client_id (micro-USD balance).

Debit reuses the conditional-update guard proven in workspace_credits.consume.
Credit is idempotent: the payment receipt (PK = nonce) is inserted alongside the
balance bump, so a replayed payment is rejected instead of double-crediting.
"""
from __future__ import annotations

from .db import Database


class InsufficientFunds(Exception):
    """Raised when a debit exceeds the available balance."""


class DuplicatePayment(Exception):
    """Raised when a payment receipt id has already been settled (replay)."""


async def ensure_wallet(db: Database, client_id: str) -> dict:
    """Return the wallet row, creating a zero-balance row if missing."""
    row = await db.fetchrow("SELECT * FROM agent_wallets WHERE client_id = $1", client_id)
    if row:
        return dict(row)
    await db.execute(
        "INSERT INTO agent_wallets (client_id, balance) VALUES ($1, 0) "
        "ON CONFLICT (client_id) DO NOTHING",
        client_id,
    )
    row = await db.fetchrow("SELECT * FROM agent_wallets WHERE client_id = $1", client_id)
    return dict(row) if row else {"client_id": client_id, "balance": 0}


async def debit(db: Database, client_id: str, amount: int) -> int:
    """Atomically subtract `amount`; return the new balance or raise InsufficientFunds."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    updated = await db.fetchrow(
        "UPDATE agent_wallets SET balance = balance - $2 "
        "WHERE client_id = $1 AND balance >= $2 RETURNING balance",
        client_id, amount,
    )
    if updated is None:
        raise InsufficientFunds(f"client={client_id} amount={amount}")
    return updated["balance"]


async def credit(db: Database, client_id: str, amount: int, *, receipt_id: str,
                 scheme: str, resource: str, tx_ref: str | None) -> int:
    """Idempotently add `amount` and record the receipt. Raise DuplicatePayment on replay."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    existing = await db.fetchrow("SELECT id FROM payment_receipts WHERE id = $1", receipt_id)
    if existing:
        raise DuplicatePayment(receipt_id)
    await db.execute(
        "INSERT INTO payment_receipts (id, client_id, scheme, amount, resource, tx_ref) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        receipt_id, client_id, scheme, amount, resource, tx_ref,
    )
    updated = await db.fetchrow(
        "UPDATE agent_wallets SET balance = balance + $2 WHERE client_id = $1 RETURNING balance",
        client_id, amount,
    )
    return updated["balance"]
```

> If `db.fetchrow`/`db.execute` use `?` placeholders for SQLite rather than `$1`, follow whatever `control_plane/__init__.py` does for `workspace_credits` (it uses `$1`-style, so the abstraction already translates — keep `$1`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_wallets.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add control_plane/agent_wallets.py tests/test_agent_wallets.py
git commit -m "feat(x402): agent_wallets ledger — atomic debit + idempotent credit"
```

---

### Task 4: Settlement — scheme verifiers

**Files:**
- Create: `control_plane/settlement.py`
- Test: `tests/test_settlement.py`

Slice 1 fully implements the **crowe-credit** scheme (an HMAC-signed grant the gateway itself mints after a Stripe top-up or enterprise allocation — self-issued, so it can't be forged by the agent). The **exact (chain)** scheme returns an explicit "not yet enabled" error until the facilitator URL is wired in slice 2 — the envelope still advertises it (no silent cap).

- [ ] **Step 1: Write the failing test**

Create `tests/test_settlement.py`:
```python
import pytest

from control_plane import settlement


def test_mint_and_verify_credit_grant():
    grant = settlement.mint_credit_grant(client_id="agent-1", amount=50, nonce="n1")
    payment = {"scheme": "crowe-credit", "nonce": "n1", "amount": 50,
               "resource": "/api/agent/v1/chat", "grant": grant}
    receipt = settlement.verify_payment(payment, client_id="agent-1",
                                        resource="/api/agent/v1/chat", price=50)
    assert receipt.scheme == "crowe-credit"
    assert receipt.amount == 50
    assert receipt.id == "n1"


def test_forged_grant_rejected():
    payment = {"scheme": "crowe-credit", "nonce": "n1", "amount": 50,
               "resource": "/api/agent/v1/chat", "grant": "forged"}
    with pytest.raises(settlement.PaymentError):
        settlement.verify_payment(payment, client_id="agent-1",
                                  resource="/api/agent/v1/chat", price=50)


def test_grant_bound_to_resource_and_amount():
    grant = settlement.mint_credit_grant(client_id="agent-1", amount=50, nonce="n1")
    # underpay attempt: price is 999 but grant is for 50
    payment = {"scheme": "crowe-credit", "nonce": "n1", "amount": 50,
               "resource": "/api/agent/v1/chat", "grant": grant}
    with pytest.raises(settlement.PaymentError):
        settlement.verify_payment(payment, client_id="agent-1",
                                  resource="/api/agent/v1/chat", price=999)


def test_chain_scheme_not_enabled_by_default(monkeypatch):
    monkeypatch.delenv("X402_FACILITATOR_URL", raising=False)
    payment = {"scheme": "exact", "nonce": "n2", "amount": 50,
               "resource": "/api/agent/v1/chat", "txHash": "0xabc"}
    with pytest.raises(settlement.PaymentError) as exc:
        settlement.verify_payment(payment, client_id="agent-1",
                                  resource="/api/agent/v1/chat", price=50)
    assert "not yet enabled" in str(exc.value).lower()


def test_unknown_scheme_rejected():
    with pytest.raises(settlement.PaymentError):
        settlement.verify_payment({"scheme": "bogus", "nonce": "n"}, client_id="a",
                                  resource="/r", price=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_settlement.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'control_plane.settlement'`.

- [ ] **Step 3: Write minimal implementation**

Create `control_plane/settlement.py`:
```python
"""Payment settlement verifiers for the x402 rail.

- crowe-credit: an HMAC-signed grant minted by the gateway (after a Stripe top-up
  or enterprise allocation). Self-issued, bound to (client_id, amount, nonce), so
  an agent cannot forge or replay-across-resource it.
- exact (chain): USDC-on-Base. Verified via the configured facilitator; returns an
  explicit "not yet enabled" error until X402_FACILITATOR_URL is set (slice 2).
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass

_GRANT_SECRET = os.environ.get("X402_GRANT_SECRET", "dev-grant-secret-change-me").encode()


class PaymentError(Exception):
    """Verification failed: forged, malformed, underpaid, or scheme unavailable."""


@dataclass(frozen=True)
class Receipt:
    id: str          # payment nonce (idempotency key)
    scheme: str
    amount: int
    tx_ref: str | None


def _grant_sig(client_id: str, amount: int, nonce: str) -> str:
    msg = f"{client_id}:{amount}:{nonce}".encode()
    return hmac.new(_GRANT_SECRET, msg, hashlib.sha256).hexdigest()


def mint_credit_grant(client_id: str, amount: int, nonce: str) -> str:
    """Mint a credit grant the agent presents as `grant` in its X-PAYMENT payload."""
    return _grant_sig(client_id, amount, nonce)


def verify_payment(payment: dict, *, client_id: str, resource: str, price: int) -> Receipt:
    """Verify a decoded X-PAYMENT payload. Return a Receipt or raise PaymentError."""
    scheme = payment.get("scheme")
    nonce = payment.get("nonce")
    amount = payment.get("amount")
    if not nonce or not isinstance(amount, int):
        raise PaymentError("missing nonce or non-integer amount")
    if amount < price:
        raise PaymentError(f"underpaid: amount={amount} < price={price}")

    if scheme == "crowe-credit":
        expected = _grant_sig(client_id, amount, nonce)
        if not hmac.compare_digest(expected, str(payment.get("grant", ""))):
            raise PaymentError("invalid credit grant signature")
        return Receipt(id=nonce, scheme=scheme, amount=amount, tx_ref=None)

    if scheme == "exact":
        if not os.environ.get("X402_FACILITATOR_URL"):
            raise PaymentError("chain settlement not yet enabled — use the crowe-credit scheme")
        # Slice 2: POST payment to the facilitator, confirm on-chain, return Receipt.
        raise PaymentError("chain settlement not yet enabled — use the crowe-credit scheme")

    raise PaymentError(f"unknown scheme: {scheme!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_settlement.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add control_plane/settlement.py tests/test_settlement.py
git commit -m "feat(x402): settlement verifiers — credit-grant (HMAC) + chain interface"
```

---

### Task 5: Agent gateway endpoint (the wiring)

**Files:**
- Create: `control_plane/agent_gateway.py`
- Modify: the app module that calls `include_router` (find with grep in Step 0)
- Test: `tests/test_agent_gateway.py`

- [ ] **Step 0: Locate where routers are mounted**

Run: `grep -rn "include_router" control_plane/`
Note the file + the `app`/router variable. You will add `app.include_router(agent_gateway.router)` there (Step 6).

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_gateway.py`:
```python
import base64
import json

import pytest
from httpx import ASGITransport, AsyncClient

from control_plane import agent_gateway, settlement, x402


def _xpayment(client_id, amount, nonce, resource):
    grant = settlement.mint_credit_grant(client_id, amount, nonce)
    payload = {"scheme": "crowe-credit", "nonce": nonce, "amount": amount,
               "resource": resource, "grant": grant}
    return base64.b64encode(json.dumps(payload).encode()).decode()


@pytest.fixture
def agent_principal(monkeypatch):
    # Bypass JWKS/JWT verification: force the resolver to return a fixed agent principal.
    monkeypatch.setattr(
        agent_gateway, "resolve_agent_principal",
        lambda authorization: {"principal": "crowe-agent", "client_id": "agent-1",
                               "workspace_id": "agent-1", "user_id": "svc",
                               "plan_id": "pro", "subject": "service-account-agent-1"},
    )
    # Stub the provider call so no real model is hit.
    async def _fake_call(**kwargs):
        return ("hello from agent", 3, 5)
    monkeypatch.setattr(agent_gateway, "call_model", _fake_call)


@pytest.fixture
async def client(monkeypatch, tmp_path):
    # Use an isolated SQLite db with the two tables.
    from control_plane.db import Database
    db = Database(f"sqlite:///{tmp_path}/agw.db")
    await db.connect()
    await db.execute("CREATE TABLE agent_wallets (client_id TEXT PRIMARY KEY, balance "
                     "INTEGER NOT NULL DEFAULT 0, funding TEXT, chain_address TEXT)")
    await db.execute("CREATE TABLE payment_receipts (id TEXT PRIMARY KEY, client_id TEXT, "
                     "scheme TEXT, amount INTEGER, resource TEXT, tx_ref TEXT)")
    monkeypatch.setattr(agent_gateway, "get_db_dep", lambda: db)
    transport = ASGITransport(app=agent_gateway.build_test_app(db))
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    await db.disconnect()


@pytest.mark.asyncio
async def test_unfunded_call_returns_x402_envelope(client, agent_principal):
    r = await client.post("/api/agent/v1/chat", headers={"Authorization": "Bearer x"},
                          json={"model": "crowelm-apex", "messages": [{"role": "user",
                                "content": "hi"}]})
    assert r.status_code == 402
    body = r.json()
    assert body["x402Version"] == 1
    assert {a["scheme"] for a in body["accepts"]} == {"exact", "crowe-credit"}


@pytest.mark.asyncio
async def test_paid_call_serves_and_debits(client, agent_principal):
    price = x402.price_for("/api/agent/v1/chat")
    hdr = _xpayment("agent-1", price, "nonce-1", "/api/agent/v1/chat")
    r = await client.post("/api/agent/v1/chat",
                          headers={"Authorization": "Bearer x", "X-PAYMENT": hdr},
                          json={"model": "crowelm-apex", "messages": [{"role": "user",
                                "content": "hi"}]})
    assert r.status_code == 200
    assert r.json()["content"] == "hello from agent"
    assert "X-PAYMENT-RESPONSE" in r.headers


@pytest.mark.asyncio
async def test_replayed_payment_rejected(client, agent_principal):
    price = x402.price_for("/api/agent/v1/chat")
    hdr = _xpayment("agent-1", price, "nonce-dup", "/api/agent/v1/chat")
    body = {"model": "crowelm-apex", "messages": [{"role": "user", "content": "hi"}]}
    h = {"Authorization": "Bearer x", "X-PAYMENT": hdr}
    first = await client.post("/api/agent/v1/chat", headers=h, json=body)
    assert first.status_code == 200
    second = await client.post("/api/agent/v1/chat", headers=h, json=body)
    assert second.status_code == 402  # replay rejected, must pay again
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_agent_gateway.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'control_plane.agent_gateway'`.

- [ ] **Step 3: Write minimal implementation**

Create `control_plane/agent_gateway.py`:
```python
"""Agent-native metered endpoint: POST /api/agent/v1/chat.

Flow: resolve agent principal -> if no/invalid X-PAYMENT, 402 with the x402 envelope
-> verify payment -> credit wallet (idempotent) -> atomic debit -> call model -> 200
with an X-PAYMENT-RESPONSE receipt. Dedicated path so the live human/API-key /chat is
untouched (slice 2 folds this into the shared metered dependency).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

from . import agent_wallets, agents, oidc, settlement, x402

RESOURCE = "/api/agent/v1/chat"
router = APIRouter()


class AgentChatRequest(BaseModel):
    model: str
    messages: list
    max_tokens: int | None = None
    temperature: float | None = None


def resolve_agent_principal(authorization: str | None) -> dict:
    """Verify a Crowe ID client_credentials bearer and return an agent principal."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Crowe ID agent token required")
    token = authorization[7:]
    if not oidc.looks_like_jwt(token):
        raise HTTPException(status_code=401, detail="not a Crowe ID token")
    try:
        claims = oidc.verify_token(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail=f"Invalid Crowe ID token: {exc}")
    if not agents.is_agent_token(claims):
        raise HTTPException(status_code=403, detail="this endpoint requires an agent (client_credentials) token")
    return agents.agent_principal(claims)


async def call_model(*, model, messages, max_tokens, temperature):
    """Forward to the existing provider call. Imported lazily to avoid a cycle."""
    from .gateway import _call_provider
    return await _call_provider(model=model, messages=messages,
                                max_tokens=max_tokens, temperature=temperature)


def get_db_dep():  # overridden in tests / wired to the real Database in build
    from .db import get_db
    return get_db()


@router.post(RESOURCE)
async def agent_chat(req: AgentChatRequest, request: Request,
                     authorization: str | None = Header(None),
                     x_payment: str | None = Header(None, alias="X-PAYMENT")):
    principal = resolve_agent_principal(authorization)
    client_id = principal["client_id"]
    price = x402.price_for(RESOURCE)
    db = request.app.state.db

    await agent_wallets.ensure_wallet(db, client_id)

    # Settle payment if presented, crediting the wallet before the debit.
    if x_payment:
        try:
            payload = x402.parse_x_payment(x_payment)
            receipt = settlement.verify_payment(payload, client_id=client_id,
                                                 resource=RESOURCE, price=price)
            await agent_wallets.credit(db, client_id, receipt.amount,
                                       receipt_id=receipt.id, scheme=receipt.scheme,
                                       resource=RESOURCE, tx_ref=receipt.tx_ref)
        except (ValueError, settlement.PaymentError, agent_wallets.DuplicatePayment):
            return Response(content=json.dumps(x402.build_payment_required(RESOURCE)),
                            status_code=402, media_type="application/json")

    # Charge for this call; if still short, demand payment.
    try:
        await agent_wallets.debit(db, client_id, price)
    except agent_wallets.InsufficientFunds:
        return Response(content=json.dumps(x402.build_payment_required(RESOURCE)),
                        status_code=402, media_type="application/json")

    content, prompt_tokens, completion_tokens = await call_model(
        model=req.model, messages=req.messages,
        max_tokens=req.max_tokens, temperature=req.temperature)

    resp = Response(content=json.dumps({
        "model": req.model, "content": content,
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens}}),
        media_type="application/json")
    resp.headers["X-PAYMENT-RESPONSE"] = json.dumps(
        {"settled": bool(x_payment), "charged": price, "client_id": client_id})
    return resp


def build_test_app(db) -> FastAPI:
    """Minimal app for tests: mounts the router and binds the db to app.state."""
    app = FastAPI()
    app.state.db = db
    app.include_router(router)
    return app
```

> The test fixture monkeypatches `resolve_agent_principal` and `call_model`, so JWT/JWKS and the real provider are never hit. `build_test_app` binds `db` to `app.state.db`, which the endpoint reads — production wiring (Step 6) must also set `app.state.db`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_agent_gateway.py -q`
Expected: PASS (3 passed). If `app.state.db` is not how the real app exposes the pool, read the `include_router` file from Step 0 and bind the db the same way it does for other routers; keep the endpoint reading `request.app.state.db`.

- [ ] **Step 5: Run the full new suite together**

Run: `.venv/bin/python -m pytest tests/test_x402_envelope.py tests/test_agent_identity.py tests/test_agent_wallets.py tests/test_settlement.py tests/test_agent_gateway.py -q`
Expected: PASS (all).

- [ ] **Step 6: Mount the router in the real app**

In the file found in Step 0, add near the other `include_router` calls:
```python
from control_plane import agent_gateway
app.include_router(agent_gateway.router)
```
Ensure `app.state.db` is set to the live `Database` at startup (mirror how existing routers obtain the pool; if they use `Depends(get_db)` instead, change the endpoint's `db = request.app.state.db` line to `db = await get_db()` matching the existing pattern).

- [ ] **Step 7: Commit**

```bash
git add control_plane/agent_gateway.py tests/test_agent_gateway.py control_plane/__init__.py
git commit -m "feat(x402): POST /api/agent/v1/chat — discover->pay->consume wiring"
```

---

### Task 6: Discovery manifests

**Files:**
- Modify: `control_plane/agent_gateway.py` (add two routes to the same router)
- Test: `tests/test_discovery_manifest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_discovery_manifest.py`:
```python
import pytest
from httpx import ASGITransport, AsyncClient

from control_plane import agent_gateway, x402


@pytest.fixture
async def client():
    transport = ASGITransport(app=agent_gateway.build_test_app(db=None))
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


@pytest.mark.asyncio
async def test_x402_manifest_lists_priced_endpoints(client):
    r = await client.get("/.well-known/x402")
    assert r.status_code == 200
    body = r.json()
    entry = next(e for e in body["resources"] if e["resource"] == "/api/agent/v1/chat")
    assert entry["price"] == x402.price_for("/api/agent/v1/chat")
    assert set(entry["schemes"]) == {"exact", "crowe-credit"}


@pytest.mark.asyncio
async def test_agent_card_describes_service(client):
    r = await client.get("/.well-known/agent")
    assert r.status_code == 200
    body = r.json()
    assert body["name"]
    assert body["payments"]["protocol"] == "x402"
    assert "/api/agent/v1/chat" in [s["resource"] for s in body["payments"]["priced"]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery_manifest.py -q`
Expected: FAIL (404 — routes not defined).

- [ ] **Step 3: Add the routes to `control_plane/agent_gateway.py`**

Append to `control_plane/agent_gateway.py`:
```python
@router.get("/.well-known/x402")
async def well_known_x402():
    """Machine-readable price catalog — agents crawl this to learn cost before paying."""
    return {
        "x402Version": 1,
        "resources": [
            {"resource": res, "price": price, "unit": "micro-usd",
             "schemes": ["exact", "crowe-credit"]}
            for res, price in x402.PRICE_CATALOG.items()
        ],
    }


@router.get("/.well-known/agent")
async def well_known_agent():
    """A2A-style agent card describing Crowe's agent-payable services."""
    return {
        "name": "Crowe Logic Foundry",
        "description": "Agent-native AI gateway: pay-per-call model + knowledge services.",
        "url": "https://chat.crowelogic.com",
        "payments": {
            "protocol": "x402",
            "discovery": "/.well-known/x402",
            "priced": [
                {"resource": res, "price": price, "unit": "micro-usd"}
                for res, price in x402.PRICE_CATALOG.items()
            ],
        },
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_discovery_manifest.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add control_plane/agent_gateway.py tests/test_discovery_manifest.py
git commit -m "feat(x402): /.well-known/x402 + /.well-known/agent discovery manifests"
```

---

### Task 7: Full-suite green + regression check

- [ ] **Step 1: Run the new suite**

Run: `.venv/bin/python -m pytest tests/test_x402_envelope.py tests/test_agent_identity.py tests/test_agent_wallets.py tests/test_settlement.py tests/test_agent_gateway.py tests/test_discovery_manifest.py -q`
Expected: PASS (all).

- [ ] **Step 2: Run the existing gateway/control-plane tests to confirm no regression**

Run: `.venv/bin/python -m pytest tests/test_gateway_principal.py tests/test_control_plane.py tests/test_billing_webhook.py tests/test_oidc.py -q`
Expected: PASS (unchanged) — the human/API-key path was not modified.

- [ ] **Step 3: Boot the preview app and hit the manifest end-to-end**

Run:
```bash
.venv/bin/python -c "
from fastapi.testclient import TestClient
from control_plane.preview import app
c = TestClient(app)
print('x402:', c.get('/.well-known/x402').status_code)
print('agent:', c.get('/.well-known/agent').status_code)
"
```
Expected: `x402: 200` and `agent: 200` (confirms the router is mounted in the real app and the manifest serves).

- [ ] **Step 4: Final commit (if Step 3 required wiring fixes)**

```bash
git add -A control_plane/ tests/
git commit -m "chore(x402): mount agent gateway router in preview/app + verify manifests serve"
```

---

## Self-Review (completed by author)

- **Spec coverage:** §3.1 agent identity → Task 2 + `resolve_agent_principal` (Task 5). §3.2 x402 envelope → Task 1 + 402 returns (Task 5). §3.3 settlement+retry → Task 4 + Task 5. §3.4 discovery → Task 6. §4 data model → Task 0. §5 security (replay/binding/verify-before-credit) → Task 3 (idempotent receipt), Task 4 (resource+amount binding, HMAC verify-before-credit), Task 5 (credit precedes debit; serve always after debit). §6 testing → tests in every task, regression in Task 7. §7 phasing: slice 1 only; chain settlement explicitly returns "not yet enabled" (Task 4) — no silent cap. **No gaps.**
- **Placeholder scan:** no TBD/TODO in requirements; `0NN` migration number is resolved by Task 0 Step 1; `0xCROWE_BASE_TREASURY_PLACEHOLDER` is an env-overridable default flagged as Open Question #2, not used until chain settlement is enabled.
- **Type consistency:** `Receipt(id, scheme, amount, tx_ref)` used identically in Tasks 4–5. `credit(... receipt_id, scheme, resource, tx_ref)` / `debit(client_id, amount)` / `ensure_wallet(db, client_id)` signatures match across Tasks 3 and 5. `price_for`/`build_payment_required`/`parse_x_payment` (Task 1) used consistently in Tasks 5–6. `agent_principal` dict shape (Task 2) matches the fixture in Task 5.

## Known integration risks — RESOLVED before execution (controller recon)
1. **DB in tests:** `control_plane.db.Database` wraps an asyncpg pool — NOT constructible from a `sqlite:///` URL. For DB-backed tests (Task 3) use `from control_plane.preview import SqliteDatabase; db = SqliteDatabase(":memory:")` (real SQLite that quacks like asyncpg, `$1` placeholders translated). After Task 0 adds the two tables to `SqliteDatabase._bootstrap`'s `executescript`, a fresh `SqliteDatabase(":memory:")` already has them — do NOT hand-create tables in the fixture. The implementer must confirm `SqliteDatabase.fetchrow/execute` translate `$1`→`?` (read `preview.py`); if not, use `?` in the new ledger SQL.
2. **DB into the endpoint:** routers use `db: Database = Depends(get_db)`, NOT `app.state.db`. So `agent_gateway.agent_chat` must take `db: Database = Depends(get_db)`. There is NO `build_test_app`/`app.state.db`. Tests build a `TestClient` over the real `control_plane.app` (or a tiny `FastAPI()` that does `app.include_router(agent_gateway.router)`) and inject the db with `app.dependency_overrides[get_db] = lambda: sqlite_db` (mirror `tests/test_control_plane.py:213-226`). Mount in production at `control_plane/main.py:29` (`app.include_router(agent_gateway.router)`), AND in `control_plane/preview.py` near line 225.
3. `_call_provider` signature **CONFIRMED** at `gateway.py:459`: `_call_provider(model=, messages=, max_tokens=, temperature=)` returning `(content, prompt_tokens, completion_tokens)`. Task 5's `call_model` mirrors it exactly.
