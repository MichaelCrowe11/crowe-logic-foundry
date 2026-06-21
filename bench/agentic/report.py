from __future__ import annotations

from bench.agentic.score import aggregate


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def render_scoreboard(
    rows: list[dict],
    crowe_agent: str = "crowe-logic",
    baseline_agent: str = "reference",
) -> str:
    agg = aggregate(rows)
    agents = sorted(agg)
    lines = ["# Agentic Coding Eval — Scoreboard", ""]
    lines += [
        "| agent | n | pass@1 | self-verified | avg rounds |",
        "|---|---|---|---|---|",
    ]
    for a in agents:
        m = agg[a]
        lines.append(
            f"| {a} | {m['n']} | {_pct(m['pass_at_1'])} "
            f"| {_pct(m['self_verified_rate'])} | {m['avg_rounds']:.1f} |"
        )
    if crowe_agent in agg and baseline_agent in agg:
        gap = (agg[baseline_agent]["pass_at_1"] - agg[crowe_agent]["pass_at_1"]) * 100
        lines += [
            "",
            f"**Harness-isolated gap (baseline − crowe-logic):** {gap:+.0f} pp",
        ]
    task_ids = sorted({r["task_id"] for r in rows})
    lines += [
        "",
        "## Per-task pass@1",
        "",
        "| task | " + " | ".join(agents) + " |",
        "|---|" + "|".join(["---"] * len(agents)) + "|",
    ]
    seen = {(r["task_id"], r["agent"]): r["passed"] for r in rows}
    for t in task_ids:
        cells = ["✅" if seen.get((t, a)) else "❌" for a in agents]
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
