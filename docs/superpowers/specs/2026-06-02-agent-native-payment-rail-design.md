# Agent-Native Payment Rail — x402 on the Crowe Foundry Gateway

**Date:** 2026-06-02
**Status:** Design (approved direction; pending spec review)
**Owner:** Michael Crowe
**Repo:** `crowe-logic-foundry` (`control_plane/`)

---

## 1. Thesis & context

The next decade's customers are **agents, not humans** — billions of them, with millions of
wallets, transacting machine-to-machine. Every SaaS category gets a machine-first rebuild whose
primitives are: programmatic **discovery** (a manifest, not a marketing site), programmatic **auth**
(`client_credentials` + scoped tokens, not a login form), programmatic **payment** (HTTP 402 + a
wallet, not a checkout page), and programmatic **consumption** (an API/MCP, not a UI). Almost nobody
is building the payment rail for this yet. This spec builds it on infrastructure Crowe already runs.

**Decisive prior-art finding (already live in this repo):** the Foundry gateway *already* has a
prepaid credit wallet with 402 semantics:

- `workspace_credits(workspace_id, tier_key, balance, allocation, active)` ledger
  (`control_plane/__init__.py`).
- Atomic debit that raises `HTTPException(status_code=402)` on insufficient balance, with race
  protection (`UPDATE ... WHERE balance >= $2`, plus a second 402 on the race) — `__init__.py:1145`.
- Per-call metering hooks: `_is_metered`, `_resolve_principal` (`gateway.py:341/434/470/536`).
- Crowe ID OIDC verify mapping tiers → plans (`oidc.py`); `client_credentials` grant is enabled on
  the live issuer `https://id.crowelogic.com/realms/crowe`.
- Stripe fiat billing (`billing.py`): customers, checkout, usage metering, plan→price map.

So this is **not** "build payments." It is three protocol-shaped gaps that turn an internal,
human-keyed credit system into an **open, agent-payable rail**.

## 2. Goals / Non-goals

**Goals (first shippable slice):**
1. An **agent** (a Crowe ID `client_credentials` principal) is a first-class gateway principal with
   its own wallet.
2. A metered call with insufficient balance returns a **machine-actionable x402 `402` envelope** an
   agent can parse and act on — not a plain string.
3. The agent can **pay and retry** with an `X-PAYMENT` header, settling via **either** on-chain
   USDC (Base) **or** the existing Crowe credit ledger — both advertised in one `accepts` array.
4. The service is **auto-discoverable**: a `/.well-known/x402` (+ `/.well-known/agent`) manifest
   advertises each endpoint and its price, so an agent crawls in, sees cost, and pays — no human.
5. End-to-end on **one** metered endpoint (a model call or a Knowledge-Lake query).

**Non-goals (explicitly deferred — YAGNI):**
- Agent-native *comms* (A2A), *memory*-as-a-service, *sandbox* provisioning — separate sub-specs.
- Multi-chain settlement (Base/USDC only for v1).
- Human-facing wallet UI (agents are the customer; humans use existing Stripe checkout).
- A2A agent-card federation / cross-vendor registry (v1 ships only Crowe's own manifest).
- Refunds / disputes / streaming-payment (per-token) — v1 is per-call, prepaid.

## 3. Architecture — four components on existing seams

```
  Agent                       Foundry Gateway (control_plane)               Crowe ID / Chain
  ─────                       ──────────────────────────────               ────────────────
   │  GET /.well-known/x402  ─────────────────────────────►  (4) Discovery manifest
   │  ◄── price catalog ──────────────────────────────────
   │
   │  POST /api/gateway/chat  (Bearer = client_credentials) ►  (1) _resolve_principal → agent wallet
   │  ◄── 402 + x402 accepts[] ───────────────────────────   (2) x402 envelope (insufficient balance)
   │
   │  ── pay (USDC on Base, or Crowe credit top-up) ───────────────────────►  facilitator / Stripe
   │  POST /api/gateway/chat  + X-PAYMENT header ──────────►  (3) verify → credit wallet → debit → serve
   │  ◄── 200 result + X-PAYMENT-RESPONSE (receipt) ───────
```

**(1) Agent identity & wallet.** Extend `_resolve_principal` so a Crowe ID `client_credentials`
token (machine principal; identified by `azp`/`client_id`, no human `email`) resolves to an
`agent_wallets` row keyed by `client_id`. This closes the documented `sub→workspace` bridge TODO.
Agents self-register by creating a `client_credentials` client in the `crowe` realm (or via a
gateway `POST /agents` that provisions one through the Keycloak admin API and returns
`client_id`/`secret`).

**(2) x402 envelope.** A new `control_plane/x402.py` builds the standard `402` body when a metered
agent call has insufficient balance:
```json
{ "x402Version": 1,
  "accepts": [
    {"scheme":"exact","network":"base","maxAmountRequired":"<price>","asset":"USDC",
     "payTo":"<crowe-base-address>","resource":"/api/gateway/chat","mimeType":"application/json"},
    {"scheme":"crowe-credit","network":"crowe","maxAmountRequired":"<price>","asset":"credit",
     "payTo":"crowe-ledger","resource":"/api/gateway/chat"}
  ],
  "error":"payment required" }
```
Same `402` status (backward-compatible with existing API-key callers, who still get the legacy
detail when the principal is not an agent).

**(3) Settlement + retry.** The agent retries with `X-PAYMENT` (base64 payment payload, per x402).
A `Facilitator` abstraction verifies it:
- `chain` scheme → verify the on-chain USDC transfer (self-hosted verify against a Base RPC, or
  Coinbase's hosted x402 facilitator), then credit the wallet.
- `crowe-credit` scheme → resolve against the existing Stripe-funded ledger / a signed credit grant.
On success: credit `agent_wallets`, then the **existing atomic debit** runs and the call proceeds.
Response carries `X-PAYMENT-RESPONSE` (settlement receipt / tx hash). All verification is
**idempotent** and **replay-protected** (nonce + resource binding; a settled `X-PAYMENT` cannot be
reused).

**(4) Discovery.** `GET /.well-known/x402` returns the price catalog (endpoint → price → accepted
schemes), generated from a single `PRICE_CATALOG` source of truth (same source the 402 envelope
reads, so quoted price and charged price can never drift). `GET /.well-known/agent` returns an
A2A-style agent card describing Crowe's services + the payment scheme. The `crowe-portfolio` MCP
server gains a `list_priced_services` tool as the richer, agent-native discovery surface.

## 4. Data model (new)

```sql
CREATE TABLE agent_wallets (
  client_id    TEXT PRIMARY KEY,          -- Crowe ID client_credentials azp
  balance      BIGINT NOT NULL DEFAULT 0, -- micro-USD (or smallest credit unit), matches ledger unit
  funding      TEXT   NOT NULL DEFAULT 'crowe-credit', -- last/default scheme
  chain_address TEXT,                     -- optional bound Base address
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE payment_receipts (
  id           TEXT PRIMARY KEY,          -- nonce / payment id (idempotency key)
  client_id    TEXT NOT NULL REFERENCES agent_wallets(client_id),
  scheme       TEXT NOT NULL,             -- 'exact'(chain) | 'crowe-credit'
  amount       BIGINT NOT NULL,
  resource     TEXT NOT NULL,
  tx_ref       TEXT,                      -- on-chain tx hash or stripe/credit ref
  settled_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id)                             -- replay protection
);
```
`agent_wallets` deliberately mirrors `workspace_credits` (same balance unit + atomic-debit pattern)
so the existing debit SQL is reused verbatim against the agent table.

## 5. Security

- **Replay:** `payment_receipts.id` (the `X-PAYMENT` nonce) is unique-constrained; a re-submitted
  payment is rejected. Payment payload is **bound to `resource` + amount** so a receipt for a cheap
  endpoint can't unlock an expensive one.
- **Verification trust:** chain payments are confirmed against an RPC/facilitator before crediting
  (no optimistic credit). Credit-scheme grants are signed by the gateway, not client-asserted.
- **Auth:** agent principals are RS256-verified against Crowe ID JWKS (existing `oidc.py`); least-
  privilege plan mapping for unknown tiers.
- **No silent failure:** a verification error returns an explicit `402`/`502` with reason; the call
  never proceeds un-paid, and a paid call never silently drops the debit (debit + serve are ordered
  so a served call is always charged).

## 6. Testing

- Unit: x402 envelope shape (golden JSON), price-catalog/envelope consistency, idempotent receipt
  insert (replay rejected), atomic agent-wallet debit + race.
- Integration: full `402 → pay(credit scheme) → 200` loop against a mock ledger; `402 → pay(chain
  scheme, mocked facilitator) → 200`; insufficient/duplicate/wrong-resource payment rejected.
- Backward-compat: an existing **API-key** principal still gets legacy 402 + serves identically
  (agent envelope only triggers for `client_credentials` principals).

## 7. Phasing

- **Slice 1 (this spec):** agent identity+wallet, x402 envelope, both settlement schemes, one
  discovery manifest, one metered endpoint. End-to-end, no human.
- **Slice 2:** `POST /agents` self-service registration (Keycloak admin API), price catalog across
  all metered endpoints, `crowe-portfolio` MCP `list_priced_services`.
- **Slice 3+ (separate specs):** A2A comms, memory-as-a-service, sandbox provisioning — each a new
  service behind the *same* discover→auth→pay→consume rail this spec establishes.

## 8. Open questions (decide during planning, not blocking)

1. **Facilitator:** self-host Base verification vs. Coinbase hosted x402 facilitator for v1?
   (Default: hosted to ship faster; revisit for sovereignty.)
2. **Crowe Base treasury address:** which wallet receives USDC, and how does it reconcile into
   Crowe Treasury / Mercury? (Coordinate with `project_crowe_treasury`.)
3. **Pricing unit:** micro-USD across both ledgers (recommended, single unit) vs. separate
   credit/chain units with a conversion.
4. **Endpoint for slice 1:** model call (`/api/gateway/chat`) vs. Knowledge-Lake query (cheaper,
   cleaner to meter). (Lean: Knowledge-Lake query — bounded cost, obvious per-call price.)
