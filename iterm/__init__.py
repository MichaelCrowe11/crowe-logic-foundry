"""
Crowe Logic -- iTerm2 Native Integration

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
