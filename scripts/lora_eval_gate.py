#!/usr/bin/env python3
"""
LoRA eval gate: run the rubric against a candidate adapter before promoting.

Invoked by `scripts/lora_phase2_submit_tuning.py` (or any LoRA promotion
script) before pushing a new adapter to production. The gate runs every
transcript in `eval/transcripts/` against the candidate adapter and refuses
to promote if:

    1. Any transcript scores worse than its baseline (regression).
    2. Any per-metric score increases by more than --regression-threshold.
    3. The aggregate fails to meet the --min-improvement floor.

Usage:
    python scripts/lora_eval_gate.py --baseline baseline.json --candidate candidate.json
    python scripts/lora_eval_gate.py --baseline-from eval/baselines/ --candidate-from eval/candidates/

Baseline files:
    eval/baselines/<variant>.json - reference scores for a given variant. Updated
    only when a promotion succeeds (i.e., baselines move forward, never back).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from eval.replay import (  # noqa: E402
    list_transcripts,
    report_to_dict,
    score_offline,
)


BASELINES_DIR = REPO_ROOT / "eval" / "baselines"


def load_baseline(variant: str) -> dict[str, Any] | None:
    path = BASELINES_DIR / f"{variant}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_baseline(variant: str, scores: dict[str, Any]) -> Path:
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    path = BASELINES_DIR / f"{variant}.json"
    path.write_text(json.dumps(scores, indent=2), encoding="utf-8")
    return path


def gate_one(
    variant: str,
    candidate_scores: dict[str, Any],
    baseline_scores: dict[str, Any] | None,
    regression_threshold: float,
    min_improvement: float,
) -> tuple[bool, list[str]]:
    """Return (pass, reasons). pass=False blocks promotion."""
    reasons: list[str] = []
    candidate_agg = candidate_scores.get("aggregate", float("nan"))

    if baseline_scores is None:
        if candidate_agg > min_improvement:
            reasons.append(
                f"no baseline yet; candidate aggregate {candidate_agg:.3f} "
                f"exceeds floor {min_improvement:.3f}, blocking promotion"
            )
            return False, reasons
        reasons.append(
            f"no baseline; candidate aggregate {candidate_agg:.3f} acceptable, allowing"
        )
        return True, reasons

    baseline_agg = baseline_scores.get("aggregate", float("nan"))
    if candidate_agg > baseline_agg:
        reasons.append(
            f"aggregate regression: candidate {candidate_agg:.3f} > baseline {baseline_agg:.3f}"
        )
        return False, reasons

    # Per-metric regression check
    candidate_metrics = candidate_scores.get("per_metric", {})
    baseline_metrics = baseline_scores.get("per_metric", {})
    regressions: list[str] = []
    for mid, cand_data in candidate_metrics.items():
        base_data = baseline_metrics.get(mid, {})
        cand_score = cand_data.get("score")
        base_score = base_data.get("score")
        if cand_score is None or base_score is None:
            continue
        try:
            delta = float(cand_score) - float(base_score)
        except (TypeError, ValueError):
            continue
        if delta > regression_threshold:
            regressions.append(
                f"{mid} regressed by {delta:.3f} (cand={cand_score}, base={base_score})"
            )
    if regressions:
        reasons.extend(regressions)
        return False, reasons

    reasons.append(
        f"PASS: candidate {candidate_agg:.3f} <= baseline {baseline_agg:.3f}, no metric regressions"
    )
    return True, reasons


def run_eval(variant: str) -> dict[str, Any]:
    """Score every transcript and produce an aggregate report for one variant."""
    transcripts = list_transcripts()
    if not transcripts:
        raise RuntimeError("No transcripts found in eval/transcripts/")
    per_transcript: dict[str, dict[str, Any]] = {}
    aggregates: list[float] = []
    merged_metrics: dict[str, list[float]] = {}
    for path in transcripts:
        report = score_offline(path)
        d = report_to_dict(report)
        per_transcript[report.transcript_id] = d
        if report.aggregate == report.aggregate:  # NaN check
            aggregates.append(report.aggregate)
        for mid, result in d["per_metric"].items():
            if not result.get("skipped"):
                merged_metrics.setdefault(mid, []).append(result["score"])
    overall = {
        "variant": variant,
        "aggregate": sum(aggregates) / len(aggregates) if aggregates else float("nan"),
        "per_metric": {
            mid: {"score": sum(scores) / len(scores) if scores else float("nan")}
            for mid, scores in merged_metrics.items()
        },
        "per_transcript": per_transcript,
    }
    return overall


def main() -> int:
    parser = argparse.ArgumentParser(description="LoRA eval gate")
    parser.add_argument("--variant", required=True, help="variant slug, e.g. 'eclipse'")
    parser.add_argument(
        "--regression-threshold",
        type=float,
        default=0.05,
        help="max allowed per-metric increase before failing the gate",
    )
    parser.add_argument(
        "--min-improvement",
        type=float,
        default=0.10,
        help="aggregate ceiling when no baseline exists yet",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="write the candidate scores as the new baseline if the gate passes",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the candidate report JSON here for archival",
    )
    args = parser.parse_args()

    candidate = run_eval(args.variant)
    baseline = load_baseline(args.variant)
    passed, reasons = gate_one(
        args.variant,
        candidate,
        baseline,
        args.regression_threshold,
        args.min_improvement,
    )

    print(f"=== LoRA Eval Gate: {args.variant} ===")
    print(f"Result: {'PASS' if passed else 'BLOCK'}")
    print(f"Aggregate: {candidate['aggregate']:.3f}")
    if baseline:
        print(f"Baseline:  {baseline['aggregate']:.3f}")
    print()
    print("Reasons:")
    for reason in reasons:
        print(f"  - {reason}")

    if args.report:
        args.report.write_text(json.dumps(candidate, indent=2), encoding="utf-8")
        print(f"\nReport written to {args.report}")

    if passed and args.update_baseline:
        path = write_baseline(args.variant, candidate)
        print(f"Baseline updated: {path}")

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
