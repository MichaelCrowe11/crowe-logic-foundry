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


async def _monitor_profile(connection):
    """Watch crowe_logic_active and switch profiles accordingly."""

    previous_profile = {}

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
                        pass

            elif active == "0" and session_id in previous_profile:
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


async def main(connection):
    """Register all Crowe Logic components and run forever."""
    await _register_status_bar(connection)
    await _register_title_provider(connection)
    asyncio.ensure_future(_monitor_profile(connection))


iterm2.run_forever(main)
