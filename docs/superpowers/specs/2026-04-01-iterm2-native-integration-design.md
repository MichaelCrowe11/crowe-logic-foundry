# iTerm2 Native Integration — Design Spec

**Date:** 2026-04-01
**Status:** Approved
**Scope:** Companion daemon, status bar, title provider, profile management, user variable bridge

## Design Philosophy

Crowe Logic branding only — no model names, no provider details, no tech stack exposed. The iTerm2 integration is a premium layer that enhances the terminal experience for iTerm2 users while the CLI works identically on any terminal.

## Architecture

```
crowe-logic CLI                        iTerm2 Companion Daemon
(Python, Rich, prompt_toolkit)         (AutoLaunch script, iterm2 package)
         │                                      │
         ├─ sets user variables ───────────────> │ reads variables
         │  via \033]1337;SetUserVar=...         │ updates status bar
         │                                      │ updates session title
         │                                      │ manages profile
         └──────────────────────────────────────┘
```

Two processes, one-directional communication. The CLI never imports `iterm2`. The daemon never imports `azure`. They share state through iTerm2 user variables set via escape sequences.

## 1. Companion Daemon

### Location
`~/Library/Application Support/iTerm2/Scripts/AutoLaunch/crowe-logic-daemon.py`

Auto-starts when iTerm2 launches. Runs as a long-lived async process connected to iTerm2 via websocket.

### Source Location
`iterm/daemon.py` in the project — copied to the AutoLaunch directory on install.

### Registered Components

#### 1a. Status Bar Component
- Identifier: `com.crowelogic.statusbar`
- Reads user variables: `crowe_logic_tools`, `crowe_logic_duration`, `crowe_logic_api`
- Display format: `Crowe Logic | 12m | 7 tools | OK`
- Update cadence: 5 seconds (polls variables)
- States:
  - Active session: full display with tools/duration/status
  - No session: `Crowe Logic` (static, indicates daemon is running)
  - API throttled: status changes to amber `THROTTLED`
  - API down: status changes to red `DOWN`
- Click handler: opens HTML popover with session summary (tool count, duration, recent tool names)

#### 1b. Session Title Provider
- Identifier: `com.crowelogic.title`
- Reads: `user.crowe_logic_active`, `user.crowe_logic_tools`
- When active (`crowe_logic_active == "1"`): title is `Crowe Logic | N tools`
- When inactive: returns empty string (falls back to iTerm2 default title)

#### 1c. Profile Management
- Profile name: `Crowe Logic`
- Created on `crowe-logic iterm install`
- Settings:
  - Background: #0d0d0d (near black)
  - Foreground: #e0e0e0 (light gray)
  - Bold: #ffffff (white)
  - Selection: rgba(191, 166, 105, 0.3) (gold tint)
  - Cursor: #bfa669 (gold)
  - Cursor text: #0d0d0d
  - ANSI colors: muted palette with gold accent in yellow slot
  - Badge text: `CL` (semi-transparent overlay, bottom-right)
  - Badge color: rgba(191, 166, 105, 0.15)
  - Font: Geist Mono if available, else Menlo
  - Minimum contrast: 0 (trust the color scheme)
- The daemon activates this profile when `crowe_logic_active` is set to `"1"`
- The daemon reverts to the previous profile when `crowe_logic_active` is set to `"0"`

## 2. User Variable Bridge

### Helper Function (CLI side)
```python
def _iterm_set_var(name: str, value: str):
    """Set an iTerm2 user variable via escape sequence. No-op on non-iTerm2."""
    if os.environ.get("TERM_PROGRAM") not in ("iTerm.app", "WezTerm"):
        return
    import base64
    encoded = base64.b64encode(f"{name}={value}".encode()).decode()
    sys.stdout.write(f"\033]1337;SetUserVar={encoded}\a")
    sys.stdout.flush()
```

Location: `iterm/__init__.py`, imported by `cli/crowe_logic.py`.

### Variables

| Variable | Set When | Value | Read By |
|---|---|---|---|
| `crowe_logic_active` | Chat start / Chat exit | `"1"` or `"0"` | Title provider, profile switcher |
| `crowe_logic_tools` | After each tool execution | Tool count as string, e.g. `"7"` | Status bar |
| `crowe_logic_duration` | Before each prompt render | Duration string, e.g. `"12m 34s"` | Status bar |
| `crowe_logic_api` | On API status change | `"ok"`, `"throttled"`, `"down"` | Status bar |

### Integration Points in CLI

- `chat()` start: `_iterm_set_var("crowe_logic_active", "1")`
- `chat()` exit (goodbye): `_iterm_set_var("crowe_logic_active", "0")`
- `resume()` start/exit: same as chat
- After `session_state["tool_count"]` increments: `_iterm_set_var("crowe_logic_tools", str(session_state["tool_count"]))`
- Before `session.prompt()`: update duration variable
- On `session_state["api_status"]` change: `_iterm_set_var("crowe_logic_api", session_state["api_status"])`

## 3. CLI Commands

### `crowe-logic iterm install`

Steps:
1. Check `TERM_PROGRAM == "iTerm.app"` — error if not running in iTerm2
2. Check if Python API is enabled — print instructions if not (`Preferences > General > Magic > Enable Python API`)
3. Ensure `iterm2` package is available in the daemon's Python environment
4. Create AutoLaunch directory if it doesn't exist
5. Copy `iterm/daemon.py` to `~/Library/Application Support/iTerm2/Scripts/AutoLaunch/crowe-logic-daemon.py`
6. Create the "Crowe Logic" profile via the iterm2 package (run a one-shot script)
7. Print: "Installed. Restart iTerm2 to activate the Crowe Logic integration."

### `crowe-logic iterm uninstall`

Steps:
1. Remove `crowe-logic-daemon.py` from AutoLaunch
2. Remove the "Crowe Logic" profile (if it exists)
3. Print: "Uninstalled. Restart iTerm2 to complete removal."

### `crowe-logic iterm status`

Shows:
- iTerm2 detected: yes/no
- Daemon installed: yes/no (checks file existence)
- Profile exists: yes/no
- Python API enabled: yes/no (attempts connection)

## 4. Deploy Integration

At the end of `crowe-logic deploy`, after agent creation succeeds:

```python
if os.environ.get("TERM_PROGRAM") == "iTerm.app":
    daemon_path = os.path.expanduser(
        "~/Library/Application Support/iTerm2/Scripts/AutoLaunch/crowe-logic-daemon.py"
    )
    if not os.path.exists(daemon_path):
        console.print()
        console.print("  [#bfa669]iTerm2 detected.[/#bfa669] Enable native terminal integration?")
        console.print("  [dim](status bar, session titles, Crowe Logic profile)[/dim]")
        response = input("  [y/N]: ").strip().lower()
        if response in ("y", "yes"):
            # Invoke install logic
            _install_iterm_integration()
```

## 5. Graceful Degradation

- **Non-iTerm2 terminals:** `_iterm_set_var()` checks `TERM_PROGRAM` and returns immediately. Zero overhead.
- **iTerm2 without daemon:** Escape sequences are emitted but nothing reads them. No errors, no side effects. The variables simply exist unread.
- **iTerm2 without Python API enabled:** Daemon won't start. CLI works normally. `crowe-logic iterm status` tells the user what to enable.
- **Daemon crashes:** CLI is unaffected. Status bar disappears but chat continues. Daemon auto-restarts on next iTerm2 launch.

## 6. File Structure

### New Files

```
iterm/
├── __init__.py          # _iterm_set_var() helper, install/uninstall logic
└── daemon.py            # Companion daemon (copied to AutoLaunch on install)
```

### Modified Files

- `cli/crowe_logic.py` — add `iterm` Click subcommand group (`install`, `uninstall`, `status`), emit user variables at integration points in `chat()` and `resume()`
- `cli/branding.py` — no changes (toolbar remains as-is; iTerm2 status bar is additive, not a replacement)

### Dependency Notes

- `iterm2` package is NOT added to crowe-logic's main dependencies
- The daemon script includes a shebang and manages its own Python environment
- `crowe-logic iterm install` checks for / installs the `iterm2` package into the system Python or a dedicated venv at `~/.crowe-logic/iterm-env/`

## 7. Daemon Python Environment

The daemon needs the `iterm2` package but crowe-logic itself doesn't. Strategy:

1. On `crowe-logic iterm install`, create a venv at `~/.crowe-logic/iterm-env/`
2. Install `iterm2` into that venv
3. The daemon script's shebang points to `~/.crowe-logic/iterm-env/bin/python3`
4. This keeps the main crowe-logic installation clean

## 8. Status Bar Popover HTML

When the user clicks the status bar component, an HTML popover appears with:

```html
<div style="font-family: monospace; padding: 12px; color: #e0e0e0; background: #1a1a1a;">
  <div style="color: #bfa669; font-weight: bold; margin-bottom: 8px;">Crowe Logic</div>
  <div>Session: {duration}</div>
  <div>Tools executed: {tool_count}</div>
  <div>API: {api_status}</div>
</div>
```

Styled with gold branding. No model or provider information.

## 9. Testing Strategy

- Manual testing in iTerm2 (primary)
- Verify daemon starts on iTerm2 launch
- Verify status bar updates when CLI sets variables
- Verify title changes on chat start/exit
- Verify profile switches on chat start/exit and reverts cleanly
- Verify graceful degradation in Terminal.app and other non-iTerm2 terminals
- Verify `crowe-logic iterm install/uninstall/status` commands
- Verify deploy-time detection and prompt
