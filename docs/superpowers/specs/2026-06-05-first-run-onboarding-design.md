# First-Run Onboarding — Design

**Date:** 2026-06-05
**Status:** Approved (design review with Michael, this session)
**Scope:** crowe-logic CLI (this repo) + foundry control plane (this repo) + DNS (manual)

## Problem

A fresh `pip install crowe-logic` with zero credentials dies on the first turn with a
raw error wall: auto-route tries CroweLM Supreme (dead `AZURE_ANTHROPIC_*` backend),
exhausts every fallback tier's credential check, and dumps provider errors. Reproduced
2026-06-05 in a clean OrbStack container. This is the package's front door for any
outside user, and it is broken.

## Decisions (locked)

1. **Audience:** both tiers — public PyPI users (sign-in/gateway path) and internal
   nodes (env-var path, the Pi pattern). Neither breaks the other.
2. **Zero-cred first turn:** returns a real model response via an anonymous free tier
   (mycelium), no account required. Sign-in is the upsell, not the wall.
3. **Default gateway:** `api.crowelogic.com`, revived to point at the Azure ACA
   control plane. Baked into the package as `DEFAULT_GATEWAY_URL`;
   `CROWE_LOGIC_GATEWAY_URL` always overrides.
4. **Free-tier metering:** anonymous device token minted by the gateway, stored
   client-side, small daily turn cap enforced server-side, plus IP rate limiting.
5. **Policy placement:** all policy (caps, tier mapping, upsell copy) lives in the
   control plane. The CLI learns protocol only. Shipped clients never go stale when
   policy changes.

## Architecture

```
fresh CLI (no creds)
  └─ detect_credential_state() ── NONE
       └─ gateway_client.register_device() ──► POST api.crowelogic.com/v1/anonymous/register
            └─ device token → ~/.config/crowe-logic/device.json (0600)
                 └─ turns ──► gateway (free-anonymous plan) ──► mycelium tier
                      └─ cap hit → structured 402 body → CLI renders gateway copy verbatim
internal node
  └─ detect_credential_state() ── ENV_CREDS → existing local routing, untouched
signed-in user
  └─ SIGNED_IN → existing PR #45 gateway routing, untouched
```

### Credential states

`detect_credential_state()` in new module `cli/first_run.py` returns one of:

| State | Meaning | Behavior |
|---|---|---|
| `SIGNED_IN` | Crowe ID session valid/refreshable | existing gateway routing (PR #45) |
| `ENV_CREDS` | any provider env creds present | existing local tier routing |
| `GATEWAY_ONLY` | `CROWE_LOGIC_GATEWAY_URL` set, no local creds | gateway routing |
| `NONE` | nothing | first-run flow (below) |

The check runs once before the REPL banner and before any auto-route attempt.
`cli/crowe_logic.py` gains one early hook; no other changes to the monolith.

## Phases (each independently shippable)

### Phase 0 — revive api.crowelogic.com

- Azure ACA: custom-domain binding + managed certificate for `api.crowelogic.com`.
- DNS: CNAME at Squarespace dashboard (manual step, Michael).
- Exit: `curl https://api.crowelogic.com/health` → 200.
- Package: add `DEFAULT_GATEWAY_URL = "https://api.crowelogic.com"`.

### Phase 1 — kill the error wall (CLI only)

- `cli/first_run.py`: `detect_credential_state()` + first-run card rendering.
- On `NONE`: one clean card, three exits —
  `crowe-logic login` (public) · `crowe-logic init --node` (internal) · free tier
  (Phase 2; until it ships, a docs pointer).
- `crowe-logic init --node`: scaffolds `~/.crowe-logic.env` template (key NAMES only,
  never values), chmod 600, prints sourcing instructions. Codifies the Pi pattern.
- Shippable alone; fully fixes the reproduced failure.

### Phase 2 — anonymous free tier (control plane + CLI)

Control plane:
- `tokens.py`: mint/verify signed anonymous device tokens.
- `plans.py`: `free-anonymous` plan — daily turn cap, initial value **20 turns/day**
  (server-side constant, tunable without client release).
- `gateway.py`: route free-anonymous turns to the mycelium tier; deny-by-default.
- `POST /v1/anonymous/register`: IP-rate-limited token mint.
- Cap exhaustion → structured 402-style JSON: `{code, message, upsell}`; sits beside
  the x402 rail's 402 semantics (PR #47) — reuse its response shape where it fits.

CLI:
- `gateway_client.register_device()`; token persisted at
  `~/.config/crowe-logic/device.json` (0600).
- Renders `message`/`upsell` from the gateway verbatim. No client-side copy.

### Phase 3 — sign-in upsell + usage merge

- `crowe-logic login` (existing PKCE) promoted in cap-hit upsell; device-code flow for
  headless nodes is follow-on work, not in this spec's commitment.
- On first authenticated call with a device token present, gateway merges the device's
  usage history into the Crowe ID account.

## Error handling

- Gateway unreachable in `NONE` state → degrade to the Phase 1 setup card. Never a
  stack trace, never the fallback-cascade wall.
- Device token corrupt/expired → silent one-shot re-register; on failure, setup card.
- Caps deny-by-default: control-plane outage must not grant free inference.
- `ENV_CREDS` path behavior is unchanged — internal nodes (Pi, containers) see zero
  behavioral difference.

## Testing

- pytest units: `first_run.py` state matrix (4 states × entry points), token
  store round-trip, `init --node` template output, control-plane register/cap/merge.
- Integration, the parallel-session loop: clean-env (`env -i`) CLI runs from this
  Mac against each phase as it lands — the same probe the OrbStack container did
  manually. Phase 2 adds a live register→chat→cap-out→upsell walk.
- All work on branch `feat/first-run-onboarding` (worktree
  `~/Projects/crowe-logic-foundry-onboarding`); main checkout is dirty on
  `feat/crowe-code-blocks` and is not touched.

## Out of scope

- Device-code auth flow for headless sign-in (noted, follow-on).
- Free-tier model quality/latency tuning (mycelium as-is).
- chat.crowelogic.com (web) revival.
- Migrating existing nodes off env vars.

## Manual steps (Michael)

1. Squarespace DNS: CNAME `api` → ACA ingress FQDN (Phase 0).
2. Confirm mycelium tier's Modal proxy can take control-plane-originated traffic
   (Phase 2 prerequisite).
