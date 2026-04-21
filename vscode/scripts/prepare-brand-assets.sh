#!/usr/bin/env bash
# Pre-render Crowe Logic icons from vscode/assets/crowe-logic-mark.svg into
# vscode/fork-overlay/resources/{darwin,linux,win32}/ so that downstream scripts
# (build-fork.sh, patch-local-install.sh) can consume cached binaries instead of
# requiring a rasterizer at run time.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_DIR="$REPO_ROOT/vscode/scripts"
ASSETS="$REPO_ROOT/vscode/assets"
OUT="$REPO_ROOT/vscode/fork-overlay/resources"
MARK="$ASSETS/crowe-logic-mark.svg"

# shellcheck source=_lib_icons.sh
source "$SCRIPT_DIR/_lib_icons.sh"

[[ -r "$MARK" ]] || { echo "Missing brand asset: $MARK" >&2; exit 1; }

mkdir -p "$OUT/darwin" "$OUT/linux" "$OUT/win32"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "▸ Rendering darwin/code.icns"
if ! build_icns "$MARK" "$OUT/darwin/code.icns" "$TMP"; then
  echo "  ! Skipped: no rasterizer or iconutil available." >&2
fi

echo "▸ Rendering linux/code.png (1024)"
if ! build_png "$MARK" "$OUT/linux/code.png" 1024; then
  echo "  ! Skipped: no rasterizer available." >&2
fi

echo "▸ Rendering win32/code.ico"
if ! build_ico "$MARK" "$OUT/win32/code.ico" "$TMP"; then
  echo "  ! Skipped: imagemagick not available." >&2
fi

echo "✓ Brand assets staged in $OUT"
