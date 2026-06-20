"""End-to-end orchestration tests for cli/ensemble.py using injected fakes.

No network, no providers: resolve / make_invoke / make_synth are all stubbed,
so this exercises the real fan-out + synthesis wiring deterministically.

    pytest tests/test_ensemble.py -v
"""

from __future__ import annotations

import pytest

from cli.ensemble import run_ensemble, render_outcome, STRATEGIES
from cli.parallel_dispatcher import DispatchResult, DispatchOutcome


def fake_resolve(known: set[str]):
    def _r(sel):
        return {"label": sel.title(), "name": sel} if sel in known else None

    return _r


def fake_make_invoke(answers: dict[str, str] | None = None):
    answers = answers or {}

    def _make():
        def invoke(cfg, prompt):
            label = cfg["label"]
            return DispatchResult(
                model_label=label, answer=answers.get(label, f"ans-{label}")
            )

        return invoke

    return _make


def fake_make_synth(capture: dict):
    def _make(synth_cfg, strategy_prompt):
        def synthesize(prompt, results):
            capture["synth_cfg"] = synth_cfg
            capture["strategy_prompt"] = strategy_prompt
            capture["labels"] = [r.model_label for r in results]
            capture["prompt"] = prompt
            return "FUSED:" + ",".join(r.model_label for r in results)

        return synthesize

    return _make


class TestRunEnsemble:
    def test_fans_out_and_synthesizes_all_tiers(self):
        cap: dict = {}
        out = run_ensemble(
            "what is mycelium?",
            selectors=["supreme", "oracle", "prime"],
            resolve=fake_resolve({"supreme", "oracle", "prime"}),
            make_invoke=fake_make_invoke(),
            make_synth=fake_make_synth(cap),
        )
        assert out.fusion == "ensemble_synthesis"
        assert out.fused_answer == "FUSED:Supreme,Oracle,Prime"
        # primary first, then companions, all fed to the synthesizer
        assert cap["labels"] == ["Supreme", "Oracle", "Prime"]
        assert cap["prompt"] == "what is mycelium?"
        assert len(out.results) == 3

    def test_unknown_selectors_are_dropped_not_fatal(self):
        cap: dict = {}
        out = run_ensemble(
            "q",
            selectors=["supreme", "nope", "oracle"],
            resolve=fake_resolve({"supreme", "oracle"}),
            make_invoke=fake_make_invoke(),
            make_synth=fake_make_synth(cap),
        )
        assert cap["labels"] == ["Supreme", "Oracle"]
        assert out.fused_answer == "FUSED:Supreme,Oracle"

    def test_all_unknown_raises(self):
        with pytest.raises(ValueError):
            run_ensemble(
                "q",
                selectors=["nope1", "nope2"],
                resolve=fake_resolve(set()),
                make_invoke=fake_make_invoke(),
                make_synth=fake_make_synth({}),
            )

    def test_strategy_selects_the_right_synth_prompt(self):
        cap: dict = {}
        run_ensemble(
            "q",
            selectors=["supreme", "oracle"],
            strategy="judge",
            resolve=fake_resolve({"supreme", "oracle"}),
            make_invoke=fake_make_invoke(),
            make_synth=fake_make_synth(cap),
        )
        assert cap["strategy_prompt"] == STRATEGIES["judge"]

    def test_synth_defaults_to_primary_tier(self):
        cap: dict = {}
        run_ensemble(
            "q",
            selectors=["supreme", "oracle"],
            resolve=fake_resolve({"supreme", "oracle"}),
            make_invoke=fake_make_invoke(),
            make_synth=fake_make_synth(cap),
        )
        assert cap["synth_cfg"]["label"] == "Supreme"

    def test_explicit_synth_selector_overrides_primary(self):
        cap: dict = {}
        run_ensemble(
            "q",
            selectors=["supreme", "oracle"],
            synth_selector="prime",
            resolve=fake_resolve({"supreme", "oracle", "prime"}),
            make_invoke=fake_make_invoke(),
            make_synth=fake_make_synth(cap),
        )
        assert cap["synth_cfg"]["label"] == "Prime"


class TestRenderOutcome:
    def test_summary_has_answer_and_per_tier_lines(self):
        outcome = DispatchOutcome(
            fused_answer="the fused answer",
            results=[
                DispatchResult(
                    model_label="Supreme", answer="a", latency_s=1.2, is_primary=True
                ),
                DispatchResult(model_label="Oracle", answer="b", latency_s=0.9),
            ],
            fusion="ensemble_synthesis",
            total_latency_s=1.3,
        )
        text = render_outcome(outcome)
        assert "the fused answer" in text
        assert "Supreme" in text and "Oracle" in text
        assert "fusion=ensemble_synthesis" in text
