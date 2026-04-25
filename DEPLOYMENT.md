# Crowe Logic Code: Launch Runbook

This is the operational sequence to take Crowe Logic Code from current state to paying customers. It is split into **you** (browser / account work I can't do via CLI) and **me** (repo + CLI work I can do autonomously).

All subsequent references assume `cwd = /Users/crowelogic/Projects/crowe-logic-foundry` and Railway CLI is authenticated as `michael@southwestmushrooms.online`, linked to project `crowe-logic-platform` / service `foundry-control-plane`.

---

## Phase 1: Control plane + billing (target: live this week)

### 1.1 Deploy control plane to Railway (me)
- Source of truth: `Dockerfile.control-plane`, config in `railway.json`.
- Current runtime URL: `https://foundry-control-plane-production.up.railway.app`.
- Healthcheck: `GET /health` returns `{"status": "healthy", ...}`.
- Build time: ~30s, healthcheck retries for 60s.
- Rollback: `railway redeploy` (last known good) or git-revert + `railway up`.

### 1.2 Create Stripe products + prices (you, once)
1. Log into the Stripe dashboard (same account Stripe CLI is authed to: `acct_1SIh8yLynR0PTQ0F`).
2. From the terminal, pull your CLI secret key into env and run the bootstrap:
    ```bash
    export STRIPE_SECRET_KEY="$(stripe config --list | awk '/^live_mode_api_key/ {gsub(/'\''/,\"\"); print $3}')"
    python scripts/stripe_bootstrap.py --out .env.railway.out
    ```
3. The script will either reuse or create: three products (`Developer`, `Studio`, `Lab`), one monthly + one annual price per product, plus a single metered `Token Overage` price at $0.02 / 1K tokens.
4. The file `.env.railway.out` now contains the `STRIPE_PRICE_*` IDs. Do not commit it (`.gitignore` already covers `*.out`).

### 1.3 Apply Stripe env vars to Railway (me, after 1.2)
```bash
# From repo root, after .env.railway.out exists:
while IFS='=' read -r k v; do [ -n "$k" ] && railway variables --set "$k=$v"; done < .env.railway.out

# Also set the publishable + webhook secret (copied from Stripe dashboard → Developers → Webhooks):
railway variables --set "STRIPE_PUBLISHABLE_KEY=pk_live_..."
railway variables --set "STRIPE_SECRET_KEY=$STRIPE_SECRET_KEY"
# STRIPE_WEBHOOK_SECRET is set after 1.4.
```

### 1.4 Register Stripe webhook (you)
1. Stripe dashboard → Developers → Webhooks → Add endpoint.
2. URL: `https://api.crowelogic.com/api/billing/webhook`.
3. Events to send: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`.
4. Copy the signing secret (`whsec_...`) and paste into Railway:
    ```bash
    railway variables --set "STRIPE_WEBHOOK_SECRET=whsec_..."
    ```

### 1.5 DNS: `api.crowelogic.com` → Railway (you)
- Current: `api.crowelogic.com` is a CNAME to `ai-revenue-agents-api.onrender.com` (SSL handshake fails, so whatever is there appears broken anyway). Confirm nobody depends on that before re-pointing.
- Target: a CNAME to `foundry-control-plane-production.up.railway.app`.
- Steps in Squarespace Domains (crowelogic.com registrar):
    1. Remove the existing `api` record.
    2. Add CNAME `api` → `foundry-control-plane-production.up.railway.app` (TTL 300).
- Then in Railway → Settings → Domains → Add `api.crowelogic.com`. Railway provisions a certificate (takes 1-5 min).
- Verify: `curl -sI https://api.crowelogic.com/health` returns 200 and the healthy JSON body.

### 1.6 Extension publishing (me + you)
**Marketplace (needs your browser once):**
1. Go to https://dev.azure.com/ and sign in as `michael@crowelogic.com` (or the Microsoft account you want to publish under).
2. If no publisher exists, go to https://marketplace.visualstudio.com/manage and create a publisher with ID `crowe-logic` and display name `Crowe Logic`.
3. Create a Personal Access Token scoped to `Marketplace: Manage` (all orgs). Save it once, Azure won't show it again.
4. On your laptop:
    ```bash
    cd deploy/ide/extensions/crowe-logic
    npx @vscode/vsce login crowe-logic       # paste the PAT
    npx @vscode/vsce publish --packagePath crowe-logic-0.2.8.vsix
    ```

**Open VSX (faster, good parallel channel):**
1. Sign in at https://open-vsx.org/ with GitHub.
2. Request or create the `crowe-logic` namespace.
3. Generate an access token → keep it safe.
4. On your laptop:
    ```bash
    npm i -g ovsx
    ovsx publish --packagePath deploy/ide/extensions/crowe-logic/crowe-logic-0.2.8.vsix -p <token>
    ```

### 1.7 Pricing page (me, after 1.3)
Read from `GET /api/public/plans` (no auth). Payload already exposes `monthly_price_cents`, `annual_price_cents`, `highlights`, `cta_label`, `tagline`. See `dashboard/` for existing static host, or lift into a new `app.crowelogic.com` Next.js project.

### 1.8 Launch day checklist (gated)
- [ ] `/health` returns 200 on `https://api.crowelogic.com`
- [ ] `/api/public/plans` returns 4 plans with correct prices
- [ ] `/api/billing/config` returns `configured: true` for `developer`, `studio`, `lab`
- [ ] Stripe webhook successfully test-fires `checkout.session.completed` and a row lands in `billing_events`
- [ ] Extension visible on Marketplace under `crowe-logic.crowe-logic` and Open VSX
- [ ] A real test-mode Checkout subscription rolls into `subscriptions` table with `status=active`

---

## Phase 2: Remote IDE (Codespaces-class)

### 2.1 Add the missing `/api/ide/launch` endpoint
- Not implemented in the Python control plane yet. The extension POSTs to it expecting `{ url: "https://ide.crowelogic.com/launch?token=..." }`.
- Needs: mint a short-lived JWT signed with `IDE_JWT_SECRET` (shared with session-router) carrying `{ user_id, workspace_id, plan_id, exp }`, then return the `ide.crowelogic.com/launch?token=<jwt>` URL.

### 2.2 Host the session-router
- Target: Fly.io (better for always-on routing with global anycast than Railway) OR a single Hetzner/Azure VM per existing `deploy/ide/README.md`.
- Env: `IDE_JWT_SECRET` (must match control plane), `CONTROL_PLANE_URL=https://api.crowelogic.com`, `CONTROL_PLANE_API_KEY` (service token), `COOKIE_DOMAIN=.crowelogic.com`.
- DNS: `ide.crowelogic.com` → session-router host.

### 2.3 Code-server image
- Build `deploy/ide/Dockerfile.code-server` and push to a container registry (Fly's built-in or GHCR).
- Pre-install the Crowe Logic extension baked-in.

---

## Phase 3: Crowe Code (downloadable desktop app)

### 3.1 Build the fork
```bash
cd vscode
VSCODE_TAG=1.95.0 ./scripts/build-fork.sh
```
Produces a `Crowe Logic Code.app` (macOS), `.exe` (win), and `.deb/.rpm/.tar.gz` (linux) under `vscode/build/out/`.

### 3.2 Signing
- **macOS**: Needs an Apple Developer ID ($99/yr via `admin@crowelogic.com`). Notarize with `xcrun notarytool submit ... --apple-id admin@crowelogic.com`.
- **Windows**: EV code-signing cert (~$300-500/yr). Sign with `signtool.exe`.
- **Linux**: GPG-sign the `.deb`/`.rpm`. Optional.

### 3.3 Download site
- Host at `https://crowecode.com` (you own this).
- Simple landing + 3 download buttons + auto-update feed (Squirrel on macOS, NSIS on Windows).

### 3.4 Gallery endpoint
- Fork cannot legally point at Microsoft Marketplace.
- Edit `vscode/fork-overlay/product.json` to set `extensionsGallery` at Open VSX:
    ```json
    "extensionsGallery": {
      "serviceUrl": "https://open-vsx.org/vscode/gallery",
      "itemUrl": "https://open-vsx.org/vscode/item",
      "resourceUrlTemplate": "https://openvsxorg.blob.core.windows.net/resources/{publisher}/{name}/{version}/{path}"
    }
    ```

---

## Appendix: Pricing as shipped in `migrations/004_pricing.sql`

| Plan | Monthly | Annual (20% off) | Overage / 1K tokens | Included tokens/mo | Hosted IDE hrs/mo |
|------|---------|------------------|---------------------|-------------------|-----------------|
| Developer | $49 | $470 | $0.02 | 500K | 0 |
| Studio | $129 | $1,240 | $0.02 | 5M | 100 |
| Lab | $399 | $3,830 | $0.01 | 50M | 500 |
| Enterprise | contact | contact | 0 | unlimited | unlimited |

These numbers are committed in the DB migration but are not hard-coded in any other place: Stripe price IDs are set via env vars, the pricing page reads from `/api/public/plans`. Adjust the migration + re-seed + update Stripe, or (quicker) adjust the Stripe prices and run an `UPDATE plans SET monthly_price_cents = ...` against Neon.
