#!/usr/bin/env bash
# weekly-readiness.sh
#
# Prints the Monday review checklist for keeping the readiness report,
# roadmap, and blueprint current. Run from the repo root:
#
#     ./scripts/weekly-readiness.sh
#
# This is a checklist driver, not an updater. The operator confirms each
# line, then asks the assistant to rewrite the affected files.

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TODAY="$(date +%Y-%m-%d)"
LAST_FRIDAY="$(date -v-fri +%Y-%m-%d 2>/dev/null || date -d 'last friday' +%Y-%m-%d)"

bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
rule()  { printf -- "----------------------------------------\n"; }

bold "Crowe Logic Platform: Weekly Readiness Review"
echo "Date: $TODAY"
rule

bold "1. Diff since last Friday ($LAST_FRIDAY)"
echo "Commits on main since $LAST_FRIDAY:"
git log --oneline --since="$LAST_FRIDAY" main 2>/dev/null | sed 's/^/  /'
echo
echo "Files changed in docs/ since $LAST_FRIDAY:"
git diff --name-only "@{$LAST_FRIDAY}" -- docs/ 2>/dev/null | sed 's/^/  /' || echo "  (no diff range available)"
rule

bold "2. Roadmap lane review"
echo "Open the roadmap and ask:"
echo "  - Any Now items that shipped this week? Move to Shipped section."
echo "  - Any Next items that are now in flight? Move to Now."
echo "  - Any Later items that should be promoted to Next?"
echo "  - Any Now item that has been there for two weeks without progress?"
echo "    (Stale lane is a positioning failure; rethink instead of grinding.)"
echo
echo "  File: docs/roadmap.md"
rule

bold "3. Readiness gate re-check"
echo "For each gate, ask: did anything clear or surface this week?"
echo
echo "  Gate 1 (Closed Beta): any new criterion go red?"
echo "  Gate 2 (Public Launch): the open blockers, are any cleared?"
echo "    Current open: Stripe webhook reconciliation, public docs site,"
echo "    status page, support channel, ToS/privacy, security policy."
echo "  Gate 3 (Scale): any item start, complete, or stay deferred?"
echo
echo "  File: docs/product-readiness.md"
rule

bold "4. View drift check"
echo "Walk through:"
echo "  docs/views/investor.md - any verdict change since last update?"
echo "  docs/views/customer.md - any tier, surface, or copy change?"
echo
echo "If the readiness verdict moved, both views need a same-day update."
rule

bold "5. Numbers to track (post-launch only)"
echo "After Public Launch, these three metrics drive the weekly review:"
echo "  - Trial-to-paid conversion rate at Day 14 (target 20%)"
echo "  - Tier mix vs revenue-projection.md assumptions"
echo "  - Day 30 churn (target under 5% monthly)"
echo
echo "If any number trends 30% off target for two consecutive weeks,"
echo "treat as a positioning issue, not a feature gap."
rule

bold "6. What to ask the assistant next"
echo "Once you have answered the prompts above, hand off to Claude with:"
echo
echo "    /loop docs-refresh"
echo
echo "or simply:"
echo
echo "    'Refresh the readiness report and roadmap based on this week's diff.'"
echo
echo "Provide the diff and your confirmed updates. The assistant will"
echo "rewrite product-readiness.md, roadmap.md, and the views in place."
rule

bold "Files in scope this week"
ls -la docs/product-readiness.md docs/roadmap.md docs/blueprint.md \
       docs/launch-plan.md docs/ARCHITECTURE.md \
       docs/views/investor.md docs/views/customer.md 2>/dev/null \
  | awk '{print "  ", $NF, "(" $6, $7, $8 ")"}'
rule
echo
echo "Done. Take 15 minutes, walk through the prompts, then call the assistant."
