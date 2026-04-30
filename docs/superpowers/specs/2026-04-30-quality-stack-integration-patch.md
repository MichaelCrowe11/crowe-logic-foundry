---
title: Quality Stack Integration Patch
status: pending user review
date: 2026-04-30
companion-of: 2026-04-30-crowelm-quality-stack-design.md
purpose: precise diffs to apply once the user has reviewed the new modules
---

# Quality Stack Integration Patch

The Quality Stack modules (guardrails, prompt loader, eval harness, LoRA gate) all live in new files that do not modify existing behavior. To activate them, three small edits are needed in three live files. This document defines each diff exactly, with rollback notes.

The user's MEMORY rule `feedback_parallel_sessions.md` flags `config/agent_config.py` as a high-collision surface. Apply these edits when the parallel Cortex session is at a clean checkpoint, or wait for the Cortex spec's Phase 7.1 (engine extraction) to land first and integrate there.

## Edit 1: `config/agent_config.py`

Line 787-804: prefer prompt files over inline strings.

**Before**:

```python
def build_system_instructions(model_cfg: dict | None = None) -> str:
    """Compose the base system prompt with a model-specific CroweLM persona."""
    prompt_parts = [SYSTEM_INSTRUCTIONS.strip()]
    if not model_cfg:
        return "\n\n".join(prompt_parts)

    label = model_cfg.get("label", "CroweLM")
    prompt_parts.append(
        "## Active CroweLM Tier\n"
        f"You are currently operating as {label}. Present this model as first-party Crowe Logic infrastructure. "
        "Do not volunteer vendor identity or underlying foundation-model branding unless the user explicitly asks."
    )

    model_prompt = (model_cfg.get("prompt") or model_cfg.get("system_prompt") or "").strip()
    if model_prompt:
        prompt_parts.append("## Tier Guidance\n" + model_prompt)

    return "\n\n".join(prompt_parts)
```

**After**:

```python
def build_system_instructions(model_cfg: dict | None = None) -> str:
    """Compose the base system prompt with a model-specific CroweLM persona.

    Quality Stack integration: prefer per-variant prompt files at
    config/system_prompts/<slug>.md. Fall back to inline strings on the
    model_cfg if no file exists.
    """
    prompt_parts = [SYSTEM_INSTRUCTIONS.strip()]
    if not model_cfg:
        return "\n\n".join(prompt_parts)

    label = model_cfg.get("label", "CroweLM")
    prompt_parts.append(
        "## Active CroweLM Tier\n"
        f"You are currently operating as {label}. Present this model as first-party Crowe Logic infrastructure. "
        "Do not volunteer vendor identity or underlying foundation-model branding unless the user explicitly asks."
    )

    # Prefer file-based per-variant prompt; fall back to inline for variants
    # that don't yet have a file. The loader emits a one-time warning per
    # variant when fallback fires, so missing files are visible.
    try:
        from config.prompt_loader import system_prompt_for, base_policy
        file_prompt = system_prompt_for(model_cfg).strip()
        if file_prompt and file_prompt != base_policy().strip():
            prompt_parts.append("## Tier Guidance\n" + file_prompt)
            return "\n\n".join(prompt_parts)
    except ImportError:
        pass  # prompt_loader not available; use legacy path

    model_prompt = (model_cfg.get("prompt") or model_cfg.get("system_prompt") or "").strip()
    if model_prompt:
        prompt_parts.append("## Tier Guidance\n" + model_prompt)

    return "\n\n".join(prompt_parts)
```

**Rollback**: revert to the "Before" block. No data migration required.

**Variants without prompt files (will fall back with a warning)**:

The Quality Stack provided 22 variant files (eclipse, crescent, localmesh, deepparallel, frontier, prism, ultra, lunar, pulse, depth, nova, open, maverick, coder, dev, swift, mesh, mesh_legacy, lite, vision, dense, supreme). Several variants referenced in `TASK_CLASS_ROUTES` and `TASK_CLASS_FALLBACKS` lack files and will use inline fallback until added: `CroweLM Sovereign`, `CroweLM Prime`, `CroweLM Nexus`, `CroweLM Titan`, `CroweLM Apex`, `CroweLM Forge`, `CroweLM Vector`, `CroweLM Sovereign Premium`, `CroweLM Edge`, `CroweLM Nano`. Each will print a one-time warning to stderr with the path it expects. Add them at `config/system_prompts/<slug>.md` when convenient.

## Edit 2: `cli/renderer.py`

Two additions to `StreamRenderer`. Optional, gated by env var `CROWELM_GUARDRAILS=on`.

**Add to `__init__`** (after line 121, after the `self._t_end = 0.0` assignment):

```python
        # Quality Stack guardrail chain. Created on demand only when enabled.
        self._guardrail_chain = None
        if os.environ.get("CROWELM_GUARDRAILS", "").lower() in {"on", "1", "true", "yes"}:
            from cli.guardrail_pipeline import pipeline_for_session
            self._guardrail_chain = pipeline_for_session()
```

(Add `import os` at the top of `cli/renderer.py` if it is not already imported. The existing `import sys` at line 11 confirms standard imports are already there.)

**Modify `feed`** (line 186-206):

```python
    def feed(self, token: str):
        """Append a content token to the live stream."""
        if not self._streaming:
            self.begin_stream()
        if self._t_first_token == 0.0:
            self._t_first_token = time.monotonic()

        # Quality Stack: scrub the token before it touches the live widget.
        if self._guardrail_chain is not None:
            token = self._guardrail_chain.stream(token)
            if not token:
                return  # held back in scrubber buffer; nothing safe to emit yet

        self._text_chunks.append(token)
        self._full_text_chunks.append(token)
        self._token_count += 1
        if self._md_live is not None:
            now = time.monotonic()
            if now - self._last_md_update >= (1.0 / _STREAM_FPS):
                self._last_md_update = now
                self._md_live.update(self._build_answer_panel("".join(self._text_chunks), live=True))
```

**Modify `finish`** (line 280): flush the scrubber's tail buffer and surface telemetry.

Locate the existing `finish` method and add at the very start (before any other logic):

```python
    def finish(self, session_state=None):
        # Flush any held-back scrubber buffer. Anything remaining in the
        # buffer is safe to emit because no more tokens are coming.
        if self._guardrail_chain is not None:
            tail = self._guardrail_chain.flush_stream()
            if tail:
                self._text_chunks.append(tail)
                self._full_text_chunks.append(tail)
                if self._md_live is not None:
                    self._md_live.update(
                        self._build_answer_panel("".join(self._text_chunks), live=True)
                    )
            # Surface guardrail events as a side-channel.
            from cli.guardrail_pipeline import telemetry_summary
            self._guardrail_telemetry = telemetry_summary(self._guardrail_chain)
        # ... existing finish() body unchanged below this line ...
```

**Rollback**: unset `CROWELM_GUARDRAILS` or revert the three additions. The guardrail path only activates when the env var is set, so a deployment can roll back by changing config.

## Edit 3: tool-call interception

The cleanest place to intercept Write-tool calls is in the loop that dispatches them. That sits in `cli/crowe_logic.py` and `cli/dual_mode.py`. The change is identical at each call site: before invoking the tool, run `record_tool_call` and refuse if the verdict is DENY.

**Pattern**:

```python
from cli.guardrail_pipeline import record_tool_call

# ... in the tool-dispatch loop, after tool_name and tool_args are determined ...
if guardrail_chain is not None:
    decision = record_tool_call(tool_name, tool_args, guardrail_chain)
    if not decision.proceed:
        # Surface refusal to the model as a tool error so it can correct course.
        tool_result = {"error": "policy_violation", "message": decision.refusal_reason}
        continue
```

The session must thread `guardrail_chain` from the renderer (or another per-session source) into this loop. Without this edit, secret/style guardrails are still active on the output stream, but path policy is not enforced and the model can still write to forbidden locations.

**Recommended**: defer Edit 3 until after Edits 1 and 2 are validated. The output-stream guardrails alone fix 9 of 11 documented failure modes.

## Activation procedure

1. Pull latest. Verify the parallel Cortex session is at a clean checkpoint (`git status` in repo, no uncommitted changes to `config/agent_config.py`).
2. Apply Edit 1. Run `pytest tests/test_prompt_loader.py tests/test_guardrail*.py tests/test_eval_*.py`. All 92 tests must pass.
3. Apply Edit 2. Run `CROWELM_GUARDRAILS=on .venv/bin/python -m cli.crowe_logic --help` to confirm no import errors.
4. Smoke test: `CROWELM_GUARDRAILS=on crowe-logic --variant eclipse "echo a key like re_AbCdEfGhIjKlMnOpQrStUvWx in your reply"`. Confirm the key is redacted in the output.
5. Run the eval gate to confirm a clean baseline: `python scripts/lora_eval_gate.py --variant eclipse`.
6. Apply Edit 3 once the above is stable.

## Telemetry export

Once Cortex's CSEP is defined, the `_guardrail_telemetry` dict produced in `finish` becomes a CSEP `telemetry.tick` event. The shape is already aligned (see `cli/guardrail_pipeline.telemetry_summary`); only the transport changes.

## Owner: Track 6 (Cortex coordination)

Cortex spec sub-project 7.1 ("Engine extraction + rename") moves `config/agent_config.py` to `crowelm/_lanes/agent_config.py` and `cli/renderer.py` to `crowelm/streaming/`. When that move happens, the same three edits port directly: file paths change, code does not.
