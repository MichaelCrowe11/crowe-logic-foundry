#!/usr/bin/env bash
# Build a full Crowe Logic-branded fork of VS Code from upstream microsoft/vscode.
#
# What it does:
#   1. Clones (or reuses) microsoft/vscode at a pinned tag into ./build/vscode-src.
#   2. Merges vscode/fork-overlay/product.json on top of upstream product.json.
#   3. Replaces app icons (resources/{darwin,linux,win32}) with the Crowe Logic mark.
#   4. Runs `yarn` + `yarn gulp` to produce a Crowe Logic Code build for the host platform.
#
# Output:
#   build/vscode-src/../VSCode-darwin-arm64/Crowe Logic Code.app   (or platform equivalent)
#
# Requirements: node 20.x, yarn 1.x, python3, librsvg or imagemagick.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPT_DIR="$REPO_ROOT/vscode/scripts"
OVERLAY="$REPO_ROOT/vscode/fork-overlay"
STAGED="$OVERLAY/resources"
ASSETS="$REPO_ROOT/vscode/assets"
BUILD_DIR="${CROWE_FORK_BUILD_DIR:-$REPO_ROOT/build/vscode-src}"
VSCODE_TAG="${VSCODE_TAG:-1.95.0}"
# Use the avatar (graphite disc + gold mark) as the dock icon source; override
# with CROWE_BRAND_ICON to use the abstract mark or any other SVG.
BRAND_ICON="${CROWE_BRAND_ICON:-$ASSETS/crowe-logic-avatar.svg}"

# shellcheck source=_lib_icons.sh
source "$SCRIPT_DIR/_lib_icons.sh"

mkdir -p "$(dirname "$BUILD_DIR")"

if [[ ! -d "$BUILD_DIR/.git" ]]; then
  echo "▸ Cloning microsoft/vscode @ $VSCODE_TAG → $BUILD_DIR"
  git clone --depth 1 --branch "$VSCODE_TAG" https://github.com/microsoft/vscode.git "$BUILD_DIR"
else
  echo "▸ Reusing existing checkout at $BUILD_DIR"
fi

echo "▸ Merging product.json overlay"
python3 - "$BUILD_DIR/product.json" "$OVERLAY/product.json" <<'PY'
import json, sys, pathlib
base_path, overlay_path = sys.argv[1:3]
base = json.loads(pathlib.Path(base_path).read_text())
overlay = json.loads(pathlib.Path(overlay_path).read_text())
overlay.pop("_comment", None)
base.update(overlay)
pathlib.Path(base_path).write_text(json.dumps(base, indent=2) + "\n")
print(f"  ✓ merged {len(overlay)} keys → {base['nameLong']} ({base['applicationName']})")
PY

echo "▸ Replacing platform icons"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
MARK="$BRAND_ICON"

place_icon() {
  local kind="$1" dest="$2"
  local staged="$STAGED/$kind"
  local fname; fname="$(basename "$dest")"
  if [[ -f "$staged/$fname" ]]; then
    cp "$staged/$fname" "$dest"
    echo "  ↪ used pre-staged $kind/$fname"
    return 0
  fi
  return 1
}

# macOS .icns
mkdir -p "$BUILD_DIR/resources/darwin"
if ! place_icon darwin "$BUILD_DIR/resources/darwin/code.icns"; then
  if build_icns "$MARK" "$BUILD_DIR/resources/darwin/code.icns" "$TMP" 2>/dev/null; then
    echo "  ✓ darwin/code.icns"
  else
    echo "  ! darwin/code.icns skipped (no rasterizer / iconutil)" >&2
  fi
fi

# Linux .png
mkdir -p "$BUILD_DIR/resources/linux"
if ! place_icon linux "$BUILD_DIR/resources/linux/code.png"; then
  if build_png "$MARK" "$BUILD_DIR/resources/linux/code.png" 1024 2>/dev/null; then
    echo "  ✓ linux/code.png"
  else
    echo "  ! linux/code.png skipped (no rasterizer)" >&2
  fi
fi

# Windows .ico
mkdir -p "$BUILD_DIR/resources/win32"
if ! place_icon win32 "$BUILD_DIR/resources/win32/code.ico"; then
  if build_ico "$MARK" "$BUILD_DIR/resources/win32/code.ico" "$TMP" 2>/dev/null; then
    echo "  ✓ win32/code.ico"
  else
    echo "  ! win32/code.ico skipped (imagemagick required)" >&2
  fi
fi

# Letter-icons (file association badges) — replace bundled .ico letters with the Crowe mark.
if [[ -f "$BUILD_DIR/resources/win32/code.ico" ]]; then
  for f in "$BUILD_DIR/resources/win32/"*.ico; do
    [[ -e "$f" && "$f" != "$BUILD_DIR/resources/win32/code.ico" ]] || continue
    cp "$BUILD_DIR/resources/win32/code.ico" "$f"
  done
fi

cd "$BUILD_DIR"
echo "▸ Installing dependencies (this can take a while)"
yarn

echo "▸ Building Crowe Logic Code"
case "$(uname -s)" in
  Darwin) yarn gulp "vscode-darwin-$(uname -m | sed 's/x86_64/x64/;s/arm64/arm64/')-min" ;;
  Linux)  yarn gulp "vscode-linux-x64-min" ;;
  *)      echo "Unsupported host: $(uname -s)" >&2; exit 1 ;;
esac

echo
echo "✓ Build complete."
echo "  Look in $(dirname "$BUILD_DIR") for the VSCode-* output directory."
echo "  The bundle is named 'Crowe Logic Code' (or 'crowe-logic' on Linux)."
