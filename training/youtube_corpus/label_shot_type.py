# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio | proprietary, private repository.

"""
Shot-type labeler | Phase 1.3 of the training pipeline. Reads each
video's info.json (produced by yt-dlp during ingest) and assigns one or
more shot_type labels from the taxonomy, with confidence and evidence
trail. Pure rules-based on title/description/tags keywords, no LLM
required, no video download required.

Why rules-first, not LLM-first:
  - Your channel titles are unusually descriptive ("Inside Our Mushroom
    Cultivation Facility", "How to Use All-American Pressure
    Sterilizers"), so simple keyword matching hits high recall on its
    own. Spending LLM tokens on the easy cases is wasteful.
  - Rules-based labels are deterministic and debuggable: every label
    carries the exact evidence string that caused it to fire.
  - LLM passes can layer on top later to resolve only the ambiguous
    cases (multi-label videos with conflicting evidence).

On-disk layout:

    <corpus>/labels/<video_id>.json    # per-video label record
    <corpus>/labels/_summary.json      # corpus-wide aggregation
    <corpus>/labels/_training.jsonl    # Phase 1.4 training pairs

Each per-video record:
    {
      "video_id":        str,
      "title":           str,
      "description":     str,
      "candidates":      [{label, confidence, evidence}, ...],
      "dominant_label":  str | None,
      "labeler_version": str
    }
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

from .ingest import CorpusSpec, DEFAULT_CONFIG_PATH, load_corpus

LABELER_VERSION = "1.0-rules"


# ────────────────────────────────────────────────────────────────────────
# Taxonomy loading

@dataclass
class ShotTypeRule:
    label: str
    keywords: list[str]
    camera_role_default: str = "close"


def load_shot_type_rules(config_path: Path = DEFAULT_CONFIG_PATH) -> list[ShotTypeRule]:
    cfg = yaml.safe_load(config_path.read_text())
    taxonomy = cfg.get("taxonomies", {}).get("shot_type", [])
    rules: list[ShotTypeRule] = []
    for entry in taxonomy:
        rules.append(ShotTypeRule(
            label=entry["label"],
            keywords=[kw.lower() for kw in entry.get("keywords", [])],
            camera_role_default=entry.get("camera_role_default", "close"),
        ))
    return rules


# ────────────────────────────────────────────────────────────────────────
# Scoring

def _corpus_text(info: dict) -> str:
    """Flatten the metadata fields that carry shot-type signal into one
    lowercase string. Weight is applied by repetition, not explicit
    scoring, so a keyword in the title counts more than in the tags."""
    parts = []
    title = info.get("title", "") or ""
    desc = info.get("description", "") or ""
    tags = info.get("tags", []) or []
    chapters = info.get("chapters", []) or []

    # Title dominates: repeat it so its keywords carry more weight.
    parts.append((title + " ") * 3)
    parts.append(desc[:1000])
    parts.append(" ".join(tags))
    parts.append(" ".join(c.get("title", "") for c in chapters if isinstance(c, dict)))
    return " ".join(parts).lower()


def _keyword_hits(text: str, keyword: str) -> int:
    """Count occurrences using word-boundary regex so 'lab' does not
    match 'labeling', but multi-word phrases still match as substrings."""
    kw = keyword.lower()
    if " " in kw or "'" in kw or "-" in kw:
        return len(re.findall(re.escape(kw), text))
    return len(re.findall(rf"\b{re.escape(kw)}\b", text))


def score_video(info: dict, rules: list[ShotTypeRule]) -> list[dict]:
    text = _corpus_text(info)
    results = []
    for rule in rules:
        hits = 0
        evidence_terms = []
        for kw in rule.keywords:
            n = _keyword_hits(text, kw)
            if n:
                hits += n
                evidence_terms.append(f"{kw!r}x{n}" if n > 1 else repr(kw))
        if hits == 0:
            continue
        # Simple confidence: saturates at 3 hits.
        confidence = min(1.0, 0.4 + 0.2 * hits)
        results.append({
            "label": rule.label,
            "confidence": round(confidence, 2),
            "hits": hits,
            "evidence": ", ".join(evidence_terms),
            "camera_role_default": rule.camera_role_default,
        })
    results.sort(key=lambda r: (-r["hits"], r["label"]))
    return results


# ────────────────────────────────────────────────────────────────────────
# Per-corpus orchestration

def label_video(info_path: Path, rules: list[ShotTypeRule]) -> dict:
    info = json.loads(info_path.read_text())
    candidates = score_video(info, rules)
    dominant = candidates[0]["label"] if candidates else None
    return {
        "video_id": info.get("id"),
        "title": info.get("title"),
        "description": (info.get("description") or "")[:300],
        "duration": info.get("duration"),
        "candidates": candidates,
        "dominant_label": dominant,
        "labeled_at": time.time(),
        "labeler_version": LABELER_VERSION,
    }


def label_corpus(corpus: CorpusSpec, *, verbose: bool = True) -> dict:
    rules = load_shot_type_rules()
    labels_dir = corpus.target_dir / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)

    info_files = sorted(corpus.videos_dir.glob("*.info.json"))
    records: list[dict] = []
    training_pairs: list[dict] = []

    for i, info_path in enumerate(info_files, 1):
        record = label_video(info_path, rules)
        out = labels_dir / f"{record['video_id']}.json"
        out.write_text(json.dumps(record, indent=2) + "\n")
        records.append(record)
        if record["dominant_label"]:
            training_pairs.append({
                "video_id": record["video_id"],
                "label": record["dominant_label"],
                "confidence": record["candidates"][0]["confidence"],
                "evidence": record["candidates"][0]["evidence"],
                "source": "metadata_rules",
            })
        if verbose:
            dom = record["dominant_label"] or "UNLABELED"
            conf = record["candidates"][0]["confidence"] if record["candidates"] else 0.0
            print(f"[{i}/{len(info_files)}] {record['video_id']}  {dom:<18} {conf:.2f}  {(record['title'] or '')[:60]}")

    # Corpus-wide summary.
    dist = Counter(r["dominant_label"] or "UNLABELED" for r in records)
    summary = {
        "corpus": corpus.name,
        "labeler_version": LABELER_VERSION,
        "labeled_at": time.time(),
        "videos_total": len(records),
        "videos_labeled": sum(1 for r in records if r["dominant_label"]),
        "videos_unlabeled": sum(1 for r in records if not r["dominant_label"]),
        "distribution": dict(dist.most_common()),
        "multi_candidate_rate": round(
            sum(1 for r in records if len(r["candidates"]) >= 2) / max(1, len(records)),
            2,
        ),
    }
    (labels_dir / "_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    jsonl_path = labels_dir / "_training.jsonl"
    with jsonl_path.open("w") as fh:
        for pair in training_pairs:
            fh.write(json.dumps(pair) + "\n")

    if verbose:
        print()
        print("=== distribution ===")
        for label, count in dist.most_common():
            bar = "#" * min(count, 50)
            print(f"  {label:<20} {count:>4}  {bar}")
        print()
        print(f"multi-candidate rate: {summary['multi_candidate_rate']:.0%}")
        print(f"training pairs written: {len(training_pairs)} -> {jsonl_path}")

    return summary


# ────────────────────────────────────────────────────────────────────────

def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Weak-label videos from info.json metadata.")
    p.add_argument("corpus")
    args = p.parse_args()
    corpus = load_corpus(args.corpus)
    label_corpus(corpus)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
