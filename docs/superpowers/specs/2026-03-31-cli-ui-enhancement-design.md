# Crowe Logic CLI UI Enhancement — Design Spec

**Date:** 2026-03-31
**Status:** Approved
**Scope:** Terminal UI enhancements for `cli/crowe_logic.py` and `cli/branding.py`

## Design Philosophy

Refined terminal — clean structure with progressive disclosure. Gold branding (#bfa669) stays dominant. Information available but not overwhelming. No new dependencies; Rich and prompt_toolkit cover all requirements.

## 1. Streaming Markdown — Live Rerender

### Problem
`stream_response()` currently uses `sys.stdout.write()` for streaming text, exposing raw markdown syntax (`**`, `##`, `` ``` ``, `---`) to the user. Markdown only renders post-tool via `console.print(Markdown(...))`.

### Solution
Replace raw stdout streaming with Rich `Live` + `Markdown` re-rendering.

### Behavior
- Accumulate text chunks into a buffer string during the `MessageDeltaChunk` stream
- On each chunk, re-render the full buffer through `rich.markdown.Markdown` inside a `rich.live.Live` context
- `Live` updates in place — no terminal scrollback pollution
- When streaming completes, finalize the `Live` display (one clean rendered block)
- Headers render as gold bold, bold renders as white bold, code blocks get syntax highlighting
- Horizontal rules (`---`) render as gold thin separators
- The user never sees raw markdown syntax

### Technical Details
- `Live(Markdown(buffer), console=console, refresh_per_second=12)` with `live.update(Markdown(buffer))` on each chunk
- The existing Phase 1 spinner ("thinking...") continues to use `Live` separately — stop spinner before starting markdown `Live`
- Phase 3 (post-tool fetch) already uses `console.print(Markdown(...))` — no change needed there
- Set `Live(..., vertical_overflow="visible")` so long responses scroll naturally

## 2. Tool Execution Cards — Hybrid Pattern

### Problem
Current tool display is a single dim line: `> tool_name args`. No duration, no result preview, no success/fail indicator.

### Solution
Hybrid cards — compact single-line with spinner during execution, expand to two-line bordered card on completion.

### During Execution
```
  > tool_name args ⣿ running...
```
Gold spinner, single line. Same visual weight as current.

### On Completion (Success)
```
  ┃ tool_name args
  ┃ ✓ result_summary · 1.2s
```
Gold left-border (`┃`), green checkmark, contextual result summary, duration.

### On Completion (Failure)
```
  ┃ tool_name args
  ┃ ✗ error_message · 0.3s
```
Red left-border, red X, truncated error message, duration.

### Result Summaries
Contextual per tool type:
- `web_search` → "N results"
- `read_file` → "N lines"
- `write_file` → "N bytes written"
- `edit_file` → "N replacements"
- `execute_shell` → "exit 0" or "exit N"
- `git_commit` → "committed abc123"
- `browse_url` → "loaded (N chars)"
- `list_directory` → "N items"
- Default → "done" or first 60 chars of output

### Implementation
- New function `_render_tool_card_hybrid()` in `branding.py`
- Accepts: tool name, args, status (running/ok/fail), result string, duration_ms
- Uses `Rich.Text` with styled segments, not `Panel` (too heavy)
- Track timing with `time.monotonic()` per tool call (already partially done)
- Parse result strings to generate contextual summaries via `_summarize_tool_result(tool_name, result)` helper

## 3. Status Bar — Sticky Bottom Toolbar

### Problem
No persistent session context visible. Rate limit status invisible between events.

### Solution
`prompt_toolkit` `bottom_toolbar` on the `PromptSession` — always visible during input.

### Layout
```
  crowe-logic v0.1.0                    12m 34s · 7 tools · API OK
```
Left-aligned: brand + version. Right-aligned: session duration + tool count + API status.

### Rate Limit States
| State | Color | Label |
|-------|-------|-------|
| Normal | Green (#6fbf73) | `API OK` |
| Throttled | Amber (#d4a645) | `THROTTLED retry Ns` |
| Failed | Red (#bf6f6f) | `API DOWN` |

### Implementation
- Create `_build_toolbar()` function in `branding.py` that returns `prompt_toolkit.formatted_text.HTML`
- Session state tracked in a simple dict: `{"started_at": float, "tool_count": int, "api_status": str, "retry_seconds": int}`
- Pass `bottom_toolbar=_build_toolbar` to `PromptSession()` — prompt_toolkit calls it on each render
- Update `api_status` from within `stream_response()` based on error detection
- Duration computed from `time.monotonic() - started_at` on each toolbar render
- Tool count incremented in the tool execution loop

### State Management
The toolbar state dict is module-level (shared between `chat()` and `stream_response()`):
```python
_session_state = {
    "started_at": 0.0,
    "tool_count": 0,
    "api_status": "ok",       # ok | throttled | down
    "retry_seconds": 0,
}
```
Reset on session start. Updated during streaming.

## 4. Rate Limit UX — Countdown Progress Bar

### Problem
Current retry logic shows `Server error — retrying in Ns (attempt N/3)...` as dim text. No visual feedback during the wait. No distinction between rate limits and server errors.

### Solution
Rich progress bar with countdown during retry backoff. Bottom toolbar turns amber simultaneously.

### Behavior
1. On 429 or "server_error" detection:
   - Set `_session_state["api_status"] = "throttled"`
   - Parse `Retry-After` header if available, else use exponential backoff: `(attempt + 1) * 2`
   - Display: `Rate limited — retry 2/3 in 4s` label above a `rich.progress_bar.ProgressBar`
   - Progress bar fills from 0% to 100% over the wait duration
   - Bottom toolbar simultaneously shows amber `THROTTLED retry 4s`

2. On successful retry:
   - Progress bar disappears (transient `Live`)
   - `_session_state["api_status"] = "ok"`
   - Toolbar returns to green `API OK`

3. On exhausted retries (3 attempts failed):
   - `_session_state["api_status"] = "down"`
   - Error panel rendered via existing `_render_error()`
   - Toolbar shows red `API DOWN` until next successful request

### 429 Detection
Current code checks for `"server_error"` in the error message string. Enhance to also check:
- HTTP status 429 directly from Azure SDK exceptions
- `"rate limit"` or `"throttl"` substrings in error messages
- `"Too many requests"` pattern

### Implementation
- New function `_show_retry_countdown(wait_seconds, attempt, max_attempts)` in `branding.py`
- Uses `Rich.Live` with a custom renderable combining label + progress bar
- Updates every 0.5s to show countdown and fill bar
- Returns after wait completes

## 5. Input Upgrades — Smart Single-Line + Escape Hatch

### Problem
Single-line `prompt_toolkit` input with no auto-completion. No multi-line support for complex prompts.

### Solution
Tab-completion for `/` commands. `Ctrl+E` opens multi-line editor.

### Slash Command Completion
- `prompt_toolkit.completion.WordCompleter` with meta text (descriptions)
- Commands: `/tools`, `/status`, `/clear`, `/help`, `/exit`, `/quit`
- Tab or start typing `/` to see popup
- Descriptions appear next to each command in the completion menu

### Multi-Line Editor
- `Ctrl+E` toggles multi-line mode via `prompt_toolkit` key binding
- Multi-line prompt shows header: `MULTI-LINE (Ctrl+D to send, Esc to cancel)`
- `Ctrl+D` submits the text
- `Esc` cancels and returns to single-line
- Continuation lines use `·` prefix for visual distinction

### Implementation
- Create `SlashCompleter` class extending `prompt_toolkit.completion.Completer`
- Yields `Completion` objects with `display_meta` for descriptions
- Register `Ctrl+E` key binding on the `PromptSession` via `prompt_toolkit.key_binding.KeyBindings`
- Multi-line uses `prompt_toolkit.PromptSession(multiline=True)` in a separate prompt call

## File Changes

### `cli/branding.py` — New Functions
- `_build_toolbar(session_state) -> HTML` — bottom toolbar renderer
- `_render_tool_card_hybrid(name, args, status, result, duration_ms)` — hybrid tool card
- `_summarize_tool_result(tool_name, result) -> str` — contextual result summary
- `_show_retry_countdown(wait_seconds, attempt, max_attempts)` — progress bar countdown
- `SlashCompleter(Completer)` — `/` command tab-completion

### `cli/crowe_logic.py` — Modifications
- `stream_response()` — replace `sys.stdout.write` with `Rich.Live` + `Markdown` re-rendering
- `stream_response()` — integrate hybrid tool cards with timing
- `stream_response()` — add 429 detection and countdown bar
- `chat()` — add `bottom_toolbar`, key bindings, `SlashCompleter` to `PromptSession`
- `chat()` — initialize and manage `_session_state`
- `resume()` — same toolbar/input changes as `chat()`
- Module-level `_session_state` dict

### No New Files
All changes are modifications to existing files. No new dependencies.

## Testing Strategy
- Manual testing in iTerm2 (primary terminal)
- Verify markdown rendering with headers, bold, code blocks, lists, tables
- Verify tool cards show timing and result summaries
- Verify status bar updates during rate limiting
- Verify countdown bar renders and completes
- Verify `/` completion popup appears
- Verify `Ctrl+E` multi-line toggle works
- Verify `resume` command inherits all UI upgrades
