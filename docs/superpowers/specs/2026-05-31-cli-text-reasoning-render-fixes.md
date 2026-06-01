# crowe-logic CLI — Text & Reasoning Render Fixes (Phase 1)

**Status:** approved 2026-05-31. Scope: bugs only, clean. No visual redesign, no route-signal plumbing (those are Phase 2).

## Problem

Live `crowe-logic` sessions show three rendering defects in the answer/reasoning output:

1. **Panels collapse to ~25 columns** while the banner and SESSION HUD render full-width — answer/reasoning boxes wrap mid-word.
2. **Double rendering** — REASONING appears as a `live` box then a `captured` box; ANSWER as a `streaming` box then a `final` box. Transient Live widgets leave their intermediate frames in scrollback instead of being replaced by a single final panel.
3. **Reasoning shown in full even for trivial turns** — e.g. 96 reasoning tokens dumped in a giant box for a one-paragraph answer.

## Root causes (confirmed in code)

- **Shared root for #1 and #2:** `cli/crowe_logic.py:67` constructs the Rich console as bare `console = Console()`. Rich auto-detects width and interactivity. In the user's terminal this resolves to ~80 cols (→ `branding.transcript_width` subtracts `GUTTER*2+2` → ~25-col panels) AND mis-detects `is_terminal`, so the `transient=True` Live widgets (`renderer.py` `begin_stream`/`feed_reasoning`) never erase themselves — both the live frame and the final `console.print` survive = double boxes. The design pattern (transient Live → one final print in `_stop_md_live` / `_stop_reasoning_live`) is correct; it just depends on a correctly-configured console.
- **#3:** `cli/renderer.py:43` gates reasoning compaction with `_COMPACT_REASONING_LABELS = {"CroweLM Apex", "CroweLM Titan"}` — both are **dead tier names** that no longer route (real routes are Helio, Helio Pro, Cinder, Hyphae, …). The allowlist never matches, so `_compact_reasoning` is always False and full traces always render.

## Fixes

### Fix 1 — Force real console width
At `cli/crowe_logic.py:67`, construct the console with an explicit width derived from the real terminal size (reuse `shutil.get_terminal_size`, already used by `branding.term_width()`), with a sane floor (e.g. 80). The renderer and `transcript_width` then size panels to the true width.

### Fix 2 — Make transient Live cleanup reliable
Configure the console so `transient=True` erase works (`force_terminal=True`), OR make the live widgets conditional on `console.is_terminal` (skip the live phase and print the final panel once when not a real TTY). The implementer MUST verify which path actually eliminates the doubling in the real terminal before committing — do not assume. Whichever is chosen, the invariant is: **exactly one answer panel and at most one reasoning panel per segment.**

### Fix 3 — Compact reasoning by default
Invert the gate in `cli/renderer.py`: compact reasoning for all models by default, with a small explicit opt-out set (or an env/flag), instead of an opt-in allowlist of names that no longer exist. Replace the dead `_COMPACT_REASONING_LABELS` allowlist accordingly. Keep `_COMPACT_REASONING_MAX_CHARS` / `_COMPACT_REASONING_LIVE_CHARS` as the truncation limits. (Phase 2 will make this route-aware via the Synapse class; Phase 1 only stops the floods.)

## Testing

`StreamRenderer` takes a `console`, so tests construct `Console(width=120, force_terminal=True, file=StringIO())` and assert:
- **Width:** a rendered answer panel's visible width tracks the console width (not a hardcoded ~25/80).
- **No double render:** after a simulated stream + `finish()`, the captured output contains exactly one final answer block (and ≤1 reasoning block) — not both a streaming and a final copy.
- **Compaction:** a long reasoning trace is truncated to the compact limit for a default-model turn.

No live terminal required.

## Scope guard

- No new look (boxes stay boxes, just correct).
- No Synapse route-class plumbing into the renderer (Phase 2).
- Files touched: `cli/crowe_logic.py` (console construction), `cli/renderer.py` (compaction gate + any is_terminal conditioning), `tests/` (new render tests). `cli/branding.py` only if `transcript_width` needs a floor adjustment.

## Phase 2 (not in scope, recorded for continuity)

Adaptive reasoning disclosure driven by the Synapse route signal: trivial/high-confidence turns show no reasoning chrome; domain/ambiguous/low-confidence turns surface (or auto-expand) reasoning. Collapsible reasoning artifact (`⊟ N tokens · Ns`, expandable). Requires threading route-class + confidence from the router into the renderer.
