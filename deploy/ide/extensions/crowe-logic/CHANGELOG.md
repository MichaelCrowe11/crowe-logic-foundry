# Changelog

All notable changes to the Crowe Logic extension.

## 0.2.44

- Wave-style Browser panel beside Terminal/Ports — socket-polled ports (ss→/proc/net/tcp[6]), one-click chips, auto-preview on single new port, localhost via `vscode.env.asExternalUri` (same-origin proxy, no forked build), --clm-* themed; settings autoOpen/pollSeconds/ignorePorts.
- crowe-logic default terminal agent via /usr/local/bin/crowe-agent, auto-launch on pod startup (croweLogic.autoLaunchAgent); bash/Claude Code under non-default "Diagnostics"; deepparallel/crowelm-cloud deferred until real.

## 0.2.14

- Startup branding now lands on the Crowe Logic walkthrough again whenever the extension is first installed or upgraded, and the configured Crowe Logic dark/light theme is applied automatically on activation.
- Deprecated workstation model selections now self-heal on activation. `CroweLM Synapse` maps to `CroweLM Reason`, and other retired non-chat labels migrate to current interactive tiers instead of crashing `@crowe`.
- The settings UI no longer hardcodes a stale model dropdown. `croweLogic.model` is now a freeform CroweLM label/alias field so the extension can track the live model registry instead of drifting behind it.

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
