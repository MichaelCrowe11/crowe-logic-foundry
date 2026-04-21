#!/usr/bin/env bash
# Restore originals saved by patch-local-install.sh.
#   sudo vscode/scripts/restore-local-install.sh                       # auto-detect
#   sudo vscode/scripts/restore-local-install.sh /path/to/VSCode.app   # explicit
set -euo pipefail

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
      for p in "/Applications/Visual Studio Code.app" "/Applications/Visual Studio Code - Insiders.app" "$HOME/Applications/Visual Studio Code.app"; do
        [[ -d "$p" ]] && { echo "$p"; return; }
      done ;;
    linux)
      for p in /usr/share/code /usr/share/code-insiders /opt/visual-studio-code; do
        [[ -d "$p" ]] && { echo "$p"; return; }
      done ;;
    windows)
      for p in "${LOCALAPPDATA:-}/Programs/Microsoft VS Code" "/c/Program Files/Microsoft VS Code"; do
        [[ -n "$p" && -d "$p" ]] && { echo "$p"; return; }
      done ;;
  esac
  return 1
}

PLATFORM="$(detect_platform)" || { echo "Unsupported platform: $(uname -s)" >&2; exit 1; }
APP_ROOT="${1:-$(detect_app_root "$PLATFORM" || true)}"
[[ -z "$APP_ROOT" || ! -d "$APP_ROOT" ]] && { echo "VS Code install not found." >&2; exit 1; }

restore_one() {
  local f="$1"
  [[ -f "$f.crowe-logic.bak" ]] || return 0
  mv -f "$f.crowe-logic.bak" "$f"
  echo "  ✓ restored $f"
}

PLIST=""; ICON=""; ICO_FILE=""; PRODUCT=""
case "$PLATFORM" in
  darwin)
    RES_DIR="$APP_ROOT/Contents/Resources"
    PRODUCT="$RES_DIR/app/product.json"
    PLIST="$APP_ROOT/Contents/Info.plist"
    ICON_NAME="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$PLIST" 2>/dev/null || echo Code)"
    [[ "$ICON_NAME" != *.icns ]] && ICON_NAME="${ICON_NAME}.icns"
    ICON="$RES_DIR/$ICON_NAME"
    ;;
  linux)
    PRODUCT="$APP_ROOT/resources/app/product.json"
    for cand in "$APP_ROOT/resources/app/resources/linux/code.png" "/usr/share/pixmaps/com.visualstudio.code.png" "/usr/share/pixmaps/vscode.png"; do
      [[ -f "$cand" || -f "$cand.crowe-logic.bak" ]] && { ICON="$cand"; break; }
    done
    ;;
  windows)
    PRODUCT="$APP_ROOT/resources/app/product.json"
    ICO_FILE="$APP_ROOT/resources/app/resources/win32/code.ico"
    ;;
esac

restore_one "$PRODUCT"
[[ -n "$ICON"     ]] && restore_one "$ICON"
[[ -n "$ICO_FILE" ]] && restore_one "$ICO_FILE"
[[ -n "$PLIST"    ]] && restore_one "$PLIST"

if [[ "$PLATFORM" == "darwin" ]]; then
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_ROOT" 2>/dev/null || true
  command -v codesign >/dev/null 2>&1 && codesign --force --deep --sign - "$APP_ROOT" 2>/dev/null || true
fi

echo "✓ Restore complete. Relaunch VS Code."
