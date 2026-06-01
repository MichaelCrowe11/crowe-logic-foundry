"""Generate bench/datasets/track_b/mycology.jsonl from the cultivation corpus.

Reads pre-fetched passages from bench/datasets/track_b/_passages.jsonl
(each: {id, text, doc}), asks the pinned JUDGE_TIER to draft a grounded
question + reference answer per passage, and writes mycology.jsonl. Output is
committed so benchmark runs are reproducible. Run manually; not part of tests.
"""

from __future__ import annotations

import json

from bench import config


def build_qa_prompt(passage: str) -> str:
    return (
        "From the cultivation passage below, write ONE specific factual "
        "question a grower would ask, plus the correct answer grounded ONLY "
        "in the passage. Respond as JSON: "
        '{"question": "...", "reference_answer": "..."}\n\n'
        f"PASSAGE:\n{passage}"
    )


def main() -> int:
    from bench.headless_client import run_headless

    passages_path = config.DATASETS_DIR / "track_b" / "_passages.jsonl"
    out_path = config.DATASETS_DIR / "track_b" / "mycology.jsonl"
    if not passages_path.exists():
        print(f"no passages file at {passages_path}; nothing to generate")
        return 1
    rows = []
    for line in passages_path.read_text().splitlines():
        if not line.strip():
            continue
        p = json.loads(line)
        res = run_headless(build_qa_prompt(p["text"]), config.JUDGE_TIER, tools=False)
        try:
            qa = json.loads(
                res.answer[res.answer.index("{") : res.answer.rindex("}") + 1]
            )
        except (ValueError, json.JSONDecodeError):
            continue
        rows.append(
            {
                "id": p["id"],
                "question": qa["question"],
                "source_passage": p["text"],
                "source_doc": p.get("doc", ""),
                "reference_answer": qa["reference_answer"],
            }
        )
    out_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(f"wrote {len(rows)} questions to {out_path}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
