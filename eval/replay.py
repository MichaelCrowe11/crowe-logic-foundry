"""
Transcript replay harness.

Loads a transcript file (JSON), normalizes it into TurnContexts, and either:
    1. Scores it against the rubric (offline, no API calls), or
    2. Replays user turns against a live variant and scores the results.

Schema for a transcript file:

    {
      "transcript_id": "2026-04-30-eclipse-email-blast",
      "variant": "eclipse",
      "turns": [
        {
          "user_message": "...",
          "assistant_output": "...",
          "reasoning_text": "...",
          "reasoning_tokens": 5856,
          "output_tokens": 698,
          "ttft_ms": 1095800,
          "tool_calls": [{"name": "Write", "args": {"file_path": "..."}}, ...],
          "capability_disclosed_on_turn": null
        },
        ...
      ]
    }

The 2026-04-30 Eclipse transcript is the seed; new transcripts can be added
by sanitizing real session logs (drop credentials, normalize paths).
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

# When invoked as a script, ensure the repo root is on sys.path so the
# `eval.rubric` import resolves. This is a no-op when imported as a module.
_REPO_ROOT_FOR_SCRIPT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT_FOR_SCRIPT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_FOR_SCRIPT))

from eval.rubric import (
    Rubric,
    RubricReport,
    TurnContext,
    score_transcript,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = REPO_ROOT / "eval" / "transcripts"


def load_transcript(path: Path) -> tuple[str, str, list[TurnContext]]:
    """Load a transcript JSON and return (id, variant, contexts)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    transcript_id = data.get("transcript_id", path.stem)
    variant = data.get("variant", "unknown")
    raw_turns: list[dict[str, Any]] = data.get("turns", [])
    contexts: list[TurnContext] = []
    for idx, turn in enumerate(raw_turns):
        contexts.append(
            TurnContext(
                user_message=turn.get("user_message", ""),
                assistant_output=turn.get("assistant_output", ""),
                reasoning_text=turn.get("reasoning_text", ""),
                reasoning_tokens=int(turn.get("reasoning_tokens", 0)),
                output_tokens=int(turn.get("output_tokens", 0)),
                ttft_ms=float(turn.get("ttft_ms", 0.0)),
                tool_calls=list(turn.get("tool_calls", [])),
                capability_disclosed_on_turn=turn.get("capability_disclosed_on_turn"),
                turn_index=idx,
            )
        )
    return transcript_id, variant, contexts


def score_offline(path: Path, rubric: Rubric | None = None) -> RubricReport:
    """Score a saved transcript without any API calls."""
    transcript_id, _variant, contexts = load_transcript(path)
    return score_transcript(transcript_id, contexts, rubric)


def list_transcripts() -> list[Path]:
    if not TRANSCRIPTS_DIR.exists():
        return []
    return sorted(TRANSCRIPTS_DIR.glob("*.json"))


def report_to_dict(report: RubricReport) -> dict[str, Any]:
    """Render a RubricReport as a JSON-friendly dict."""
    return {
        "transcript_id": report.transcript_id,
        "aggregate": report.aggregate,
        "per_metric": {
            mid: {
                "score": result.score,
                "skipped": result.skipped,
                "detail": result.detail,
            }
            for mid, result in report.per_metric.items()
        },
    }


def render_report(report: RubricReport) -> str:
    """Compact human-readable text report."""
    lines = [
        f"Transcript: {report.transcript_id}",
        f"Aggregate score: {report.aggregate:.3f} (0=perfect, 1=catastrophic)",
        "",
        f"{'Metric':<8}{'Score':<8}{'Verdict':<14}Detail",
        "-" * 80,
    ]
    for mid in sorted(report.per_metric.keys()):
        result = report.per_metric[mid]
        if result.skipped:
            lines.append(f"{mid:<8}{'--':<8}{'skipped':<14}{result.detail}")
            continue
        verdict = (
            "perfect"
            if result.score == 0.0
            else "ok"
            if result.score < 0.25
            else "warn"
            if result.score < 0.6
            else "FAIL"
        )
        lines.append(
            f"{mid:<8}{result.score:<8.3f}{verdict:<14}{_compact_detail(result.detail)}"
        )
    return "\n".join(lines)


def _compact_detail(detail: dict[str, Any]) -> str:
    if not detail:
        return ""
    parts: list[str] = []
    for k, v in list(detail.items())[:4]:
        if isinstance(v, list) and len(v) > 3:
            parts.append(f"{k}=[{len(v)} items]")
        elif isinstance(v, dict):
            parts.append(f"{k}={{...}}")
        else:
            parts.append(f"{k}={v}")
    return " ".join(parts)


def main(argv: Iterable[str] | None = None) -> int:
    """Command-line entry: score one or all transcripts."""
    import argparse

    parser = argparse.ArgumentParser(description="Score CroweLM transcripts")
    parser.add_argument(
        "transcript",
        nargs="?",
        help="Path to a transcript JSON, or empty to score all in eval/transcripts/",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of human-readable"
    )
    args = parser.parse_args(list(argv) if argv else None)

    paths = [Path(args.transcript)] if args.transcript else list_transcripts()
    if not paths:
        print("No transcripts found in eval/transcripts/")
        return 1

    for path in paths:
        report = score_offline(path)
        if args.json:
            print(json.dumps(report_to_dict(report), indent=2))
        else:
            print(render_report(report))
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
