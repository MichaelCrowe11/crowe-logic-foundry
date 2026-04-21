#!/usr/bin/env bash
# Build the Crowe Logic VS Code extension into a .vsix.
#
# Run from this directory (deploy/ide/extensions/crowe-logic). Produces
# ./crowe-logic.vsix, which the Dockerfile installs via
# `code-server --install-extension`. Idempotent: safe to re-run.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d node_modules ]; then
  npm install --no-audit --no-fund
fi

# Compile TypeScript → out/
npx tsc -p ./

# Package. --no-dependencies skips the npm tree (we only ship `out/` and
# the static media/themes assets, none of which need runtime deps).
npx vsce package --no-dependencies --out crowe-logic.vsix

echo "Built crowe-logic.vsix"
ls -la crowe-logic.vsix
