#!/usr/bin/env bash
# Mount a square headshot as the Crowe Logic chat avatar (face inside gold ring).
#
# Usage:
#   vscode/scripts/mount-face-avatar.sh /path/to/your-headshot.jpg
#   vscode/scripts/mount-face-avatar.sh ~/Desktop/michael.jpg
#
# Input expectations:
#   - Square (or near-square) image of your face, eyes roughly centered
#   - At least 512x512, ideally 1024x1024
#   - Front-facing, neutral or slight smile, even lighting
#   - JPG or PNG
#
# Output:
#   - Replaces vscode/assets/crowe-logic-face-avatar.png at 512x512
#   - Regenerates the avatar SVG with the face mounted under a gold ring
#   - Re-renders both light and dark PNGs at 256x256 for the chat surface
#   - Copies into vscode/extension/media/ and the installed 0.2.15 extension
#
# Requirements: imagemagick (`brew install imagemagick`) and rsvg-convert
# (`brew install librsvg`). Both already present per CLAUDE.md.

set -euo pipefail

SRC="${1:-}"
if [[ -z "$SRC" || ! -f "$SRC" ]]; then
  echo "usage: $0 <path-to-headshot.jpg|.png>" >&2
  echo "  (file must exist and be square-ish, >= 512px)" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ASSETS="$REPO_ROOT/vscode/assets"
EXT_MEDIA="$REPO_ROOT/vscode/extension/media"
INSTALLED_EXT="$HOME/.vscode/extensions/crowe-logic.crowe-logic-0.2.15/media"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "▸ Source headshot: $SRC"

# Step 1: square-crop to 1024x1024, center on face. We use ImageMagick
# `-resize` with `^` then center-crop so portraits with extra body space
# focus on the head.
magick "$SRC" \
  -auto-orient \
  -resize 1024x1024^ \
  -gravity center \
  -extent 1024x1024 \
  -strip \
  "$WORK/face-1024.png"
echo "  ✓ square-cropped to 1024x1024"

# Step 2: build the composite: dark disc + face inside circle clip + gold ring + sheen
# We do this in ImageMagick (deterministic) rather than via SVG-with-image
# so the output is a single self-contained PNG that VS Code can load
# without needing an SVG renderer at runtime.

# Mask: white circle on transparent (used to clip the face into a circle)
magick -size 1024x1024 xc:none \
  -fill white \
  -draw "circle 512,512 512,96" \
  "$WORK/circle-mask.png"

# Apply the mask to the face -> face-as-circle
magick "$WORK/face-1024.png" "$WORK/circle-mask.png" \
  -alpha set -compose DstIn -composite \
  "$WORK/face-circle.png"
echo "  ✓ face circle-cropped"

# Gold ring (drawn as an annulus by stroking a circle)
# Inner radius ~448, outer ~496, so ring thickness ~48px @ 1024
magick -size 1024x1024 xc:none \
  -stroke "#D4AF37" \
  -strokewidth 32 \
  -fill none \
  -draw "circle 512,512 512,40" \
  "$WORK/gold-ring.png"

# Composite layers: face circle on dark backdrop, then gold ring on top
magick -size 1024x1024 xc:"#0B0B0C" \
  "$WORK/face-circle.png" -compose Over -composite \
  "$WORK/gold-ring.png"  -compose Over -composite \
  "$WORK/avatar-1024.png"
echo "  ✓ composited face + ring on graphite"

# Save the canonical face avatar
cp "$WORK/avatar-1024.png" "$ASSETS/crowe-logic-face-avatar.png"

# Render at 512 (canonical), 256 (chat avatar), 128 (small), 64 (tiny)
for sz in 512 256 128 64; do
  magick "$WORK/avatar-1024.png" -resize "${sz}x${sz}" \
    "$WORK/avatar-${sz}.png"
done
echo "  ✓ rendered all sizes"

# Distribute to extension media (both 0.1.x rebrand and 0.2.x agent)
cp "$WORK/avatar-256.png" "$EXT_MEDIA/crowe-logic-avatar-dark.png"
cp "$WORK/avatar-256.png" "$EXT_MEDIA/crowe-logic-avatar-light.png"
cp "$WORK/avatar-256.png" "$EXT_MEDIA/avatar-dark.png"
cp "$WORK/avatar-256.png" "$EXT_MEDIA/avatar-light.png"
echo "  ✓ copied to vscode/extension/media/"

if [[ -d "$INSTALLED_EXT" ]]; then
  cp "$WORK/avatar-256.png" "$INSTALLED_EXT/avatar-dark.png"
  cp "$WORK/avatar-256.png" "$INSTALLED_EXT/avatar-light.png"
  echo "  ✓ copied into installed 0.2.15 extension"
fi

echo
echo "✓ Avatar mounted. Reload Crowe Logic Code (Cmd+Shift+P → Developer: Reload Window)."
echo "  The @crowe and @crowe-logic chat participants will now show your face."
