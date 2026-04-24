# Changelog

All notable changes to the Crowe Logic extension.

## 0.2.11

- Light theme polished to match the dark pass: borders, menus, notifications, scrollbars, and git decorations tuned deliberately. Added tokenColor rules for punctuation, attribute names, markup, and invalid scopes so syntax reads cleanly in daylight.
- README rewritten for premium positioning. Auto-detect defaults documented. Domain defaults updated.
- Marketplace icon now points at the dark-disc avatar variant that reads on both light and dark Marketplace surfaces.
- Status bar remote-IDE quick-pick now points at `ide.crowelogic.com`.

## 0.2.10

- Python interpreter auto-detect. No more `ENOENT` when the extension is installed into local VS Code. Resolver checks the foundry's own `.venv`, the container default, and `python3` on PATH, then surfaces a readable error if nothing matches.
- Foundry path auto-detect. Picks the current workspace if it contains `cli/headless.py`, then `~/Projects/crowe-logic-foundry`.
- `croweLogic.pythonPath` and `croweLogic.foundryPath` defaults are now empty (= auto-detect). `markdownDescription` fields tell the user where the resolver looks.
- Mark and avatar redesigned. `mark.svg` rebuilt on a 24-unit grid: partial-ring C with an inset forward-chevron. New `avatar.svg` source: pressed-coin composition with a gold gradient mark on a deep-graphite disc. 256x256 PNGs for both theme variants.
- Dark theme polish: borders, menus, notifications, scrollbars, git decorations, plus extended tokenColor rules for punctuation, attribute names, markup, and invalid scopes.
- Walkthrough copy tightened.

## 0.2.8

- Extension defaults updated to `api.crowelogic.com` and `ide.crowelogic.com`. The old `*.southwestmushrooms.com` defaults still work if overridden in settings.

## 0.2.7

- Sign-in flow, remote-IDE handoff, code-actions provider, and status bar item.
