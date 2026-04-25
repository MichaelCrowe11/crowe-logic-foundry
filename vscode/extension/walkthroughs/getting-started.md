# Welcome to Crowe Logic

Crowe Logic re-skins VS Code as a Crowe Logic-facing product:

- **Crowe Logic Dark** & **Crowe Logic Light** color themes (gold on graphite).
- **Crowe Logic Icons** product-icon theme.
- A title bar that reads *Crowe Logic* instead of *Visual Studio Code*.
- A **`@crowe-logic`** chat participant that replaces the default Copilot
  avatar with the actual Crowe Logic avatar and routes prompts through the CroweLM
  model chain.

## Apply the rebrand

Run `Crowe Logic: Apply Full Rebrand` from the command palette
(<kbd>⇧⌘P</kbd> / <kbd>Ctrl+Shift+P</kbd>) to set the theme, product icons, and
window title in one shot.

## Talk to Crowe Logic

Open the chat panel and start a message with **`@crowe-logic`**. The agent
identifies itself as Crowe Logic, never as Copilot.

## Want full app rebranding?

The themes here re-skin stock VS Code. To rename the application itself
(*"Crowe Logic Code"* in the Dock, About box, and window title bar), run:

```bash
sudo vscode/scripts/patch-local-install.sh
```

from the `crowe-logic-foundry` repo. Restore with `restore-local-install.sh`.
