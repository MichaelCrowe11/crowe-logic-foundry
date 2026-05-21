"""
iTerm2 terminal control tools — deep integration via the iTerm2 Python API.

Gives the Crowe Logic agent full control over the terminal environment:
window/tab/pane management, screen reading, profile theming, session
broadcasting, focus control, and custom status bar components.

Requires: pip install iterm2
Docs: https://iterm2.com/python-api/
"""

import asyncio
import json


# ── Connection management ──────────────────────────────────────────────

_connection = None


async def _get_connection():
    """Get or create a persistent iTerm2 API connection."""
    global _connection
    if _connection is None:
        import iterm2
        _connection = await iterm2.Connection.async_create()
    return _connection


def _run(coro):
    """Run an async iTerm2 API call synchronously."""
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return loop.run_in_executor(pool, lambda: asyncio.run(coro))
    except RuntimeError:
        return asyncio.run(coro)


# ── Window & Tab Management ────────────────────────────────────────────


def iterm_create_window(profile: str = "", command: str = "") -> str:
    """
    Create a new iTerm2 terminal window.

    :param profile: Profile name to use (empty for default).
    :param command: Command to run in the new window.
    :return: JSON with window_id and session_id of the new window.
    :rtype: str
    """
    async def _create():
        import iterm2
        conn = await _get_connection()
        window = await iterm2.Window.async_create(
            conn,
            profile=profile or None,
            command=command or None,
        )
        if window:
            session = window.current_tab.current_session
            return json.dumps({
                "window_id": window.window_id,
                "tab_id": window.current_tab.tab_id,
                "session_id": session.session_id if session else None,
            })
        return json.dumps({"error": "Failed to create window"})

    return _run(_create())


def iterm_create_tab(window_id: str = "", profile: str = "", command: str = "") -> str:
    """
    Create a new tab in an existing window (or current window if window_id is empty).

    :param window_id: Target window ID (empty for current window).
    :param profile: Profile name to use (empty for default).
    :param command: Command to run in the new tab.
    :return: JSON with tab_id and session_id.
    :rtype: str
    """
    async def _create():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)

        if window_id:
            window = app.get_window_by_id(window_id)
        else:
            window = app.current_terminal_window

        if not window:
            return json.dumps({"error": "Window not found"})

        tab = await window.async_create_tab(
            profile=profile or None,
            command=command or None,
        )
        if tab:
            session = tab.current_session
            return json.dumps({
                "tab_id": tab.tab_id,
                "session_id": session.session_id if session else None,
            })
        return json.dumps({"error": "Failed to create tab"})

    return _run(_create())


def iterm_split_pane(
    session_id: str = "",
    vertical: bool = True,
    profile: str = "",
    command: str = "",
) -> str:
    """
    Split the current or specified session into a new pane.

    :param session_id: Session to split (empty for active session).
    :param vertical: True for vertical split, False for horizontal.
    :param profile: Profile name for new pane (empty for default).
    :param command: Command to run in the new pane.
    :return: JSON with the new session_id.
    :rtype: str
    """
    async def _split():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)

        if session_id:
            session = app.get_session_by_id(session_id)
        else:
            session = app.current_terminal_window.current_tab.current_session

        if not session:
            return json.dumps({"error": "Session not found"})

        new_session = await session.async_split_pane(
            vertical=vertical,
            profile=profile or None,
        )
        if command:
            await new_session.async_send_text(command + "\n")

        return json.dumps({"session_id": new_session.session_id})

    return _run(_split())


# ── Session Control ────────────────────────────────────────────────────


def iterm_send_text(text: str, session_id: str = "") -> str:
    """
    Send text to a session as if the user typed it.

    :param text: Text to send (will be typed into the terminal).
    :param session_id: Target session (empty for active session).
    :return: JSON confirmation.
    :rtype: str
    """
    async def _send():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)

        if session_id:
            session = app.get_session_by_id(session_id)
        else:
            session = app.current_terminal_window.current_tab.current_session

        if not session:
            return json.dumps({"error": "Session not found"})

        await session.async_send_text(text)
        return json.dumps({"sent": True, "session_id": session.session_id})

    return _run(_send())


def iterm_read_screen(session_id: str = "", lines: int = 50) -> str:
    """
    Read the visible screen content from a session.

    :param session_id: Target session (empty for active session).
    :param lines: Number of lines to read from the screen.
    :return: JSON with screen content, cursor position, and dimensions.
    :rtype: str
    """
    async def _read():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)

        if session_id:
            session = app.get_session_by_id(session_id)
        else:
            session = app.current_terminal_window.current_tab.current_session

        if not session:
            return json.dumps({"error": "Session not found"})

        contents = await session.async_get_screen_contents()
        screen_lines = []
        num_lines = min(lines, contents.number_of_lines)
        for i in range(num_lines):
            line = contents.line(i)
            screen_lines.append(line.string.rstrip())

        cursor = contents.cursor_coord
        return json.dumps({
            "lines": screen_lines,
            "cursor": {"x": cursor.x, "y": cursor.y},
            "dimensions": {
                "width": session.grid_size.width,
                "height": session.grid_size.height,
            },
            "session_id": session.session_id,
        })

    return _run(_read())


def iterm_inject_output(data: str, session_id: str = "") -> str:
    """
    Inject data into a session as if it were program output.
    Useful for displaying formatted content without running a command.

    :param data: Text to inject (supports ANSI escape sequences).
    :param session_id: Target session (empty for active session).
    :return: JSON confirmation.
    :rtype: str
    """
    async def _inject():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)

        if session_id:
            session = app.get_session_by_id(session_id)
        else:
            session = app.current_terminal_window.current_tab.current_session

        if not session:
            return json.dumps({"error": "Session not found"})

        await session.async_inject(data.encode("utf-8"))
        return json.dumps({"injected": True, "session_id": session.session_id})

    return _run(_inject())


# ── Window Layout & Focus ──────────────────────────────────────────────


def iterm_list_sessions() -> str:
    """
    List all windows, tabs, and sessions in the terminal.

    :return: JSON tree of windows > tabs > sessions with IDs and names.
    :rtype: str
    """
    async def _list():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)

        windows = []
        for window in app.terminal_windows:
            tabs = []
            for tab in window.tabs:
                sessions = []
                for session in tab.sessions:
                    sessions.append({
                        "session_id": session.session_id,
                        "name": await session.async_get_variable("name") if hasattr(session, "async_get_variable") else None,
                        "grid": {"w": session.grid_size.width, "h": session.grid_size.height},
                    })
                tabs.append({
                    "tab_id": tab.tab_id,
                    "sessions": sessions,
                })
            windows.append({
                "window_id": window.window_id,
                "tabs": tabs,
            })

        return json.dumps({"windows": windows, "total_sessions": sum(
            len(s["sessions"]) for w in windows for s in w["tabs"]
        )})

    return _run(_list())


def iterm_focus_session(session_id: str) -> str:
    """
    Focus a specific session, selecting its tab and bringing the window forward.

    :param session_id: The session to focus.
    :return: JSON confirmation.
    :rtype: str
    """
    async def _focus():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)
        session = app.get_session_by_id(session_id)

        if not session:
            return json.dumps({"error": f"Session {session_id} not found"})

        await session.async_activate(select_tab=True, order_window_front=True)
        return json.dumps({"focused": True, "session_id": session_id})

    return _run(_focus())


def iterm_set_fullscreen(fullscreen: bool = True, window_id: str = "") -> str:
    """
    Toggle fullscreen mode on a window.

    :param fullscreen: True to enter fullscreen, False to exit.
    :param window_id: Target window (empty for current window).
    :return: JSON confirmation.
    :rtype: str
    """
    async def _fullscreen():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)

        if window_id:
            window = app.get_window_by_id(window_id)
        else:
            window = app.current_terminal_window

        if not window:
            return json.dumps({"error": "Window not found"})

        await window.async_set_fullscreen(fullscreen)
        return json.dumps({"fullscreen": fullscreen, "window_id": window.window_id})

    return _run(_fullscreen())


# ── Profile & Theme ────────────────────────────────────────────────────


def iterm_get_theme() -> str:
    """
    Get the current iTerm2 theme (light/dark/automatic/minimal).

    :return: JSON with theme attributes.
    :rtype: str
    """
    async def _theme():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)
        theme = await app.async_get_theme()
        return json.dumps({"theme": theme})

    return _run(_theme())


def iterm_set_profile_colors(
    session_id: str = "",
    background: str = "",
    foreground: str = "",
    cursor: str = "",
    badge: str = "",
) -> str:
    """
    Modify profile colors for a session. Colors are hex strings (#RRGGBB).

    :param session_id: Target session (empty for active).
    :param background: Background color hex.
    :param foreground: Foreground/text color hex.
    :param cursor: Cursor color hex.
    :param badge: Badge color hex.
    :return: JSON confirmation with applied colors.
    :rtype: str
    """
    async def _set_colors():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)

        if session_id:
            session = app.get_session_by_id(session_id)
        else:
            session = app.current_terminal_window.current_tab.current_session

        if not session:
            return json.dumps({"error": "Session not found"})

        def hex_to_color(hex_str):
            h = hex_str.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return iterm2.Color(r, g, b)

        profile = await session.async_get_profile()
        changes = iterm2.LocalWriteOnlyProfile()
        applied = {}

        if background:
            changes.set_background_color(hex_to_color(background))
            applied["background"] = background
        if foreground:
            changes.set_foreground_color(hex_to_color(foreground))
            applied["foreground"] = foreground
        if cursor:
            changes.set_cursor_color(hex_to_color(cursor))
            applied["cursor"] = cursor
        if badge:
            changes.set_badge_color(hex_to_color(badge))
            applied["badge"] = badge

        await session.async_set_profile_properties(changes)
        return json.dumps({"applied": applied, "session_id": session.session_id})

    return _run(_set_colors())


def iterm_set_badge(text: str, session_id: str = "") -> str:
    """
    Set the badge text overlay on a session. Badges show faintly in the background.

    :param text: Badge text (supports iTerm2 interpolated strings).
    :param session_id: Target session (empty for active).
    :return: JSON confirmation.
    :rtype: str
    """
    async def _badge():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)

        if session_id:
            session = app.get_session_by_id(session_id)
        else:
            session = app.current_terminal_window.current_tab.current_session

        if not session:
            return json.dumps({"error": "Session not found"})

        changes = iterm2.LocalWriteOnlyProfile()
        changes.set_badge_text(text)
        await session.async_set_profile_properties(changes)
        return json.dumps({"badge": text, "session_id": session.session_id})

    return _run(_badge())


# ── Broadcasting ───────────────────────────────────────────────────────


def iterm_broadcast(session_ids: str) -> str:
    """
    Enable keyboard input broadcasting to multiple sessions.
    Typing in any session in the group will broadcast to all.

    :param session_ids: Comma-separated list of session IDs to broadcast to.
    :return: JSON confirmation with broadcast group.
    :rtype: str
    """
    async def _broadcast():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)

        ids = [s.strip() for s in session_ids.split(",")]
        domain = iterm2.BroadcastDomain()

        found = []
        for sid in ids:
            session = app.get_session_by_id(sid)
            if session:
                domain.add_session(session)
                found.append(sid)

        await iterm2.async_set_broadcast_domains(conn, [domain])
        return json.dumps({"broadcasting": found, "count": len(found)})

    return _run(_broadcast())


def iterm_stop_broadcast() -> str:
    """
    Stop all keyboard broadcasting (clear all broadcast domains).

    :return: JSON confirmation.
    :rtype: str
    """
    async def _stop():
        import iterm2
        conn = await _get_connection()
        await iterm2.async_set_broadcast_domains(conn, [])
        return json.dumps({"broadcasting": False})

    return _run(_stop())


# ── Alerts & User Input ───────────────────────────────────────────────


def iterm_alert(title: str, message: str, buttons: str = "OK") -> str:
    """
    Show a modal dialog in iTerm2.

    :param title: Dialog title (bold).
    :param message: Dialog body text.
    :param buttons: Comma-separated button labels (default: "OK").
    :return: JSON with the index of the button clicked (0-based).
    :rtype: str
    """
    async def _alert():
        import iterm2
        conn = await _get_connection()
        alert = iterm2.Alert(title, message)
        for btn in buttons.split(","):
            alert.add_button(btn.strip())
        result = await alert.async_run(conn)
        return json.dumps({"button_index": result - 1000})

    return _run(_alert())


def iterm_prompt_input(title: str, message: str, placeholder: str = "", default: str = "") -> str:
    """
    Show a text input dialog in iTerm2.

    :param title: Dialog title.
    :param message: Prompt text.
    :param placeholder: Grayed-out placeholder text.
    :param default: Pre-filled default value.
    :return: JSON with user input or null if cancelled.
    :rtype: str
    """
    async def _prompt():
        import iterm2
        conn = await _get_connection()
        alert = iterm2.TextInputAlert(title, message, placeholder, default)
        result = await alert.async_run(conn)
        return json.dumps({"input": result})

    return _run(_prompt())


# ── Session Variables ──────────────────────────────────────────────────


def iterm_set_variable(name: str, value: str, session_id: str = "") -> str:
    """
    Set a user-defined variable on a session. Variables can be used in
    badges, title providers, and status bar components.

    :param name: Variable name (will be prefixed with 'user.' if not already).
    :param value: Variable value.
    :param session_id: Target session (empty for active).
    :return: JSON confirmation.
    :rtype: str
    """
    async def _set_var():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)

        if session_id:
            session = app.get_session_by_id(session_id)
        else:
            session = app.current_terminal_window.current_tab.current_session

        if not session:
            return json.dumps({"error": "Session not found"})

        var_name = name if name.startswith("user.") else f"user.{name}"
        await session.async_set_variable(var_name, value)
        return json.dumps({"variable": var_name, "value": value})

    return _run(_set_var())


def iterm_get_variable(name: str, session_id: str = "") -> str:
    """
    Get a variable value from a session.

    :param name: Variable name (e.g. 'jobName', 'user.myvar', 'path').
    :param session_id: Target session (empty for active).
    :return: JSON with the variable value.
    :rtype: str
    """
    async def _get_var():
        import iterm2
        conn = await _get_connection()
        app = await iterm2.async_get_app(conn)

        if session_id:
            session = app.get_session_by_id(session_id)
        else:
            session = app.current_terminal_window.current_tab.current_session

        if not session:
            return json.dumps({"error": "Session not found"})

        result = await session.async_get_variable(name)
        return json.dumps({"variable": name, "value": str(result) if result else None})

    return _run(_get_var())
