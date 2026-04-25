#!/usr/bin/env bash
# Create Stripe products + prices for the Crowe Logic Code launch,
# using the stripe CLI's stored credentials (no secret key handling).
#
# Idempotent: every resource is keyed by metadata.crowe_plan_id (products)
# or by interval + unit_amount match (prices), so re-runs reuse rather
# than duplicate.
#
# Usage:
#     scripts/stripe_bootstrap.sh [--test | --live] [--out PATH]
#
# Defaults to --live, writes env-var block to .env.railway.out.

set -euo pipefail

MODE="live"
OUT=".env.railway.out"
while [ $# -gt 0 ]; do
    case "$1" in
        --live) MODE="live"; shift ;;
        --test) MODE="test"; shift ;;
        --out) OUT="$2"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done
# Stripe CLI uses --live for live mode; omitting it means test mode.
# If STRIPE_API_KEY env is exported, append --api-key so every command uses
# that key instead of the CLI's stored restricted key (needed when the
# stored key lacks Products/Prices write permission).
if [ "$MODE" = "live" ]; then
    MODE_FLAG="--live"
else
    MODE_FLAG=""
fi
if [ -n "${STRIPE_API_KEY:-}" ]; then
    MODE_FLAG="$MODE_FLAG --api-key=$STRIPE_API_KEY"
fi

# Plan metadata. Keep in sync with migrations/004_pricing.sql.
# Fields: tag | display_name | description | monthly_cents | annual_cents
plans=(
    "developer|Crowe Logic Code: Developer|Solo developer tier. Crowe Logic extension for VS Code plus 500K tokens / month on Developer-tier models.|4900|47000"
    "studio|Crowe Logic Code: Studio|Professional tier. Studio-tier models (DeepSeek R1, Mistral Large 3, Kimi K2.5), 5M tokens / month, 100 hosted IDE hours.|12900|124000"
    "lab|Crowe Logic Code: Lab|Team tier. Lab-tier models (Claude Opus 4.6, GPT-5.4), 50M tokens / month, 500 hosted IDE hours, private datasets, SSO.|39900|383000"
)

OVERAGE_CENTS=2
OVERAGE_NAME="Crowe Logic Code: Token Overage"
OVERAGE_DESC="Metered billing for tokens beyond the included plan budget, billed monthly in arrears per 1000 tokens."

log() { printf '%s\n' "$*" >&2; }

find_product_by_tag() {
    local tag="$1"
    stripe products search ${MODE_FLAG} --query "metadata[\"crowe_plan_id\"]:\"$tag\"" 2>/dev/null \
        | jq -r '.data[0].id // empty'
}

ensure_product() {
    local tag="$1" name="$2" desc="$3" existing
    existing=$(find_product_by_tag "$tag")
    if [ -n "$existing" ]; then
        log "  reuse product $tag -> $existing"
        echo "$existing"
        return
    fi
    stripe products create ${MODE_FLAG} --confirm \
        --name="$name" \
        --description="$desc" \
        -d "metadata[crowe_plan_id]=$tag" 2>/dev/null \
        | jq -r '.id'
}

# Return a price id on $product with recurring.interval=$interval and unit_amount=$amount, or empty.
find_price() {
    local product="$1" interval="$2" amount="$3"
    stripe prices list ${MODE_FLAG} --product="$product" --limit=100 --active=true 2>/dev/null \
        | jq -r --arg i "$interval" --argjson a "$amount" \
            '.data[] | select(.recurring.interval == $i and .unit_amount == $a) | .id' \
        | head -n 1
}

ensure_recurring_price() {
    local product="$1" interval="$2" amount="$3" nickname="$4" existing
    existing=$(find_price "$product" "$interval" "$amount")
    if [ -n "$existing" ]; then
        log "    reuse $interval price \$$((amount/100)) -> $existing"
        echo "$existing"
        return
    fi
    stripe prices create ${MODE_FLAG} --confirm \
        --product="$product" \
        --currency=usd \
        --unit-amount="$amount" \
        --nickname="$nickname" \
        -d "recurring[interval]=$interval" 2>/dev/null \
        | jq -r '.id'
}

# Metered price on $product at $amount cents per 1K tokens. Idempotent match by usage_type=metered.
ensure_metered_price() {
    local product="$1" amount="$2" existing
    existing=$(stripe prices list ${MODE_FLAG} --product="$product" --limit=100 --active=true 2>/dev/null \
        | jq -r --argjson a "$amount" \
            '.data[] | select(.recurring.usage_type == "metered" and .unit_amount == $a) | .id' \
        | head -n 1)
    if [ -n "$existing" ]; then
        log "    reuse metered price \$$(awk "BEGIN{printf \"%.2f\", $amount/100}") -> $existing"
        echo "$existing"
        return
    fi
    stripe prices create ${MODE_FLAG} --confirm \
        --product="$product" \
        --currency=usd \
        --unit-amount="$amount" \
        --nickname="Token overage per 1K" \
        -d "recurring[interval]=month" \
        -d "recurring[usage_type]=metered" \
        -d "recurring[aggregate_usage]=sum" 2>/dev/null \
        | jq -r '.id'
}

log "=== Stripe bootstrap in $MODE mode ==="

: > "$OUT"

for row in "${plans[@]}"; do
    IFS='|' read -r tag name desc monthly annual <<< "$row"
    log "Plan: $tag"
    product=$(ensure_product "$tag" "$name" "$desc")
    monthly_price=$(ensure_recurring_price "$product" "month" "$monthly" "$name Monthly")
    annual_price=$(ensure_recurring_price  "$product" "year"  "$annual"  "$name Annual")
    upper=$(printf '%s' "$tag" | tr '[:lower:]' '[:upper:]')
    printf 'STRIPE_PRICE_%s=%s\n'        "$upper" "$monthly_price" >> "$OUT"
    printf 'STRIPE_PRICE_%s_ANNUAL=%s\n' "$upper" "$annual_price"  >> "$OUT"
done

log "Plan: token_overage"
overage_product=$(ensure_product "token_overage" "$OVERAGE_NAME" "$OVERAGE_DESC")
overage_price=$(ensure_metered_price "$overage_product" "$OVERAGE_CENTS")
if [ -n "$overage_price" ] && [ "$overage_price" != "null" ]; then
    printf 'STRIPE_PRICE_USAGE_TOKENS=%s\n' "$overage_price" >> "$OUT"
else
    log "  metered pricing needs a Stripe Billing Meter (API 2025-03-31+)."
    log "  Shipping without overage for now; add post-launch via 'stripe billing meters create'."
    printf '# STRIPE_PRICE_USAGE_TOKENS=  # pending billing meter setup\n' >> "$OUT"
fi

log ""
log "Wrote env block to $OUT:"
cat "$OUT" >&2
