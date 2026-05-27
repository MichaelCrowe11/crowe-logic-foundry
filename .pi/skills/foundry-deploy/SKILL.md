# Foundry Deploy Skill

Use this skill when the user wants to deploy the Crowe Logic control plane, update Railway env vars, or publish the VS Code extension.

## Prerequisites
- Railway CLI authenticated (`railway login`)
- Stripe CLI authenticated for billing bootstrap
- Docker context set to `orbstack`
- `npm` with access to GitHub Packages registry

## Steps

### Deploy Control Plane to Railway
1. Verify `Dockerfile.control-plane` and `railway.json` exist in project root
2. Ensure `.env.railway.out` has Stripe price IDs (from `scripts/stripe_bootstrap.py`)
3. Deploy:
   ```bash
   railway up
   ```
4. Healthcheck: `curl -s https://foundry-control-plane-production.up.railway.app/health`

### Apply Stripe Env Vars to Railway
```bash
while IFS='=' read -r k v; do
  [ -n "$k" ] && railway variables --set "$k=$v"
done < .env.railway.out
railway variables --set "STRIPE_PUBLISHABLE_KEY=pk_live_..."
railway variables --set "STRIPE_SECRET_KEY=$STRIPE_SECRET_KEY"
railway variables --set "STRIPE_WEBHOOK_SECRET=whsec_..."
```

### Register Stripe Webhook
- URL: `https://api.crowelogic.com/api/billing/webhook`
- Events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`

### DNS Configuration
- `api.crowelogic.com` → CNAME to `foundry-control-plane-production.up.railway.app`
- Use Squarespace Domains (registrar for crowelogic.com)

### VS Code Extension Publishing
1. **Marketplace**:
```bash
cd deploy/ide/extensions/crowe-logic
npx @vscode/vsce login crowe-logic
npx @vscode/vsce publish --packagePath crowe-logic-0.2.8.vsix
```
2. **Open VSX**:
```bash
npm i -g ovsx
ovsx publish --packagePath deploy/ide/extensions/crowe-logic/crowe-logic-0.2.8.vsix -p <token>
```

### Rollback
```bash
railway redeploy   # redeploy last known good
# OR
git revert HEAD --no-edit
railway up
```

## Safety
- Never commit `.env.railway.out` or Stripe secrets
- Always test `preview` (`make preview`) before `prod` deploy
- Railway variables take precedence over `.env` files

## Troubleshooting
- "SSL handshake fails" on `api.crowelogic.com`: DNS propagation can take 5 min; verify CNAME
- Railway build fails: check `Dockerfile.control-plane` syntax and port exposure
- Stripe webhook 400s: verify `STRIPE_WEBHOOK_SECRET` matches Stripe dashboard
