# Crowe Logic Foundry landing page

Single-file landing at `index.html`. Tailwind via CDN, no build step,
deploys to Railway as a static site.

## Preview locally

```bash
cd landing
python3 -m http.server 8000
```

Open http://localhost:8000 and click around. All links work, pricing
anchors scroll correctly, FAQ sections expand.

## Deploy to Railway

First time:

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
railway login
railway init           # pick "Empty Project" and name it crowe-logic-foundry
railway link           # link this repo to the new project
railway up             # deploys the landing/ directory
```

Railway auto-generates a `*.up.railway.app` hostname. Custom domain:

```bash
railway domain add foundry.crowelogic.com
```

Then set a CNAME at your DNS provider pointing
`foundry.crowelogic.com` to the Railway hostname Railway prints.

## Update

Every push to `main` triggers a redeploy if the Railway service has
GitHub integration enabled. Otherwise:

```bash
railway up
```

## What to edit

- **Copy**: `index.html` body text. Hero, wedge, features, FAQ. All inline.
- **Pricing**: update when `config/customer_pricing.json` changes. Currently hardcoded for page weight (no client-side JSON fetch to keep the page single-file and cached).
- **Demo panel**: the hero terminal demo is a static snapshot. Swap the
  prompt text or model output when the canonical demo prompt changes.
- **CTAs**: all three currently route to `mailto:michael@crowelogic.com`
  with prefilled subjects. Swap to Stripe Checkout links once the
  billing pipeline is wired (Gate 2 work from
  `docs/product-readiness.md`).

## Files

- `index.html`: the landing page
- `railway.json`: Railway service config
- `nixpacks.toml`: build/start commands for the static server
- `README.md`: this file
