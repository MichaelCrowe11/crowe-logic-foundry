# First-Run Onboarding — Phase 3: Sign-In Upsell + Anti-Abuse Usage Merge

Date: 2026-06-07
Status: Design (awaiting review)
Builds on: `2026-06-05-first-run-onboarding-design.md` (Phases 1–2, shipped)

## Goal

Close the loop between the anonymous free tier and Crowe ID accounts. When an
anonymous user signs in, their free-tier usage follows them to their account so
that signing up cannot reset the daily free counter ("anon 20 + signup 20"
farming). Signing in also turns the free counter into an account-level,
cross-device counter — which is the honest pitch for signing in.

This is **anti-abuse continuity**, not a conversion bonus: signing in grants the
*same* 20/day Mycelium allowance, now keyed to the account.

## Decisions (from brainstorming)

1. **Merge purpose = anti-abuse continuity.** Carry the device's used-turn count
   onto the account; do not reset on signup.
2. **Signed-in unpaid users keep the same free tier** — Mycelium, 20/day — now
   account-tracked and synced across devices. (Requires a new `free` plan tier.)
3. **Merge trigger = explicit link at login.** `crowe-logic login` calls a
   dedicated `/v1/anonymous/link` endpoint after PKCE; the chat path is untouched.
4. **The cap wall is honest:** signing in does *not* grant more turns today.
   Login = continuity/sync; upgrade = the "more turns" lever.

## Non-goals

- Device-code flow for headless nodes (deferred; follow-on).
- The paid upgrade/purchase flow (billing; separate).
- Any change to paid-tier monthly token budgets.
- A sign-in turn bonus. Deliberately excluded: it reintroduces a farming vector
  and muddies the anti-abuse story. If sign-in conversion later measures weak,
  revisit as its own change.

## Architecture

Four units, each independently testable.

### 1. `free` plan tier (metering classification)

Metering today is binary:
- **Anonymous** → daily *turn* cap (20/day on Mycelium), tokens unmetered.
- **Paid** (`personal`/`pro`/`team`) → monthly *token* budget.

A signed-in unpaid user must behave like the anonymous case, not the paid case.

- Add a `free` plan (rank below `personal`) via migration. Its members are
  metered by the **daily turn cap**, never the token budget.
- `control_plane/plans.py`: `free` is a known canonical plan id, lowest paid-rank,
  and `is_metered(free) == False` for the *token-budget* sense (it is turn-capped
  instead — see unit 2).
- `control_plane/gateway.py`: principal classification gains a third branch.
  A `free`-plan signed-in principal is routed through the **turn-cap** path
  (unit 2), not the token-budget path. `crowelm-mycelium` access for `free` is
  unchanged (it is already the lowest-tier model).
- New Crowe ID accounts without a subscription resolve to `free`, not `personal`.
  (Confirm the default-plan resolution point; today workspaces default to
  `personal`. The change is: an account with no active paid subscription is
  treated as `free` for gateway metering.)

### 2. Principal-keyed daily counter (generalize `anon_usage`)

`anon_usage(device_id, day, turns)` becomes principal-keyed:

```
free_usage(principal_id TEXT, day DATE, turns INT, PRIMARY KEY (principal_id, day))
```

- `principal_id` is `device:<device_id>` for anonymous, `user:<sub>` for a
  signed-in free account.
- The cap check and `INSERT … ON CONFLICT (principal_id, day) DO UPDATE SET
  turns = turns + 1` increment are identical — just keyed by whoever the
  principal is. One mechanism serves both anonymous devices and free accounts.
- Gateway helpers that read/write the counter take a `principal_id` instead of a
  bare `device_id`.

**Migration safety (important):** `anon_usage` is already live in prod with real
rows. Migration `011` must **rekey in place** — copy existing rows with
`principal_id = 'device:' || device_id` into `free_usage`, not drop-and-recreate.
Ship a `011_free_usage.down.sql` that reverses it (project existing `device:` rows
back to `anon_usage.device_id`). No data loss on up or down.

### 3. `/v1/anonymous/link` endpoint (the merge)

`POST /v1/anonymous/link`
- **Auth:** Crowe ID bearer (the just-authenticated account). Required.
- **Body:** `{ "device_token": "crowe_anon_..." }`.
- **Behavior:**
  1. Verify the device token fail-closed (same HMAC verify as the chat path).
     Invalid/expired → 200 no-op (nothing to merge; never an error that blocks
     login).
  2. For each day present in the device's `free_usage` rows, set the account's
     row to `min(cap, account_turns + device_turns)`. The **`min(cap, …)` is
     load-bearing**: it kills farming (20 + fresh 20 → capped at 20) while never
     locking out a legitimate two-device user by summing past the cap.
  3. Delete the device's `free_usage` rows (idempotent — re-linking the same
     device is a no-op after the first).
  4. Return `{ merged_days, today_turns, cap }`.
- **Idempotency:** running twice yields the same account state (device rows gone
  after the first; second call verifies a now-unknown device → no-op).

`cli/auth.py` `login_pkce()`: after a successful browser login, if
`DEVICE_STORE` (`~/.config/crowe-logic/device.json`) exists, call `/v1/anonymous/link`
with the stored token, then delete `device.json` (the account is now the
principal of record). Link failure must not fail the login — log and continue;
the next login attempt can retry while the device file remains.

### 4. Cap-wall upsell copy (honest, server-rendered)

The cap-hit response is already a structured 402 `{code, message, upsell}` the CLI
renders verbatim. Phase 3 updates the **server-side** copy for the anonymous
cap-hit to drive both levers honestly:

> *You've used your 20 free turns for today. Sign in with `crowe-logic login` to
> sync your free usage across devices and save your history — or upgrade to
> Personal for higher limits.*

No client-side copy; the CLI continues to render `message`/`upsell` verbatim. No
interactive keystroke capture — the rendered command (`crowe-logic login`) is the
call to action. For a signed-in `free` user who hits the same cap, the copy drops
the "sign in" line and leads with upgrade.

## Abuse boundary (stated explicitly)

Merge closes the **signup-reset** hole only. A determined user can still
`logout` and register a fresh device for another 20 turns; the device-register
**IP rate-limiter (5/hr/IP)** is the guard there, and that is acceptable and
unchanged. Phase 3 does not claim to make the free tier farm-proof — it makes
*signing in* not a free reset.

## Error handling

- Link endpoint, invalid/expired device token → 200 no-op. Never blocks login.
- Link endpoint unreachable during login → login still succeeds; `device.json`
  is retained so a later login retries the merge. Deny-by-default still holds:
  the device keeps its own counter until successfully merged.
- Caps remain deny-by-default: a control-plane outage must not grant free
  inference, anon or free-account.
- `ENV_CREDS` path (Pi, internal containers) is unchanged — no behavioral diff.

## Testing

**Unit (pytest):**
- `free` plan: canonical-id resolution, rank below `personal`, routed through the
  turn-cap path (not token-budget) in gateway classification.
- Principal-keyed counter: cap check + increment for `device:` and `user:`
  principals; cap denial at 20.
- Link endpoint: verify-and-merge with `min(cap, …)` math (account 18 + device 17
  → 20, not 35); fresh account 0 + device 20 → 20; idempotent second call;
  expired/invalid token → no-op; unauth → 401.
- Migration 011 up/down round-trip preserves existing `device:` rows.

**Integration (clean-env CLI from this Mac, the same probe Phases 1–2 used):**
- Anon hits cap (20/20) → `crowe-logic login` → link → account still 20/20 today
  (no reset). New turn denied.
- Two devices: device A used 12 (signed in), device B anon used 15 → link B →
  account today = `min(20, 12+15)` = 20.
- Fresh anon under cap → login with low usage → account inherits exact count,
  remaining turns intact.

## Rollout

Server-side (gateway + migration) deploys first via the established path
(ACR build → digest-pin → ACA revision). The `login_pkce()` link call is a CLI
change that ships in the next wheel **after** the endpoint is live — same
ordering discipline as Phase 2 (never ship a client that calls a dead endpoint).
