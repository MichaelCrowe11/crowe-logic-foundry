"""
CroweLM tuning pipeline — converts the curated_export.jsonl into the
watsonx.ai prompt-tuning format, uploads to COS as a versioned dataset,
registers it as a watsonx training data asset, and submits a tuning job.

Designed to be re-runnable: each invocation creates a new dated artifact
prefix so prior runs are preserved for audit.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

from .brand_registry import resolve
from .watsonx_adapter import _load_env, get_iam_token, _post, _wx_url, WatsonxError


def convert_jsonl(src: Path, dst: Path) -> int:
    """Turn instruction/response → input/output. Returns example count."""
    n = 0
    with src.open() as fin, dst.open("w") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            instr = row.get("instruction") or row.get("input") or ""
            resp = row.get("response") or row.get("output") or ""
            if not instr or not resp:
                continue
            fout.write(json.dumps({"input": instr, "output": resp}) + "\n")
            n += 1
    return n


def _upload_to_cos(local: Path, key: str, env: dict[str, str]) -> str:
    """Upload via ibm_boto3 if available; else SDK-less PUT signed with HMAC."""
    bucket = env.get("COS_BUCKET", "crowe-logic-crowelm")
    try:
        import ibm_boto3
        from ibm_botocore.client import Config
        client = ibm_boto3.client(
            "s3",
            ibm_api_key_id=env["COS_APIKEY"],
            ibm_service_instance_id=env["COS_CRN"],
            config=Config(signature_version="oauth"),
            endpoint_url="https://s3.us-south.cloud-object-storage.appdomain.cloud",
        )
        client.upload_file(str(local), bucket, key)
        return f"cos://{bucket}/{key}"
    except ImportError:
        raise WatsonxError("ibm_boto3 not installed; pip install ibm-cos-sdk")


def register_data_asset(name: str, cos_path: str, env: dict[str, str]) -> str:
    """Create a data asset in the watsonx project pointing at the COS object.
    Returns the asset_id.

    NOTE: requires IBM Knowledge Catalog plan with capacity > 0 (the Lite
    plan ``wkc-base`` is provisioned with capacity 0 and rejects asset
    creation with HTTP 403 ``entitlement_enforcement``). Upgrade to a paid
    Knowledge Catalog tier in the watsonx UI before invoking this without
    --dry-run.
    """
    project_id = env["WATSONX_PROJECT_ID"]
    bucket, _, key = cos_path.replace("cos://", "").partition("/")
    asset_payload = {
        "metadata": {
            "name": name,
            "description": f"CroweLM tuning dataset {name}",
            "asset_type": "data_asset",
            "origin_country": "us",
        },
        "entity": {
            "data_asset": {"mime_type": "application/json"},
        },
        "attachments": [{
            "asset_type": "data_asset",
            "name": name,
            "mime": "application/json",
            "connection_path": f"/{bucket}/{key}",
        }],
    }
    url = (f"https://api.dataplatform.cloud.ibm.com/v2/assets"
           f"?project_id={project_id}")
    r = _post(url, asset_payload, env, timeout=60)
    return r.get("metadata", {}).get("asset_id") or r.get("asset_id", "")


def submit_prompt_tuning(brand_id: str, asset_id: str, env: dict[str, str],
                         num_epochs: int = 6) -> dict:
    brand = resolve(brand_id)
    if brand is None or not brand.tunable:
        raise WatsonxError(f"brand {brand_id} is not marked tunable in registry")
    project_id = env["WATSONX_PROJECT_ID"]
    payload = {
        "name": f"crowelm-{brand_id}-{dt.datetime.utcnow():%Y%m%dT%H%M%S}",
        "description": f"Prompt tuning of {brand.base_model} on CroweLM unified.",
        "project_id": project_id,
        "task_id": "summarization",
        "base_model": {"model_id": brand.base_model},
        "auto_update_model": True,
        "training_data_references": [{
            "type": "data_asset",
            "location": {"href": f"/v2/assets/{asset_id}?project_id={project_id}"},
        }],
        "results_reference": {
            "type": "container",
            "name": "trainings",
            "location": {"path": f"crowelm-tunings/{brand_id}/"},
        },
        "prompt_tuning": {
            "task_id": "summarization",
            "tuning_type": "prompt_tuning",
            "num_epochs": num_epochs,
            "learning_rate": 0.3,
            "accumulate_steps": 16,
            "batch_size": 8,
            "max_input_tokens": 256,
            "max_output_tokens": 128,
            "verbalizer": "Input: {{input}} Output:",
        },
    }
    url = _wx_url(env, "/ml/v1/trainings")
    return _post(url, payload, env, timeout=60)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--brand", default="crowelm-prime")
    p.add_argument("--src", default="data/crowelm-unified/curated_export.jsonl")
    p.add_argument("--dry-run", action="store_true",
                   help="Convert + upload + register, but do NOT submit tuning job")
    args = p.parse_args()

    env = _load_env()
    src = Path(args.src).resolve()
    if not src.exists():
        print(f"ERROR: source dataset not found: {src}", file=sys.stderr)
        return 2

    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    dst = Path(f"/tmp/crowelm_train_{stamp}.jsonl")
    n = convert_jsonl(src, dst)
    h = hashlib.sha256(dst.read_bytes()).hexdigest()[:12]
    print(f"[convert] {n} examples → {dst} (sha256:{h})")

    cos_key = f"datasets/tunings/{stamp}_{h}.jsonl"
    cos_path = _upload_to_cos(dst, cos_key, env)
    print(f"[upload]  {cos_path}")

    asset_id = register_data_asset(f"crowelm-train-{stamp}", cos_path, env)
    print(f"[asset]   {asset_id}")

    if args.dry_run:
        print("[skip]    dry-run: not submitting tuning job")
        return 0

    job = submit_prompt_tuning(args.brand, asset_id, env)
    print(f"[tuning]  {json.dumps(job, indent=2)[:800]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
