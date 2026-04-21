#!/usr/bin/env bash
# Patch a locally-installed VS Code (or VS Code Insiders) into "Crowe Logic Code".
# - Rewrites Resources/app/product.json (nameShort, nameLong, applicationName,
#   dataFolderName, win32 ids, urlProtocol, etc.).
# - Replaces the platform app icon with the Crowe Logic mark.
# - macOS only: also rewrites CFBundleName / CFBundleDisplayName /
#   CFBundleIdentifier in Info.plist and re-registers with Launch Services.
# - All originals are backed up next to themselves with a `.crowe-logic.bak`
#   suffix so `restore-local-install.sh` can put them back exactly.
#
# Usage:
#   sudo vscode/scripts/patch-local-install.sh                       # auto-detect
#   sudo vscode/scripts/patch-local-install.sh /path/to/VSCode.app   # explicit
#
# Restore with: sudo vscode/scripts/restore-local-install.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_DIR="$REPO_ROOT/vscode/scripts"
ASSET_DIR="$REPO_ROOT/vscode/assets"
STAGED_DIR="$REPO_ROOT/vscode/fork-overlay/resources"
MARK_SVG="$ASSET_DIR/crowe-logic-mark.svg"

# shellcheck source=_lib_icons.sh
source "$SCRIPT_DIR/_lib_icons.sh"

NAME_SHORT="Crowe Logic"
NAME_LONG="Crowe Logic Code"
APP_NAME="crowe-logic"
DATA_FOLDER=".crowe-logic"
WIN32_APP_USER_MODEL_ID="CroweLogic.CroweLogicCode"
WIN32_MUTEX_NAME="crowelogiccode"
SERVER_APP_NAME="crowe-logic-server"
URL_PROTOCOL="crowe-logic"
DARWIN_BUNDLE_ID="io.crowelogic.code"

usage() { sed -n '2,16p' "$0"; exit 1; }

detect_platform() {
  case "$(uname -s)" in
    Darwin) echo darwin ;;
    Linux)  echo linux ;;
    CYGWIN*|MINGW*|MSYS*) echo windows ;;
    *) return 1 ;;
  esac
}

detect_app_root() {
  case "$1" in
    darwin)
      for p in \
        "/Applications/Visual Studio Code.app" \
        "/Applications/Visual Studio Code - Insiders.app" \
        "$HOME/Applications/Visual Studio Code.app"; do
        [[ -d "$p" ]] && { echo "$p"; return; }
      done ;;
    linux)
      for p in /usr/share/code /usr/share/code-insiders /opt/visual-studio-code; do
        [[ -d "$p" ]] && { echo "$p"; return; }
      done ;;
    windows)
      for p in \
        "${LOCALAPPDATA:-}/Programs/Microsoft VS Code" \
        "/c/Program Files/Microsoft VS Code" \
        "/c/Program Files (x86)/Microsoft VS Code"; do
        [[ -n "$p" && -d "$p" ]] && { echo "$p"; return; }
      done ;;
  esac
  return 1
}

PLATFORM="$(detect_platform)" || { echo "Unsupported platform: $(uname -s)" >&2; exit 1; }
APP_ROOT="${1:-}"
[[ -z "$APP_ROOT" ]] && APP_ROOT="$(detect_app_root "$PLATFORM" || true)"
[[ -z "$APP_ROOT" || ! -d "$APP_ROOT" ]] && { echo "Could not locate a VS Code install. Pass the path explicitly." >&2; usage; }

ICO_FILE=""; PLIST=""; ICON=""
case "$PLATFORM" in
  darwin)
    RES_DIR="$APP_ROOT/Contents/Resources"
    APP_DIR="$RES_DIR/app"
    PRODUCT="$APP_DIR/product.json"
    PLIST="$APP_ROOT/Contents/Info.plist"
    ICON_NAME="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$PLIST" 2>/dev/null || echo Code)"
    [[ "$ICON_NAME" != *.icns ]] && ICON_NAME="${ICON_NAME}.icns"
    ICON="$RES_DIR/$ICON_NAME"
    ;;
  linux)
    APP_DIR="$APP_ROOT/resources/app"
    PRODUCT="$APP_DIR/product.json"
    for cand in "$APP_DIR/resources/linux/code.png" "/usr/share/pixmaps/com.visualstudio.code.png" "/usr/share/pixmaps/vscode.png"; do
      [[ -f "$cand" ]] && { ICON="$cand"; break; }
    done
    ;;
  windows)
    APP_DIR="$APP_ROOT/resources/app"
    PRODUCT="$APP_DIR/product.json"
    ICO_FILE="$APP_DIR/resources/win32/code.ico"
    ;;
esac

[[ -f "$PRODUCT" ]] || { echo "product.json not found at $PRODUCT" >&2; exit 1; }
[[ -r "$MARK_SVG" ]] || { echo "Missing brand asset: $MARK_SVG" >&2; exit 1; }
[[ -w "$PRODUCT" ]] || { echo "✗ $PRODUCT is not writable. Re-run with sudo." >&2; exit 1; }

echo "▸ Platform:      $PLATFORM"
echo "▸ Target app:    $APP_ROOT"
echo "▸ product.json:  $PRODUCT"
[[ -n "$ICON"     ]] && echo "▸ Icon:          $ICON"
[[ -n "$ICO_FILE" ]] && echo "▸ Icon (.ico):   $ICO_FILE"

backup_file() { [[ -f "$1" && ! -f "$1.crowe-logic.bak" ]] && cp -p "$1" "$1.crowe-logic.bak"; }
backup_file "$PRODUCT"
[[ -n "$ICON"     && -f "$ICON"     ]] && backup_file "$ICON"
[[ -n "$ICO_FILE" && -f "$ICO_FILE" ]] && backup_file "$ICO_FILE"
[[ -n "$PLIST"    && -f "$PLIST"    ]] && backup_file "$PLIST"

python3 - "$PRODUCT" \
  "$NAME_SHORT" "$NAME_LONG" "$APP_NAME" "$DATA_FOLDER" \
  "$WIN32_APP_USER_MODEL_ID" "$WIN32_MUTEX_NAME" "$SERVER_APP_NAME" "$URL_PROTOCOL" \
  "$DARWIN_BUNDLE_ID" <<'PY'
import json, sys, pathlib
(path, name_short, name_long, app_name, data_folder,
 win32_id, win32_mutex, server_app, url_proto, darwin_bundle) = sys.argv[1:11]
p = pathlib.Path(path)
data = json.loads(p.read_text())
data.update({
    "nameShort": name_short, "nameLong": name_long,
    "applicationName": app_name, "dataFolderName": data_folder,
    "win32AppUserModelId": win32_id, "win32MutexName": win32_mutex,
    "win32DirName": name_long, "win32NameVersion": name_long,
    "win32RegValueName": name_long, "win32ShellNameShort": name_short,
    "darwinBundleIdentifier": darwin_bundle,
    "serverApplicationName": server_app,
    "serverDataFolderName": data_folder + "-server",
    "urlProtocol": url_proto,
    "reportIssueUrl": "https://github.com/MichaelCrowe11/crowe-logic-foundry/issues/new",
    "documentationUrl": "https://github.com/MichaelCrowe11/crowe-logic-foundry",
    "releaseNotesUrl": "https://github.com/MichaelCrowe11/crowe-logic-foundry/releases",
})
p.write_text(json.dumps(data, indent=2))
print(f"  ✓ patched product.json → {name_long}")
PY

TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT

apply_icon_macos() {
  if build_icns "$MARK_SVG" "$TMP_DIR/code.icns" "$TMP_DIR" 2>/dev/null; then
    cp "$TMP_DIR/code.icns" "$ICON"
  elif [[ -f "$STAGED_DIR/darwin/code.icns" ]]; then
    cp "$STAGED_DIR/darwin/code.icns" "$ICON"
    echo "  ↪ used pre-staged $STAGED_DIR/darwin/code.icns"
  else
    echo "  ! No rasterizer and no pre-staged icon. Run vscode/scripts/prepare-brand-assets.sh first." >&2
    return 1
  fi
  echo "  ✓ replaced macOS app icon"
}

apply_icon_linux() {
  [[ -z "$ICON" ]] && { echo "  ! Could not find Linux app icon path; skipping." >&2; return 0; }
  if build_png "$MARK_SVG" "$TMP_DIR/code.png" 1024 2>/dev/null; then
    cp "$TMP_DIR/code.png" "$ICON"
  elif [[ -f "$STAGED_DIR/linux/code.png" ]]; then
    cp "$STAGED_DIR/linux/code.png" "$ICON"
  else
    echo "  ! No rasterizer and no pre-staged icon." >&2; return 1
  fi
  echo "  ✓ replaced Linux app icon"
}

apply_icon_windows() {
  [[ -z "$ICO_FILE" || ! -f "$ICO_FILE" ]] && { echo "  ! No code.ico under $APP_ROOT; skipping." >&2; return 0; }
  if build_ico "$MARK_SVG" "$TMP_DIR/code.ico" "$TMP_DIR" 2>/dev/null; then
    cp "$TMP_DIR/code.ico" "$ICO_FILE"
  elif [[ -f "$STAGED_DIR/win32/code.ico" ]]; then
    cp "$STAGED_DIR/win32/code.ico" "$ICO_FILE"
  else
    echo "  ! No imagemagick and no pre-staged icon." >&2; return 1
  fi
  echo "  ✓ replaced Windows app icon (resources/app/resources/win32/code.ico)"
  echo "  ⓘ The Code.exe icon is embedded; only a full rebuild changes the .exe itself."
}

case "$PLATFORM" in
  darwin)  apply_icon_macos  || true ;;
  linux)   apply_icon_linux  || true ;;
  windows) apply_icon_windows || true ;;
esac

if [[ "$PLATFORM" == "darwin" && -f "$PLIST" ]]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleName $NAME_SHORT" "$PLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName $NAME_LONG" "$PLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $DARWIN_BUNDLE_ID" "$PLIST" 2>/dev/null || true
  echo "  ✓ updated Info.plist (CFBundleName, CFBundleDisplayName, CFBundleIdentifier)"
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_ROOT" 2>/dev/null || true
  if command -v codesign >/dev/null 2>&1; then
    codesign --force --deep --sign - "$APP_ROOT" 2>/dev/null && echo "  ✓ re-signed bundle (ad-hoc)" || echo "  ! ad-hoc codesign failed — relaunch may show a Gatekeeper prompt." >&2
  fi
fi

cat <<EOF

✓ Crowe Logic rebrand applied to: $APP_ROOT

Next steps:
  1. Quit and relaunch VS Code (force-quit if it is already running).
  2. Install the Crowe Logic extension to apply themes + icon theme:
       (cd vscode/extension && npx vsce package && code --install-extension crowe-logic-*.vsix)
  3. To revert: sudo vscode/scripts/restore-local-install.sh "$APP_ROOT"
EOF
