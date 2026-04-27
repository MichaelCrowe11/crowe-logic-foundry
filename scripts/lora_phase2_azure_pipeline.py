#!/usr/bin/env python3
"""
Azure OpenAI fine-tuning pipeline for CroweLM Prime / CroweLM Kernel.

Pivot from watsonx after that path turned up "Full fine tuning not available"
on the current plan. Azure has none of those gates, and the web app already
routes crowelogic/mini -> Azure deployment gpt-5.4-nano, so the inference
rewire after training is a one-line change.

Phases (all in this one script with subcommands):
    convert   :  read v3 train/val jsonl, emit Azure chat-completion JSONL
    upload    :  POST /v1/files with purpose=fine-tune, capture file_id
    submit    :  POST /v1/fine_tuning/jobs with training_file, model, hparams
    status    :  GET /v1/fine_tuning/jobs/{id}, print state, loss, file ids
    deploy    :  once succeeded, deploy the tuned model to a custom name
                (this requires az CLI since deployment is mgmt-API not data-API)

Usage:
    .venv/bin/python scripts/lora_phase2_azure_pipeline.py convert
    .venv/bin/python scripts/lora_phase2_azure_pipeline.py upload
    .venv/bin/python scripts/lora_phase2_azure_pipeline.py submit
    .venv/bin/python scripts/lora_phase2_azure_pipeline.py status <job_id>
    .venv/bin/python scripts/lora_phase2_azure_pipeline.py deploy <fine_tuned_model_id>

Environment:
    AZURE_CORE_API_KEY, AZURE_CORE_ENDPOINT must be set. The script will fall
    back to pulling key1 via `az cognitiveservices account keys list` if not.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAIN_DIR = ROOT / "data" / "training"
SRC_TRAIN = TRAIN_DIR / "crowelm_v3_train.jsonl"
SRC_VAL = TRAIN_DIR / "crowelm_v3_val.jsonl"
AZURE_TRAIN = TRAIN_DIR / "crowelm_v3_azure_train.jsonl"
AZURE_VAL = TRAIN_DIR / "crowelm_v3_azure_val.jsonl"
STATE_FILE = TRAIN_DIR / "azure_ft_state.json"

# Azure OpenAI account
AZURE_ACCOUNT = "crowelogicos-4667-resource"
AZURE_RG = "rg-crowelogicos-4667"
AZURE_BASE_DEFAULT = f"https://{AZURE_ACCOUNT}.openai.azure.com/openai/v1"

# Fine-tuning target. On this regular eastus2 resource the candidates with
# fine_tune=true are: gpt-35-turbo-*, gpt-4o-2024-08-06, o4-mini-*. gpt-4o-mini
# is gated behind a global/devtier resource (fine_tune=false on regular).
# gpt-4o-2024-08-06 is the right call: modern, vision-capable, fine-tunable here.
FT_MODEL = "gpt-4o-2024-08-06"
SYSTEM_PROMPT = (
    "You are CroweLM Kernel, Crowe Logic's cultivation operations model. "
    "Answer with operationally specific guidance: numbers, ratios, "
    "temperatures, timeframes, troubleshooting steps. Use direct, "
    "calibrated voice. Output Markdown when it improves clarity. "
    "Never use vendor names. Never use em dashes."
)


def get_azure_creds() -> tuple[str, str]:
    """Return (api_key, base_url). Falls back to az CLI if env not set."""
    key = os.environ.get("AZURE_CORE_API_KEY", "")
    endpoint = os.environ.get("AZURE_CORE_ENDPOINT", "")
    if not key:
        try:
            key = subprocess.check_output([
                "az", "cognitiveservices", "account", "keys", "list",
                "--name", AZURE_ACCOUNT, "--resource-group", AZURE_RG,
                "--query", "key1", "-o", "tsv",
            ], text=True).strip()
        except Exception as e:
            sys.exit(f"FAIL: AZURE_CORE_API_KEY not set and az fallback failed: {e}")
    if not endpoint:
        endpoint = AZURE_BASE_DEFAULT
    base = endpoint.rstrip("/")
    if not base.endswith("/v1"):
        if base.endswith("/openai"):
            base = base + "/v1"
        elif "/openai/v1" not in base:
            base = base + "/openai/v1"
    return key, base


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def http_request(method: str, url: str, key: str, body: bytes = None,
                 content_type: str = "application/json", extra_headers: dict = None,
                 timeout: int = 60) -> dict:
    headers = {"api-key": key, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = content_type
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as ex:
        body = ex.read().decode()
        try:
            err = json.loads(body)
        except json.JSONDecodeError:
            err = {"error": body[:400]}
        raise RuntimeError(f"HTTP {ex.code}: {json.dumps(err)[:600]}")


def cmd_convert(args) -> int:
    """v3 {input, output} -> Azure {messages: [system, user, assistant]}."""
    if not SRC_TRAIN.exists() or not SRC_VAL.exists():
        return _fail("v3 corpus not found at " + str(TRAIN_DIR))

    for src, dst, label in [(SRC_TRAIN, AZURE_TRAIN, "train"), (SRC_VAL, AZURE_VAL, "val")]:
        n = 0
        with src.open() as fin, dst.open("w") as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                instr = row.get("input") or row.get("instruction") or ""
                resp = row.get("output") or row.get("response") or ""
                if not instr or not resp:
                    continue
                msg = {"messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": instr},
                    {"role": "assistant", "content": resp},
                ]}
                fout.write(json.dumps(msg) + "\n")
                n += 1
        size = dst.stat().st_size
        print(f"  {label}: {n} examples -> {dst.name}  ({size:,} bytes)")

    return 0


def cmd_upload(args) -> int:
    """POST /v1/files for both train and val with purpose=fine-tune."""
    if not AZURE_TRAIN.exists() or not AZURE_VAL.exists():
        return _fail("Azure-formatted corpus not found. Run `convert` first.")

    key, base = get_azure_creds()
    state = load_state()

    for label, path in [("train", AZURE_TRAIN), ("val", AZURE_VAL)]:
        # Multipart manually (no requests dependency)
        boundary = "----CroweFTUpload" + str(int(time.time() * 1000))
        body = []
        body.append(f"--{boundary}".encode())
        body.append(b'Content-Disposition: form-data; name="purpose"')
        body.append(b"")
        body.append(b"fine-tune")
        body.append(f"--{boundary}".encode())
        body.append(f'Content-Disposition: form-data; name="file"; filename="{path.name}"'.encode())
        body.append(b"Content-Type: application/jsonl")
        body.append(b"")
        body.append(path.read_bytes())
        body.append(f"--{boundary}--".encode())
        payload = b"\r\n".join(body)

        url = f"{base}/files"
        try:
            resp = http_request("POST", url, key, body=payload,
                content_type=f"multipart/form-data; boundary={boundary}",
                timeout=180)
            file_id = resp.get("id")
            state[f"file_id_{label}"] = file_id
            state[f"file_name_{label}"] = path.name
            print(f"  {label}: uploaded as {file_id} (status: {resp.get('status', '?')})")
        except RuntimeError as e:
            return _fail(f"upload {label}: {e}")

    save_state(state)
    return 0


def cmd_submit(args) -> int:
    """POST /v1/fine_tuning/jobs once both files are uploaded."""
    state = load_state()
    train_id = state.get("file_id_train")
    val_id = state.get("file_id_val")
    if not train_id or not val_id:
        return _fail("file IDs missing :  run `upload` first.")

    key, base = get_azure_creds()

    payload = {
        "training_file": train_id,
        "validation_file": val_id,
        "model": FT_MODEL,
        "hyperparameters": {
            "n_epochs": args.epochs,
        },
        "suffix": "crowelm-prime-v3",
    }
    print(f"submitting fine-tune on {FT_MODEL} ({args.epochs} epochs)...")
    print(f"  training_file: {train_id}")
    print(f"  validation_file: {val_id}")
    print()

    try:
        resp = http_request("POST", f"{base}/fine_tuning/jobs", key,
            body=json.dumps(payload).encode())
    except RuntimeError as e:
        return _fail(str(e))

    job_id = resp.get("id")
    state["job_id"] = job_id
    state["job_created_at"] = resp.get("created_at")
    state["job_model"] = resp.get("model")
    state["job_status"] = resp.get("status")
    save_state(state)

    print(f"job created: {job_id}")
    print(f"  status:    {resp.get('status')}")
    print(f"  model:     {resp.get('model')}")
    print(f"  created:   {resp.get('created_at')}")
    print()
    print(f"watch: .venv/bin/python {sys.argv[0]} status")
    print(f"or in browser: https://oai.azure.com/portal/{AZURE_ACCOUNT}/finetune")
    return 0


def cmd_status(args) -> int:
    state = load_state()
    job_id = args.job_id or state.get("job_id")
    if not job_id:
        return _fail("no job id")
    key, base = get_azure_creds()
    resp = http_request("GET", f"{base}/fine_tuning/jobs/{job_id}", key)

    print(f"job:           {resp.get('id')}")
    print(f"status:        {resp.get('status')}")
    print(f"model:         {resp.get('model')}")
    print(f"fine_tuned:    {resp.get('fine_tuned_model', '<not yet>')}")
    print(f"created:       {resp.get('created_at')}")
    print(f"finished:      {resp.get('finished_at', '<not yet>')}")
    err = resp.get("error")
    if err:
        print(f"error:         {err}")
    print(f"trained tokens: {resp.get('trained_tokens', '<not yet>')}")
    print(f"result_files:  {resp.get('result_files', [])}")

    if resp.get("fine_tuned_model"):
        state["fine_tuned_model"] = resp["fine_tuned_model"]
        save_state(state)
    return 0


def cmd_deploy(args) -> int:
    state = load_state()
    ft_model = args.model_id or state.get("fine_tuned_model")
    if not ft_model:
        return _fail("no fine_tuned_model id; pass <id> or run `status` first when complete")

    deployment_name = args.deployment_name or "crowelm-kernel-v3"
    print(f"deploying {ft_model} as {deployment_name}...")
    # Azure deployment is via mgmt API. Use az CLI which already has auth.
    cmd = [
        "az", "cognitiveservices", "account", "deployment", "create",
        "--name", AZURE_ACCOUNT,
        "--resource-group", AZURE_RG,
        "--deployment-name", deployment_name,
        "--model-name", ft_model,
        "--model-version", "1",
        "--model-format", "OpenAI",
        "--sku-capacity", "1",
        "--sku-name", "Standard",
    ]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        print("OK")
        print(out[:500])
        state["deployment_name"] = deployment_name
        save_state(state)
        return 0
    except subprocess.CalledProcessError as e:
        return _fail(f"az deploy failed: {e.output[:500]}")


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("convert", help="v3 -> Azure chat-completion JSONL")
    sub.add_parser("upload", help="upload to Azure /v1/files")

    s_submit = sub.add_parser("submit", help="submit FT job")
    s_submit.add_argument("--epochs", type=int, default=3)

    s_status = sub.add_parser("status", help="poll FT job")
    s_status.add_argument("job_id", nargs="?", default=None)

    s_deploy = sub.add_parser("deploy", help="deploy tuned model")
    s_deploy.add_argument("model_id", nargs="?", default=None)
    s_deploy.add_argument("--deployment-name", default="crowelm-kernel-v3")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 1

    handlers = {
        "convert": cmd_convert,
        "upload": cmd_upload,
        "submit": cmd_submit,
        "status": cmd_status,
        "deploy": cmd_deploy,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
