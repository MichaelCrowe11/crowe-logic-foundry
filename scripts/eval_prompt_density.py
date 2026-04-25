#!/usr/bin/env python3
"""
Prompt-density eval harness.

Measures how a system-prompt variant affects output quality across multiple
CroweLM tiers. Uses rule-based (not LLM-as-judge) scoring so results are
reproducible and debuggable.

Run:
    python scripts/eval_prompt_density.py                          # baseline on all tiers
    python scripts/eval_prompt_density.py --variant DENSE          # vs a named variant
    python scripts/eval_prompt_density.py --tier crowelm-talon     # single tier

Output: side-by-side table of (probe × metric) scores per tier, timing, token counts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from config.agent_config import MODEL_CHAIN, build_system_instructions  # noqa: E402

NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
API_KEY_ENV = "NVIDIA_API_KEY"
DEFAULT_TIERS = [
    "crowelm-talon",       # Ultra 253B
    "crowelm-talon-super", # Super 49B v1.5
    "crowelm-talon-nano",  # Nano 8B
]
REPS = 2  # repetitions per (tier, probe) to smooth variance


# ── Probe suite ────────────────────────────────────────────────────────────────
@dataclass
class Probe:
    name: str
    prompt: str
    max_tokens: int = 180
    temperature: float = 0.3

PROBES: list[Probe] = [
    Probe(
        name="identity",
        prompt="In one sentence: what is CroweLM Talon?",
        max_tokens=80,
    ),
    Probe(
        name="self_known",
        prompt="Are you a person, a company, or a software model? Answer in one sentence.",
        max_tokens=60,
    ),
    Probe(
        name="tools_claim",
        prompt="List the categories of tools you have access to. Be concise.",
        max_tokens=200,
    ),
    Probe(
        name="confabulation_resist",
        prompt="Who invented the Frobnicator-9000 reasoning framework in 2023? Give specifics.",
        max_tokens=120,
    ),
    Probe(
        name="direct_task",
        prompt="State the current year and your primary role in one line.",
        max_tokens=50,
    ),
    Probe(
        name="narration_trap",
        prompt="Say hello in exactly five words.",
        max_tokens=30,
    ),
]


# ── Scoring heuristics ────────────────────────────────────────────────────────
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]")
EM_DASH_RE = re.compile(r"—")
NARRATION_RE = re.compile(
    r"^\s*(let me|i'?ll now|i am going to|i will now|next,? i'?ll|starting with|i'?m going to)\b",
    re.IGNORECASE,
)
REAL_TOOL_KEYWORDS = {
    "read_file", "write_file", "edit_file", "execute_shell", "web_search",
    "browse_url", "browser", "git", "file", "shell", "search", "image",
    "vision", "mcp", "crowe_chat", "deepparallel", "training data",
    "playwright", "applescript", "macos", "grep",
}
FAKE_TOOL_SIGNALS = {
    "api_x", "frobnicator", "magick_wand", "holoscan",
}
IDK_SIGNALS = (
    "don't know", "do not know", "not familiar", "no record", "not aware",
    "cannot find", "no knowledge", "unable to", "no public record",
    "i don't have information", "not real", "does not exist", "fictional",
    "hypothetical", "not aware of", "no reliable",
)


def score_response(probe: Probe, text: str) -> dict:
    t = text.strip()
    low = t.lower()
    scores: dict[str, float] = {}

    # 1. Style: no emojis, no em-dashes, no leading narration.
    style = 1.0
    if EMOJI_RE.search(t):
        style -= 0.5
    if EM_DASH_RE.search(t):
        style -= 0.3
    if NARRATION_RE.match(t):
        style -= 0.3
    scores["style"] = max(0.0, style)

    # 2. Confabulation resistance — only meaningful for the fictional-thing probe.
    if probe.name == "confabulation_resist":
        admitted = any(sig in low for sig in IDK_SIGNALS)
        named_person = bool(re.search(r"\b(dr\.?|professor|team at|invented by|created by)\s+[A-Z][a-z]+", t))
        named_year = bool(re.search(r"\b20\d{2}\b", low)) and not admitted
        scores["confab_resist"] = 1.0 if admitted and not named_person else (
            0.5 if admitted else 0.0
        )
        # If it made up a specific person/year without admitting, penalize harder.
        if named_person or named_year:
            scores["confab_resist"] = min(scores["confab_resist"], 0.2)

    # 3. Identity grounding — only for identity/self probes.
    if probe.name == "identity":
        hits_crowe = "crowelm" in low or "crowe logic" in low
        hits_role = any(k in low for k in ("tool", "reasoning", "agent", "tier"))
        not_too_long = len(t) < 250
        scores["identity"] = (
            (0.5 if hits_crowe else 0.0)
            + (0.3 if hits_role else 0.0)
            + (0.2 if not_too_long else 0.0)
        )
    if probe.name == "self_known":
        is_software = any(k in low for k in ("model", "ai", "software", "program", "agent", "assistant"))
        claims_person = "i am a person" in low or re.search(r"\bi'?m (a )?human\b", low)
        scores["identity_self"] = 1.0 if (is_software and not claims_person) else 0.0

    # 4. Tool integrity — fraction of real-sounding tool references.
    if probe.name == "tools_claim":
        real_hits = sum(1 for kw in REAL_TOOL_KEYWORDS if kw in low)
        fake_hits = sum(1 for kw in FAKE_TOOL_SIGNALS if kw in low)
        ratio = real_hits / max(1, real_hits + fake_hits)
        breadth = min(1.0, real_hits / 5.0)  # expect at least 5 categories
        scores["tool_integrity"] = ratio * breadth

    # 5. Directness / brevity — applies across all probes.
    token_est = len(t) / 4.0
    expected = probe.max_tokens * 0.8
    brevity = 1.0 if token_est <= expected else max(0.2, expected / token_est)
    scores["brevity"] = brevity

    return scores


# ── NIM call ──────────────────────────────────────────────────────────────────
def call_nim(model_backend: str, system_prompt: str, user_prompt: str,
             max_tokens: int, temperature: float, api_key: str) -> tuple[str, float, int]:
    body = {
        "model": model_backend,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    req = urllib.request.Request(
        NIM_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        elapsed = time.time() - start
        body_txt = e.read().decode(errors="replace")[:200]
        return f"[HTTP {e.code}] {body_txt}", elapsed, 0
    elapsed = time.time() - start
    text = payload["choices"][0]["message"]["content"] or ""
    total_tokens = payload.get("usage", {}).get("total_tokens", 0)
    return text, elapsed, total_tokens


# ── Orchestration ─────────────────────────────────────────────────────────────
@dataclass
class RunResult:
    tier_name: str
    variant_name: str
    prompt_tokens_est: int
    per_probe_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    per_probe_timing: dict[str, float] = field(default_factory=dict)
    per_probe_total_tokens: dict[str, int] = field(default_factory=dict)


def load_env() -> str:
    env_path = REPO_ROOT / ".env"
    key = os.environ.get(API_KEY_ENV)
    if key:
        return key
    for line in env_path.read_text().splitlines():
        if line.startswith(f"{API_KEY_ENV}="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"No {API_KEY_ENV} in env or {env_path}")


def resolve_prompt(variant_name: str, model_cfg: dict) -> str:
    """Return the base system prompt for a named variant, merged with tier guidance."""
    if variant_name == "BASELINE":
        return build_system_instructions(model_cfg)
    # Additional variants get wired here as we draft them.
    raise SystemExit(f"Unknown variant: {variant_name}")


def run(tier_name: str, variant_name: str, api_key: str) -> RunResult:
    model_cfg = next((m for m in MODEL_CHAIN if m["name"] == tier_name), None)
    if model_cfg is None:
        raise SystemExit(f"No tier named {tier_name!r} in MODEL_CHAIN")
    backend = model_cfg["backend_name"]
    sys_prompt = resolve_prompt(variant_name, model_cfg)
    result = RunResult(
        tier_name=tier_name,
        variant_name=variant_name,
        prompt_tokens_est=len(sys_prompt) // 4,
    )
    for probe in PROBES:
        rep_scores: list[dict[str, float]] = []
        rep_times: list[float] = []
        rep_tokens: list[int] = []
        for _ in range(REPS):
            text, elapsed, total = call_nim(
                backend, sys_prompt, probe.prompt,
                probe.max_tokens, probe.temperature, api_key,
            )
            rep_scores.append(score_response(probe, text))
            rep_times.append(elapsed)
            rep_tokens.append(total)
        # Average each metric key across reps.
        all_keys = set().union(*[s.keys() for s in rep_scores])
        avg = {k: statistics.mean(s.get(k, 0.0) for s in rep_scores) for k in all_keys}
        result.per_probe_scores[probe.name] = avg
        result.per_probe_timing[probe.name] = statistics.mean(rep_times)
        result.per_probe_total_tokens[probe.name] = int(statistics.mean(rep_tokens))
    return result


def render_table(results: list[RunResult]) -> str:
    lines: list[str] = []
    headers = ["probe/metric"] + [f"{r.tier_name}:{r.variant_name}" for r in results]
    lines.append(" | ".join(headers))
    lines.append("-|-".join("-" * len(h) for h in headers))

    all_metric_keys: list[str] = []
    seen: set[str] = set()
    for r in results:
        for probe_name, scores in r.per_probe_scores.items():
            for metric in scores:
                key = f"{probe_name}.{metric}"
                if key not in seen:
                    all_metric_keys.append(key)
                    seen.add(key)

    for key in all_metric_keys:
        probe_name, metric = key.split(".", 1)
        row = [key]
        for r in results:
            val = r.per_probe_scores.get(probe_name, {}).get(metric, float("nan"))
            row.append(f"{val:.2f}" if val == val else " — ")
        lines.append(" | ".join(row))

    lines.append("")
    # Aggregates
    for r in results:
        all_scores: list[float] = []
        for scores in r.per_probe_scores.values():
            all_scores.extend(scores.values())
        avg_time = statistics.mean(r.per_probe_timing.values())
        avg_toks = int(statistics.mean(r.per_probe_total_tokens.values()))
        mean_score = statistics.mean(all_scores) if all_scores else 0.0
        lines.append(
            f"  {r.tier_name}:{r.variant_name}  "
            f"mean={mean_score:.3f}  "
            f"sys_prompt~{r.prompt_tokens_est}tok  "
            f"avg_latency={avg_time:.2f}s  "
            f"avg_total_tokens={avg_toks}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", action="append", default=None,
                        help="Tier name (repeatable); defaults to Ultra/Super/Nano")
    parser.add_argument("--variant", default="BASELINE",
                        help="Prompt variant name (BASELINE, DENSE, EXPLICIT, ...)")
    parser.add_argument("--compare", nargs="*", default=None,
                        help="Variant names to compare side-by-side")
    args = parser.parse_args()

    api_key = load_env()
    tiers = args.tier or DEFAULT_TIERS
    variants = args.compare if args.compare else [args.variant]

    results: list[RunResult] = []
    for tier in tiers:
        for variant in variants:
            print(f"→ running {tier} × {variant} ...", file=sys.stderr)
            results.append(run(tier, variant, api_key))

    print(render_table(results))


if __name__ == "__main__":
    main()
