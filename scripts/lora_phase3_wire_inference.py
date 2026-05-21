#!/usr/bin/env python3
"""
Phase 3 :  wire the deployed CroweLM Kernel fine-tune into web inference.

Runs after the Azure fine-tuning job succeeds. Three steps:
  1. Read the fine_tuned_model id from the persisted state file
  2. Deploy it as `crowelm-kernel-v3` on the Azure resource (uses az CLI)
  3. Print the diff that needs to land in the web repo's
     app/api/chat/route.ts AZURE_MODEL_ALIASES entry for `crowelogic/mini`

Usage:
    .venv/bin/python scripts/lora_phase3_wire_inference.py [--auto-status-poll]

The --auto-status-poll flag will block until the job reaches a terminal
state (succeeded/failed/cancelled) before deploying. Default polls once
and exits if not succeeded yet.
"""
from __future__ import annotations

import argparse
import json
import sys
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAIN_DIR = ROOT / "data" / "training"
STATE_FILE = TRAIN_DIR / "azure_ft_state.json"

AZURE_ACCOUNT = "crowelogicos-4667-resource"
AZURE_RG = "rg-crowelogicos-4667"
DEPLOYMENT_NAME = "crowelm-kernel-v3"


def get_azure_creds() -> tuple[str, str]:
    import os
    key = os.environ.get("AZURE_CORE_API_KEY", "")
    if not key:
        key = subprocess.check_output([
            "az", "cognitiveservices", "account", "keys", "list",
            "--name", AZURE_ACCOUNT, "--resource-group", AZURE_RG,
            "--query", "key1", "-o", "tsv",
        ], text=True).strip()
    base = f"https://{AZURE_ACCOUNT}.openai.azure.com/openai/v1"
    return key, base


def fetch_job(job_id: str, key: str, base: str) -> dict:
    req = urllib.request.Request(
        f"{base}/fine_tuning/jobs/{job_id}",
        headers={"api-key": key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto-status-poll", action="store_true",
                    help="Block until job reaches terminal state.")
    ap.add_argument("--poll-interval", type=int, default=300,
                    help="Seconds between polls when --auto-status-poll set.")
    ap.add_argument("--deploy", action="store_true",
                    help="After confirming success, run the az deployment create command.")
    ap.add_argument("--print-rewire-diff", action="store_true",
                    help="Print the diff to land in app/api/chat/route.ts.")
    args = ap.parse_args()

    if not STATE_FILE.exists():
        print(f"FAIL: {STATE_FILE} not found. Run lora_phase2_azure_pipeline.py submit first.")
        return 1

    state = json.loads(STATE_FILE.read_text())
    job_id = state.get("job_id")
    if not job_id:
        print("FAIL: no job_id in state file.")
        return 1

    key, base = get_azure_creds()

    while True:
        job = fetch_job(job_id, key, base)
        status = job.get("status")
        ft_model = job.get("fine_tuned_model")
        print(f"[{time.strftime('%H:%M:%S')}] job={job_id}  status={status}  fine_tuned={ft_model or '<pending>'}")

        if status == "succeeded":
            state["fine_tuned_model"] = ft_model
            state["finished_at"] = job.get("finished_at")
            state["trained_tokens"] = job.get("trained_tokens")
            STATE_FILE.write_text(json.dumps(state, indent=2))
            break

        if status in ("failed", "cancelled"):
            err = job.get("error", {})
            print(f"FAIL: training {status}: {err}")
            return 2

        if not args.auto_status_poll:
            print()
            print("Not succeeded yet. Re-run with --auto-status-poll to block.")
            return 0

        time.sleep(args.poll_interval)

    print()
    print(f"Training succeeded. fine_tuned_model = {ft_model}")
    print(f"trained_tokens: {state.get('trained_tokens')}")
    print()

    if args.deploy:
        print(f"deploying as {DEPLOYMENT_NAME}...")
        cmd = [
            "az", "cognitiveservices", "account", "deployment", "create",
            "--name", AZURE_ACCOUNT,
            "--resource-group", AZURE_RG,
            "--deployment-name", DEPLOYMENT_NAME,
            "--model-name", ft_model,
            "--model-version", "1",
            "--model-format", "OpenAI",
            "--sku-capacity", "1",
            "--sku-name", "Standard",
        ]
        try:
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
            print("OK:", out[:300])
            state["deployment_name"] = DEPLOYMENT_NAME
            STATE_FILE.write_text(json.dumps(state, indent=2))
        except subprocess.CalledProcessError as e:
            print(f"FAIL: az deploy: {e.output[:500]}")
            return 3

    if args.print_rewire_diff or args.deploy:
        print()
        print("=" * 70)
        print("Inference rewire diff (apply manually in crowe-logic-ai/app/api/chat/route.ts):")
        print("=" * 70)
        print(f"""
@@ AZURE_MODEL_ALIASES "crowelogic/mini" entry @@
   "crowelogic/mini": {{
     provider: "openai_compatible",
-    deployment: "gpt-5.4-nano",
+    deployment: "{DEPLOYMENT_NAME}",
     label: "CroweLM Kernel",
     endpointEnvNames: ["AZURE_CORE_ENDPOINT", "AZURE_AI_ENDPOINT"],
     apiKeyEnvNames: ["AZURE_CORE_API_KEY", "AZURE_AI_API_KEY"],
   }},
""")
        print("After committing + deploying via railway up, CroweLM Kernel selections")
        print("will route to the fine-tuned model instead of the base gpt-5.4-nano.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
