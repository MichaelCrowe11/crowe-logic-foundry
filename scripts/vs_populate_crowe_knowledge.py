#!/usr/bin/env python3
"""
Populate the crowe-knowledge Azure vector store.

Tier 1 sources (high-signal, definitely in):
  - Lions Mane SOP PDF
  - Mycelial Nexus SOP DOCX
  - mycology_deep_research_report.md
  - foundry docs/*.md, docs/superpowers/plans/*.md, docs/superpowers/specs/*.md
  - The v3 mycology Q&A corpus (already curated)
  - Supabase knowledge_articles + sop_templates

Tier 2 sources (Crowe-relevant repo docs):
  - README.md and docs/**/*.md from a curated GitHub repo list

Tier 3 (skipped here, separate vector stores later):
  - Mick Raven book draft material (different domain)
  - Code files
  - Legal content

Each file is uploaded via /v1/files purpose=assistants then attached to the
vector store. The vector store handles chunking + embedding internally
(text-embedding-3-large is the default).

Usage:
    .venv/bin/python scripts/vs_populate_crowe_knowledge.py [--dry-run]
        [--skip-github] [--skip-supabase] [--limit-github 20]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "training" / "vector_store_state.json"
CACHE_DIR = ROOT / "data" / "training" / "vs_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

AZURE_ACCOUNT = "crowelogicos-4667-resource"
AZURE_RG = "rg-crowelogicos-4667"
AZURE_BASE = f"https://{AZURE_ACCOUNT}.openai.azure.com/openai/v1"
GH_USER = "MichaelCrowe11"

# Local files: (path, label_prefix)
LOCAL_TIER1 = [
    ("/Users/crowelogic/Library/CloudStorage/Dropbox/Southwest Mushrooms/Digital Products/Lions Mane SOP/Lions-Mane-Cultivation-SOP.pdf",
     "lions-mane-sop"),
    ("/Users/crowelogic/Library/CloudStorage/Dropbox/Mycelial_Nexus_SOP_and_Cultivation_Matrix.docx",
     "mycelial-nexus-sop"),
    ("/Users/crowelogic/mycology_deep_research_report.md",
     "mycology-deep-research-report"),
]

# Foundry docs glob roots (relative to ROOT)
FOUNDRY_DOC_GLOBS = [
    "docs/*.md",
    "docs/protocols/*.md",
    "docs/superpowers/plans/*.md",
    "docs/superpowers/specs/*.md",
]

# Curated GitHub repos. Includes only the ones whose docs are directly
# relevant to the platform (cultivation, foundry, web app, biotech). Excludes
# code-only dumps and Mick Raven book repos (those need separate stores).
GH_REPOS_TIER2 = [
    "crowe-logic-foundry",
    "v0-crowe-mycology-2",
    "crowe-research-engine",
    "crowe-psychedelics",
    "crowelogic-bio",
    "crowe-logic-mcp",
    "crowe-logic-studio",
    "crowe-logic-mono",
    "crowe-quantum",
    "talon",
    "synapse-lang",
    "southwest-mushrooms",
    "southwest-mushrooms-storefront",
    "crowelogicos",
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


def http(method: str, url: str, key: str, *, body: bytes | None = None,
         content_type: str = "application/json", timeout: int = 120) -> dict:
    headers = {"api-key": key, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as ex:
        raise RuntimeError(f"HTTP {ex.code}: {ex.read().decode()[:400]}")


def upload_file(local: Path, key: str, base: str) -> str:
    """POST /v1/files purpose=assistants. Returns file_id."""
    boundary = f"----CroweVS{int(time.time() * 1000)}"
    parts = [
        f"--{boundary}".encode(),
        b'Content-Disposition: form-data; name="purpose"',
        b"",
        b"assistants",
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{local.name}"'.encode(),
        b"Content-Type: application/octet-stream",
        b"",
        local.read_bytes(),
        f"--{boundary}--".encode(),
    ]
    payload = b"\r\n".join(parts)
    resp = http("POST", f"{base}/files", key, body=payload,
                content_type=f"multipart/form-data; boundary={boundary}",
                timeout=180)
    return resp["id"]


def attach_to_vs(vs_id: str, file_id: str, key: str, base: str) -> dict:
    return http("POST", f"{base}/vector_stores/{vs_id}/files", key,
                body=json.dumps({"file_id": file_id}).encode())


def collect_local_files() -> list[tuple[Path, str]]:
    """Return list of (path, label) for tier 1 local files."""
    out: list[tuple[Path, str]] = []
    for path_str, label in LOCAL_TIER1:
        p = Path(path_str)
        if p.exists():
            out.append((p, label))
    for glob in FOUNDRY_DOC_GLOBS:
        for p in sorted(ROOT.glob(glob)):
            if p.stat().st_size > 1024:
                out.append((p, f"foundry-{p.parent.name}"))
    # The v3 corpus
    v3 = ROOT / "data" / "training" / "crowelm_v3_train.jsonl"
    if v3.exists():
        out.append((v3, "v3-corpus"))
    return out


def fetch_github_docs(repo: str, limit_files: int = 50) -> list[Path]:
    """Walk the repo's git tree via /repos/{owner}/{repo}/git/trees/HEAD?recursive=1
    and download .md files. More reliable than the search API for private repos.
    """
    cache_dir = CACHE_DIR / "github" / repo
    cache_dir.mkdir(parents=True, exist_ok=True)

    skip_prefixes = ("node_modules/", ".git/", "build/", "dist/", "__pycache__/",
                     ".next/", "vendor/", "out/", "test_data/", "fixtures/",
                     ".venv/", "venv/", "site-packages/")

    # Get default branch first
    try:
        meta = json.loads(subprocess.check_output([
            "gh", "api", f"/repos/{GH_USER}/{repo}",
        ], text=True, timeout=20))
        default_branch = meta.get("default_branch", "main")
    except Exception:
        default_branch = "main"

    # Get full tree
    try:
        tree_data = json.loads(subprocess.check_output([
            "gh", "api",
            f"/repos/{GH_USER}/{repo}/git/trees/{default_branch}?recursive=1",
        ], text=True, timeout=30))
    except subprocess.CalledProcessError as e:
        print(f"  [{repo}] tree fetch failed")
        return []

    md_paths = []
    for entry in tree_data.get("tree", []):
        if entry.get("type") != "blob":
            continue
        path = entry.get("path", "")
        if not path.endswith(".md"):
            continue
        if any(path.startswith(p) or f"/{p}" in path for p in skip_prefixes):
            continue
        # Skip files larger than 256 KB (likely vendored docs / huge logs)
        size = entry.get("size", 0)
        if size > 256 * 1024 or size < 200:
            continue
        md_paths.append(path)

    md_paths = md_paths[:limit_files]
    local_paths: list[Path] = []
    for path in md_paths:
        cache_path = cache_dir / path.replace("/", "__")
        if cache_path.exists() and cache_path.stat().st_size > 0:
            local_paths.append(cache_path)
            continue
        try:
            content = subprocess.check_output([
                "gh", "api",
                f"/repos/{GH_USER}/{repo}/contents/{path}",
                "-H", "Accept: application/vnd.github.raw",
            ], timeout=30)
            if len(content) > 200:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(content)
                local_paths.append(cache_path)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            continue
    return local_paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-github", action="store_true")
    ap.add_argument("--skip-supabase", action="store_true")
    ap.add_argument("--limit-github", type=int, default=30,
                    help="Max .md files per repo")
    args = ap.parse_args()

    if not STATE_FILE.exists():
        sys.exit(f"FAIL: vector store not created. Run T2.1 first.")
    state = json.loads(STATE_FILE.read_text())
    vs_id = state["vector_store_id"]
    print(f"target vector store: {vs_id} ({state.get('name')})")

    key, base = get_azure_creds()

    # 1. Tier 1 local
    local_files = collect_local_files()
    print(f"\nTier 1 local: {len(local_files)} files")
    for p, label in local_files[:8]:
        print(f"  {label:30s}  {p.name}  ({p.stat().st_size:,}b)")
    if len(local_files) > 8:
        print(f"  ... +{len(local_files) - 8} more")

    # 2. Tier 2 GitHub
    github_files: list[tuple[Path, str]] = []
    if not args.skip_github:
        print(f"\nTier 2 GitHub: pulling .md files from {len(GH_REPOS_TIER2)} repos...")
        for repo in GH_REPOS_TIER2:
            paths = fetch_github_docs(repo, limit_files=args.limit_github)
            for p in paths:
                github_files.append((p, f"gh-{repo}"))
            print(f"  {repo}: {len(paths)} files")

    all_files = local_files + github_files
    print(f"\nTotal to upload: {len(all_files)} files")
    total_bytes = sum(p.stat().st_size for p, _ in all_files)
    print(f"Total bytes: {total_bytes:,} ({total_bytes / 1024 / 1024:.1f} MB)")

    if args.dry_run:
        print("\nDRY RUN: stopping before upload.")
        return 0

    # 3. Upload + attach
    upload_state_path = ROOT / "data" / "training" / "vs_uploaded.json"
    uploaded = json.loads(upload_state_path.read_text()) if upload_state_path.exists() else {}

    print(f"\nUploading + attaching ({len(uploaded)} previously)...")
    n_new = 0
    n_failed = 0
    for path, label in all_files:
        cache_key = f"{label}:{path.name}:{path.stat().st_size}"
        if cache_key in uploaded:
            continue
        try:
            file_id = upload_file(path, key, base)
            attach = attach_to_vs(vs_id, file_id, key, base)
            uploaded[cache_key] = {
                "file_id": file_id,
                "path": str(path),
                "label": label,
                "vs_file_id": attach.get("id"),
                "status": attach.get("status"),
            }
            n_new += 1
            if n_new % 5 == 0:
                upload_state_path.write_text(json.dumps(uploaded, indent=2))
                print(f"  +{n_new} uploaded so far")
        except Exception as e:
            n_failed += 1
            print(f"  FAIL {label}/{path.name}: {str(e)[:120]}")
    upload_state_path.write_text(json.dumps(uploaded, indent=2))

    print(f"\nDone: +{n_new} new, {n_failed} failed, {len(uploaded)} total in store.")

    # 4. Final vector store state
    vs = http("GET", f"{base}/vector_stores/{vs_id}", key)
    print(f"\nVector store final state:")
    print(f"  status: {vs.get('status')}")
    print(f"  file_counts: {vs.get('file_counts')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
