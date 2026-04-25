# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio — proprietary, private repository.
"""
Studio fast-launch. Bypasses tools/__init__.py (which eagerly imports
Azure/Anthropic/Playwright/Talon/etc. and would require ~1GB of wheels)
by turning `tools` into a bare namespace package at import time.

Only the Studio submodules (capture, presentation, studio_route, shoot,
shot_selector, edl_render, sync, control_center) get imported.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"

pkg = types.ModuleType("tools")
pkg.__path__ = [str(TOOLS_DIR)]
sys.modules["tools"] = pkg

sys.path.insert(0, str(REPO_ROOT))

from tools.control_center import start_control_center  # noqa: E402

if __name__ == "__main__":
    import threading
    start_control_center(port=7777, open_browser=True)
    # start_control_center runs uvicorn in a DAEMON thread so that agents
    # can call it from a REPL. Standalone, we need to block the main
    # thread or the daemon dies.
    threading.Event().wait()
