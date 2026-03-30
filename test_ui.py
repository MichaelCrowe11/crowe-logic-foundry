#!/usr/bin/env python3
"""UI inspection test — run this inside iTerm2."""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from cli.branding import (
    _is_iterm_compatible, _prepare_avatar, get_favicon,
    _inline_image_seq, show_welcome, GOLD, BOLD, RESET
)
from config.agent_config import AGENT_VERSION

print("=" * 50)
print("CROWE LOGIC UI INSPECTION")
print("=" * 50)

# 1. Detection
print(f"\n[1] TERM_PROGRAM = {os.environ.get('TERM_PROGRAM', 'NOT SET')}")
print(f"    iTerm compatible: {_is_iterm_compatible()}")

# 2. Avatar prep
icon = os.path.join(PROJECT_ROOT, "cli", "icon.png")
print(f"\n[2] Icon path: {icon}")
print(f"    Icon exists: {os.path.exists(icon)}")
clean = _prepare_avatar(icon)
print(f"    Clean avatar: {clean}")
print(f"    Clean exists: {os.path.exists(clean)}")

# 3. Favicon test
print(f"\n[3] Favicon test:")
fav = get_favicon()
print(f"    Length: {len(fav)} chars")
print(f"    Has ESC: {chr(27) in fav}")
sys.stdout.write(f"    Render: {fav} <-- favicon should appear left of this\n")
sys.stdout.flush()

# 4. Inline image test (small)
print(f"\n[4] Small inline image (width=4):")
seq = _inline_image_seq(clean, width=4)
if seq:
    sys.stdout.write(f"    {seq}\n")
    sys.stdout.flush()
    print("    ^ image should appear above")
else:
    print("    NO SEQUENCE GENERATED")

# 5. Large inline image (welcome avatar size)
print(f"\n[5] Large inline image (width=10):")
seq2 = _inline_image_seq(clean, width=10)
if seq2:
    sys.stdout.write(f"    {seq2}\n")
    sys.stdout.flush()
    print("    ^ welcome avatar should appear above")
else:
    print("    NO SEQUENCE GENERATED")

# 6. Full welcome screen
print(f"\n[6] Full welcome screen:")
show_welcome(AGENT_VERSION)

# 7. Chat prompt preview
print(f"\n[7] Chat prompt preview:")
sys.stdout.write(f"  {fav} ")
sys.stdout.flush()
print(f"{GOLD}{BOLD}crowe-logic{RESET}")
print("  This is where the response would stream...")

# 8. Prompt chevron preview
print(f"\n[8] User prompt preview:")
print(f"  {GOLD}\u276f{RESET} what can you do")

print(f"\n{'=' * 50}")
print("INSPECTION COMPLETE")
print(f"{'=' * 50}")
