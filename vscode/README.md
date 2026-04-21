# Crowe Logic — VS Code

This module turns VS Code into a **Crowe Logic-facing product**. It ships four
layers of rebranding, from light-touch to full fork — use whichever fits.

```
vscode/
├── assets/                  Brand SVGs (mark + wordmark)
├── extension/               Drop-in extension for stock VS Code
│   ├── themes/              Crowe Logic Dark / Light color themes
│   ├── product-icons/       Crowe Logic icon theme
│   ├── walkthroughs/        Getting-started welcome page
│   └── src/extension.ts     Auto-applies rebrand + registers @crowe-logic chat participant
├── product-icons → see extension/product-icons
├── copilot/
│   ├── user-settings.json   Snippet that overrides Copilot Chat persona
│   └── system-prompt.md     Crowe Logic identity rules
├── fork-overlay/
│   └── product.json         Branding overlay for a full microsoft/vscode fork
└── scripts/
    ├── patch-local-install.sh   Rebrand the VS Code on this machine in place
    ├── restore-local-install.sh Undo the patch
    └── build-fork.sh             Build a "Crowe Logic Code" Electron app from upstream vscode
```

## 1. Drop-in extension (recommended start)

Re-skins stock VS Code with the Crowe Logic palette, product icons, title bar
text, welcome walkthrough, and a `@crowe-logic` chat participant whose avatar
is the Crowe Logic mark — replacing the default Copilot avatar in any chat
thread that targets it.

```bash
cd vscode/extension
npm install
npm run compile
npx @vscode/vsce package          # produces crowe-logic-vscode-0.1.0.vsix
code --install-extension crowe-logic-vscode-0.1.0.vsix
```

On first activation the extension automatically:

- switches to **Crowe Logic Dark**,
- applies **Crowe Logic Icons**,
- rewrites `window.title` to *"Crowe Logic — …"*, and
- opens the welcome walkthrough.

Run `Crowe Logic: Apply Full Rebrand` any time to re-apply.

## 2. Rebrand Copilot Chat without touching VS Code

If you only want the Copilot Chat persona to read as **Crowe Logic** (avatar +
identity + system prompt), merge `vscode/copilot/user-settings.json` into your
VS Code user settings. The `customInstructions` block enforces the identity
rules in `vscode/copilot/system-prompt.md`.

The drop-in extension already registers a `@crowe-logic` chat participant with
the correct avatar — the user-settings snippet additionally re-instructs the
default `@github` participant.

## 3. Rebrand your installed VS Code in place

To rename the application itself ("Crowe Logic Code" in the Dock, About box,
window title, and Launch Services), patch the installed bundle:

```bash
sudo vscode/scripts/patch-local-install.sh
# Optional explicit target:
sudo vscode/scripts/patch-local-install.sh "/Applications/Visual Studio Code.app"
```

The script:

- backs up the originals to `*.crowe-logic.bak`,
- rewrites `Resources/app/product.json` (`nameShort`, `nameLong`,
  `applicationName`, `dataFolderName`, win32 IDs, URL protocol, …),
- replaces `Code.icns` / `code.png` with the Crowe Logic mark
  (requires `librsvg` or `imagemagick`), and
- updates macOS `Info.plist` and refreshes Launch Services.

Revert with `sudo vscode/scripts/restore-local-install.sh`.

> ⚠️ Code-signed apps will report a broken signature after patching. Re-sign
> with `codesign --force --deep --sign -` for local use, or expect Gatekeeper
> warnings. The patch is intended for **local developer machines**, not for
> redistribution.

## 4. Build a true "Crowe Logic Code" fork

For a redistributable Electron app:

```bash
vscode/scripts/build-fork.sh           # uses VSCODE_TAG=1.95.0 by default
VSCODE_TAG=1.96.0 vscode/scripts/build-fork.sh
```

The script clones `microsoft/vscode`, merges `vscode/fork-overlay/product.json`
on top of upstream `product.json`, replaces `resources/{darwin,linux,win32}`
icons with the Crowe Logic mark, then runs `yarn && yarn gulp vscode-<host>-min`
to produce a packaged build.

Requirements: Node 20.x, Yarn 1.x, Python 3, and `librsvg` or `imagemagick`.

> The Microsoft EULA forbids redistributing the **Marketplace** under a
> different brand. Your fork must use a different (or empty) `extensionsGallery`
> endpoint — Open VSX is the common choice. The overlay leaves
> `extensionsGallery` untouched precisely so you make this decision explicitly.

## Brand tokens

| Token              | Value      |
| ------------------ | ---------- |
| Gold (primary)     | `#bfa669`  |
| Gold (highlight)   | `#d8c089`  |
| Gold (deep)        | `#9c8451`  |
| Graphite (canvas)  | `#0b0b0c`  |
| Graphite (panel)   | `#121214`  |
| Parchment (text)   | `#e8e2cf`  |

These mirror `cli/branding.py` so the CLI, terminal HUD, and VS Code UI share
one palette.
