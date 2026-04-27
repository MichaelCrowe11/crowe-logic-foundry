#!/usr/bin/env python3
"""
CroweLM v3 corpus curation :  synthesize a tuning corpus from local sources.

Sources:
  1. 9 hand-curated mycology Q&A pairs (the seed in COS curated_export.jsonl)
  2. mycology_deep_research_report.md
  3. Lions-Mane-Cultivation-SOP.pdf (in Dropbox)
  4. Mycelial_Nexus_SOP_and_Cultivation_Matrix.docx (in Dropbox)

For prose sources, gpt-4o-mini is asked to produce 3-5 instruction/response
pairs per chunk in the schema:
    {"input": "<question>", "output": "<answer>"}

Output:
  data/training/crowelm_v3_train.jsonl
  data/training/crowelm_v3_val.jsonl
  data/training/crowelm_v3_manifest.json

Usage:
    .venv/bin/python scripts/curate_crowelm_v3_local.py [--target-size 500] [--dry-run]

Cost target: roughly $0.30-0.50 against gpt-4o-mini.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

# OpenAI key lives in foundry's .env file (loaded ad-hoc to keep this script
# free of dependencies on the rest of the foundry).
ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
AZURE_API_KEY = os.environ.get("AZURE_CORE_API_KEY", "") or os.environ.get("AZURE_AI_API_KEY", "")
AZURE_ENDPOINT = os.environ.get("AZURE_CORE_ENDPOINT", "") or os.environ.get("AZURE_AI_ENDPOINT", "")

if OPENAI_API_KEY:
    SYNTH_BACKEND = "openai"
elif AZURE_API_KEY and AZURE_ENDPOINT:
    SYNTH_BACKEND = "azure"
else:
    print("FAIL: need either OPENAI_API_KEY or AZURE_CORE_{API_KEY,ENDPOINT}")
    print("Hint: pull Azure key via")
    print("  AZ_KEY=$(az cognitiveservices account keys list --name crowelogicos-4667-resource \\")
    print("              --resource-group rg-crowelogicos-4667 --query key1 -o tsv)")
    print("  AZ_EP='https://crowelogicos-4667-resource.openai.azure.com/'")
    print("  AZURE_CORE_API_KEY=$AZ_KEY AZURE_CORE_ENDPOINT=$AZ_EP \\")
    print("    .venv/bin/python scripts/curate_crowelm_v3_local.py")
    sys.exit(1)

OUT_DIR = ROOT / "data" / "training"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DROPBOX = Path("/Users/crowelogic/Library/CloudStorage/Dropbox")
SOURCES = {
    "research_report": Path("/Users/crowelogic/mycology_deep_research_report.md"),
    "lions_mane_sop": DROPBOX / "Southwest Mushrooms" / "Digital Products" / "Lions Mane SOP" / "Lions-Mane-Cultivation-SOP.pdf",
    "mycelial_nexus_sop": DROPBOX / "Mycelial_Nexus_SOP_and_Cultivation_Matrix.docx",
}

CHUNK_CHARS = 2400
CHUNK_OVERLAP = 200
SYNTH_MODEL = "gpt-4o-mini"
SYNTH_PROMPT = """You are creating a high-quality fine-tuning dataset for a Crowe Logic AI model that helps commercial mushroom cultivators.

Read the source text below and produce 3-5 distinct instruction/response pairs in JSON Lines format. Each pair should:
- Ask a precise question a working cultivator would actually ask
- Answer with operationally specific information drawn from the source (numbers, ratios, temperatures, timeframes, troubleshooting steps)
- Use the same direct, calibrated voice as the rest of Crowe Logic content (no fluff, no marketing tone, no caveats stacking)
- Output Markdown formatting in the response when it improves clarity (bullets, bold for parameters)

Format: one JSON object per line. Schema: {"input": "<question>", "output": "<answer>"}

Do not output anything other than the JSONL lines. No preamble, no commentary, no code fences.

Source text:
---
%s
---"""


@dataclass
class Pair:
    input: str
    output: str
    source: str

    def fingerprint(self) -> str:
        # Hash the first 80 chars of the question for dedup
        return hashlib.sha1(self.input.lower().strip()[:80].encode()).hexdigest()[:16]


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> Iterator[str]:
    text = text.strip()
    if len(text) <= size:
        yield text
        return
    i = 0
    while i < len(text):
        chunk = text[i : i + size]
        # Try to break at paragraph boundary if possible
        if i + size < len(text):
            last_para = chunk.rfind("\n\n")
            if last_para > size // 2:
                chunk = chunk[:last_para]
                i += last_para
            else:
                i += size - overlap
        else:
            i += size
        yield chunk.strip()


def extract_md(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if t.strip():
            parts.append(t)
    return "\n\n".join(parts)


def extract_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _build_synth_client():
    if SYNTH_BACKEND == "openai":
        from openai import OpenAI
        return OpenAI(api_key=OPENAI_API_KEY), SYNTH_MODEL
    # Azure path. Endpoint shape can be either
    #   https://<acct>.openai.azure.com/openai/v1/   (modern OpenAI-compat)
    # or https://<acct>.openai.azure.com/             (classic Azure OpenAI)
    # The OpenAI SDK's OpenAI() class with explicit base_url speaks both.
    from openai import OpenAI
    base = AZURE_ENDPOINT.rstrip("/")
    if not base.endswith("/openai/v1"):
        base = base + "/openai/v1" if not base.endswith("/openai") else base + "/v1"
    client = OpenAI(api_key=AZURE_API_KEY, base_url=base, default_headers={"api-key": AZURE_API_KEY})
    # On Azure, the deployment NAME (not the upstream model id) is what the API
    # routes on. The 4667 account has gpt-5.4-nano deployed :  same model behind
    # CroweLM Kernel today. Using nano keeps cost low and is the model we'd
    # ultimately be replacing with the Granite LoRA, so the synthesis style is
    # already aligned to "what Crowe sounds like."
    return client, "gpt-5.4-nano"


def call_openai_synth(chunk: str, source_label: str, dry_run: bool) -> list[Pair]:
    if dry_run:
        return []
    client, model_name = _build_synth_client()
    prompt = SYNTH_PROMPT % chunk
    # Reasoning-tuned models (gpt-5.4-*) require max_completion_tokens and
    # reject custom temperature. Older completion models still accept
    # max_tokens. Try the new param first; fall back if rejected.
    base_kwargs = dict(model=model_name, messages=[{"role": "user", "content": prompt}])
    try:
        resp = client.chat.completions.create(**base_kwargs, max_completion_tokens=2000)
    except Exception as e1:
        msg = str(e1).lower()
        if "max_completion_tokens" in msg and ("unsupported" in msg or "not support" in msg):
            try:
                resp = client.chat.completions.create(**base_kwargs, max_tokens=1800, temperature=0.4)
            except Exception as e2:
                print(f"  [{source_label}] {SYNTH_BACKEND} error: {type(e2).__name__}: {str(e2)[:140]}")
                return []
        else:
            print(f"  [{source_label}] {SYNTH_BACKEND} error: {type(e1).__name__}: {str(e1)[:140]}")
            return []
    text = (resp.choices[0].message.content or "").strip()
    if text.startswith("```"):
        # Strip code fences if the model added them despite instructions
        text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```"))
    pairs: list[Pair] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ip = obj.get("input") or obj.get("instruction") or obj.get("question")
        op = obj.get("output") or obj.get("response") or obj.get("answer")
        if isinstance(ip, str) and isinstance(op, str) and len(ip) > 10 and len(op) > 40:
            pairs.append(Pair(input=ip.strip(), output=op.strip(), source=source_label))
    return pairs


def load_seed_pairs() -> list[Pair]:
    """The 9 pre-curated examples. Inlined to avoid an extra COS round-trip."""
    seed_jsonl = ROOT / "data" / "training" / "_seed_curated_export.jsonl"
    if not seed_jsonl.exists():
        # Pull from COS using the existing helper
        sys.path.insert(0, str(ROOT))
        from config.crowelm.watsonx_adapter import _load_env
        import hashlib as h, hmac, datetime as dt, urllib.request
        env = _load_env()
        ak, sk = env["COS_HMAC_ACCESS_KEY_ID"], env["COS_HMAC_SECRET_ACCESS_KEY"]
        bucket, endpoint = env["COS_BUCKET"], env["COS_ENDPOINT"].rstrip("/")
        host = endpoint.replace("https://", "")
        key = "datasets/crowelm-unified/curated_export.jsonl"
        now = dt.datetime.now(dt.timezone.utc)
        amz_date, date_stamp = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        empty_hash = h.sha256(b"").hexdigest()
        cr = f"GET\n/{bucket}/{key}\n\nhost:{host}\nx-amz-content-sha256:{empty_hash}\nx-amz-date:{amz_date}\n\nhost;x-amz-content-sha256;x-amz-date\n{empty_hash}"
        sts = f"AWS4-HMAC-SHA256\n{amz_date}\n{date_stamp}/us-south/s3/aws4_request\n{h.sha256(cr.encode()).hexdigest()}"
        def sign(k, m): return hmac.new(k, m.encode(), h.sha256).digest()
        ks = sign(sign(sign(sign(("AWS4" + sk).encode(), date_stamp), "us-south"), "s3"), "aws4_request")
        sig = hmac.new(ks, sts.encode(), h.sha256).hexdigest()
        auth = f"AWS4-HMAC-SHA256 Credential={ak}/{date_stamp}/us-south/s3/aws4_request, SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature={sig}"
        req = urllib.request.Request(f"{endpoint}/{bucket}/{key}",
            headers={"Authorization": auth, "Host": host, "x-amz-content-sha256": empty_hash, "x-amz-date": amz_date})
        with urllib.request.urlopen(req, timeout=30) as r:
            seed_jsonl.write_bytes(r.read())
    pairs: list[Pair] = []
    for line in seed_jsonl.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        ip = row.get("instruction") or row.get("input")
        op = row.get("response") or row.get("output")
        if ip and op:
            pairs.append(Pair(input=ip, output=op, source="seed_curated"))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-size", type=int, default=500,
                    help="Target number of synthesized pairs (seed pairs are added on top).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Skip OpenAI calls; just inventory chunks and report cost estimate.")
    args = ap.parse_args()

    print("=" * 72)
    print("CroweLM v3 corpus curation")
    print("=" * 72)
    print(f"target synth pairs: {args.target_size}")
    print(f"dry run: {args.dry_run}")
    print()

    # 1. Seed (9 hand-curated)
    seed_pairs = load_seed_pairs()
    print(f"seed pairs loaded: {len(seed_pairs)}")

    # 2. Extract source text
    extracted: dict[str, str] = {}
    for label, path in SOURCES.items():
        if not path.exists():
            print(f"  [{label}] MISSING: {path}")
            continue
        try:
            if path.suffix == ".md":
                text = extract_md(path)
            elif path.suffix.lower() == ".pdf":
                text = extract_pdf(path)
            elif path.suffix.lower() == ".docx":
                text = extract_docx(path)
            else:
                continue
            extracted[label] = text
            print(f"  [{label}] {len(text):,} chars from {path.name}")
        except Exception as e:
            print(f"  [{label}] extract failed: {e}")
    print()

    # 3. Chunk
    all_chunks: list[tuple[str, str]] = []
    for label, text in extracted.items():
        chunks = list(chunk_text(text))
        all_chunks.extend((label, c) for c in chunks if len(c) > 400)
    print(f"total chunks: {len(all_chunks)}")
    if not all_chunks:
        print("FAIL: no usable source content extracted.")
        return 1

    # Estimated cost: each chunk ~ 2400 chars input + 1800 tokens output
    est_input_tokens = len(all_chunks) * 700  # ~700 tokens per 2400-char chunk
    est_output_tokens = min(args.target_size, len(all_chunks) * 4) * 350
    est_cost = (est_input_tokens / 1_000_000) * 0.15 + (est_output_tokens / 1_000_000) * 0.60
    print(f"estimated cost: ~${est_cost:.2f} ({est_input_tokens:,} in / {est_output_tokens:,} out tokens)")
    print()

    if args.dry_run:
        print("DRY RUN: would synthesize but stopping here.")
        return 0

    # 4. Synthesize
    synth_pairs: list[Pair] = []
    seen_fps: set[str] = {p.fingerprint() for p in seed_pairs}
    chunks_used = 0
    for label, chunk in all_chunks:
        if len(synth_pairs) >= args.target_size:
            break
        chunks_used += 1
        new = call_openai_synth(chunk, label, dry_run=False)
        added = 0
        for p in new:
            fp = p.fingerprint()
            if fp not in seen_fps:
                seen_fps.add(fp)
                synth_pairs.append(p)
                added += 1
        print(f"  [{label}] chunk {chunks_used}/{len(all_chunks)}: +{added} new (running: {len(synth_pairs)}/{args.target_size})")

    # 5. Combine + split 90/10
    all_pairs = seed_pairs + synth_pairs
    print()
    print(f"total pairs: {len(all_pairs)} (seed={len(seed_pairs)}, synth={len(synth_pairs)})")

    # Stable split: hash-based, deterministic
    train, val = [], []
    for p in all_pairs:
        if int(p.fingerprint(), 16) % 10 == 0:
            val.append(p)
        else:
            train.append(p)
    print(f"split: train={len(train)} val={len(val)} (target ~90/10)")

    # 6. Write
    train_path = OUT_DIR / "crowelm_v3_train.jsonl"
    val_path = OUT_DIR / "crowelm_v3_val.jsonl"
    manifest_path = OUT_DIR / "crowelm_v3_manifest.json"

    with train_path.open("w") as f:
        for p in train:
            f.write(json.dumps({"input": p.input, "output": p.output}) + "\n")
    with val_path.open("w") as f:
        for p in val:
            f.write(json.dumps({"input": p.input, "output": p.output}) + "\n")

    manifest = {
        "version": "v3",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "creator": "Crowe Logic, Inc.",
        "target_brand": "crowelm-prime",
        "base_model": "ibm/granite-4-h-small",
        "synth_model": SYNTH_MODEL,
        "total_samples": len(all_pairs),
        "train_samples": len(train),
        "validation_samples": len(val),
        "source_breakdown": {
            "seed_curated": sum(1 for p in all_pairs if p.source == "seed_curated"),
            **{label: sum(1 for p in all_pairs if p.source == label) for label in extracted},
        },
        "files": {
            "train": str(train_path.relative_to(ROOT)),
            "validation": str(val_path.relative_to(ROOT)),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print()
    print("=" * 72)
    print("DONE")
    print("=" * 72)
    print(f"train:    {train_path}")
    print(f"val:      {val_path}")
    print(f"manifest: {manifest_path}")
    print()
    print("Next: scripts/lora_phase1_upload_to_cos.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
