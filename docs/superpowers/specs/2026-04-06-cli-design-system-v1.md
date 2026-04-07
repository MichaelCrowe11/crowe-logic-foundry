# Crowe Logic CLI Design System v1

**Date:** 2026-04-06
**Owner:** Crowe Logic, Inc.
**Scope:** Visual identity and rendering system for the `crowe-logic` CLI agent.

## Problem

The current CLI welcome screen and output surfaces have several specific defects:

1. **Header alignment is broken at any terminal width above ~72 columns.** The welcome banner clamps content width via `tw = min(_term_width(), 72)`, then centers ASCII inside the clamped block. The result is a stub-width header floating left-anchored in dead space on wide terminals, with horizontal rules that visually disconnect from the wordmark below.
2. **No coherent design language across surfaces.** Tool cards, telemetry footers, the toolbar, the welcome screen, and slash command output were each built ad-hoc. They share a gold accent color but no other tokens (typography, spacing, separators, layout grid).
3. **No signature mark.** Docker has the whale, Claude has the wordmark, Codex has the block, Vercel has the triangle. Crowe Logic has a heavy six-line ASCII wordmark that does not scale to narrow terminals and is not memorable.
4. **The 12-line double-stroke ASCII banner consumes half a screen** for content that conveys only the product name.

## Goals

A comprehensive visual identity for the Crowe Logic CLI that is:

- **Centered correctly at every terminal width** (40 cols to 200+ cols), with no clipping, no left-anchored content, no rules that disconnect from content.
- **Signature-able.** Recognizable in a screenshot. A single mark plus a wordmark that reads like a brand, not a banner.
- **Coherent across every surface** (welcome, streaming, tool cards, telemetry, toolbar, help, errors, prompts) via shared design tokens.
- **Premium feel.** Reads like a boutique studio product, not a 1990s BBS.
- **Backwards-compatible with existing terminal capabilities.** iTerm2 / WezTerm / Ghostty get the inline image avatar; everything else gets a Unicode glyph fallback that is itself attractive.

## Non-Goals

- Replacing prompt_toolkit or Rich.
- Changing slash command syntax or the underlying renderer architecture (segmented streaming, reasoning panels).
- Adding new product features. This is purely a presentation pass.

## Design Tokens

A single source of truth for all rendering. Lives in `cli/branding.py` as module-level constants.

### Color

| Token | Value | Usage |
|---|---|---|
| `GOLD` | `#bfa669` | Primary accent. Wordmark, marks, tool card borders, key labels. |
| `GOLD_DIM` | `dim #bfa669` | Hairline rules, secondary text, separators. |
| `WHITE` | `#ffffff` | Tagline, primary content. |
| `GREEN` | `#6fbf73` | Success states, LIVE status, completed tools. |
| `RED` | `#bf6f6f` | Error states, DOWN status, failed tools. |
| `AMBER` | `#d4a645` | Warning states, THROTTLED status, retries. |
| `BLUE` | `#8fa4bf` | Informational, model labels. |
| `MUTE` | `dim` | De-emphasized content (commands hint, telemetry units). |

### Glyphs

A small alphabet of typographic symbols used throughout. Selected for legibility in monospace fonts and the absence of ambiguous-width characters.

| Token | Glyph | Usage |
|---|---|---|
| `MARK` | `◆` | Signature mark (fallback when inline image unavailable). |
| `RULE` | `─` | Hairline horizontal rule. |
| `RULE_HEAVY` | `━` | Heavy horizontal rule (reserved for emphasis, used sparingly). |
| `DOT` | `·` | Inline separator between items. |
| `BAR` | `│` | Vertical rail (left edge of tool cards). |
| `CHECK` | `✓` | Success indicator. |
| `CROSS` | `✗` | Failure indicator. |
| `ARROW` | `›` | Prompt continuation, running tool indicator. |

No emojis. No em dashes. The codebase already enforces this; the design system codifies it.

### Typography

The CLI is monospace, so typography is letter-spacing and capitalization, not font choice.

| Style | Treatment | Usage |
|---|---|---|
| `WORDMARK` | `C R O W E   L O G I C` (single space between letters, three between words) | Welcome screen brand display. |
| `LABEL` | `bold` uppercase | Section headings (`MODELS`, `TOOLS`, `STATUS`). |
| `BODY` | regular case | Tagline, help text, prose output. |
| `META` | `dim` regular | Telemetry units, command hints, version strings. |

### Spacing

Vertical rhythm uses a 1-line unit. Horizontal rhythm uses 2-space gutters.

- **Section gap:** 2 blank lines between major welcome sections (mark, wordmark, tagline, commands).
- **Content gap:** 1 blank line between subsections.
- **Indent:** 2 spaces for content, 4 spaces for nested content.
- **Card padding:** 1 char left of vertical rail.

### Layout

A single layout primitive: **center against the full terminal width**. No content clamp. Rules span the full terminal width.

```python
def center(text: str, width: int | None = None) -> str:
    """Center plain text against the terminal width (or override)."""
    w = width if width is not None else term_width()
    pad = max(0, (w - cell_width(text)) // 2)
    return " " * pad + text
```

`cell_width()` accounts for ambiguous-width and double-width characters via `wcwidth`. This is the fix for the "lines half sticking out" defect: `len()` undercounts box-drawing characters in some fonts.

## Components

### 1. Welcome Screen

The signature surface. First impression of the product.

**Anatomy (top to bottom):**

```
[blank line]
[hairline rule, full terminal width, dim gold]
[blank line]
[blank line]
[centered mark: inline avatar OR ◆ glyph in gold]
[blank line]
[blank line]
[centered wordmark: "C R O W E   L O G I C" in bold gold]
[centered version+build line: "v0.1.0 · CroweLM Core" in dim]
[blank line]
[blank line]
[centered tagline: "Universal AI Agent · Crowe Logic, Inc." in white]
[blank line]
[blank line]
[hairline rule, full terminal width, dim gold]
[blank line]
[2-space-indent commands hint in dim]
[blank line]
[2-space-indent slash command list in dim]
[blank line]
```

The mark is the **signature**. In iTerm2 / WezTerm / Ghostty (detected via `TERM_PROGRAM`), it is the `icon.png` rendered as an inline image at `width=4` cells, centered. Elsewhere, it is the `◆` glyph rendered in gold at the same horizontal center. The mark is consistent in both modes: a single visual anchor where the eye lands first.

The wordmark uses **letter-spaced caps** rather than ASCII art. This reads as typography, not as decoration, and works at any terminal width down to ~30 columns.

### 2. Tool Card

The repeated unit during agent execution. Already in good shape; needs token alignment and a tighter footprint.

**Running state (single line):**

```
  › <tool_name>  <args preview>
```

**Completed state (two lines, gold border for success, red for failure):**

```
  │ <tool_name>  <args preview>
  │ ✓ <summary> · <duration>
```

Changes from current: use `›` (not `>`) for the running indicator (matches the prompt arrow). Use the new `DOT` token consistently. Remove the trailing line that the current implementation prints; rely on the next renderable to break the visual flow.

### 3. Streaming Output

Already cleanly handled by `cli/renderer.py`. No structural changes. Two adjustments:

- **Header:** the inline avatar plus `[bold gold]<model_label>[/]` line is kept as-is (this is the per-response signature mark, mirrors the welcome mark).
- **Telemetry footer:** uses the new `DOT` token instead of inline `\u00b7`, and a consistent style. Format: `<tokens> tokens · <tps> tok/s · TTFT <ms> · total <ms>`.

### 4. Reasoning Panel

Already in good shape (`renderer._build_reasoning_panel`). One tweak: use `─` rounded box instead of the current ROUNDED box (which is the same), and ensure the panel `expand=False` is centered when standalone.

### 5. Toolbar

The bottom toolbar built by `branding.build_toolbar()`. Currently functional but visually noisy with redundant separators. Tighten:

```
 CroweLM v0.1.0      45s · 3 tools · 1247 tok @ 89/s · CroweLM Core · LIVE
```

Same content, more consistent dot separators, no double-separator clusters.

### 6. Slash Command Help (`/help`, `/tools`, `/status`, `/model`)

A unified table renderer using Rich. All four commands produce a centered table with:

- Title row (gold, uppercase label)
- Columns aligned consistently
- Dim hint row at the bottom

`/model` already does most of this; `/help` and `/status` need rebuilding to match.

### 7. Error Display

Errors during a chat turn currently render via Rich exceptions or raw print. Standardize on a single helper:

```python
def render_error(console, title: str, detail: str | None = None):
    """Render a structured error block."""
```

Output:

```
  ✗ <title>
  │ <detail line 1>
  │ <detail line 2>
```

Red border, consistent with failed tool cards.

### 8. Spinner Labels

Already consistent. No changes.

## Architecture

### File-level changes

| File | Change | Reason |
|---|---|---|
| `cli/branding.py` | Add design token module-level constants. Refactor `welcome_screen()`. Refactor `render_tool_card()` to use tokens. Add `render_error()`, `center()` with wcwidth, `hairline()` helper. | Single source of truth for design system. |
| `cli/renderer.py` | Update telemetry footer format to use `DOT` token. Center reasoning panel. | Token alignment. |
| `cli/crowe_logic.py` | Update `_show_models()`, `_show_status()`, the `/help` handler to use the new helpers. Remove ad-hoc styling. | Consistency across slash commands. |
| `cli/icon.png` | No change. | Existing asset works. |

### Module structure of `cli/branding.py` after refactor

```
constants:
  GOLD, GOLD_DIM, WHITE, GREEN, RED, AMBER, BLUE, MUTE         # color tokens
  MARK, RULE, RULE_HEAVY, DOT, BAR, CHECK, CROSS, ARROW        # glyph tokens
  GUTTER = 2                                                    # horizontal indent

primitives:
  term_width() -> int
  cell_width(s: str) -> int                                     # via wcwidth
  center(s: str, width: int | None = None) -> str
  hairline(width: int | None = None, heavy: bool = False) -> str

iTerm2 helpers (unchanged):
  _is_iterm_compatible()
  _inline_image_seq()
  _prepare_avatar()
  get_favicon()
  _get_avatar_seq()

components:
  welcome_screen(version: str, avatar_seq: str = "") -> str
  show_welcome(version: str)
  render_tool_card(console, name, args, status, result, duration_ms)
  render_error(console, title, detail=None)
  build_toolbar() -> HTML
  summarize_tool_result(tool_name, result) -> str               # unchanged

prompt_toolkit helpers (unchanged):
  SlashCompleter
  create_chat_keybindings()

session state (unchanged):
  session_state, reset_session_state()
```

### Dependency

Add `wcwidth` to `requirements.txt`. It is a lightweight pure-Python package (~30KB) and is the standard solution for terminal cell-width calculation. Falls back gracefully if absent (use `len()`).

## Data Flow

No changes to data flow. The design system is purely a presentation layer.

## Error Handling

The new `render_error()` helper is the only behavioral addition. Existing error paths (provider exceptions, tool failures, rate limit retries) continue to use their current code paths but route their final display through `render_error()` for visual consistency.

## Testing

No automated visual regression tests (terminal rendering is hard to snapshot reliably). The verification protocol:

1. **Render at 40, 60, 80, 100, 120, 160, 200 columns** via `COLUMNS=N python -c "from cli import branding; print(branding.welcome_screen(...))"`. Each width must produce centered output with no clipping and no off-screen content.
2. **Render in iTerm2 with the inline image enabled** and confirm the avatar replaces the diamond fallback.
3. **Render under `TERM_PROGRAM=Apple_Terminal`** and confirm the diamond fallback appears.
4. **Run `crowe-logic chat`** and exercise: tool execution, streaming response, reasoning panel, slash commands (`/model`, `/help`, `/status`, `/tools`), error display.
5. **Run `crowe-logic deploy`** and confirm the model status table still renders correctly.

Each step produces a screenshot or terminal capture committed to `docs/screenshots/cli-design-v1/`.

## Migration / Rollout

No migration. The current welcome screen is replaced in a single commit. There is no user data to preserve and no API to deprecate.

## Open Questions

None. All questions resolved during brainstorming:

- Direction: Option B (refined wordmark + signature mark) confirmed.
- Avatar: existing `cli/icon.png` reused as the inline mark on supported terminals.
- Fallback glyph: `◆` (filled diamond) chosen for visibility and absence of ambiguous-width.
- Wordmark style: letter-spaced caps `C R O W E   L O G I C`.
- Color palette: keep existing gold `#bfa669` as the brand anchor.

## Rollback

If the redesign degrades any surface in production use, revert via `git revert <commit>`. The change is presentation-only and has no persistent state. Rollback is one command and one minute of downtime (which itself is just "users see the old welcome screen").

## Success Criteria

1. `crowe-logic chat` welcome screen renders centered at every terminal width from 40 to 200 columns with no clipping.
2. The wordmark is recognizable in a screenshot. A viewer who has never seen the CLI can identify "Crowe Logic" as the product name within one second.
3. All five surfaces (welcome, tool cards, streaming, toolbar, slash commands) share consistent color, spacing, and glyph tokens.
4. No regressions in the tool-calling loop, model deploy check, or chat session.
5. The full deploy health check (`crowe-logic deploy`) still produces the expected status table.
