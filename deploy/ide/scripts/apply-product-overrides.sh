#!/usr/bin/env bash
# Merge deploy/ide/product-overrides.json into the code-server product.json.
#
# code-server reads product.json at startup to set branding, the
# default chat agent, and other host-level identifiers. We don't ship
# our own product.json (it changes between code-server releases), we
# just deep-merge our overrides on top of whatever the upstream image
# provides. This makes us forward-compatible with new code-server
# versions: if upstream adds a new field, we inherit it untouched.
#
# Run inside the Docker build (or the running container as root) with:
#   apply-product-overrides.sh /path/to/code-server/product.json
set -euo pipefail

OVERRIDES="${OVERRIDES:-/tmp/product-overrides.json}"
TARGET="${1:-}"

if [ -z "$TARGET" ]; then
  # Try common code-server install paths.
  for candidate in \
    /usr/lib/code-server/lib/vscode/product.json \
    /usr/lib/code-server/out/vs/platform/product/common/product.json \
    /usr/lib/code-server/product.json
  do
    if [ -f "$candidate" ]; then
      TARGET="$candidate"
      break
    fi
  done
fi

if [ -z "$TARGET" ] || [ ! -f "$TARGET" ]; then
  echo "apply-product-overrides: target product.json not found" >&2
  exit 1
fi

if [ ! -f "$OVERRIDES" ]; then
  echo "apply-product-overrides: $OVERRIDES not found" >&2
  exit 1
fi

# Back up once. Subsequent runs leave the .orig in place so reverts are
# trivial: cp product.json.orig product.json.
if [ ! -f "${TARGET}.orig" ]; then
  cp "$TARGET" "${TARGET}.orig"
fi

# Deep merge with Python (already installed for the Foundry venv).
# Avoids pulling jq into the runtime image just for one merge. Nested
# objects merge key-by-key; arrays are concatenated rather than
# replaced so additive overrides don't clobber upstream defaults.
# Write to a sibling tempfile inside $(dirname TARGET) so the final
# `mv` is an atomic rename on the same filesystem (mv across
# filesystems falls back to copy+unlink, which is not crash-safe).
TARGET_DIR="$(dirname "$TARGET")"
TMP="$(mktemp "${TARGET_DIR}/product.json.XXXXXX")"
python3 - "${TARGET}.orig" "$OVERRIDES" "$TMP" <<'PY'
import json, sys
base_path, overrides_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(base_path) as f: base = json.load(f)
with open(overrides_path) as f: overrides = json.load(f)
def merge(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = merge(a[k], v) if k in a else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    return b
with open(out_path, 'w') as f:
    json.dump(merge(base, overrides), f, indent=2)
PY
# Match the original file's mode + ownership before the rename so the
# coder (uid 1000) user can still read product.json. mktemp creates
# 0600 root:root by default, which silently breaks code-server's
# bootstrap with EACCES on the merged file.
chmod --reference="${TARGET}.orig" "$TMP"
chown --reference="${TARGET}.orig" "$TMP" 2>/dev/null || true
mv "$TMP" "$TARGET"

echo "apply-product-overrides: merged $OVERRIDES into $TARGET"
