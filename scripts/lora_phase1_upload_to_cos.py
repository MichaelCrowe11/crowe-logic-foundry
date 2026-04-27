#!/usr/bin/env python3
"""
Phase 1 upload :  push the curated v3 corpus to IBM COS.

Reads from data/training/crowelm_v3_{train,val}.jsonl plus the manifest,
and writes to cos://crowe-logic-crowelm/datasets/crowelm-unified/v3/.

Single-part SigV4 PUT is used since the v3 corpus is small (sub-100MB).
The verify script already validated the local files; this script trusts
that and just pushes.

Usage:
    .venv/bin/python scripts/lora_phase1_upload_to_cos.py [--prefix v3]

Exit codes:
    0  all files uploaded
    1  required local file missing
    2  upload failure
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from config.crowelm.watsonx_adapter import _load_env

DEFAULT_LOCAL_FILES = [
    "data/training/crowelm_v3_train.jsonl",
    "data/training/crowelm_v3_val.jsonl",
    "data/training/crowelm_v3_manifest.json",
]


def sign(k: bytes, m: str) -> bytes:
    return hmac.new(k, m.encode(), hashlib.sha256).digest()


def signing_key(secret: str, date_stamp: str, region: str = "us-south") -> bytes:
    k = sign(("AWS4" + secret).encode(), date_stamp)
    k = sign(k, region)
    k = sign(k, "s3")
    return sign(k, "aws4_request")


def cos_put(local: Path, key: str, env: dict) -> tuple[int, str]:
    bucket = env["COS_BUCKET"]
    endpoint = env["COS_ENDPOINT"].rstrip("/")
    host = endpoint.replace("https://", "").replace("http://", "")
    ak = env["COS_HMAC_ACCESS_KEY_ID"]
    sk = env["COS_HMAC_SECRET_ACCESS_KEY"]

    payload = local.read_bytes()
    payload_hash = hashlib.sha256(payload).hexdigest()
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    canonical_uri = f"/{bucket}/{key}"
    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join([
        "PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash,
    ])
    credential_scope = f"{date_stamp}/us-south/s3/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, credential_scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    sig = hmac.new(
        signing_key(sk, date_stamp), string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    auth = (
        f"AWS4-HMAC-SHA256 Credential={ak}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={sig}"
    )
    url = f"{endpoint}/{bucket}/{key}"
    req = urllib.request.Request(url, data=payload, method="PUT", headers={
        "Authorization": auth,
        "Host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read().decode()[:300]


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", default="v3", help="Prefix under datasets/crowelm-unified/")
    args = ap.parse_args()

    env = _load_env()
    bucket = env["COS_BUCKET"]
    print(f"target: cos://{bucket}/datasets/crowelm-unified/{args.prefix}/")

    missing = [p for p in DEFAULT_LOCAL_FILES if not (ROOT / p).exists()]
    if missing:
        print("FAIL: local files missing:")
        for p in missing:
            print(f"  - {p}")
        print("Run scripts/curate_crowelm_v3_local.py first.")
        return 1

    failures = 0
    for local_rel in DEFAULT_LOCAL_FILES:
        local = ROOT / local_rel
        # Strip leading "data/training/" so the COS key is just the filename
        # under the v3 prefix
        cos_key = f"datasets/crowelm-unified/{args.prefix}/{local.name}"
        size = local.stat().st_size
        print(f"  {local.name:40s}  ({fmt_size(size)}) -> {cos_key}", end="  ", flush=True)
        status, body = cos_put(local, cos_key, env)
        if 200 <= status < 300:
            print(f"[{status} OK]")
        else:
            print(f"[FAIL {status}: {body}]")
            failures += 1

    if failures:
        print(f"FAIL: {failures} upload(s) errored.")
        return 2

    print()
    print("All uploads succeeded. Cataloging:")
    print(f"  cos://{bucket}/datasets/crowelm-unified/{args.prefix}/crowelm_v3_train.jsonl")
    print(f"  cos://{bucket}/datasets/crowelm-unified/{args.prefix}/crowelm_v3_val.jsonl")
    print(f"  cos://{bucket}/datasets/crowelm-unified/{args.prefix}/crowelm_v3_manifest.json")
    print()
    print("Next: scripts/lora_phase2_submit_tuning.py (will pause for explicit user go).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
