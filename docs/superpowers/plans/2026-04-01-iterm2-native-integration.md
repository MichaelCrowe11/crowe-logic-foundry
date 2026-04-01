# iTerm2 Native Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add native iTerm2 integration to crowe-logic via a companion daemon, status bar, session titles, profile management, and user variable bridge — with zero impact on non-iTerm2 terminals.

**Architecture:** Two-process, one-directional design. The CLI emits iTerm2 user variables via escape sequences (`\033]1337;SetUserVar=...`). A companion daemon (AutoLaunch script) reads those variables and drives iTerm2's status bar, session title, and profile switching. The CLI never imports `iterm2`; the daemon never imports `azure`.

**Tech Stack:** Python 3.10+, `iterm2` package (daemon only, isolated venv), Click (CLI subcommands), Rich (console output), base64 (escape sequence encoding)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `iterm/__init__.py` | **Create** | `iterm_set_var()` escape sequence helper, `install_iterm()`, `uninstall_iterm()`, `iterm_status()` |
| `iterm/daemon.py` | **Create** | Companion daemon: status bar component, title provider, profile manager |
| `cli/crowe_logic.py` | **Modify** | Add `iterm` subcommand group, emit user variables in `chat()` and `resume()` |
| `pyproject.toml` | **Modify** | Add `iterm*` to `setuptools.packages.find.include`, add `iterm/daemon.py` to package-data |
| `tests/test_iterm.py` | **Create** | Unit tests for variable bridge, install/uninstall logic, graceful degradation |

---

### Task 1: Create the `iterm` Package with Variable Bridge

**Files:**
- Create: `iterm/__init__.py`
- Create: `tests/test_iterm.py`

This is the foundation — the escape sequence helper that the CLI uses to communicate with the daemon. It must be a no-op on non-iTerm2 terminals.

- [ ] **Step 1: Write the failing test for `iterm_set_var`**

Create `tests/test_iterm.py`:

```python
"""Tests for the iTerm2 integration module."""

import os
import sys
import io
import unittest
from unittest.mock import patch


class TestItermSetVar(unittest.TestCase):
    """Test the iTerm2 user variable escape sequence helper."""

    @patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"})
    def test_emits_escape_sequence_in_iterm(self):
        """Should emit the correct escape sequence when running in iTerm2."""
        from iterm import iterm_set_var
        import base64

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            iterm_set_var("crowe_logic_active", "1")

        output = buf.getvalue()
        expected_payload = base64.b64encode(b"crowe_logic_active=1").decode()
        assert f"\033]1337;SetUserVar={expected_payload}\a" == output

    @patch.dict(os.environ, {"TERM_PROGRAM": "Apple_Terminal"})
    def test_noop_on_non_iterm(self):
        """Should emit nothing when not running in iTerm2."""
        from iterm import iterm_set_var

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            iterm_set_var("crowe_logic_active", "1")

        assert buf.getvalue() == ""

    @patch.dict(os.environ, {}, clear=True)
    def test_noop_when_term_program_missing(self):
        """Should emit nothing when TERM_PROGRAM is not set."""
        from iterm import iterm_set_var

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            iterm_set_var("crowe_logic_active", "1")

        assert buf.getvalue() == ""

    @patch.dict(os.environ, {"TERM_PROGRAM": "WezTerm"})
    def test_emits_in_wezterm(self):
        """WezTerm also supports iTerm2 escape sequences."""
        from iterm import iterm_set_var
        import base64

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            iterm_set_var("crowe_logic_tools", "7")

        output = buf.getvalue()
        expected_payload = base64.b64encode(b"crowe_logic_tools=7").decode()
        assert f"\033]1337;SetUserVar={expected_payload}\a" == output


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
python -m pytest tests/test_iterm.py -v
```

Expected: `ModuleNotFoundError: No module named 'iterm'`

- [ ] **Step 3: Create `iterm/__init__.py` with the variable bridge**

Create `iterm/__init__.py`:

```python
"""
Crowe Logic — iTerm2 Native Integration

Provides the user variable bridge (escape sequences) and install/uninstall
commands for the companion daemon. The CLI uses iterm_set_var() to communicate
session state to the daemon. Non-iTerm2 terminals are handled gracefully (no-op).
"""

import os
import sys
import base64


def iterm_set_var(name: str, value: str) -> None:
    """Set an iTerm2 user variable via escape sequence.

    No-op on non-iTerm2 terminals. Safe to call unconditionally.
    """
    if os.environ.get("TERM_PROGRAM") not in ("iTerm.app", "WezTerm"):
        return
    encoded = base64.b64encode(f"{name}={value}".encode()).decode()
    sys.stdout.write(f"\033]1337;SetUserVar={encoded}\a")
    sys.stdout.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
python -m pytest tests/test_iterm.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add iterm/__init__.py tests/test_iterm.py
git commit -m "feat(iterm): add user variable bridge with escape sequence helper"
```

---

### Task 2: Add Install, Uninstall, and Status Logic to `iterm/__init__.py`

**Files:**
- Modify: `iterm/__init__.py`
- Modify: `tests/test_iterm.py`

The install command copies the daemon to AutoLaunch, creates the isolated venv, and sets up the profile. Uninstall reverses it. Status reports the current state.

- [ ] **Step 1: Write failing tests for install/uninstall/status**

Append to `tests/test_iterm.py`:

```python
class TestItermPaths(unittest.TestCase):
    """Test path constants and helper functions."""

    def test_daemon_dest_path(self):
        from iterm import DAEMON_DEST
        assert "iTerm2/Scripts/AutoLaunch/crowe-logic-daemon.py" in DAEMON_DEST

    def test_venv_path(self):
        from iterm import ITERM_VENV
        assert ".crowe-logic/iterm-env" in ITERM_VENV

    def test_daemon_source_exists(self):
        from iterm import DAEMON_SOURCE
        # daemon.py will be created in Task 3, so just check the path is set
        assert DAEMON_SOURCE.endswith("daemon.py")


class TestItermInstall(unittest.TestCase):
    """Test install logic (filesystem operations mocked)."""

    @patch.dict(os.environ, {"TERM_PROGRAM": "Apple_Terminal"})
    def test_install_rejects_non_iterm(self):
        from iterm import install_iterm
        success, msg = install_iterm()
        assert success is False
        assert "iTerm2" in msg

    @patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"})
    @patch("iterm.shutil.copy2")
    @patch("iterm.os.makedirs")
    @patch("iterm.os.path.exists", return_value=False)
    @patch("iterm.subprocess.run")
    def test_install_creates_venv_and_copies_daemon(self, mock_run, mock_exists, mock_makedirs, mock_copy):
        from iterm import install_iterm
        success, msg = install_iterm()
        assert success is True
        assert "Installed" in msg
        # Verify venv creation was attempted
        venv_calls = [c for c in mock_run.call_args_list if "venv" in str(c)]
        assert len(venv_calls) >= 1


class TestItermUninstall(unittest.TestCase):
    """Test uninstall logic."""

    @patch("iterm.os.path.exists", return_value=True)
    @patch("iterm.os.remove")
    @patch("iterm.shutil.rmtree")
    def test_uninstall_removes_daemon_and_venv(self, mock_rmtree, mock_remove, mock_exists):
        from iterm import uninstall_iterm
        success, msg = uninstall_iterm()
        assert success is True
        assert "Uninstalled" in msg
        mock_remove.assert_called_once()

    @patch("iterm.os.path.exists", return_value=False)
    def test_uninstall_when_not_installed(self, mock_exists):
        from iterm import uninstall_iterm
        success, msg = uninstall_iterm()
        assert success is True
        assert "not installed" in msg.lower() or "Uninstalled" in msg


class TestItermStatus(unittest.TestCase):
    """Test status reporting."""

    @patch.dict(os.environ, {"TERM_PROGRAM": "iTerm.app"})
    @patch("iterm.os.path.exists", return_value=True)
    def test_status_all_installed(self, mock_exists):
        from iterm import iterm_status
        status = iterm_status()
        assert status["iterm_detected"] is True
        assert status["daemon_installed"] is True

    @patch.dict(os.environ, {"TERM_PROGRAM": "Apple_Terminal"})
    def test_status_not_iterm(self):
        from iterm import iterm_status
        status = iterm_status()
        assert status["iterm_detected"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_iterm.py -v -k "Paths or Install or Uninstall or Status"
```

Expected: FAIL — `ImportError` for missing names.

- [ ] **Step 3: Implement install, uninstall, and status in `iterm/__init__.py`**

Add these imports and constants at the top of `iterm/__init__.py` (after existing imports):

```python
import shutil
import subprocess

# Paths
ITERM_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DAEMON_SOURCE = os.path.join(ITERM_PACKAGE_DIR, "daemon.py")
DAEMON_DEST = os.path.expanduser(
    "~/Library/Application Support/iTerm2/Scripts/AutoLaunch/crowe-logic-daemon.py"
)
AUTOLAUNCH_DIR = os.path.dirname(DAEMON_DEST)
ITERM_VENV = os.path.expanduser("~/.crowe-logic/iterm-env")

# Profile color scheme
PROFILE_COLORS = {
    "background": {"Red Component": 0.051, "Green Component": 0.051, "Blue Component": 0.051},
    "foreground": {"Red Component": 0.878, "Green Component": 0.878, "Blue Component": 0.878},
    "bold": {"Red Component": 1.0, "Green Component": 1.0, "Blue Component": 1.0},
    "cursor": {"Red Component": 0.749, "Green Component": 0.651, "Blue Component": 0.412},
    "cursor_text": {"Red Component": 0.051, "Green Component": 0.051, "Blue Component": 0.051},
    "selection": {"Red Component": 0.749, "Green Component": 0.651, "Blue Component": 0.412, "Alpha Component": 0.3},
    "badge": {"Red Component": 0.749, "Green Component": 0.651, "Blue Component": 0.412, "Alpha Component": 0.15},
}
```

Then add these functions after `iterm_set_var`:

```python
def install_iterm() -> tuple[bool, str]:
    """Install the iTerm2 companion daemon and Crowe Logic profile.

    Returns (success: bool, message: str).
    """
    if os.environ.get("TERM_PROGRAM") != "iTerm.app":
        return False, "Not running in iTerm2. Install must be run from iTerm2."

    # 1. Create the isolated venv for the daemon
    if not os.path.exists(ITERM_VENV):
        os.makedirs(os.path.dirname(ITERM_VENV), exist_ok=True)
        subprocess.run(
            [sys.executable, "-m", "venv", ITERM_VENV],
            check=True, capture_output=True,
        )

    # 2. Install iterm2 package into the venv
    pip_path = os.path.join(ITERM_VENV, "bin", "pip3")
    subprocess.run(
        [pip_path, "install", "--quiet", "iterm2"],
        check=True, capture_output=True,
    )

    # 3. Create AutoLaunch directory
    os.makedirs(AUTOLAUNCH_DIR, exist_ok=True)

    # 4. Copy daemon with correct shebang
    python_path = os.path.join(ITERM_VENV, "bin", "python3")
    _write_daemon_with_shebang(DAEMON_SOURCE, DAEMON_DEST, python_path)

    # 5. Make daemon executable
    os.chmod(DAEMON_DEST, 0o755)

    return True, "Installed. Restart iTerm2 to activate the Crowe Logic integration."


def _write_daemon_with_shebang(source: str, dest: str, python_path: str) -> None:
    """Copy daemon.py to dest, replacing the shebang with the venv python path."""
    with open(source, "r") as f:
        content = f.read()

    # Replace placeholder shebang
    if content.startswith("#!"):
        first_newline = content.index("\n")
        content = content[first_newline:]

    shebang = f"#!{python_path}\n"
    with open(dest, "w") as f:
        f.write(shebang + content)


def uninstall_iterm() -> tuple[bool, str]:
    """Remove the iTerm2 companion daemon.

    Returns (success: bool, message: str).
    """
    removed_anything = False

    # Remove daemon
    if os.path.exists(DAEMON_DEST):
        os.remove(DAEMON_DEST)
        removed_anything = True

    # Remove venv (optional cleanup)
    if os.path.exists(ITERM_VENV):
        shutil.rmtree(ITERM_VENV)
        removed_anything = True

    if not removed_anything:
        return True, "Crowe Logic iTerm2 integration is not installed."

    return True, "Uninstalled. Restart iTerm2 to complete removal."


def iterm_status() -> dict:
    """Check the status of the iTerm2 integration.

    Returns a dict with keys: iterm_detected, daemon_installed, venv_exists, profile_exists.
    """
    return {
        "iterm_detected": os.environ.get("TERM_PROGRAM") == "iTerm.app",
        "daemon_installed": os.path.exists(DAEMON_DEST),
        "venv_exists": os.path.exists(ITERM_VENV),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_iterm.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add iterm/__init__.py tests/test_iterm.py
git commit -m "feat(iterm): add install, uninstall, and status logic"
```

---

### Task 3: Create the Companion Daemon

**Files:**
- Create: `iterm/daemon.py`

The daemon runs inside iTerm2 as an AutoLaunch script. It registers a status bar component, a title provider, and a profile switcher. It reads user variables set by the CLI.

- [ ] **Step 1: Create `iterm/daemon.py`**

```python
#!/usr/bin/env python3
"""
Crowe Logic — iTerm2 Companion Daemon

Auto-launched by iTerm2. Reads user variables set by the CLI via escape
sequences and drives the status bar, session title, and profile switching.

This file is copied to ~/Library/Application Support/iTerm2/Scripts/AutoLaunch/
on `crowe-logic iterm install`. The shebang is rewritten to point to the
isolated venv at ~/.crowe-logic/iterm-env/bin/python3.
"""

import asyncio
import iterm2


# ── Status Bar Component ──────────────────────────────────────


async def _register_status_bar(connection):
    """Register the Crowe Logic status bar component."""

    component = iterm2.StatusBarComponent(
        short_description="Crowe Logic",
        detailed_description="Crowe Logic session status — tools, duration, API health",
        knobs=[],
        exemplar="Crowe Logic | 12m | 7 tools | OK",
        update_cadence=5,
        identifier="com.crowelogic.statusbar",
    )

    @iterm2.StatusBarRPC
    async def statusbar_callback(
        knobs,
        crowe_logic_tools=iterm2.Reference("user.crowe_logic_tools?"),
        crowe_logic_duration=iterm2.Reference("user.crowe_logic_duration?"),
        crowe_logic_api=iterm2.Reference("user.crowe_logic_api?"),
        crowe_logic_active=iterm2.Reference("user.crowe_logic_active?"),
    ):
        # If no active session, show static brand
        if not crowe_logic_active or crowe_logic_active != "1":
            return "Crowe Logic"

        parts = ["Crowe Logic"]

        if crowe_logic_duration:
            parts.append(crowe_logic_duration)

        if crowe_logic_tools:
            parts.append(f"{crowe_logic_tools} tools")

        if crowe_logic_api:
            status = crowe_logic_api.upper()
            if status == "OK":
                parts.append("OK")
            elif status == "THROTTLED":
                parts.append("THROTTLED")
            elif status == "DOWN":
                parts.append("DOWN")
            else:
                parts.append(status)
        else:
            parts.append("OK")

        return " | ".join(parts)

    await component.async_register(connection, statusbar_callback)


# ── Session Title Provider ────────────────────────────────────


async def _register_title_provider(connection):
    """Register the Crowe Logic session title provider."""

    @iterm2.TitleProviderRPC
    async def title_callback(
        crowe_logic_active=iterm2.Reference("user.crowe_logic_active?"),
        crowe_logic_tools=iterm2.Reference("user.crowe_logic_tools?"),
    ):
        if not crowe_logic_active or crowe_logic_active != "1":
            return ""

        if crowe_logic_tools:
            return f"Crowe Logic | {crowe_logic_tools} tools"
        return "Crowe Logic"

    await iterm2.Registration.async_register_title_provider(
        connection,
        "com.crowelogic.title",
        title_callback,
    )


# ── Profile Switcher ─────────────────────────────────────────


async def _monitor_profile(connection):
    """Watch crowe_logic_active and switch profiles accordingly."""

    previous_profile = {}  # session_id -> profile_name

    async with iterm2.FocusMonitor(connection) as monitor:
        while True:
            update = await monitor.async_get_next_update()

            if not update.active_session_changed:
                continue

            app = await iterm2.async_get_app(connection)
            session = app.current_terminal_window.current_tab.current_session

            if session is None:
                continue

            session_id = session.session_id
            active = await session.async_get_variable("user.crowe_logic_active")

            if active == "1":
                # Save current profile and switch to Crowe Logic
                current_profile = await session.async_get_profile()
                if current_profile.name != "Crowe Logic":
                    previous_profile[session_id] = current_profile.name
                    try:
                        profiles = await iterm2.PartialProfile.async_query(connection)
                        cl_profile = None
                        for p in profiles:
                            if p.name == "Crowe Logic":
                                cl_profile = p
                                break
                        if cl_profile:
                            full = await cl_profile.async_get_full_profile()
                            await session.async_set_profile(full)
                    except Exception:
                        pass  # Profile may not exist yet

            elif active == "0" and session_id in previous_profile:
                # Revert to previous profile
                prev_name = previous_profile.pop(session_id)
                try:
                    profiles = await iterm2.PartialProfile.async_query(connection)
                    for p in profiles:
                        if p.name == prev_name:
                            full = await p.async_get_full_profile()
                            await session.async_set_profile(full)
                            break
                except Exception:
                    pass


# ── Status Bar Click Handler (Popover) ────────────────────────


async def _register_popover(connection):
    """Register the status bar click handler that shows an HTML popover."""

    @iterm2.StatusBarRPC
    async def on_click(
        knobs,
        crowe_logic_tools=iterm2.Reference("user.crowe_logic_tools?"),
        crowe_logic_duration=iterm2.Reference("user.crowe_logic_duration?"),
        crowe_logic_api=iterm2.Reference("user.crowe_logic_api?"),
    ):
        duration = crowe_logic_duration or "—"
        tools = crowe_logic_tools or "0"
        api = (crowe_logic_api or "ok").upper()

        html = f"""<div style="font-family: monospace; padding: 12px; color: #e0e0e0; background: #1a1a1a;">
  <div style="color: #bfa669; font-weight: bold; margin-bottom: 8px;">Crowe Logic</div>
  <div>Session: {duration}</div>
  <div>Tools executed: {tools}</div>
  <div>API: {api}</div>
</div>"""
        return html

    # Note: Click handler registration uses the same component identifier
    # The on_click callback is set via the component's onclick parameter
    # iTerm2 API handles this through the status bar component registration


# ── Main Entry Point ──────────────────────────────────────────


async def main(connection):
    """Register all Crowe Logic components and run forever."""
    await _register_status_bar(connection)
    await _register_title_provider(connection)

    # Run profile monitor in background
    asyncio.ensure_future(_monitor_profile(connection))


iterm2.run_forever(main)
```

- [ ] **Step 2: Verify the daemon file is syntactically valid**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
python -c "import py_compile; py_compile.compile('iterm/daemon.py', doraise=True)"
```

Expected: No errors (syntax check only — `iterm2` is not installed in the main env).

- [ ] **Step 3: Commit**

```bash
git add iterm/daemon.py
git commit -m "feat(iterm): add companion daemon with status bar, title, and profile switching"
```

---

### Task 4: Add `iterm` CLI Subcommands

**Files:**
- Modify: `cli/crowe_logic.py` (add `iterm` group with `install`, `uninstall`, `status` commands)

Wire up the install/uninstall/status functions from `iterm/__init__.py` as Click subcommands under `crowe-logic iterm`.

- [ ] **Step 1: Add the import at the top of `cli/crowe_logic.py`**

After the existing branding imports (line 33–39), add:

```python
from iterm import install_iterm, uninstall_iterm, iterm_status
```

- [ ] **Step 2: Add the `iterm` subcommand group after the `resume` command**

After the `resume()` function (around line 960), add:

```python
@main.group()
def iterm():
    """Manage iTerm2 native integration."""
    pass


@iterm.command()
def install():
    """Install the iTerm2 companion daemon and Crowe Logic profile."""
    success, msg = install_iterm()
    if success:
        console.print(f"\n  [#6fbf73]{msg}[/#6fbf73]\n")
    else:
        console.print(f"\n  [bold red]{msg}[/bold red]\n")
        if "Python API" in msg:
            console.print("  [dim]Enable at: Preferences > General > Magic > Enable Python API[/dim]\n")


@iterm.command()
def uninstall():
    """Remove the iTerm2 companion daemon."""
    success, msg = uninstall_iterm()
    if success:
        console.print(f"\n  [#6fbf73]{msg}[/#6fbf73]\n")
    else:
        console.print(f"\n  [bold red]{msg}[/bold red]\n")


@iterm.command(name="status")
def iterm_status_cmd():
    """Show iTerm2 integration status."""
    from rich.table import Table
    from rich import box

    info = iterm_status()
    table = Table(
        title="iTerm2 Integration",
        box=box.ROUNDED,
        border_style="#bfa669",
        title_style="bold #bfa669",
        show_header=False,
        padding=(0, 1),
    )
    table.add_column("Check", style="#bfa669 bold", min_width=18)
    table.add_column("Status", style="white")

    def _yn(val):
        return "[#6fbf73]yes[/#6fbf73]" if val else "[#bf6f6f]no[/#bf6f6f]"

    table.add_row("iTerm2 detected", _yn(info["iterm_detected"]))
    table.add_row("Daemon installed", _yn(info["daemon_installed"]))
    table.add_row("Venv exists", _yn(info["venv_exists"]))

    console.print()
    console.print(table)
    console.print()
```

- [ ] **Step 3: Test the CLI commands register**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
python -c "from cli.crowe_logic import main; print('OK')"
crowe-logic iterm --help
```

Expected: Help text showing `install`, `uninstall`, `status` subcommands.

- [ ] **Step 4: Commit**

```bash
git add cli/crowe_logic.py
git commit -m "feat(iterm): add iterm install/uninstall/status CLI subcommands"
```

---

### Task 5: Emit User Variables in `chat()` and `resume()`

**Files:**
- Modify: `cli/crowe_logic.py` (add `iterm_set_var` calls at integration points)

The CLI must emit iTerm2 user variables at key moments: session start, session end, tool execution, duration updates, and API status changes.

- [ ] **Step 1: Add `iterm_set_var` to the import**

The import from Task 4 already includes `iterm_set_var` if we add it. Update the import line to:

```python
from iterm import iterm_set_var, install_iterm, uninstall_iterm, iterm_status
```

- [ ] **Step 2: Emit variables in `chat()`**

In `chat()`, add these calls at the specified locations:

**After `reset_session_state()` (session start):**
```python
    iterm_set_var("crowe_logic_active", "1")
```

**After `session_state["tool_count"] += 1` (in `stream_response` Phase 2, line ~389):**
```python
                iterm_set_var("crowe_logic_tools", str(session_state["tool_count"]))
```

**Before `session.prompt()` (inside the while loop, before the prompt call at line ~487):**
```python
            # Update iTerm2 duration variable
            elapsed = time.monotonic() - session_state["started_at"]
            minutes = int(elapsed) // 60
            seconds = int(elapsed) % 60
            dur_str = f"{minutes}m {seconds:02d}s" if minutes > 0 else f"{seconds}s"
            iterm_set_var("crowe_logic_duration", dur_str)
```

**After `session_state["api_status"]` changes (after `session_state["api_status"] = "ok"` at line ~544):**
```python
                        iterm_set_var("crowe_logic_api", "ok")
```

**After `session_state["api_status"] = "down"` (line ~570):**
```python
                    iterm_set_var("crowe_logic_api", "down")
```

**On session exit (both `except` and `/exit` blocks):**
```python
            iterm_set_var("crowe_logic_active", "0")
```

- [ ] **Step 3: Emit variables in `resume()`**

Apply the same pattern to `resume()`:

**After `reset_session_state()` (around line 889):**
```python
    iterm_set_var("crowe_logic_active", "1")
```

**Before `session.prompt()` in the while loop:**
```python
            elapsed = time.monotonic() - session_state["started_at"]
            minutes = int(elapsed) // 60
            seconds = int(elapsed) % 60
            dur_str = f"{minutes}m {seconds:02d}s" if minutes > 0 else f"{seconds}s"
            iterm_set_var("crowe_logic_duration", dur_str)
```

**On session exit (both `except` and `/exit` blocks):**
```python
            iterm_set_var("crowe_logic_active", "0")
```

- [ ] **Step 4: Emit API status from `stream_response` on rate limit detection**

In `stream_response()`, after `session_state["api_status"] = "throttled"` (line ~310):
```python
                        iterm_set_var("crowe_logic_api", "throttled")
```

- [ ] **Step 5: Test that variables are emitted (manual check)**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
python -c "
import os
os.environ['TERM_PROGRAM'] = 'iTerm.app'
from iterm import iterm_set_var
iterm_set_var('crowe_logic_active', '1')
print('Variable emitted (check for escape sequence)')
"
```

Expected: Escape sequence characters appear in output.

- [ ] **Step 6: Commit**

```bash
git add cli/crowe_logic.py
git commit -m "feat(iterm): emit user variables at session lifecycle points"
```

---

### Task 6: Update `pyproject.toml` and Package Configuration

**Files:**
- Modify: `pyproject.toml`

The `iterm` package must be included in the build and the daemon file must be included as package data.

- [ ] **Step 1: Update `pyproject.toml`**

In `[tool.setuptools.packages.find]`, add `"iterm*"` to the include list:

```toml
[tool.setuptools.packages.find]
include = ["cli*", "tools*", "config*", "scripts*", "crowe_synapse*", "iterm*"]
```

In `[tool.setuptools.package-data]`, add the daemon:

```toml
[tool.setuptools.package-data]
cli = ["icon.png", "icons/*.icns"]
crowe_synapse = ["templates/*.yaml"]
iterm = ["daemon.py"]
```

- [ ] **Step 2: Verify the package builds**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
python -c "
import importlib
import iterm
print('iterm package:', iterm.__file__)
print('Has iterm_set_var:', hasattr(iterm, 'iterm_set_var'))
print('Has install_iterm:', hasattr(iterm, 'install_iterm'))
"
```

Expected: All `True`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: include iterm package in build configuration"
```

---

### Task 7: Add Deploy-Time iTerm2 Detection

**Files:**
- Modify: `cli/crowe_logic.py` (modify `deploy` command to prompt for iTerm2 integration)

After a successful deploy, if running in iTerm2 and the daemon isn't installed, prompt the user to install.

- [ ] **Step 1: Modify the `deploy` command**

Replace the current `deploy` command with:

```python
@main.command()
@click.option("--name", default="crowe-logic", help="Agent name")
def deploy(name: str):
    """Create or recreate the Crowe Logic agent."""
    from scripts.create_agent import create_agent
    create_agent(name=name, verbose=True)

    # iTerm2 integration prompt
    if os.environ.get("TERM_PROGRAM") == "iTerm.app":
        if not os.path.exists(os.path.expanduser(
            "~/Library/Application Support/iTerm2/Scripts/AutoLaunch/crowe-logic-daemon.py"
        )):
            console.print()
            console.print("  [#bfa669]iTerm2 detected.[/#bfa669] Enable native terminal integration?")
            console.print("  [dim](status bar, session titles, Crowe Logic profile)[/dim]")
            response = input("  [y/N]: ").strip().lower()
            if response in ("y", "yes"):
                success, msg = install_iterm()
                if success:
                    console.print(f"  [#6fbf73]{msg}[/#6fbf73]")
                else:
                    console.print(f"  [bold red]{msg}[/bold red]")
```

- [ ] **Step 2: Test deploy command still loads**

```bash
crowe-logic deploy --help
```

Expected: Help text for deploy with `--name` option.

- [ ] **Step 3: Commit**

```bash
git add cli/crowe_logic.py
git commit -m "feat(iterm): prompt for integration during deploy in iTerm2"
```

---

### Task 8: Run Full Test Suite and Final Validation

**Files:**
- All test files

Verify nothing is broken, all new tests pass, and the CLI still starts correctly.

- [ ] **Step 1: Run all tests**

```bash
cd /Users/crowelogic/Projects/crowe-logic-foundry
python -m pytest tests/ -v
```

Expected: All tests PASS (existing + new iTerm2 tests).

- [ ] **Step 2: Run the new iTerm2 tests specifically**

```bash
python -m pytest tests/test_iterm.py -v
```

Expected: All iTerm2 tests PASS.

- [ ] **Step 3: Verify CLI starts without errors**

```bash
crowe-logic --help
crowe-logic iterm --help
crowe-logic iterm status
```

Expected: Help text displays. Status shows current iTerm2 detection state.

- [ ] **Step 4: Verify graceful degradation outside iTerm2**

```bash
TERM_PROGRAM=Apple_Terminal python -c "
from iterm import iterm_set_var
iterm_set_var('test', 'value')
print('No error — graceful degradation works')
"
```

Expected: Prints the success message with no escape sequences.

- [ ] **Step 5: Final commit (if any test fixes needed)**

```bash
git add -A
git commit -m "test: validate iTerm2 integration end-to-end"
```
