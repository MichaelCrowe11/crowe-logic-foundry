# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio | proprietary, private repository.

"""
Per-frame relabel pass | zero-shot CLIP over representative frames.

Why
---
The v0.8.0 baseline trained on labels inherited from each video's
dominant shot_type. Mixed-content videos (a tour that includes a
talking-head intro and a harvest cutaway) push every frame's label
toward whichever class the video as a whole was tagged with. The
classifier then has to learn through that noise. Per-class precision
shows it: ``facility_wide`` reaches val P=1.000 R=0.056 because the
model is *correctly* refusing to call mixed frames a tour, but the
labels say it should.

This script runs zero-shot CLIP on each representative frame against
text prompts derived from the taxonomy descriptions, picks the argmax
label, and writes a relabeled training corpus. Original labels survive
in ``meta.label_inherited`` for audit and diff.

Performance trick
-----------------
The v0.8.0 baseline already cached CLIP image embeddings under
``models/baseline/cache/{split}.npz``. Those features are identical to
what this script would compute, so we reuse them when the dataset
fingerprint matches. That collapses the relabel pass from ~2 minutes of
GPU work to a sub-second NumPy multiply per split. Cache miss falls
back to live encoding so the script still works on a fresh corpus.

Run
---
::

    .venv/bin/python -m training.youtube_corpus.relabel_per_frame \\
        --corpus southwest-mushrooms-yt
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from training.shot_selector.baseline_clip import (
    ARTIFACT_SUBDIR,
    CACHE_SUBDIR,
    Example,
    _dataset_fingerprint,
    _encode_split,
    _image_embed,
    _load_backbone,
    _records_from_jsonl,
    _resolve_device,
)
from training.shot_selector.train_vision_clip import (
    DEFAULT_CONFIG_PATH,
    load_corpus_paths,
)


SPLITS = ("train", "val", "test")
MODEL_ID_DEFAULT = "openai/clip-vit-base-patch32"
RELABEL_SUBDIR = "training_relabeled"


# ────────────────────────────────────────────────────────────────────────
# Taxonomy with descriptions

def _load_taxonomy_full(config_path: Path) -> list[dict[str, Any]]:
    """Return the full shot_type taxonomy entries (label + description +
    keywords) so we can build rich text prompts. ``load_taxonomy`` only
    returns label strings, which is too thin for zero-shot prompting."""
    import yaml
    cfg = yaml.safe_load(config_path.read_text())
    return cfg["taxonomies"]["shot_type"]


def _build_text_prompts(taxonomy: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Build one text prompt per class. Returns (labels, prompts).

    Prompt template wraps the taxonomy description in a domain-specific
    framing phrase so CLIP scores against "a video frame in this domain
    showing X" rather than the more abstract description alone. The
    description is collapsed to one line and trimmed because CLIP's text
    encoder caps at 77 tokens.
    """
    labels: list[str] = []
    prompts: list[str] = []
    for entry in taxonomy:
        label = entry["label"]
        desc = " ".join(entry["description"].split())  # collapse whitespace
        # Keep the prompt under ~60 tokens. The description first sentence
        # carries the visual essentials; trim to it if the full one is long.
        first = desc.split(". ")[0]
        prompt = f"A still frame from a mushroom cultivation video showing {first.lower()}."
        labels.append(label)
        prompts.append(prompt)
    return labels, prompts


def _text_embed(model, inputs):
    """Text tower companion to ``_image_embed``. In transformers 5.x,
    ``get_text_features`` returns a ``BaseModelOutputWithPooling`` whose
    ``pooler_output`` is **already projected** to the joint embedding
    space (shape matches ``text_projection.out_features``). Empirical
    check: applying ``text_projection`` a second time gives an embedding
    with cosine similarity ≈ 0.23 to the real one, so the "dim equals
    projection in_features" heuristic used by ``_image_embed`` is unsafe
    here because text's pre-projection hidden size also equals 512.

    Fall back to re-projecting only when a ``text_embeds`` attribute is
    explicitly absent and the pooler dim looks like the encoder hidden
    size of a model other than ViT-B/32. Otherwise trust pooler_output."""
    import torch
    out = model.get_text_features(**inputs)
    if torch.is_tensor(out):
        return out
    if hasattr(out, "text_embeds") and out.text_embeds is not None:
        return out.text_embeds
    if hasattr(out, "pooler_output"):
        return out.pooler_output
    raise TypeError(f"unexpected CLIP text-feature output type: {type(out)}")


def _encode_text_prompts(prompts: list[str], model, processor, device: str) -> np.ndarray:
    """Encode the per-class prompts and L2-normalize. Returns (C, D) float32."""
    import torch
    with torch.no_grad():
        inputs = processor(text=prompts, return_tensors="pt", padding=True).to(device)
        feats = _text_embed(model, inputs)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats.detach().to("cpu", dtype=torch.float32).numpy()


# ────────────────────────────────────────────────────────────────────────
# Image features | reuse cache when possible

def _features_from_cache_or_encode(
    split: str,
    jsonl_path: Path,
    cache_dir: Path,
    model_id: str,
    device: str,
    backbone_holder: list[Any],
) -> np.ndarray:
    """Try to load image features from the v0.8.0 baseline cache. If the
    cache is missing or the dataset fingerprint has drifted, re-encode
    the split from scratch using the CLIP backbone (loaded lazily)."""
    cache_path = cache_dir / f"{split}.npz"
    fingerprint = _dataset_fingerprint(jsonl_path, model_id)
    examples = _records_from_jsonl(jsonl_path)

    if cache_path.exists():
        cached = np.load(cache_path, allow_pickle=True)
        if (
            str(cached.get("fingerprint", "")) == fingerprint
            and len(cached["labels"]) == len(examples)
        ):
            return cached["features"].astype(np.float32)

    # Cache miss. Lazy-load CLIP and encode.
    if backbone_holder[0] is None:
        backbone_holder[0], backbone_holder[1] = _load_backbone(model_id, device)
    model, processor = backbone_holder
    return _encode_split(examples, model, processor, device, desc=split)


# ────────────────────────────────────────────────────────────────────────
# Relabel one split

def _relabel_split(
    split: str,
    jsonl_path: Path,
    out_path: Path,
    image_features: np.ndarray,
    text_features: np.ndarray,
    class_labels: list[str],
    min_score: float | None = None,
    min_margin: float | None = None,
) -> dict[str, Any]:
    """Score image_features against text_features, write a new JSONL.

    By default (gating off) every example's assistant content is replaced
    by the zero-shot argmax. When ``min_score`` and/or ``min_margin`` are
    set, a label is only replaced if the zero-shot top score clears the
    score floor AND beats the inherited label's score by at least
    ``min_margin``. Otherwise the inherited label is kept and the meta
    block records both the candidate replacement and why it was rejected.

    ``meta.relabel_top3`` is always written so downstream audit can spot
    the model's per-frame view independent of the gating decision.
    """
    sims = image_features @ text_features.T  # (N, C). Cosine since both unit-norm.
    top_idx = sims.argmax(axis=1)
    top_score = sims.max(axis=1)
    order = np.argsort(-sims, axis=1)[:, :3]
    label_to_idx = {lbl: i for i, lbl in enumerate(class_labels)}

    original_records = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line.strip()]
    if len(original_records) != image_features.shape[0]:
        raise RuntimeError(
            f"feature/example mismatch for {split}: "
            f"{image_features.shape[0]} feats vs {len(original_records)} records"
        )

    changed = 0
    rejected_low_score = 0
    rejected_low_margin = 0
    per_class_orig = Counter()
    per_class_new = Counter()
    transitions: Counter = Counter()
    with out_path.open("w") as fh:
        for i, rec in enumerate(original_records):
            old_label = rec["messages"][-1]["content"]
            cand_label = class_labels[int(top_idx[i])]
            cand_score = float(top_score[i])
            old_score = float(sims[i, label_to_idx[old_label]]) if old_label in label_to_idx else float("nan")
            margin = cand_score - old_score

            replace = (cand_label != old_label)
            decision_reason = None
            if replace and min_score is not None and cand_score < min_score:
                replace = False
                decision_reason = f"score<{min_score:.3f}"
                rejected_low_score += 1
            if replace and min_margin is not None and margin < min_margin:
                replace = False
                decision_reason = f"margin<{min_margin:.3f}"
                rejected_low_margin += 1

            new_label = cand_label if replace else old_label
            top3 = [
                {"label": class_labels[int(j)], "score": float(sims[i, int(j)])}
                for j in order[i]
            ]
            rec["messages"][-1]["content"] = new_label
            meta = rec.setdefault("meta", {})
            meta["label_inherited"] = old_label
            meta["relabel_source"] = "zero_shot_clip"
            meta["relabel_candidate"] = cand_label
            meta["relabel_candidate_score"] = cand_score
            meta["relabel_inherited_score"] = old_score
            meta["relabel_margin"] = margin
            meta["relabel_top3"] = top3
            if decision_reason:
                meta["relabel_rejected"] = decision_reason
            fh.write(json.dumps(rec) + "\n")
            per_class_orig[old_label] += 1
            per_class_new[new_label] += 1
            if new_label != old_label:
                changed += 1
                transitions[(old_label, new_label)] += 1

    return {
        "split": split,
        "n": len(original_records),
        "changed": changed,
        "rejected_low_score": rejected_low_score,
        "rejected_low_margin": rejected_low_margin,
        "agreement": 1.0 - (changed / max(1, len(original_records))),
        "per_class_orig": dict(per_class_orig),
        "per_class_new": dict(per_class_new),
        "top_transitions": transitions.most_common(10),
        "score_stats": {
            "mean": float(top_score.mean()),
            "p10": float(np.quantile(top_score, 0.10)),
            "p50": float(np.quantile(top_score, 0.50)),
            "p90": float(np.quantile(top_score, 0.90)),
        },
        "gating": {"min_score": min_score, "min_margin": min_margin},
    }


# ────────────────────────────────────────────────────────────────────────
# Main

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[1])
    p.add_argument("--corpus", default="southwest-mushrooms-yt")
    p.add_argument("--model-id", default=MODEL_ID_DEFAULT)
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda", "mps"])
    p.add_argument("--out-subdir", default=RELABEL_SUBDIR,
                   help="subdir of target_dir to write relabeled JSONLs into")
    p.add_argument("--min-score", type=float, default=None,
                   help="If set, only replace a label when zero-shot top score "
                        "clears this floor. Disable replacement otherwise.")
    p.add_argument("--min-margin", type=float, default=None,
                   help="If set, only replace a label when the candidate score "
                        "beats the inherited label's score by this much.")
    args = p.parse_args(argv)

    device = _resolve_device(args.device)
    paths = load_corpus_paths(args.corpus, DEFAULT_CONFIG_PATH)
    taxonomy = _load_taxonomy_full(DEFAULT_CONFIG_PATH)
    class_labels, prompts = _build_text_prompts(taxonomy)

    artifact_dir = paths["target_dir"] / ARTIFACT_SUBDIR
    cache_dir = artifact_dir / CACHE_SUBDIR
    out_dir = paths["target_dir"] / args.out_subdir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"corpus:   {args.corpus}")
    print(f"out dir:  {out_dir}")
    print(f"device:   {device}")
    print(f"classes:  {len(class_labels)} ({', '.join(class_labels)})")
    print()

    # Encode text prompts once. Loading the backbone here is cheap if we
    # also need it for cache misses below; otherwise it's the only cost.
    backbone_holder: list[Any] = [None, None]
    backbone_holder[0], backbone_holder[1] = _load_backbone(args.model_id, device)
    text_feats = _encode_text_prompts(prompts, *backbone_holder, device)
    print(f"text features: {text_feats.shape}")
    print()

    summaries: list[dict[str, Any]] = []
    for split in SPLITS:
        jsonl = paths["training_dir"] / f"{split}.jsonl"
        if not jsonl.exists():
            print(f"SKIP {split}: missing {jsonl}")
            continue
        img_feats = _features_from_cache_or_encode(
            split=split,
            jsonl_path=jsonl,
            cache_dir=cache_dir,
            model_id=args.model_id,
            device=device,
            backbone_holder=backbone_holder,
        )
        out_path = out_dir / f"{split}.jsonl"
        summary = _relabel_split(
            split=split,
            jsonl_path=jsonl,
            out_path=out_path,
            image_features=img_feats,
            text_features=text_feats,
            class_labels=class_labels,
            min_score=args.min_score,
            min_margin=args.min_margin,
        )
        summaries.append(summary)
        print(f"{split:<5}  N={summary['n']:>5}  "
              f"changed={summary['changed']:>5}  "
              f"agree={summary['agreement']:.3f}  "
              f"score p50={summary['score_stats']['p50']:.3f}")

    summary_path = out_dir / "_relabel_summary.json"
    summary_path.write_text(json.dumps(summaries, indent=2) + "\n")
    print()
    print(f"summary written: {summary_path}")

    # Pretty-print the per-class shifts on stdout for human eyeballing.
    print()
    print("per-class distribution (orig -> new):")
    all_labels = sorted({lbl for s in summaries for lbl in s["per_class_orig"]} |
                        {lbl for s in summaries for lbl in s["per_class_new"]})
    print(f"{'label':<22} | " + " | ".join(f"{s['split']:>15}" for s in summaries))
    for lbl in all_labels:
        row = f"{lbl:<22} | "
        cells = []
        for s in summaries:
            o = s["per_class_orig"].get(lbl, 0)
            n = s["per_class_new"].get(lbl, 0)
            cells.append(f"{o:>4} -> {n:>4}  ")
        row += " | ".join(cells)
        print(row)

    print()
    print("top label transitions (orig -> new : count) across all splits:")
    rollup: Counter = Counter()
    for s in summaries:
        for (a, b), c in s["top_transitions"]:
            rollup[(a, b)] += c
    for (a, b), c in rollup.most_common(15):
        print(f"  {a:<22} -> {b:<22}  {c:>5}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
