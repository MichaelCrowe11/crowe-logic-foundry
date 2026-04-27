#!/usr/bin/env python3
"""
Smoke-test retrieval against the crowe-knowledge vector store.

Uses /v1/responses with the file_search tool attached to the vector store.
Sends three probe queries spanning the indexed domains:

  1. cultivation: a Lions Mane SOP question
  2. architecture: a foundry plan question
  3. cross-repo: a question that should pull from multiple sources

Prints the answer + which files were cited so we can confirm the right
content is being retrieved.

Usage:
    .venv/bin/python scripts/vs_test_retrieval.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "training" / "vector_store_state.json"

AZURE_ACCOUNT = "crowelogicos-4667-resource"
AZURE_RG = "rg-crowelogicos-4667"
AZURE_BASE = f"https://{AZURE_ACCOUNT}.openai.azure.com/openai/v1"
PROBE_MODEL = "gpt-5.4-pro"  # The most-capable deployed model

PROBES = [
    ("cultivation", "What's the optimal substrate moisture content for Lion's Mane "
                    "fruiting and how do you measure it accurately?"),
    ("architecture", "What's the design intent behind the Crowe Synapse plan, "
                     "and what role does it play in the Foundry?"),
    ("cross-domain", "How does the Crowe Logic platform tie cultivation operations "
                     "to its agent infrastructure?"),
]


def get_azure_creds() -> tuple[str, str]:
    key = os.environ.get("AZURE_CORE_API_KEY", "")
    if not key:
        key = subprocess.check_output([
            "az", "cognitiveservices", "account", "keys", "list",
            "--name", AZURE_ACCOUNT, "--resource-group", AZURE_RG,
            "--query", "key1", "-o", "tsv",
        ], text=True).strip()
    return key, AZURE_BASE


def http(method: str, url: str, key: str, body: bytes | None = None,
         timeout: int = 180) -> dict:
    headers = {"api-key": key, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as ex:
        raise RuntimeError(f"HTTP {ex.code}: {ex.read().decode()[:600]}")


def main() -> int:
    if not STATE_FILE.exists():
        sys.exit("FAIL: vector store state missing")
    state = json.loads(STATE_FILE.read_text())
    vs_id = state["vector_store_id"]

    key, base = get_azure_creds()

    # Confirm vector store is ready
    vs = http("GET", f"{base}/vector_stores/{vs_id}", key)
    print(f"vector store: {vs_id}")
    print(f"  status: {vs.get('status')}")
    print(f"  file_counts: {vs.get('file_counts')}")
    if vs.get("file_counts", {}).get("in_progress", 0) > 0:
        print(f"  WARNING: files still indexing; results may be incomplete")
    print()

    for label, question in PROBES:
        print(f"--- probe: {label} ---")
        print(f"Q: {question}")
        payload = {
            "model": PROBE_MODEL,
            "input": question,
            "tools": [{"type": "file_search", "vector_store_ids": [vs_id]}],
            "include": ["file_search_call.results"],
            "max_output_tokens": 600,
        }
        try:
            resp = http("POST", f"{base}/responses", key,
                        body=json.dumps(payload).encode(), timeout=240)
        except RuntimeError as e:
            print(f"FAIL: {e}")
            continue

        # Extract answer + cited files
        answer_chunks = []
        cited_files: dict = {}
        for item in resp.get("output", []):
            if item.get("type") == "message":
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        answer_chunks.append(c.get("text", ""))
                    for ann in c.get("annotations", []) or []:
                        if ann.get("type") == "file_citation":
                            fid = ann.get("file_id")
                            if fid:
                                cited_files[fid] = ann.get("filename", "<no name>")

        answer = "".join(answer_chunks).strip()
        print(f"\nA: {answer[:800]}")
        if cited_files:
            print(f"\ncited files ({len(cited_files)}):")
            for fid, fname in cited_files.items():
                print(f"  {fid}  {fname}")
        else:
            print(f"\n(no file citations: model may have answered from base knowledge)")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
