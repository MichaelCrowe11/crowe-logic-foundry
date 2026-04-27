#!/usr/bin/env bash
# Recover from the half-completed mv of ~/.vscode/extensions and finish the
# task: move the duplicate extensions dir to /Volumes/Elements/crowe-work
# and put a symlink in its place.
#
# Run:    bash ~/Projects/crowe-logic-foundry/vscode/scripts/free-disk-recover.sh

set -e

EXT_DIR="$HOME/.vscode/extensions"
TARGET="/Volumes/Elements/crowe-work/vscode-extensions-backup"
TRUNCATED="/Volumes/Elements/crowe-wor"

# Make sure the parent dirs exist
mkdir -p /Volumes/Elements/crowe-work

# If the truncated path exists (because the mangled command ran), move it
# to the proper backup location.
if [[ -e "$TRUNCATED" && ! -e "$TARGET" ]]; then
  mv "$TRUNCATED" "$TARGET"
  echo "  ✓ relocated truncated path to $TARGET"
elif [[ -e "$TARGET" ]]; then
  echo "  · $TARGET already in place"
fi

# Replace the missing ~/.vscode/extensions with a symlink to the backup
if [[ ! -e "$EXT_DIR" ]]; then
  if [[ -d "$TARGET" ]]; then
    ln -s "$TARGET" "$EXT_DIR"
    echo "  ✓ symlinked $EXT_DIR -> $TARGET"
  else
    # Backup doesn't exist either — create empty extensions dir so VS Code
    # can recreate as needed
    mkdir -p "$EXT_DIR"
    echo "  ! no backup found; created empty $EXT_DIR"
  fi
elif [[ -L "$EXT_DIR" ]]; then
  echo "  · $EXT_DIR is already a symlink to $(readlink "$EXT_DIR")"
fi

echo
echo "=== free space after recovery ==="
df -h /System/Volumes/Data | tail -1
echo
echo "=== verify the rebrand still has its extensions ==="
ls "$HOME/.crowe-logic/extensions/" 2>/dev/null | grep -i crowe || echo "  (none found)"
