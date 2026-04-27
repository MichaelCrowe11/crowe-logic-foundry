#!/usr/bin/env bash
# Migrate VS Code user state (extensions + user data) into the rebranded paths
# that patch-local-install.sh introduces. Run AFTER the patch and BEFORE the
# first relaunch of the rebranded editor, so the first launch carries forward
# the existing settings, themes, signed-in accounts, and 74 installed
# extensions instead of looking like a fresh install.
#
# Source paths (stock VS Code layout):
#   ~/.vscode/                                       (extensions, argv.json, ...)
#   ~/Library/Application Support/Code/              (User/, History/, globalStorage/, ...)
#
# Destination paths (Crowe Logic layout, derived from product.json):
#   ~/<dataFolderName>/                              (default: ~/.crowe-logic/)
#   ~/Library/Application Support/<nameShort>/       (default: ~/Library/Application Support/Crowe Logic/)
#
# The destination paths are read from the patched product.json so we stay in
# sync with whatever overlay is active rather than hardcoding names.
#
# Usage:
#   vscode/scripts/migrate-user-data.sh              # do the migration
#   vscode/scripts/migrate-user-data.sh --dry-run    # show what would happen, change nothing
#   vscode/scripts/migrate-user-data.sh --reverse    # undo: copy from new paths back to stock
set -euo pipefail

DRY_RUN=0
REVERSE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY_RUN=1 ;;
    --reverse)    REVERSE=1 ;;
    -h|--help)    sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

PRODUCT_JSON=""
for cand in \
  "/Applications/Visual Studio Code.app/Contents/Resources/app/product.json" \
  "/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/product.json" \
  "$HOME/Applications/Visual Studio Code.app/Contents/Resources/app/product.json"; do
  [[ -f "$cand" ]] && { PRODUCT_JSON="$cand"; break; }
done
[[ -z "$PRODUCT_JSON" ]] && { echo "Could not locate VS Code product.json. Patch first." >&2; exit 1; }

read_product_key() {
  python3 -c '
import json, sys, pathlib
p = json.loads(pathlib.Path(sys.argv[1]).read_text())
print(p.get(sys.argv[2], sys.argv[3]))
' "$PRODUCT_JSON" "$1" "$2"
}
DATA_FOLDER="$(read_product_key dataFolderName .vscode)"
NAME_SHORT="$(read_product_key nameShort Code)"
NAME_LONG="$(read_product_key nameLong 'Visual Studio Code')"

# Stock layout (source of truth before the patch).
OLD_DATA_FOLDER="$HOME/.vscode"
OLD_USER_DATA="$HOME/Library/Application Support/Code"

# Branded layout (what the patched editor will read).
NEW_DATA_FOLDER="$HOME/$DATA_FOLDER"
NEW_USER_DATA="$HOME/Library/Application Support/$NAME_SHORT"

if (( REVERSE )); then
  src_data="$NEW_DATA_FOLDER";  dst_data="$OLD_DATA_FOLDER"
  src_user="$NEW_USER_DATA";    dst_user="$OLD_USER_DATA"
  direction="rebranded -> stock"
else
  src_data="$OLD_DATA_FOLDER";  dst_data="$NEW_DATA_FOLDER"
  src_user="$OLD_USER_DATA";    dst_user="$NEW_USER_DATA"
  direction="stock -> rebranded ($NAME_LONG)"
fi

echo "▸ Direction:    $direction"
echo "▸ Data folder:  $src_data  ->  $dst_data"
echo "▸ User data:    $src_user  ->  $dst_user"
(( DRY_RUN )) && echo "▸ Mode:         dry-run (no changes)"

if [[ "$src_data" == "$dst_data" && "$src_user" == "$dst_user" ]]; then
  cat >&2 <<EOF

! Source and destination paths are identical. This means product.json has not
  been rewritten yet — the patch is what changes 'dataFolderName' and 'nameShort',
  which in turn changes the destination paths the script reads.

  Run the patch first:
    sudo /Users/crowelogic/Projects/crowe-logic-foundry/vscode/scripts/patch-local-install.sh

  Then re-run this migration script. Nothing was changed.
EOF
  exit 0
fi

mirror() {
  local src="$1" dst="$2" label="$3"
  if [[ ! -d "$src" ]]; then
    echo "  - skip $label: source does not exist"
    return 0
  fi
  if [[ -d "$dst" && -n "$(ls -A "$dst" 2>/dev/null)" ]]; then
    echo "  - skip $label: destination already populated ($dst). Move it aside if you want a fresh copy."
    return 0
  fi
  if (( DRY_RUN )); then
    echo "  ~ would copy $label ($(du -sh "$src" 2>/dev/null | cut -f1)) to $dst"
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  # rsync preserves perms, symlinks, hardlinks, and is restartable. Trailing
  # slashes copy the contents of src into dst (not src itself as a child dir).
  rsync -a "$src"/ "$dst"/
  echo "  + copied $label"
}

mirror "$src_data" "$dst_data" "data folder"
mirror "$src_user" "$dst_user" "user data"

if ! (( DRY_RUN )); then
  cat <<EOF

✓ Migration complete. Quit and relaunch VS Code (now branded as $NAME_LONG)
  to pick up the migrated extensions and settings. The original stock paths
  are still intact at:
    $([[ $REVERSE -eq 0 ]] && echo "$OLD_DATA_FOLDER" || echo "$NEW_DATA_FOLDER")
    $([[ $REVERSE -eq 0 ]] && echo "$OLD_USER_DATA"   || echo "$NEW_USER_DATA")
  so this is reversible.
EOF
fi
