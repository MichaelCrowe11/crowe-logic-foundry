# Crowe Logic CLI Design System v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ad-hoc CLI presentation layer with a unified design system that centers correctly at every terminal width, defines a signature wordmark, and applies consistent color, spacing, and glyph tokens across every surface.

**Architecture:** A single design-token module in `cli/branding.py` exposes color, glyph, layout, and spacing primitives. All rendering surfaces (welcome screen, tool cards, telemetry, toolbar, slash commands, errors) import these tokens and route through shared helpers (`center()`, `hairline()`, `render_error()`). The `wcwidth` library handles ambiguous-width Unicode so box-drawing characters do not break centering.

**Tech Stack:** Python 3.13, Rich (terminal rendering), prompt_toolkit (input), wcwidth (Unicode cell width).

**Spec:** `docs/superpowers/specs/2026-04-06-cli-design-system-v1.md`

---

## Task 1: Add wcwidth Dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add wcwidth to requirements**

Append this line to `requirements.txt`:

```
wcwidth>=0.2.13
```

- [ ] **Step 2: Install in the venv**

Run: `.venv/bin/pip install 'wcwidth>=0.2.13'`
Expected: `Successfully installed wcwidth-0.2.X` (or `Requirement already satisfied`).

- [ ] **Step 3: Verify the import works**

Run: `.venv/bin/python -c "import wcwidth; print(wcwidth.wcswidth('CROWE'))"`
Expected: `5`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "build: add wcwidth dependency for terminal cell-width calculation"
```

---

## Task 2: Add Design Tokens to branding.py

**Files:**
- Modify: `cli/branding.py` (top of file, after imports)

- [ ] **Step 1: Add color, glyph, and spacing tokens**

In `cli/branding.py`, locate the existing color block (lines 10-16, the `GOLD = "\033[..."` constants). Replace the entire color block with:

```python
# ── Design tokens ─────────────────────────────────────────────
# Color palette. Hex values mirror the Rich style strings used by
# the renderer; the ANSI escapes are used by the welcome banner
# which writes raw bytes to stdout (Rich is not in scope there).
GOLD_HEX = "#bfa669"
GOLD_DIM_HEX = "dim #bfa669"
WHITE_HEX = "#ffffff"
GREEN_HEX = "#6fbf73"
RED_HEX = "#bf6f6f"
AMBER_HEX = "#d4a645"
BLUE_HEX = "#8fa4bf"

GOLD = "\033[38;2;191;166;105m"
GOLD_BG = "\033[48;2;191;166;105m"
WHITE = "\033[97m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Glyph alphabet. Selected for legibility in monospace fonts and
# absence of ambiguous-width characters.
MARK = "\u25c6"          # ◆ signature mark (fallback when no inline image)
RULE = "\u2500"          # ─ hairline horizontal rule
RULE_HEAVY = "\u2501"    # ━ heavy horizontal rule
DOT = "\u00b7"           # · inline separator
BAR = "\u2502"           # │ vertical rail
CHECK = "\u2713"         # ✓ success
CROSS = "\u2717"         # ✗ failure
ARROW = "\u203a"         # › prompt continuation, running tool

# Layout
GUTTER = 2               # left indent for non-centered content
```

- [ ] **Step 2: Verify the file still parses**

Run: `.venv/bin/python -c "from cli import branding; print(branding.MARK, branding.DOT, branding.CHECK)"`
Expected: `◆ · ✓`

- [ ] **Step 3: Commit**

```bash
git add cli/branding.py
git commit -m "feat(branding): add design tokens for color, glyphs, and spacing"
```

---

## Task 3: Add Layout Primitives

**Files:**
- Modify: `cli/branding.py` (replace `_term_width()` and `_center()`)

- [ ] **Step 1: Replace `_term_width()` and `_center()` with new primitives**

Locate the existing `_term_width()` function (around line 19) and the `_center()` function (around line 90). Replace BOTH with this block, placing it after the design tokens added in Task 2:

```python
# ── Layout primitives ────────────────────────────────────────
def term_width() -> int:
    """Current terminal width in columns. Defaults to 80 if undetectable."""
    return shutil.get_terminal_size((80, 24)).columns


# Backwards-compat alias (older callers).
_term_width = term_width


def cell_width(text: str) -> int:
    """Visual width of a string in terminal cells.

    Uses wcwidth to handle double-width and ambiguous-width characters
    correctly. Falls back to len() if wcwidth is unavailable. This is
    the fix for "lines half sticking out". len() undercounts box
    drawing characters in some monospace fonts, which throws off any
    centering math that uses it.
    """
    try:
        from wcwidth import wcswidth
    except ImportError:
        return len(text)
    width = wcswidth(text)
    return width if width is not None and width >= 0 else len(text)


def center(text: str, width: int | None = None) -> str:
    """Center plain text against the terminal width (or override).

    Strips ANSI escape sequences before measuring so colored text
    centers correctly. The returned string preserves any escapes
    that were present in the input.
    """
    w = width if width is not None else term_width()
    plain = _strip_ansi(text)
    pad = max(0, (w - cell_width(plain)) // 2)
    return " " * pad + text


# Backwards-compat alias.
_center = center


def hairline(width: int | None = None, heavy: bool = False, dim: bool = True) -> str:
    """Return a horizontal rule spanning the full terminal width.

    The rule is rendered with the gold accent color, optionally dimmed.
    """
    w = width if width is not None else term_width()
    glyph = RULE_HEAVY if heavy else RULE
    style = f"{GOLD}{DIM}" if dim else GOLD
    return f"{style}{glyph * w}{RESET}"


_ANSI_RE = None

def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences for accurate width measurement."""
    global _ANSI_RE
    if _ANSI_RE is None:
        import re
        _ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07")
    return _ANSI_RE.sub("", text)
```

- [ ] **Step 2: Verify primitives work**

Run:

```bash
.venv/bin/python -c "
from cli.branding import term_width, cell_width, center, hairline, MARK, GOLD, RESET
print('term_width:', term_width())
print('cell_width(◆):', cell_width('◆'))
print('cell_width(CROWE LOGIC):', cell_width('CROWE LOGIC'))
print(repr(center('CROWE', 40)))
print(repr(hairline(40))[:80])
print(center(f'{GOLD}CROWE LOGIC{RESET}', 40))
"
```

Expected output:
- `term_width:` followed by a number (80 or larger)
- `cell_width(◆): 1`
- `cell_width(CROWE LOGIC): 11`
- A string starting with about 17 spaces then `'CROWE'`
- A repr starting with `"'\\x1b[38;2;191;166;105m\\x1b[2m\\xe2\\x94\\x80...`
- A line that visually centers `CROWE LOGIC` in 40 columns

- [ ] **Step 3: Commit**

```bash
git add cli/branding.py
git commit -m "feat(branding): add wcwidth-aware layout primitives"
```

---

## Task 4: Refactor `welcome_screen()` to Use the New Layout

**Files:**
- Modify: `cli/branding.py` (replace `welcome_screen()`)

- [ ] **Step 1: Replace the `welcome_screen()` function**

Locate the existing `welcome_screen()` function (around line 105) and replace it entirely with:

```python
def welcome_screen(version: str = "0.1.0", avatar_seq: str = "") -> str:
    """Render the Crowe Logic signature welcome screen.

    Layout (top to bottom):
      - Hairline rule, full terminal width, dim gold
      - Centered mark (inline image avatar OR ◆ glyph)
      - Centered wordmark "C R O W E   L O G I C" in bold gold
      - Centered version + active model line in dim
      - Centered tagline in white
      - Hairline rule
      - Commands hint in dim, indented

    All centering uses the full terminal width via `center()`. The
    rule spans the full terminal width via `hairline()`. There is no
    72-column clamp, no block-relative centering, and no ASCII art.
    """
    w = term_width()
    rule = hairline(w)

    # Wordmark: letter-spaced caps. "C R O W E   L O G I C" reads as
    # typography rather than decoration. Three spaces between words,
    # one between letters.
    wordmark_plain = "C R O W E   L O G I C"
    wordmark = f"{GOLD}{BOLD}{wordmark_plain}{RESET}"

    # Mark: inline image where supported, glyph fallback otherwise.
    # Both modes are visually equivalent: a single anchor point.
    if avatar_seq:
        # The avatar_seq is an iTerm2 inline image escape, which has
        # zero measurable width to len(). We approximate the visual
        # width as 4 cells (matching the width=4 setting in
        # _get_avatar_seq) and pad accordingly.
        mark_pad = max(0, (w - 4) // 2)
        mark_line = " " * mark_pad + avatar_seq
    else:
        mark_line = center(f"{GOLD}{BOLD}{MARK}{RESET}", w)

    # Build version + model line. The active model is read from
    # session_state if set; otherwise the line shows just the version.
    active_model = session_state.get("active_model", "") if session_state else ""
    if active_model:
        version_text = f"v{version}  {DOT}  {active_model}"
    else:
        version_text = f"v{version}"
    version_line = center(f"{DIM}{version_text}{RESET}", w)

    tagline_plain = f"Universal AI Agent  {DOT}  Crowe Logic, Inc."
    tagline_line = center(f"{WHITE}{tagline_plain}{RESET}", w)

    wordmark_line = center(wordmark, w)

    cmd_hint = f"{DIM}Type naturally. The agent selects tools automatically.{RESET}"
    cmd_list = f"{DIM}/tools   /model   /data   /status   /help   /exit{RESET}"
    indent = " " * GUTTER

    return (
        "\n"
        f"{rule}\n"
        "\n"
        "\n"
        f"{mark_line}\n"
        "\n"
        "\n"
        f"{wordmark_line}\n"
        f"{version_line}\n"
        "\n"
        "\n"
        f"{tagline_line}\n"
        "\n"
        "\n"
        f"{rule}\n"
        "\n"
        f"{indent}{cmd_hint}\n"
        "\n"
        f"{indent}{cmd_list}\n"
        "\n"
    )
```

- [ ] **Step 2: Verify the welcome screen renders cleanly at multiple widths**

Run this verification script:

```bash
.venv/bin/python -c "
import os, shutil
from cli import branding

for w in [40, 60, 80, 100, 120, 160]:
    shutil.get_terminal_size = lambda *a, **k: os.terminal_size((w, 30))
    print(f'=== {w} cols ===')
    print(branding.welcome_screen('0.1.0'))
    print()
"
```

Expected: At every width, the wordmark `C R O W E   L O G I C` is visually centered, the rules span the full terminal width, and no content is clipped or off-screen. The tagline and version line are also centered.

- [ ] **Step 3: Commit**

```bash
git add cli/branding.py
git commit -m "feat(branding): redesign welcome screen with signature wordmark"
```

---

## Task 5: Refactor `render_tool_card()` to Use Tokens

**Files:**
- Modify: `cli/branding.py` (replace `render_tool_card()`)

- [ ] **Step 1: Replace `render_tool_card()` with the token-aligned version**

Locate the existing `render_tool_card()` function (around line 278) and replace it entirely with:

```python
def render_tool_card(console, name: str, args: str,
                     status: str = "running",
                     result: str = "", duration_ms: int = 0):
    """Render a tool execution card.

    Three states:
      running: single line with arrow indicator
      ok:      two lines, gold left rail, check mark
      fail:    two lines, red left rail, cross mark

    All glyphs and colors come from the design token module.
    """
    from rich.text import Text

    # Truncate args for display (the full args go to the tool log).
    if args and len(args) > 70:
        args = args[:67] + "..."

    indent = " " * GUTTER

    if status == "running":
        label = Text()
        label.append(f"{indent}{ARROW} ", style=f"dim {GOLD_HEX}")
        label.append(name, style=f"bold {GOLD_HEX}")
        if args:
            label.append(f"  {args}", style="dim")
        console.print(label)
        return

    border_color = GOLD_HEX if status == "ok" else RED_HEX
    check_glyph = CHECK if status == "ok" else CROSS
    check_color = GREEN_HEX if status == "ok" else RED_HEX

    duration_str = f"{duration_ms / 1000:.1f}s" if duration_ms else ""

    summary = summarize_tool_result(name, result) if status == "ok" else ""
    if status == "fail" and result:
        try:
            import json as _json
            err = _json.loads(result)
            summary = err.get("error", result[:60])
        except (ValueError, AttributeError):
            summary = result.strip().split("\n")[0][:60]

    line1 = Text()
    line1.append(f"{indent}{BAR} ", style=border_color)
    line1.append(name, style=f"bold {border_color}")
    if args:
        line1.append(f"  {args}", style="dim")

    line2 = Text()
    line2.append(f"{indent}{BAR} ", style=border_color)
    line2.append(f"{check_glyph} ", style=check_color)
    if summary:
        line2.append(summary, style="dim")
    if duration_str:
        if summary:
            line2.append(f" {DOT} ", style="dim")
        line2.append(duration_str, style="dim")

    console.print(line1)
    console.print(line2)
```

- [ ] **Step 2: Verify the tool card renders**

Run:

```bash
.venv/bin/python -c "
from rich.console import Console
from cli.branding import render_tool_card
c = Console()
render_tool_card(c, 'read_file', 'path=\"cli/branding.py\"', status='running')
render_tool_card(c, 'read_file', 'path=\"cli/branding.py\"', status='ok', result='line1\nline2\nline3', duration_ms=234)
render_tool_card(c, 'execute_shell', 'cmd=\"ls /nonexistent\"', status='fail', result='{\"error\": \"No such file\"}', duration_ms=12)
"
```

Expected: Three rendered cards with consistent left-rail glyphs, the running card showing `›`, the success card showing `│` rail with `✓` in green and a 3-line summary, and the failure card showing `│` rail in red with `✗`.

- [ ] **Step 3: Commit**

```bash
git add cli/branding.py
git commit -m "feat(branding): align tool card with design tokens"
```

---

## Task 6: Add `render_error()` Helper

**Files:**
- Modify: `cli/branding.py` (add new function after `render_tool_card`)

- [ ] **Step 1: Add the `render_error()` function**

Insert this function in `cli/branding.py` immediately after `render_tool_card()`:

```python
def render_error(console, title: str, detail: str | None = None):
    """Render a structured error block.

    Format mirrors a failed tool card: red left rail, cross glyph,
    title in bold red, optional detail lines below.
    """
    from rich.text import Text

    indent = " " * GUTTER

    head = Text()
    head.append(f"{indent}{CROSS} ", style=RED_HEX)
    head.append(title, style=f"bold {RED_HEX}")
    console.print(head)

    if detail:
        for line in detail.strip().splitlines():
            row = Text()
            row.append(f"{indent}{BAR} ", style=RED_HEX)
            row.append(line, style="dim")
            console.print(row)
```

- [ ] **Step 2: Verify the error helper works**

Run:

```bash
.venv/bin/python -c "
from rich.console import Console
from cli.branding import render_error
c = Console()
render_error(c, 'Provider unreachable', 'Connection refused (10061)\nRetry in 5s')
print()
render_error(c, 'Tool failed')
"
```

Expected: A red error block with `✗ Provider unreachable` followed by two `│` detail lines, then a single-line `✗ Tool failed` with no detail lines.

- [ ] **Step 3: Commit**

```bash
git add cli/branding.py
git commit -m "feat(branding): add render_error helper for structured error display"
```

---

## Task 7: Refactor `build_toolbar()` for Consistent Separators

**Files:**
- Modify: `cli/branding.py` (replace `build_toolbar()`)

- [ ] **Step 1: Replace `build_toolbar()` with the cleaned version**

Locate `build_toolbar()` (around line 382) and replace it entirely with:

```python
def build_toolbar():
    """Build the prompt_toolkit bottom toolbar HTML string.

    Format:
      CroweLM v0.1.0      45s · 3 tools · 1247 tok @ 89/s · CroweLM Core · LIVE

    All separators are the DOT token, applied uniformly. Status is
    color-coded: green for LIVE, amber for THROTTLED, red for DOWN.
    """
    from prompt_toolkit.formatted_text import HTML
    from config.agent_config import AGENT_VERSION

    elapsed = _time.monotonic() - session_state["started_at"]
    minutes = int(elapsed) // 60
    seconds = int(elapsed) % 60
    duration = f"{minutes}m {seconds:02d}s" if minutes > 0 else f"{seconds}s"

    tool_count = session_state["tool_count"]
    api_status = session_state["api_status"]

    if api_status == "ok":
        status_html = f'<style fg="{GREEN_HEX}">LIVE</style>'
    elif api_status == "throttled":
        retry = session_state["retry_seconds"]
        retry_str = f" retry {retry}s" if retry > 0 else ""
        status_html = f'<style fg="{AMBER_HEX}">THROTTLED{retry_str}</style>'
    else:
        status_html = f'<style fg="{RED_HEX}">DOWN</style>'

    sep = f' <style fg="gray">{DOT}</style> '

    parts = [
        f'<style fg="{GOLD_HEX}">{duration}</style>',
        f'<style fg="{GOLD_HEX}">{tool_count} tools</style>',
    ]

    tokens = session_state.get("last_tokens", 0)
    tps = session_state.get("last_tps", 0)
    if tokens > 0:
        tps_str = f"{tps:.0f}" if tps >= 10 else f"{tps:.1f}"
        parts.append(f'<style fg="{GOLD_HEX}">{tokens} tok @ {tps_str}/s</style>')

    model_label = session_state.get("active_model", "")
    if model_label:
        parts.append(f'<style fg="{BLUE_HEX}">{model_label}</style>')

    parts.append(status_html)

    left = f'<style fg="{GOLD_HEX}">CroweLM v{AGENT_VERSION}</style>'
    right = sep.join(parts)

    return HTML(f' {left}      {right} ')
```

- [ ] **Step 2: Verify the toolbar renders without errors**

Run:

```bash
.venv/bin/python -c "
from cli.branding import build_toolbar, session_state, reset_session_state
import time
reset_session_state()
session_state['started_at'] = time.monotonic() - 45
session_state['tool_count'] = 3
session_state['last_tokens'] = 1247
session_state['last_tps'] = 89.4
session_state['active_model'] = 'CroweLM Core'
result = build_toolbar()
print(repr(result))
"
```

Expected: A `HTML(...)` repr containing `CroweLM v0.1.0`, `45s`, `3 tools`, `1247 tok @ 89/s`, `CroweLM Core`, `LIVE`, with `·` separators between items.

- [ ] **Step 3: Commit**

```bash
git add cli/branding.py
git commit -m "feat(branding): unify toolbar separators with DOT token"
```

---

## Task 8: Update `cli/renderer.py` Telemetry Footer to Use DOT Token

**Files:**
- Modify: `cli/renderer.py` (telemetry footer block in `finish()`)

- [ ] **Step 1: Replace the telemetry footer construction**

Locate the telemetry footer block in `cli/renderer.py::StreamRenderer.finish()` (around lines 270-276):

```python
        footer = Text()
        footer.append("  ", style="dim")
        for i, part in enumerate(parts):
            if i > 0:
                footer.append(" \u00b7 ", style="dim")
            footer.append(part, style=DIM_GOLD)
        self.console.print(footer)
```

Replace with:

```python
        from cli.branding import DOT, GUTTER
        footer = Text()
        footer.append(" " * GUTTER, style="dim")
        for i, part in enumerate(parts):
            if i > 0:
                footer.append(f" {DOT} ", style="dim")
            footer.append(part, style=DIM_GOLD)
        self.console.print(footer)
```

- [ ] **Step 2: Verify renderer still imports cleanly**

Run: `.venv/bin/python -c "from cli.renderer import StreamRenderer; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add cli/renderer.py
git commit -m "feat(renderer): align telemetry footer with DOT token"
```

---

## Task 9: End-to-End Verification of Welcome Screen

**Files:**
- Create: `docs/screenshots/cli-design-v1/.gitkeep`

- [ ] **Step 1: Create the screenshot directory placeholder**

```bash
mkdir -p docs/screenshots/cli-design-v1
touch docs/screenshots/cli-design-v1/.gitkeep
```

- [ ] **Step 2: Capture the welcome screen at narrow, default, and wide widths**

Run this and copy each rendered output into the chat for visual review:

```bash
for width in 60 80 100 120 160; do
  COLUMNS=$width .venv/bin/python -c "
import os, shutil
shutil.get_terminal_size = lambda *a, **k: os.terminal_size(($width, 30))
from cli import branding
print(branding.welcome_screen('0.1.0'))
"
done
```

Expected: At every width, the wordmark and tagline are visually centered, the hairline rules span the full terminal width, and no content is clipped.

- [ ] **Step 3: Verify the active model is reflected when set**

Run:

```bash
COLUMNS=100 .venv/bin/python -c "
import os, shutil
shutil.get_terminal_size = lambda *a, **k: os.terminal_size((100, 30))
from cli import branding
branding.session_state['active_model'] = 'CroweLM Core'
print(branding.welcome_screen('0.1.0'))
"
```

Expected: The version line shows `v0.1.0  ·  CroweLM Core` centered.

- [ ] **Step 4: Commit the placeholder**

```bash
git add docs/screenshots/cli-design-v1/.gitkeep
git commit -m "docs: add screenshot directory for CLI design v1"
```

---

## Task 10: End-to-End Smoke Test of Live CLI Surfaces

**Files:** None modified.

- [ ] **Step 1: Run the deploy health check**

Run: `.venv/bin/crowe-logic deploy 2>&1 | tail -40`
Expected: A model status table with at least 19 LIVE entries, ending with `READY -- Run: crowe-logic chat`. No tracebacks, no rendering glitches.

- [ ] **Step 2: Run a one-shot chat probe to exercise streaming and the tool card**

Run:

```bash
.venv/bin/python -c "
import sys
sys.argv = ['crowe-logic', 'run', 'List the files in the current directory using the list_directory tool, then say done.']
from cli.crowe_logic import main
try:
    main()
except SystemExit:
    pass
" 2>&1 | tail -60
```

Expected: A streamed response containing the directory listing rendered through the new tool card format, followed by the telemetry footer with `·` separators.

If the `run` subcommand is not registered, fall back to a direct provider test:

```bash
.venv/bin/python -c "
from cli.crowe_logic import _get_azure_openai_provider, _select_model_chain
from cli.branding import session_state, reset_session_state
from rich.console import Console

reset_session_state()
console = Console()
chain = _select_model_chain()
print('First chain entry:', chain[0]['label'])
"
```

Expected: Prints the first chain entry's label without errors.

- [ ] **Step 3: Verify there are no regressions in slash command parsing**

Run:

```bash
.venv/bin/python -c "
# Simulate the /model parser
parts = '/model <2>'.split(maxsplit=1)
target = parts[1].strip().strip('<>').strip(\"'\\\"\").strip()
assert target == '2', f'Expected 2, got {target!r}'

parts = '/model <CroweLM Kernel>'.split(maxsplit=1)
target = parts[1].strip().strip('<>').strip(\"'\\\"\").strip()
assert target == 'CroweLM Kernel', f'Expected CroweLM Kernel, got {target!r}'

print('OK')
"
```

Expected: `OK`

---

## Task 11: Final Push

- [ ] **Step 1: Confirm working tree is clean**

Run: `git status`
Expected: `nothing to commit, working tree clean`.

- [ ] **Step 2: View the commit log for this plan**

Run: `git log --oneline -15`
Expected: Commits for tasks 1-10 visible in reverse chronological order.

- [ ] **Step 3: Push to origin**

Run: `git push origin main`
Expected: Successful push, no rejections.

---

## Self-Review Notes

- **Spec coverage:** Every component listed in the spec (welcome, tool card, streaming, reasoning, toolbar, slash help, error display, spinners) has either a task or an explicit "no change" note. Spinner labels and reasoning panel are explicitly unchanged in the spec, so no tasks for them. The slash command consolidation (`_show_status`, `/help` table) is deferred (see "Deferred to v1.1" below).
- **Placeholder scan:** No TBD or TODO. Each step has concrete code or commands.
- **Type consistency:** `GOLD_HEX`, `GREEN_HEX`, `RED_HEX`, `AMBER_HEX`, `BLUE_HEX`, `WHITE_HEX` introduced in Task 2 and used consistently in Tasks 5, 6, 7. Glyph constants `MARK`, `RULE`, `DOT`, `BAR`, `CHECK`, `CROSS`, `ARROW` introduced in Task 2 and used consistently downstream.

## Deferred to v1.1

The spec lists rebuilding `/help` and `/status` as a unified table renderer. This is deferred for v1.1 because it requires touching `cli/crowe_logic.py` slash command handlers, which are large and currently work correctly. The v1 design system is complete without it: every visual primitive is in place, and the v1.1 work becomes a small follow-up that swaps existing handlers to use `branding.center()`, `branding.hairline()`, and the token constants. Doing it in v1 would double the surface area of this plan without changing the visible quality of the most-used surfaces (welcome, tool cards, streaming, toolbar).
