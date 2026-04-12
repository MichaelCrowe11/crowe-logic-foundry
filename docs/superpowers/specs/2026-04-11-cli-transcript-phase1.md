# CroweLM CLI Transcript Phase 1

**Date:** 2026-04-11  
**Scope:** Concrete terminal UI spec for the CroweLM transcript system, with phase-1 implementation focused on answer, reasoning, action, and error rendering.

## Goal

The CLI should read like an instrument panel, not a stream of terminal primitives.

Phase 1 upgrades the transcript itself:

- clamp prose to a readable authored width
- separate answer, reasoning, action, and error into distinct block types
- keep tool execution visible without flooding the screen
- make streaming feel stable instead of jumpy

This phase does not redesign the whole shell. The welcome banner, toolbar, and command loop remain intact. The change is the language of the transcript.

## Layout Model

The CLI is treated as three stacked zones:

1. Top shell
   Current welcome, model roster, slash commands, and session banner.
2. Transcript
   The primary conversation surface. This is the focus of phase 1.
3. Composer/footer
   Prompt input, toolbar telemetry, retry notices, and shell state.

Phase 1 only changes zone 2, but the blocks are designed so later HUD and composer work can align to them.

## Transcript Primitives

The transcript uses four first-class block types.

### Answer

- Render inside a rounded panel.
- Clamp width to roughly 88-96 columns regardless of terminal width.
- Use Markdown formatting for headings, lists, and code.
- Stream live into the same block, then finalize in place when the segment closes.
- Panel title format:
  - `ANSWER · streaming`
  - `ANSWER · final`

### Reasoning

- Render in a muted panel distinct from the answer.
- Reasoning must remain visible but visually subordinate.
- Stream live before content starts, then finalize in place.
- If reasoning arrives after content, flush it as its own captured block at the segment boundary.
- Panel title format:
  - `REASONING · live`
  - `REASONING · captured`

### Action

- Action cards replace ad hoc spinner text plus loose follow-up summaries.
- Running state remains a compact single-line rail.
- Completed or failed state becomes a rounded panel with:
  - tool name
  - compact args preview
  - outcome summary
  - duration when available
- Panel title format:
  - `ACTION · ok`
  - `ACTION · failed`

### Error

- Errors render as deliberate incident blocks, not raw exceptions dumped into the flow.
- The title is the incident label.
- Detail lines are stacked beneath it.
- The renderer must preserve literal text content; it should not visibly leak Rich escaping.
- Panel title format:
  - `ERROR`

## Width Rules

- Transcript blocks should respect a left gutter.
- Transcript width should be capped with a default max of 96 columns.
- Wide terminals must not create unreadably long prose lines.
- Code, logs, and tables can be addressed in later phases with wider or adaptive rendering, but phase 1 prioritizes readable prose.

## Streaming Rules

- The answer block should update in place while streaming.
- Once a segment is committed, it should stop reflowing.
- Tool execution starts a new segment boundary.
- Segment boundaries should preserve prior blocks in scrollback.
- The same transcript language must apply to both:
  - the shared OpenAI-compatible provider path
  - the legacy Azure Agents path

## Action Preview Rules

- Tool args shown in the transcript are previews, not raw payload dumps.
- JSON args should collapse into short `key=value` snippets.
- Show at most the first three keys, then summarize remaining keys as `+N more`.
- Long string values should be truncated.
- Raw payloads remain available internally for logs, orchestrator recording, and future expanded views.

## Phase 1 Implementation Map

- `cli/branding.py`
  - transcript width helper
  - answer panel builder
  - reasoning panel builder
  - compact tool-args preview
  - upgraded action and error cards
- `cli/renderer.py`
  - shared streaming renderer adopts the new answer and reasoning panels
- `cli/crowe_logic.py`
  - legacy Azure Agents transcript path uses the shared `StreamRenderer`
  - final fetched Azure answer uses the same answer block renderer

## Out of Scope for Phase 1

- sticky top HUD
- command palette
- transcript rail / session navigator
- block expansion or collapse affordances
- syntax-highlighted code panels with line numbers
- mode switching such as Zen, Ops, Review, Debug
- provider-health badges inside the transcript

## Phase 2 Candidates

- sticky HUD with model, branch, cwd, sandbox, and API health
- richer action timeline with queued/running/done states
- dedicated code/log/table blocks
- command palette and model quick switcher
- incident-style approval prompts for risky actions
- transcript display modes

## Acceptance Criteria

- A normal streamed answer renders in a width-clamped `ANSWER` panel.
- A reasoning-capable model renders muted `REASONING` panels without mixing them into answer prose.
- Successful and failed tool calls render as authored `ACTION` cards.
- Error blocks show literal details cleanly, including bracketed text.
- The Azure Agents path and the primary provider path share the same transcript language.
