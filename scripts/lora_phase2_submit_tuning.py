#!/usr/bin/env python3
"""
Phase 2 :  submit a watsonx prompt-tuning experiment for crowelm-prime.

Bypasses the Knowledge Catalog blocker by using training_data_references of
type "container" (the project's default COS storage), which is the same
crowe-logic-crowelm bucket we just uploaded the v3 corpus into.

Submits to /ml/v1/trainings on https://us-south.ml.cloud.ibm.com.

The watsonx prompt-tuning request schema:
    https://cloud.ibm.com/apidocs/watsonx-ai#trainings-create

Usage:
    .venv/bin/python scripts/lora_phase2_submit_tuning.py [--dry-run] [--submit]

By default this script runs a DRY-RUN: prints the full request body and stops.
Pass --submit to actually create the training job (this incurs cost).

Cost: prompt-tuning a small base model (Granite 4 H Small) on ~300 examples
typically runs $5-30 depending on epochs. Far cheaper than the original
$30-100 estimate that assumed a 145k-sample corpus.

Exit codes:
    0  job submitted (or dry-run printed) successfully
    1  prereq missing
    2  watsonx API error
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from config.crowelm.watsonx_adapter import _load_env, get_iam_token, _wx_url, WatsonxError
from config.crowelm.brand_registry import resolve

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


def build_payload(brand_id: str, env: dict, num_epochs: int = 6) -> dict:
    """Build a /ml/v1/trainings request body using container-type refs.

    Uses the project's default COS bucket (which is the same
    crowe-logic-crowelm bucket the v3 upload landed in) so we don't need
    a registered data_asset.
    """
    brand = resolve(brand_id)
    if brand is None:
        raise ValueError(f"unknown brand: {brand_id}")
    if not brand.tunable:
        raise ValueError(f"brand {brand_id} is not marked tunable in registry")

    project_id = env["WATSONX_PROJECT_ID"]
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%S")

    return {
        "name": f"crowelm-{brand_id}-v3-{stamp}",
        "description": (
            f"Prompt-tune {brand.base_model} on the v3 CroweLM corpus "
            "(339 examples: 9 hand-curated mycology Q&A + 330 synthesized "
            "from Lions Mane SOP, mycology research report, and Mycelial "
            "Nexus SOP)."
        ),
        "tags": ["crowelm-prime", "v3", "lora"],
        "project_id": project_id,
        "results_reference": {
            "type": "container",
            "name": "trainings",
            "location": {
                "path": f"datasets/trainings/crowelm-{brand_id}-v3-{stamp}/",
            },
        },
        "training_data_references": [
            {
                "type": "container",
                "location": {
                    "path": "datasets/crowelm-unified/v3/crowelm_v3_train.jsonl",
                },
                "schema": {
                    "id": "crowelm_v3_train",
                    "fields": [
                        {"name": "input", "type": "string"},
                        {"name": "output", "type": "string"},
                    ],
                    "type": "struct",
                },
            }
        ],
        "prompt_tuning": {
            "base_model": {"model_id": brand.base_model},
            "task_id": "generation",
            "tuning_type": "prompt_tuning",
            "num_epochs": num_epochs,
            "learning_rate": 0.3,
            "accumulate_steps": 16,
            "batch_size": 8,
            "max_input_tokens": 256,
            "max_output_tokens": 512,
            "init_method": "random",
            "verbalizer": "Input: {{input}}\nOutput:",
        },
    }


def submit_training(payload: dict, env: dict) -> dict:
    token = get_iam_token(env)
    # /ml/v4/trainings is the current path; /ml/v1/trainings returns nginx 404
    # in this region. The legacy tuning_pipeline.py predates this migration.
    base = env.get("WATSONX_URL", "https://us-south.ml.cloud.ibm.com").rstrip("/")
    url = f"{base}/ml/v4/trainings?version=2024-09-16"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as ex:
        body_text = ex.read().decode()
        raise WatsonxError(f"HTTP {ex.code}: {body_text[:600]}") from ex


def confirm_corpus_in_cos(env: dict) -> bool:
    """Quick HEAD check that the v3 train file is reachable via the project bucket."""
    import hashlib
    import hmac

    bucket = env["COS_BUCKET"]
    endpoint = env["COS_ENDPOINT"].rstrip("/")
    host = endpoint.replace("https://", "")
    ak = env["COS_HMAC_ACCESS_KEY_ID"]
    sk = env["COS_HMAC_SECRET_ACCESS_KEY"]
    key = "datasets/crowelm-unified/v3/crowelm_v3_train.jsonl"
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    empty_hash = hashlib.sha256(b"").hexdigest()
    cr = (
        f"HEAD\n/{bucket}/{key}\n\n"
        f"host:{host}\nx-amz-content-sha256:{empty_hash}\nx-amz-date:{amz_date}\n\n"
        "host;x-amz-content-sha256;x-amz-date\n"
        f"{empty_hash}"
    )
    sts = (
        f"AWS4-HMAC-SHA256\n{amz_date}\n"
        f"{date_stamp}/us-south/s3/aws4_request\n"
        f"{hashlib.sha256(cr.encode()).hexdigest()}"
    )

    def sign(k, m):
        return hmac.new(k, m.encode(), hashlib.sha256).digest()

    ks = sign(sign(sign(sign(("AWS4" + sk).encode(), date_stamp), "us-south"), "s3"), "aws4_request")
    sig = hmac.new(ks, sts.encode(), hashlib.sha256).hexdigest()
    auth = (
        f"AWS4-HMAC-SHA256 Credential={ak}/{date_stamp}/us-south/s3/aws4_request, "
        f"SignedHeaders=host;x-amz-content-sha256;x-amz-date, Signature={sig}"
    )
    req = urllib.request.Request(
        f"{endpoint}/{bucket}/{key}",
        method="HEAD",
        headers={
            "Authorization": auth,
            "Host": host,
            "x-amz-content-sha256": empty_hash,
            "x-amz-date": amz_date,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except urllib.error.HTTPError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="crowelm-prime")
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true",
                    help="(default) Print the request body but do not submit.")
    ap.add_argument("--submit", action="store_true",
                    help="Actually submit the training job. Costs money.")
    args = ap.parse_args()

    if args.submit and args.dry_run:
        print("FAIL: --dry-run and --submit are mutually exclusive")
        return 1
    if not args.submit:
        args.dry_run = True  # default

    print("=" * 72)
    print(f"CroweLM Phase 2 :  prompt-tune {args.brand}")
    print("=" * 72)

    env = _load_env()
    print(f"watsonx project: {env['WATSONX_PROJECT_ID'][:8]}...")
    print(f"region:          {env.get('WATSONX_REGION')}")
    print(f"COS bucket:      {env['COS_BUCKET']}")
    print()

    print("Pre-flight: confirm v3 train corpus is in COS...", end=" ", flush=True)
    if confirm_corpus_in_cos(env):
        print("ok")
    else:
        print("MISSING")
        print("FAIL: Run scripts/lora_phase1_upload_to_cos.py first.")
        return 1
    print()

    payload = build_payload(args.brand, env, num_epochs=args.epochs)
    print("Request body preview:")
    print(json.dumps(payload, indent=2))
    print()

    if args.dry_run:
        print("DRY RUN: not submitting. Pass --submit to actually create the training job.")
        print()
        print("Cost forecast: roughly $5-30 for a 339-example tuning over 6 epochs on Granite 4 H Small.")
        return 0

    print("Submitting to /ml/v1/trainings ...")
    try:
        result = submit_training(payload, env)
    except WatsonxError as e:
        print(f"FAIL: {e}")
        return 2

    print("OK :  training job created")
    print(json.dumps(result, indent=2)[:1500])
    md = result.get("metadata", {})
    print()
    print(f"training id: {md.get('id', '<none>')}")
    print(f"created:     {md.get('created_at', '<none>')}")
    print()
    print("Watch progress:")
    print(f"  https://dataplatform.cloud.ibm.com/wx/training-runs?context=wx&projectId={env['WATSONX_PROJECT_ID']}")
    print()
    print("Next: scripts/lora_phase3_deploy_and_wire.py once status=completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
