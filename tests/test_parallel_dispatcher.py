"""Tests for the (previously dormant) parallel dispatcher + its new
``ensemble_synthesis`` fusion mode.

Pure logic: every test injects a fake ``invoke`` / ``synthesize`` adapter, so
there is no network, no provider, and no LLM call. Run:

    pytest tests/test_parallel_dispatcher.py -v
"""

from __future__ import annotations

import time

import pytest

from cli.parallel_dispatcher import (
    DispatchResult,
    DispatchOutcome,
    dispatch,
    build_synthesis_input,
    DEFAULT_ENSEMBLE_SYNTH_PROMPT,
)


def cfg(label: str) -> dict:
    return {"label": label, "name": label.lower()}


def ok_invoke(answers: dict[str, str]):
    """Build an invoke adapter that returns a canned answer per model label."""

    def _invoke(model_cfg: dict, prompt: str) -> DispatchResult:
        label = model_cfg["label"]
        return DispatchResult(model_label=label, answer=answers[label])

    return _invoke


def failing_invoke(fail_labels: set[str], answers: dict[str, str]):
    def _invoke(model_cfg: dict, prompt: str) -> DispatchResult:
        label = model_cfg["label"]
        if label in fail_labels:
            raise RuntimeError(f"{label} boom")
        return DispatchResult(model_label=label, answer=answers[label])

    return _invoke


# --------------------------------------------------------------------------
# Regression: existing fusion modes must keep working after the edit.
# --------------------------------------------------------------------------


class TestExistingModes:
    def test_primary_only_returns_primary(self):
        out = dispatch(
            "q",
            cfg("Primary"),
            invoke=ok_invoke({"Primary": "P", "C1": "c1"}),
            companions=[cfg("C1")],
            fusion="primary_only",
        )
        assert out.fused_answer == "P"
        assert len(out.results) == 2

    def test_primary_with_fallback_uses_companion_when_primary_fails(self):
        out = dispatch(
            "q",
            cfg("Primary"),
            invoke=failing_invoke({"Primary"}, {"C1": "c1"}),
            companions=[cfg("C1")],
            fusion="primary_with_fallback",
        )
        assert out.fused_answer == "c1"

    def test_present_both_concatenates_under_labels(self):
        out = dispatch(
            "q",
            cfg("Primary"),
            invoke=ok_invoke({"Primary": "P", "C1": "c1"}),
            companions=[cfg("C1")],
            fusion="present_both",
        )
        assert "### Primary" in out.fused_answer
        assert "### C1" in out.fused_answer
        assert "P" in out.fused_answer and "c1" in out.fused_answer


# --------------------------------------------------------------------------
# New: ensemble_synthesis (was NotImplementedError).
# --------------------------------------------------------------------------


class TestEnsembleSynthesis:
    def test_no_longer_raises_not_implemented(self):
        # Must not raise just for selecting the mode.
        dispatch(
            "q",
            cfg("Primary"),
            invoke=ok_invoke({"Primary": "P"}),
            fusion="ensemble_synthesis",
            synthesize=lambda prompt, results: "FUSED",
        )

    def test_synthesize_receives_prompt_and_successful_results(self):
        seen = {}

        def synth(prompt, results):
            seen["prompt"] = prompt
            seen["labels"] = [r.model_label for r in results]
            return "MERGED ANSWER"

        out = dispatch(
            "what is mycelium?",
            cfg("Primary"),
            invoke=ok_invoke({"Primary": "P", "C1": "c1", "C2": "c2"}),
            companions=[cfg("C1"), cfg("C2")],
            fusion="ensemble_synthesis",
            synthesize=synth,
        )
        assert out.fused_answer == "MERGED ANSWER"
        assert seen["prompt"] == "what is mycelium?"
        # all three succeeded -> all three feed the synthesizer
        assert set(seen["labels"]) == {"Primary", "C1", "C2"}
        assert out.fusion == "ensemble_synthesis"

    def test_only_successful_results_feed_synthesizer(self):
        captured = {}

        def synth(prompt, results):
            captured["labels"] = [r.model_label for r in results]
            return "X"

        dispatch(
            "q",
            cfg("Primary"),
            invoke=failing_invoke({"C1"}, {"Primary": "P", "C2": "c2"}),
            companions=[cfg("C1"), cfg("C2")],
            fusion="ensemble_synthesis",
            synthesize=synth,
        )
        assert "C1" not in captured["labels"]  # the failed one is excluded
        assert set(captured["labels"]) == {"Primary", "C2"}

    def test_single_success_skips_synthesis(self):
        # Synthesizing one answer is the answer itself; don't pay for a synth call.
        called = {"n": 0}

        def synth(prompt, results):
            called["n"] += 1
            return "SHOULD NOT RUN"

        out = dispatch(
            "q",
            cfg("Primary"),
            invoke=failing_invoke({"C1"}, {"Primary": "only"}),
            companions=[cfg("C1")],
            fusion="ensemble_synthesis",
            synthesize=synth,
        )
        assert out.fused_answer == "only"
        assert called["n"] == 0

    def test_all_fail_returns_empty(self):
        out = dispatch(
            "q",
            cfg("Primary"),
            invoke=failing_invoke({"Primary", "C1"}, {}),
            companions=[cfg("C1")],
            fusion="ensemble_synthesis",
            synthesize=lambda p, r: "X",
        )
        assert out.fused_answer == ""

    def test_missing_synthesizer_degrades_to_present_both(self):
        # No synthesize fn provided -> must NOT crash; degrade to a readable
        # side-by-side rather than dropping answers on the floor.
        out = dispatch(
            "q",
            cfg("Primary"),
            invoke=ok_invoke({"Primary": "P", "C1": "c1"}),
            companions=[cfg("C1")],
            fusion="ensemble_synthesis",
            synthesize=None,
        )
        assert "Primary" in out.fused_answer and "C1" in out.fused_answer


class TestBuildSynthesisInput:
    def test_frames_prompt_and_each_answer(self):
        results = [
            DispatchResult(model_label="Apex", answer="alpha", is_primary=True),
            DispatchResult(model_label="Oracle", answer="beta"),
        ]
        text = build_synthesis_input("original Q", results)
        assert "original Q" in text
        assert "Apex" in text and "alpha" in text
        assert "Oracle" in text and "beta" in text

    def test_default_prompt_is_nonempty_and_brand_consistent(self):
        assert isinstance(DEFAULT_ENSEMBLE_SYNTH_PROMPT, str)
        assert "CroweLM" in DEFAULT_ENSEMBLE_SYNTH_PROMPT
