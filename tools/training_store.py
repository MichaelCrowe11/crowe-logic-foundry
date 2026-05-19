# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
# Part of Crowe Studio — proprietary, private repository.

"""
Training store — records every shot-selection decision and user
override into JSONL files under training/shot_selector/.

These tuples are the ground truth for fine-tuning crowelm-studio. Each
row captures what the rule-based or CroweLM model picked, what the
user ultimately kept, and full script + camera context. After ~50
shoots worth of overrides we have enough to train a LoRA that makes
DeepParallel your shot model specifically.

Schema (one JSON object per line, one file per month):
  timestamp, shoot_id, edl_id, script_path, strategy,
  effective_strategy, cameras_used,
  sections: [{index, title, word_count, duration, zoom}],
  original_picks: {section_index: camera_name},  # what the model chose
  final_picks:    {section_index: camera_name},  # what shipped
  overrides:      [{section_index, from, to}],
  render_output
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


TRAINING_ROOT = Path(os.environ.get(
    "STUDIO_TRAINING_ROOT",
    str(Path(__file__).resolve().parent.parent / "training" / "shot_selector"),
))


def _ensure_root() -> None:
    TRAINING_ROOT.mkdir(parents=True, exist_ok=True)


def _current_jsonl() -> Path:
    stamp = time.strftime("%Y-%m")
    return TRAINING_ROOT / f"shots-{stamp}.jsonl"


def record_shot_selection(
    shoot_id: str,
    edl_id: str,
    strategy: str | None,
    effective_strategy: str | None,
    script_path: str,
    original_picks: dict,
    final_picks: dict,
    overrides: list,
    sections: list,
    cameras_used: list,
    render_output: str | None,
) -> str:
    """
    Append one training tuple for the Studio shot-selection model.

    :param shoot_id: Identifier of the shoot the tuple belongs to.
    :param edl_id: Identifier of the EDL (edit decision list) snapshot.
    :param strategy: Strategy name the operator asked for (may be None).
    :param effective_strategy: Strategy name the engine actually used
        after fallbacks (may be None).
    :param script_path: Path to the script/source file the shoot was cut against.
    :param original_picks: Dict mapping section id to the engine's initial
        pick (before any operator overrides).
    :param final_picks: Dict mapping section id to the final pick that was
        rendered into the cut.
    :param overrides: List of per-section override records the operator
        applied on top of the engine's initial picks.
    :param sections: List of section descriptors (id, start, end, label).
    :param cameras_used: List of camera identifiers that contributed clips.
    :param render_output: Path to the final rendered output (may be None
        if the tuple was logged before render).
    :return: Filesystem path the tuple was appended to (monthly JSONL).
    :rtype: str
    """
    _ensure_root()
    tuple_ = {
        "timestamp": time.time(),
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "shoot_id": shoot_id,
        "edl_id": edl_id,
        "strategy": strategy,
        "effective_strategy": effective_strategy,
        "script_path": script_path,
        "cameras_used": cameras_used,
        "sections": [
            {
                "index": s.get("index"),
                "title": s.get("title"),
                "zoom": s.get("zoom"),
                "source_start": s.get("source_start"),
                "source_end": s.get("source_end"),
                "duration": s.get("duration"),
            }
            for s in sections
        ],
        "original_picks": {str(k): v for k, v in original_picks.items()},
        "final_picks": {str(k): v for k, v in final_picks.items()},
        "overrides": overrides,
        "render_output": render_output,
    }
    path = _current_jsonl()
    with path.open("a") as f:
        f.write(json.dumps(tuple_) + "\n")
    return str(path)


def training_stats() -> str:
    """
    Summary stats across all training tuples. Shown in the dashboard
    so Michael knows when the dataset is ripe for fine-tuning.

    :return: JSON with {count, files, oldest, newest, override_rate,
        top_overrides: [{from, to, count}]}.
    :rtype: str
    """
    _ensure_root()
    count = 0
    override_rows = 0
    oldest = None
    newest = None
    files = 0
    cam_overrides: dict[tuple[str, str], int] = {}

    for p in sorted(TRAINING_ROOT.glob("shots-*.jsonl")):
        files += 1
        with p.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                count += 1
                ts = d.get("timestamp", 0)
                if oldest is None or ts < oldest:
                    oldest = ts
                if newest is None or ts > newest:
                    newest = ts
                if d.get("overrides"):
                    override_rows += 1
                    for o in d["overrides"]:
                        key = (o.get("from", "?"), o.get("to", "?"))
                        cam_overrides[key] = cam_overrides.get(key, 0) + 1

    top = sorted(
        ({"from": k[0], "to": k[1], "count": v} for k, v in cam_overrides.items()),
        key=lambda x: x["count"], reverse=True,
    )[:10]

    return json.dumps({
        "count": count,
        "files": files,
        "oldest": oldest,
        "newest": newest,
        "override_rate": (override_rows / count) if count else 0,
        "top_overrides": top,
        "root": str(TRAINING_ROOT),
        "ready_for_finetune": count >= 50,
    })


def export_finetune_jsonl(output_path: str, format: str = "openai") -> str:
    """
    Export all collected tuples into a format-specific JSONL for
    downstream fine-tuning. Currently supports:
      - "openai": {messages: [system, user, assistant]} chat format
      - "raw": the original tuple rows (passthrough)

    :param output_path: Destination .jsonl file.
    :param format: "openai" or "raw".
    :return: JSON with {output, count, skipped}.
    :rtype: str
    """
    _ensure_root()
    count = 0
    skipped = 0
    with open(output_path, "w") as out:
        for p in sorted(TRAINING_ROOT.glob("shots-*.jsonl")):
            with p.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        skipped += 1
                        continue

                    if format == "raw":
                        out.write(line + "\n")
                        count += 1
                        continue

                    # OpenAI chat format. System describes the task,
                    # user provides script + cameras, assistant provides
                    # the final picks (post-override = ground truth).
                    system = (
                        "You are CroweLM, the shot-selection brain of Crowe Studio. "
                        "For each script section, pick the single best camera from the "
                        "available list. Return strict JSON: a list of "
                        "{section_index, camera, reason}."
                    )
                    user_payload = {
                        "available_cameras": d.get("cameras_used", []),
                        "script_sections": d.get("sections", []),
                    }
                    assistant = {
                        "picks": [
                            {"section_index": int(k), "camera": v, "reason": ""}
                            for k, v in (d.get("final_picks") or {}).items()
                        ]
                    }
                    out.write(json.dumps({
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": json.dumps(user_payload)},
                            {"role": "assistant", "content": json.dumps(assistant)},
                        ]
                    }) + "\n")
                    count += 1

    return json.dumps({
        "output": output_path,
        "count": count,
        "skipped": skipped,
        "format": format,
    })
