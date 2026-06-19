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
    # Benchmark rows may key a tier by its internal `name` (e.g. "gpt-5.4") or by
    # the underlying vendor `backend_name` (e.g. "Kimi-K2-6"). Brand both so the
    # public scoreboard never leaks a backend. `name` wins on any collision, as
    # it's the canonical tier identifier.
    labels: dict[str, str] = {}
    for c in MODEL_CHAIN:
        backend = c.get("backend_name")
        if backend and backend not in labels:
            labels[backend] = c.get("label", backend)
    for c in MODEL_CHAIN:
        name = c.get("name")
        if name:
            labels[name] = c.get("label", name)
    return labels


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
        excluded = []
        for tier, conds in b.items():
            # A grounded-vs-bare delta is undefined without BOTH sides. If either
            # condition has no scored rows (all errored/blank), exclude the tier
            # rather than fabricate a 0.00 placeholder for the missing side.
            if not conds["grounded"] or not conds["bare"]:
                excluded.append(tier)
                continue
            g = statistics.mean(conds["grounded"])
            bare = statistics.mean(conds["bare"])
            scored.append((tier, g, bare, g - bare))
        for tier, g, bare, d in sorted(scored, key=lambda x: -x[3]):
            out.append(f"| {brand(tier)} | {g:.2f} | {bare:.2f} | {d:+.2f} |")
        out.append("")
        if excluded:
            names = ", ".join(sorted(brand(t) for t in excluded))
            out.append(f"_Excluded (incomplete grounded/bare data this run): {names}._")
        out.append(
            "_Δ = grounded − bare on a 0–5 scale. The delta is the platform's "
            "contribution over the base model._"
        )

    return "\n".join(out)
