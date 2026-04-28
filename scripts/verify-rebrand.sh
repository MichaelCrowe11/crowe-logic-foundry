#!/usr/bin/env bash
# verify-rebrand.sh
#
# Health check for the Crowe Logic Code IDE rebrand. Combines static
# checks (pytest) with live process inspection. Run from the repo root:
#
#     ./scripts/verify-rebrand.sh
#
# Exit code: 0 if every check passes, 1 if any check fails.
#
# Static checks (always run):
#   - tests/test_rebrand_extensions.py    duplicate commands, theme parity, em dashes
#   - tests/test_billing_webhook.py       webhook idempotency regression
#
# Live checks (skipped if app is not running):
#   - Crowe Code main process is alive
#   - No new crash reports in the last 5 minutes
#   - Latest exthost.log shows both extensions activated cleanly
#   - No "command already exists" errors in exthost.log
#   - Helper bundle names match CFBundleName (would crash V8 startup if not)
#   - product.json proposed-API allowlist contains crowe-logic.crowe-logic
#
# Returns a one-line PASS/FAIL summary at the bottom.

set -u
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PASS=0
FAIL=0
SKIP=0

bold()   { printf "\033[1m%s\033[0m\n" "$*"; }
green()  { printf "\033[32m%s\033[0m" "$*"; }
red()    { printf "\033[31m%s\033[0m" "$*"; }
yellow() { printf "\033[33m%s\033[0m" "$*"; }

check() {
    local label="$1"; shift
    if "$@" >/tmp/verify-out 2>&1; then
        printf "  %s %s\n" "$(green PASS)" "$label"
        PASS=$((PASS+1))
    else
        printf "  %s %s\n" "$(red FAIL)" "$label"
        sed 's/^/      /' /tmp/verify-out | head -8
        FAIL=$((FAIL+1))
    fi
}

skip() {
    printf "  %s %s\n" "$(yellow SKIP)" "$1"
    SKIP=$((SKIP+1))
}

rule() { printf -- "----------------------------------------\n"; }


# ─── Static checks ──────────────────────────────────────────────────

bold "Static checks"
rule

PYTHON=".venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3

check "rebrand extension regression suite" \
    "$PYTHON" -m pytest tests/test_rebrand_extensions.py -q --no-header

check "billing webhook idempotency suite" \
    "$PYTHON" -m pytest tests/test_billing_webhook.py -q --no-header

check "theme JSONs parse" bash -c '
    python3 -c "
import json, pathlib
for f in pathlib.Path(\"vscode/extension/themes\").glob(\"*.json\"):
    json.loads(f.read_text())
"'

check "no em dashes in customer-facing docs" bash -c '
    ! grep -l "—" docs/views/customer.md docs/views/investor.md 2>/dev/null'

check "no provider names in customer.md (no tech-stack exposure)" bash -c '
    ! grep -iE "anthropic|openai|azure|stripe|fastapi|postgres|claude\b|chatgpt|cursor|aider" docs/views/customer.md'


# ─── Live checks ────────────────────────────────────────────────────

bold ""
bold "Live checks (running app)"
rule

APP="/Applications/Crowe Code.app"
[[ -d "$APP" ]] || APP="/Applications/Visual Studio Code.app"

if [[ ! -d "$APP" ]]; then
    skip "no Crowe Code app installed at /Applications"
else
    PID=$(pgrep -f "$APP/Contents/MacOS/Code\$" | head -1)
    if [[ -z "$PID" ]]; then
        skip "Crowe Code is not running (open it first to enable live checks)"
    else
        check "main process alive" kill -0 "$PID"

        check "no crash reports in last 5 minutes" bash -c "
            count=\$(find ~/Library/Logs/DiagnosticReports -name 'Code-*.ips' -mmin -5 2>/dev/null | wc -l | tr -d ' ')
            [[ \"\$count\" == '0' ]]"

        # Find the latest exthost log under any plausible Application Support
        # name. The patch script sets CFBundleName to NAME_SHORT, which is
        # what Electron uses for the userData folder, so the directory name
        # changes with the rebrand.
        LOG=""
        for base in "Crowe Logic" "Crowe Code" "Code"; do
            d="$HOME/Library/Application Support/$base/logs"
            [[ -d "$d" ]] || continue
            latest=$(ls -t "$d" 2>/dev/null | head -1)
            cand="$d/$latest/window1/exthost/exthost.log"
            if [[ -f "$cand" ]]; then
                # Pick the most recent across all candidate dirs.
                if [[ -z "$LOG" ]] || [[ "$cand" -nt "$LOG" ]]; then
                    LOG="$cand"
                fi
            fi
        done

        if [[ -z "$LOG" ]]; then
            skip "no exthost log found yet (app just started?)"
        else
            check "exthost log: chat extension activated" \
                grep -q "_doActivateExtension crowe-logic.crowe-logic," "$LOG"

            check "exthost log: rebrand extension activated" \
                grep -q "_doActivateExtension crowe-logic.crowe-logic-vscode," "$LOG"

            check "exthost log: no 'command already exists' error" bash -c "
                ! grep -q 'command .* already exists' '$LOG'"

            check "exthost log: no extension activation failures" bash -c "
                ! grep -qiE 'Activating extension .* failed' '$LOG'"
        fi
    fi
fi

# Bundle integrity (independent of running state)
if [[ -d "$APP" ]]; then
    bold ""
    bold "Bundle integrity"
    rule

    NAME=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleName' "$APP/Contents/Info.plist" 2>/dev/null)
    check "Info.plist CFBundleName set" test -n "$NAME"

    if [[ -n "$NAME" ]]; then
        check "main helper matches CFBundleName" \
            test -d "$APP/Contents/Frameworks/$NAME Helper.app"
        for variant in "(Renderer)" "(GPU)" "(Plugin)"; do
            check "helper $variant matches CFBundleName" \
                test -d "$APP/Contents/Frameworks/$NAME Helper $variant.app"
        done
    fi

    check "product.json allows crowe-logic.crowe-logic for chatProvider" bash -c "
        jq -e '.extensionEnabledApiProposals.\"crowe-logic.crowe-logic\" | index(\"chatProvider\")' '$APP/Contents/Resources/app/product.json' >/dev/null"
fi

# ─── Summary ─────────────────────────────────────────────────────────

rule
TOTAL=$((PASS+FAIL+SKIP))
if [[ "$FAIL" == "0" ]]; then
    printf "%s  %d passed" "$(green RESULT)" "$PASS"
    [[ "$SKIP" -gt 0 ]] && printf ", %d skipped" "$SKIP"
    printf " of %d total\n" "$TOTAL"
    exit 0
else
    printf "%s  %d failed, %d passed" "$(red RESULT)" "$FAIL" "$PASS"
    [[ "$SKIP" -gt 0 ]] && printf ", %d skipped" "$SKIP"
    printf " of %d total\n" "$TOTAL"
    exit 1
fi
