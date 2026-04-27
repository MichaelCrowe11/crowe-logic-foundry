#!/usr/bin/env bash
# Register the crowe-logic:// URL protocol with macOS Launch Services so
# GitHub auth callbacks (and any other crowe-logic:// deep links) route
# back to Crowe Logic Code.
#
# Why this is needed: the rebrand patches product.json's urlProtocol from
# `vscode` to `crowe-logic`, but the macOS Info.plist's CFBundleURLTypes
# block still only declares `vscode`. Without this script, every link
# the IDE generates with the new scheme dies in the browser with
# "no application knows how to open URL crowe-logic://...".
#
# Run once with sudo:
#     sudo bash vscode/scripts/register-url-protocol.sh
#
# Reversible: backup at $PLIST.crowe-protocol.bak; mv it back to undo.

set -e

APP="/Applications/Visual Studio Code.app"
PLIST="$APP/Contents/Info.plist"
BAK="$PLIST.crowe-protocol.bak"
PB=/usr/libexec/PlistBuddy

[[ -f "$PLIST" ]] || { echo "✗ $PLIST not found"; exit 1; }
[[ -f "$BAK" ]] || cp -p "$PLIST" "$BAK"

# Already registered?
if $PB -c 'Print :CFBundleURLTypes' "$PLIST" 2>/dev/null | grep -q crowe-logic; then
  echo "  · crowe-logic:// already in Info.plist"
else
  # Find the next free index in the existing URLTypes array
  IDX=$($PB -c 'Print :CFBundleURLTypes' "$PLIST" 2>/dev/null | grep -c CFBundleURLName || echo 0)
  $PB -c "Add :CFBundleURLTypes:$IDX dict" "$PLIST"
  $PB -c "Add :CFBundleURLTypes:$IDX:CFBundleURLName string io.crowelogic.code" "$PLIST"
  $PB -c "Add :CFBundleURLTypes:$IDX:CFBundleTypeRole string Viewer" "$PLIST"
  $PB -c "Add :CFBundleURLTypes:$IDX:CFBundleURLSchemes array" "$PLIST"
  $PB -c "Add :CFBundleURLTypes:$IDX:CFBundleURLSchemes:0 string crowe-logic" "$PLIST"
  echo "  ✓ added crowe-logic:// scheme to Info.plist"
fi

# Re-sign so the adhoc signature stays consistent after the bundle change
codesign --force --deep --sign - "$APP" 2>&1 | head -1
echo "  ✓ re-signed adhoc"

# Re-register with Launch Services so macOS picks up the new scheme NOW
LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
"$LSREG" -f "$APP"
echo "  ✓ Launch Services rebuilt"

echo
echo "verifying:"
$PB -c 'Print :CFBundleURLTypes' "$PLIST" | sed 's/^/  /'

echo
echo "Test by clicking the GitHub auth callback again — it should now open Crowe Logic Code."
