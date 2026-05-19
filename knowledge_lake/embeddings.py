# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Embedding provider for the knowledge lake.

Today's footprint is small on purpose: one provider (Azure OpenAI),
one model (CroweLM Embed Large, name `text-embedding-3-large` on the
backing resource), and one entry point `embed_text`.

Configuration is environment-driven and matches the rest of the
foundry. Required env when embeddings are desired:

  CROWE_KB_EMBED_PROVIDER    must be "azure_openai"
  AZURE_CORE_ENDPOINT        endpoint root (same as the gateway uses)
  AZURE_CORE_API_KEY         api key (same as the gateway uses)
  CROWE_KB_EMBED_DEPLOYMENT  Azure deployment name (default: text-embedding-3-large)

When any required var is missing, `embed_text` returns None and
callers fall back to FTS-only recall. This is the no-op path that
tests and offline development exercise.

Vectors are returned as Python `list[float]` and serialized as JSON
into the SQLite `chunks.embedding` column. JSON is suboptimal but
keeps the storage format human-debuggable, doesn't require a third-
party SQLite extension, and is easy to swap for `bytes` (`array.array`
+ struct.pack) when the corpus grows past the point where JSON
overhead matters.
"""
from __future__ import annotations

import json
import math
import os
from typing import Optional

# Cosine similarity in pure Python so the search path doesn't depend
# on numpy. At foundry-docs scale (~700 chunks) the cost is dominated
# by the JSON decode, not the math.
def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def serialize(vec: list[float]) -> str:
    """Round to 6 sig figs to keep the JSON small without harming recall."""
    return json.dumps([round(v, 6) for v in vec])


def deserialize(blob: Optional[str]) -> Optional[list[float]]:
    if not blob:
        return None
    try:
        out = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if isinstance(out, list) and all(isinstance(v, (int, float)) for v in out):
        return [float(v) for v in out]
    return None


# ─── Provider selection ─────────────────────────────────────────

def _config() -> Optional[dict]:
    """Return provider config if all required env is present, else None."""
    provider = os.environ.get("CROWE_KB_EMBED_PROVIDER", "").strip().lower()
    if provider != "azure_openai":
        return None
    endpoint = os.environ.get("AZURE_CORE_ENDPOINT", "").strip()
    api_key = os.environ.get("AZURE_CORE_API_KEY", "").strip()
    if not endpoint or not api_key:
        return None
    deployment = (
        os.environ.get("CROWE_KB_EMBED_DEPLOYMENT", "").strip()
        or "text-embedding-3-large"
    )
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
    return {
        "endpoint": endpoint.rstrip("/"),
        "api_key": api_key,
        "deployment": deployment,
        "api_version": api_version,
    }


def is_configured() -> bool:
    return _config() is not None


def embed_text(text: str, *, timeout: float = 30.0) -> Optional[list[float]]:
    """Compute an embedding via Azure OpenAI, or return None when the
    provider isn't configured.

    Callers must handle the None case — it is the documented signal
    that recall should fall back to FTS-only.
    """
    text = (text or "").strip()
    if not text:
        return None
    cfg = _config()
    if cfg is None:
        return None
    try:
        import httpx
    except ImportError:
        return None

    url = (
        f"{cfg['endpoint']}/openai/deployments/{cfg['deployment']}"
        f"/embeddings?api-version={cfg['api_version']}"
    )
    headers = {
        "api-key": cfg["api_key"],
        "content-type": "application/json",
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json={"input": text})
        if resp.status_code != 200:
            return None
        data = resp.json()
        embedding = data.get("data", [{}])[0].get("embedding")
        if isinstance(embedding, list) and embedding:
            return [float(v) for v in embedding]
    except Exception:
        # Embedding failures must never break ingestion or search.
        # FTS still works without vectors.
        return None
    return None


__all__ = [
    "cosine",
    "serialize",
    "deserialize",
    "is_configured",
    "embed_text",
]
