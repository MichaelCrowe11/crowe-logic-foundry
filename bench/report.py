"""Render a scored results file into a Markdown scoreboard.

Track A: per-tier accuracy (backend baseline, honestly labelled).
Track B: per-tier grounded-vs-bare delta on the mycology set — the platform's
contribution over the base model.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path


def _load(path: Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _brand_labels() -> dict[str, str]:
    """Map each MODEL_CHAIN backend `name` to its CroweLM `label`.

    The scoreboard is public/website-facing, so it must show CroweLM brand
    names (e.g. "CroweLM Helio"), never the underlying backend (e.g. gpt-5.4).
    """
    try:
        from config.agent_config import MODEL_CHAIN
    except Exception:
        return {}
    return {c["name"]: c.get("label", c["name"]) for c in MODEL_CHAIN if c.get("name")}


def build_scoreboard(scored_path: Path) -> str:
    rows = _load(scored_path)
    labels = _brand_labels()

    def brand(tier: str) -> str:
        return labels.get(tier, tier)

    out = ["# CroweLM Benchmark Scoreboard", ""]

    # Track A: tier -> mean accuracy
    a = defaultdict(list)
    for r in rows:
        if r.get("track") == "a" and r.get("score") is not None:
            a[r["tier"]].append(r["score"])
    if a:
        out += [
            "## Track A — public benchmarks (baseline)",
            "",
            "| CroweLM tier | Accuracy | N |",
            "|---|---|---|",
        ]
        for tier, scores in sorted(a.items(), key=lambda kv: brand(kv[0])):
            out.append(
                f"| {brand(tier)} | {statistics.mean(scores) * 100:.1f}% | {len(scores)} |"
            )
        out.append("")

    # Track B: tier -> {grounded, bare} mean -> delta
    b = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.get("track") == "b" and r.get("score") is not None:
            b[r["tier"]][r["condition"]].append(r["score"])
    if b:
        out += [
            "## Track B — mycology: grounded vs bare (the CroweLM delta)",
            "",
            "| CroweLM tier | Grounded | Bare | Δ (delta) |",
            "|---|---|---|---|",
        ]
        scored = []
        for tier, conds in b.items():
            g = statistics.mean(conds["grounded"]) if conds["grounded"] else 0.0
            bare = statistics.mean(conds["bare"]) if conds["bare"] else 0.0
            scored.append((tier, g, bare, g - bare))
        for tier, g, bare, d in sorted(scored, key=lambda x: -x[3]):
            out.append(f"| {brand(tier)} | {g:.2f} | {bare:.2f} | {d:+.2f} |")
        out.append("")
        out.append(
            "_Δ = grounded − bare on a 0–5 scale. The delta is the platform's "
            "contribution over the base model._"
        )

    return "\n".join(out)
