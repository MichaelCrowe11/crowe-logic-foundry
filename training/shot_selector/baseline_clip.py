# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio | proprietary, private repository.

"""
Baseline shot-type classifier | CLIP features + logistic regression.

Purpose
-------
Turn the 2,580 labeled pairs in
``training/youtube_corpus/sw-mushrooms/training/{train,val,test}.jsonl``
into a working shot-type classifier. This is the first real model the
Studio can ship, and the floor any future fine-tune (Qwen3-VL LoRA,
CroweLM-Vision-Clip) has to beat.

Design
------
Two stages, cached independently so each is cheap to re-run:

1. Image encoding
    - Backbone: ``openai/clip-vit-base-patch32`` via HF transformers.
    - Each frame (already sampled at 1 fps and sliced to the
      shot-midpoint JPG by the dataset builder) is encoded once into
      a 512-dim feature vector.
    - Features and labels cache to
      ``models/baseline/cache/{split}.npz`` with a dataset hash so the
      cache invalidates automatically if the JSONL content changes.

2. Classifier head
    - ``LogisticRegression(class_weight="balanced")`` on top of CLIP
      features. Balanced weighting compensates for the known imbalance
      (process_work 608 vs grow_tent 129 vs timelapse 0).
    - Saved as ``models/baseline/model.joblib`` plus a label index.

Evaluation
----------
Reports top-1 accuracy, per-class precision/recall/F1, and a confusion
matrix on both val and test splits. Output lands at
``models/baseline/baseline_report.md`` next to the artifact.

Inference helper
----------------
``classify_frame(image_path)`` loads the saved artifact lazily and
returns ``(label, confidence, per_label_probabilities)`` so the
director loop can import it without re-running training.

Run
---
::

    .venv/bin/python -m training.shot_selector.baseline_clip \\
        --corpus southwest-mushrooms-yt

Flags
-----
``--no-cache``      rebuild feature cache even if it exists
``--limit N``       cap examples per split (smoke-test mode)
``--device auto``   cpu | cuda | mps | auto
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from training.shot_selector.train_vision_clip import (
    DEFAULT_CONFIG_PATH,
    load_corpus_paths,
    load_taxonomy,
)


# ────────────────────────────────────────────────────────────────────────
# Constants

MODEL_ID_DEFAULT = "openai/clip-vit-base-patch32"
SPLITS = ("train", "val", "test")
BATCH_SIZE = 32
ARTIFACT_SUBDIR = "models/baseline"
CACHE_SUBDIR = "cache"


# ────────────────────────────────────────────────────────────────────────
# Dataset loading from the JSONL split files

@dataclass
class Example:
    image_path: Path
    label: str
    video_id: str


def _records_from_jsonl(path: Path) -> list[Example]:
    out: list[Example] = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            # Chat format: user.content[0] is the image, assistant.content is the label.
            image_rel = rec["messages"][0]["content"][0]["image"]
            label = rec["messages"][-1]["content"]
            meta = rec.get("meta", {})
            out.append(Example(
                image_path=Path(image_rel),
                label=label,
                video_id=meta.get("video_id", ""),
            ))
    return out


def _dataset_fingerprint(jsonl_path: Path, model_id: str) -> str:
    """Hash the JSONL content + the backbone id so the feature cache
    invalidates if either changes. Streaming hash keeps memory bounded
    for large corpora."""
    h = hashlib.sha256()
    h.update(model_id.encode())
    h.update(b"\n")
    with jsonl_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# ────────────────────────────────────────────────────────────────────────
# Device selection

def _resolve_device(pref: str) -> str:
    import torch
    if pref != "auto":
        return pref
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


# ────────────────────────────────────────────────────────────────────────
# CLIP feature encoding

def _image_embed(model, inputs):
    """Return the projected image embedding tensor across transformers
    versions. In <5.0 ``get_image_features`` returned a plain tensor.
    In 5.x it returns a ``BaseModelOutputWithPooling`` whose
    ``pooler_output`` is already the projected embedding (same dim as
    ``model.visual_projection.out_features``). Older structures expose
    the projection under ``image_embeds``. Fall back to an explicit
    ``visual_projection`` pass only if the pooler output still has the
    pre-projection hidden size."""
    import torch
    out = model.get_image_features(**inputs)
    if torch.is_tensor(out):
        return out
    if hasattr(out, "image_embeds") and out.image_embeds is not None:
        return out.image_embeds
    if hasattr(out, "pooler_output"):
        pooled = out.pooler_output
        proj = getattr(model, "visual_projection", None)
        if proj is None:
            return pooled
        target_dim = proj.in_features
        if pooled.shape[-1] == target_dim:
            return proj(pooled)
        # pooler_output is already the projected embedding.
        return pooled
    raise TypeError(f"unexpected CLIP image-feature output type: {type(out)}")


def _encode_split(
    examples: list[Example],
    model,
    processor,
    device: str,
    desc: str,
) -> np.ndarray:
    """Encode a list of Examples into an (N, D) float32 matrix. Skips
    any example whose image has vanished, which should never happen
    because the dataset builder already filtered missing frames."""
    import torch
    from PIL import Image

    feats: list[np.ndarray] = []
    total = len(examples)
    t0 = time.time()
    with torch.no_grad():
        for start in range(0, total, BATCH_SIZE):
            batch = examples[start:start + BATCH_SIZE]
            images = [Image.open(ex.image_path).convert("RGB") for ex in batch]
            inputs = processor(images=images, return_tensors="pt").to(device)
            emb = _image_embed(model, inputs)
            emb = emb / emb.norm(dim=-1, keepdim=True)
            feats.append(emb.detach().to("cpu", dtype=torch.float32).numpy())
            done = min(start + BATCH_SIZE, total)
            if done % (BATCH_SIZE * 4) == 0 or done == total:
                rate = done / max(time.time() - t0, 1e-6)
                print(f"  {desc}: {done}/{total}  ({rate:.1f} img/s)", flush=True)
    if not feats:
        return np.zeros((0, 512), dtype=np.float32)
    return np.concatenate(feats, axis=0)


def _load_backbone(model_id: str, device: str):
    from transformers import CLIPModel, CLIPProcessor
    print(f"loading backbone: {model_id} on {device}", flush=True)
    model = CLIPModel.from_pretrained(model_id).to(device).eval()
    processor = CLIPProcessor.from_pretrained(model_id)
    return model, processor


def _features_for_split(
    split: str,
    jsonl_path: Path,
    cache_dir: Path,
    model_id: str,
    device: str,
    backbone,
    use_cache: bool,
    limit: int | None,
) -> tuple[np.ndarray, list[str], list[str]]:
    """Return (features, labels, video_ids). Uses cache if the dataset
    fingerprint matches; otherwise re-encodes and writes a fresh cache.
    ``backbone`` is a ``(model, processor)`` tuple built lazily by the
    caller so we only load CLIP when at least one split needs it."""
    cache_path = cache_dir / f"{split}.npz"
    fingerprint = _dataset_fingerprint(jsonl_path, model_id)
    examples = _records_from_jsonl(jsonl_path)
    if limit:
        examples = examples[:limit]

    if use_cache and cache_path.exists():
        cached = np.load(cache_path, allow_pickle=True)
        if str(cached.get("fingerprint", "")) == fingerprint and len(cached["labels"]) == len(examples):
            print(f"cache hit:  {split}  N={len(examples)}", flush=True)
            return (
                cached["features"].astype(np.float32),
                list(cached["labels"]),
                list(cached["video_ids"]),
            )

    if backbone[0] is None:
        backbone[0], backbone[1] = _load_backbone(model_id, device)
    model, processor = backbone

    print(f"encoding:   {split}  N={len(examples)}", flush=True)
    features = _encode_split(examples, model, processor, device, desc=split)
    labels = [ex.label for ex in examples]
    video_ids = [ex.video_id for ex in examples]

    cache_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        cache_path,
        features=features,
        labels=np.array(labels),
        video_ids=np.array(video_ids),
        fingerprint=fingerprint,
    )
    return features, labels, video_ids


# ────────────────────────────────────────────────────────────────────────
# Classifier fit + eval

def _fit_logreg(X: np.ndarray, y: list[str], class_order: list[str]):
    from sklearn.linear_model import LogisticRegression
    # Restrict to classes actually present so LogisticRegression does not
    # index into absent labels (sklearn uses its own sorted unique order).
    present = sorted(set(y))
    # sklearn >=1.5 auto-selects multinomial for multiclass; the
    # ``multi_class`` kwarg is deprecated, so we omit it.
    clf = LogisticRegression(
        max_iter=2000,
        class_weight="balanced",
        solver="lbfgs",
        C=1.0,
    )
    clf.fit(X, y)
    return clf, present


def _evaluate(
    clf,
    X: np.ndarray,
    y_true: list[str],
    split_name: str,
    all_labels: list[str],
) -> dict[str, Any]:
    from sklearn.metrics import (
        accuracy_score,
        classification_report,
        confusion_matrix,
    )
    y_pred = clf.predict(X)
    acc = accuracy_score(y_true, y_pred)
    # Restrict to labels present in either y_true or y_pred so the report
    # does not warn about labels with zero support. Keep taxonomy order.
    labels_used = [lbl for lbl in all_labels if lbl in set(y_true) | set(y_pred)]
    report = classification_report(
        y_true, y_pred, labels=labels_used, digits=3, zero_division=0,
        output_dict=True,
    )
    cm = confusion_matrix(y_true, y_pred, labels=labels_used)
    return {
        "split": split_name,
        "n": int(len(y_true)),
        "accuracy": float(acc),
        "labels": labels_used,
        "classification_report": report,
        "confusion_matrix": cm.tolist(),
    }


# ────────────────────────────────────────────────────────────────────────
# Reporting

def _render_report(
    model_id: str,
    device: str,
    train_n: int,
    evals: list[dict[str, Any]],
    out_path: Path,
) -> None:
    lines: list[str] = []
    lines.append("# Crowe Studio | Baseline Shot-Type Classifier")
    lines.append("")
    lines.append("Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.")
    lines.append("")
    lines.append(f"- Backbone: `{model_id}`")
    lines.append("- Head:     LogisticRegression(class_weight='balanced', C=1.0)")
    lines.append(f"- Device:   {device}")
    lines.append(f"- Train N:  {train_n}")
    lines.append("")

    for ev in evals:
        lines.append(f"## {ev['split']} (N={ev['n']})")
        lines.append("")
        lines.append(f"Top-1 accuracy: **{ev['accuracy']:.3f}**")
        lines.append("")
        lines.append("| label | precision | recall | f1 | support |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for label in ev["labels"]:
            r = ev["classification_report"].get(label, {})
            lines.append(
                f"| {label} | {r.get('precision', 0):.3f} | "
                f"{r.get('recall', 0):.3f} | {r.get('f1-score', 0):.3f} | "
                f"{int(r.get('support', 0))} |"
            )
        macro = ev["classification_report"].get("macro avg", {})
        weighted = ev["classification_report"].get("weighted avg", {})
        lines.append(f"| **macro avg** | {macro.get('precision',0):.3f} | {macro.get('recall',0):.3f} | {macro.get('f1-score',0):.3f} | {int(macro.get('support',0))} |")
        lines.append(f"| **weighted avg** | {weighted.get('precision',0):.3f} | {weighted.get('recall',0):.3f} | {weighted.get('f1-score',0):.3f} | {int(weighted.get('support',0))} |")
        lines.append("")
        lines.append("Confusion matrix (rows = actual, cols = predicted):")
        lines.append("")
        header = "| actual \\\\ pred | " + " | ".join(ev["labels"]) + " |"
        sep = "| --- |" + " ---: |" * len(ev["labels"])
        lines.append(header)
        lines.append(sep)
        for i, label in enumerate(ev["labels"]):
            row = ev["confusion_matrix"][i]
            lines.append(f"| {label} | " + " | ".join(str(c) for c in row) + " |")
        lines.append("")

    out_path.write_text("\n".join(lines))


# ────────────────────────────────────────────────────────────────────────
# Artifact save / load

def _save_artifact(
    out_dir: Path,
    clf,
    model_id: str,
    taxonomy: list[str],
    classes_present: list[str],
    device: str,
    evals: list[dict[str, Any]],
) -> Path:
    import joblib
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "model.joblib"
    joblib.dump(
        {
            "classifier": clf,
            "classes_present": classes_present,
            "taxonomy": taxonomy,
            "backbone_model_id": model_id,
            "device_trained_on": device,
        },
        model_path,
    )
    (out_dir / "eval_summary.json").write_text(
        json.dumps({e["split"]: {"n": e["n"], "accuracy": e["accuracy"]} for e in evals}, indent=2) + "\n"
    )
    return model_path


# ────────────────────────────────────────────────────────────────────────
# Public inference helper (used by the director loop)

_INFER_CACHE: dict[str, Any] = {}


def classify_frame(image_path: str | Path, artifact_path: str | Path | None = None) -> dict[str, Any]:
    """Classify a single frame with the baseline artifact.

    Returns ``{"label": str, "confidence": float, "probabilities":
    {label: prob, ...}}``. Lazy-loads the backbone + classifier on
    first call and caches both for subsequent calls.
    """
    import joblib
    import torch
    from PIL import Image

    artifact_path = Path(artifact_path) if artifact_path else _default_artifact_path()
    cache_key = str(artifact_path)
    bundle = _INFER_CACHE.get(cache_key)
    if bundle is None:
        data = joblib.load(artifact_path)
        device = _resolve_device("auto")
        model, processor = _load_backbone(data["backbone_model_id"], device)
        bundle = {
            "classifier": data["classifier"],
            "classes_present": data["classes_present"],
            "model": model,
            "processor": processor,
            "device": device,
        }
        _INFER_CACHE[cache_key] = bundle

    image = Image.open(image_path).convert("RGB")
    with torch.no_grad():
        inputs = bundle["processor"](images=[image], return_tensors="pt").to(bundle["device"])
        emb = _image_embed(bundle["model"], inputs)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    feats = emb.detach().to("cpu", dtype=torch.float32).numpy()

    clf = bundle["classifier"]
    probs = clf.predict_proba(feats)[0]
    classes = list(clf.classes_)
    top_idx = int(probs.argmax())
    return {
        "label": classes[top_idx],
        "confidence": float(probs[top_idx]),
        "probabilities": {classes[i]: float(probs[i]) for i in range(len(classes))},
    }


def _default_artifact_path() -> Path:
    from training.shot_selector.train_vision_clip import load_corpus_paths
    paths = load_corpus_paths("southwest-mushrooms-yt")
    return paths["target_dir"] / ARTIFACT_SUBDIR / "model.joblib"


# ────────────────────────────────────────────────────────────────────────
# Main

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[1])
    p.add_argument("--corpus", default="southwest-mushrooms-yt")
    p.add_argument("--model-id", default=MODEL_ID_DEFAULT)
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda", "mps"])
    p.add_argument("--no-cache", action="store_true", help="Force re-encoding features.")
    p.add_argument("--limit", type=int, default=None, help="Cap examples per split (smoke test).")
    p.add_argument("--training-subdir", default=None,
                   help="Subdir of target_dir holding {train,val,test}.jsonl. "
                        "Overrides the default 'training' dir.")
    p.add_argument("--artifact-subdir", default=None,
                   help="Subdir of target_dir to write the model + report into. "
                        "Overrides the default 'models/baseline'.")
    args = p.parse_args(argv)

    device = _resolve_device(args.device)

    paths = load_corpus_paths(args.corpus, DEFAULT_CONFIG_PATH)
    taxonomy = load_taxonomy(DEFAULT_CONFIG_PATH)
    if args.training_subdir:
        paths["training_dir"] = paths["target_dir"] / args.training_subdir
    artifact_subdir = args.artifact_subdir or ARTIFACT_SUBDIR
    artifact_dir = paths["target_dir"] / artifact_subdir
    cache_dir = artifact_dir / CACHE_SUBDIR

    print(f"corpus:     {args.corpus}")
    print(f"artifact:   {artifact_dir}")
    print(f"device:     {device}")
    print()

    # Lazy backbone holder (model, processor). Stays None until a cache miss forces a load.
    backbone: list[Any] = [None, None]

    features: dict[str, np.ndarray] = {}
    labels: dict[str, list[str]] = {}
    for split in SPLITS:
        jsonl = paths["training_dir"] / f"{split}.jsonl"
        if not jsonl.exists():
            print(f"SKIP {split}: missing {jsonl}")
            continue
        X, y, _vids = _features_for_split(
            split=split,
            jsonl_path=jsonl,
            cache_dir=cache_dir,
            model_id=args.model_id,
            device=device,
            backbone=backbone,
            use_cache=not args.no_cache,
            limit=args.limit,
        )
        features[split] = X
        labels[split] = y
        print(f"{split}:      features {X.shape}  labels {len(y)}", flush=True)

    if "train" not in features:
        print("FATAL: no train split", file=sys.stderr)
        return 2

    print()
    print("fitting LogisticRegression...", flush=True)
    clf, classes_present = _fit_logreg(features["train"], labels["train"], taxonomy)
    print(f"fit complete. classes: {classes_present}")

    evals: list[dict[str, Any]] = []
    for split in ("val", "test"):
        if split not in features:
            continue
        ev = _evaluate(clf, features[split], labels[split], split, taxonomy)
        evals.append(ev)
        print(f"{split:<5} accuracy = {ev['accuracy']:.3f}")

    model_path = _save_artifact(
        out_dir=artifact_dir,
        clf=clf,
        model_id=args.model_id,
        taxonomy=taxonomy,
        classes_present=classes_present,
        device=device,
        evals=evals,
    )
    report_path = artifact_dir / "baseline_report.md"
    _render_report(
        model_id=args.model_id,
        device=device,
        train_n=len(labels["train"]),
        evals=evals,
        out_path=report_path,
    )

    print()
    print(f"saved model:   {model_path}")
    print(f"saved report:  {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
