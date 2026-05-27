# Copyright (c) 2026 Crowe Logic, Inc. All rights reserved.
"""
Embedding provider for the knowledge lake.

Two providers, one entry point `embed_text`. Configuration is
environment-driven and matches the rest of the foundry.

`CROWE_KB_EMBED_PROVIDER` selects the backend:

  azure_openai (premium, 3072-dim text-embedding-3-large):
    AZURE_CORE_ENDPOINT        endpoint root (same as the gateway uses)
    AZURE_CORE_API_KEY         api key (same as the gateway uses)
    CROWE_KB_EMBED_DEPLOYMENT  Azure deployment (default: text-embedding-3-large)

  ollama (local/offline, free, 768-dim nomic-embed-text):
    CROWE_KB_OLLAMA_URL        base url; falls back to OLLAMA_BASE_URL,
                               then http://localhost:11434. A trailing /v1
                               (the OpenAI-compat path) is stripped so the
                               native /api/embeddings endpoint resolves. With
                               OLLAMA_BASE_URL pointed at nexus, embeddings run
                               on the GPU box, not the local CPU.
    CROWE_KB_EMBED_MODEL       ollama model (default: nomic-embed-text)

When the provider isn't set or required env is missing, `embed_text`
returns None and callers fall back to FTS-only recall. This is the
no-op path that tests and offline development exercise.

Provider backends differ in vector dimension (3072 vs 768), so switching
providers requires a re-ingest; mixing dimensions in one corpus breaks cosine.

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


def _normalize_ollama_base(url: str) -> str:
    """Strip a trailing /v1 (the OpenAI-compat path) and slashes so the
    native /api/embeddings endpoint resolves whether the caller passes a
    bare host or reuses OLLAMA_BASE_URL."""
    url = (url or "").strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3].rstrip("/")
    return url or "http://localhost:11434"


def _config() -> Optional[dict]:
    """Return provider config if all required env is present, else None."""
    provider = os.environ.get("CROWE_KB_EMBED_PROVIDER", "").strip().lower()
    if provider == "azure_openai":
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
            "provider": "azure_openai",
            "endpoint": endpoint.rstrip("/"),
            "api_key": api_key,
            "deployment": deployment,
            "api_version": api_version,
        }
    if provider == "ollama":
        base = (
            os.environ.get("CROWE_KB_OLLAMA_URL", "").strip()
            or os.environ.get("OLLAMA_BASE_URL", "").strip()
            or "http://localhost:11434"
        )
        model = os.environ.get("CROWE_KB_EMBED_MODEL", "").strip() or "nomic-embed-text"
        return {
            "provider": "ollama",
            "base_url": _normalize_ollama_base(base),
            "model": model,
        }
    return None


def is_configured() -> bool:
    return _config() is not None


def _embed_azure(cfg: dict, text: str, httpx, timeout: float) -> Optional[list[float]]:
    url = (
        f"{cfg['endpoint']}/openai/deployments/{cfg['deployment']}"
        f"/embeddings?api-version={cfg['api_version']}"
    )
    headers = {
        "api-key": cfg["api_key"],
        "content-type": "application/json",
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json={"input": text})
    if resp.status_code != 200:
        return None
    data = resp.json()
    embedding = data.get("data", [{}])[0].get("embedding")
    if isinstance(embedding, list) and embedding:
        return [float(v) for v in embedding]
    return None


def _embed_ollama(cfg: dict, text: str, httpx, timeout: float) -> Optional[list[float]]:
    url = f"{cfg['base_url']}/api/embeddings"
    headers = {"content-type": "application/json"}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            url, headers=headers, json={"model": cfg["model"], "prompt": text}
        )
    if resp.status_code != 200:
        return None
    embedding = resp.json().get("embedding")
    if isinstance(embedding, list) and embedding:
        return [float(v) for v in embedding]
    return None


def embed_text(text: str, *, timeout: float = 30.0) -> Optional[list[float]]:
    """Compute an embedding via the configured provider, or return None
    when no provider is configured.

    Callers must handle the None case, which is the documented signal
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
    try:
        if cfg["provider"] == "ollama":
            return _embed_ollama(cfg, text, httpx, timeout)
        return _embed_azure(cfg, text, httpx, timeout)
    except Exception:
        # Embedding failures must never break ingestion or search.
        # FTS still works without vectors.
        return None


__all__ = [
    "cosine",
    "serialize",
    "deserialize",
    "is_configured",
    "embed_text",
]
