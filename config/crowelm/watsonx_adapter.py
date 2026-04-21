"""
watsonx.ai inference adapter for CroweLM brands.

Exposes a small, dependency-free client for:
  - obtaining IBM IAM bearer tokens
  - calling /ml/v1/text/chat for text_chat brands
  - calling /ml/v1/text/generation for base_completion brands
  - calling /ml/v1/text/embeddings for embedding brands
  - calling /ml/v1/text/rerank for rerank brands
  - calling /ml/v1/time_series/forecast for time-series brands

Credentials are read from ~/.crowe-logic/ibm.env (chmod 600). The adapter
caches the IAM token until a few minutes before expiry.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from .brand_registry import CroweBrand, resolve

DEFAULT_ENV = Path.home() / ".crowe-logic" / "ibm.env"
DEFAULT_VERSION = "2024-09-16"


class WatsonxError(RuntimeError):
    pass


def _load_env(path: Path = DEFAULT_ENV) -> dict[str, str]:
    if not path.exists():
        raise WatsonxError(f"ibm.env not found at {path}")
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


_TOKEN_CACHE: dict[str, Any] = {"token": None, "exp": 0.0}


def get_iam_token(env: dict[str, str] | None = None) -> str:
    env = env or _load_env()
    apikey = env.get("WATSONX_APIKEY") or env.get("IBMCLOUD_APIKEY")
    if not apikey:
        raise WatsonxError("WATSONX_APIKEY/IBMCLOUD_APIKEY missing from ibm.env")
    now = time.time()
    if _TOKEN_CACHE["token"] and _TOKEN_CACHE["exp"] - 120 > now:
        return _TOKEN_CACHE["token"]
    body = urllib.parse.urlencode({
        "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
        "apikey": apikey,
    }).encode()
    req = urllib.request.Request(
        "https://iam.cloud.ibm.com/identity/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    _TOKEN_CACHE["token"] = data["access_token"]
    _TOKEN_CACHE["exp"] = now + data.get("expires_in", 3600)
    return _TOKEN_CACHE["token"]


def _post(url: str, payload: dict, env: dict[str, str], timeout: int = 120) -> dict:
    token = get_iam_token(env)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) crowe-logic-foundry/0.3.0 Safari/537.36",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise WatsonxError(f"watsonx HTTP {e.code}: {body[:1500]}") from None


def _wx_url(env: dict[str, str], path: str, version: str = DEFAULT_VERSION) -> str:
    base = env.get("WATSONX_URL", "https://us-south.ml.cloud.ibm.com").rstrip("/")
    sep = "&" if "?" in path else "?"
    return f"{base}{path}{sep}version={version}"


def chat(
    brand_id: str,
    messages: list[dict],
    *,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    env: dict[str, str] | None = None,
) -> dict:
    env = env or _load_env()
    brand = resolve(brand_id)
    if brand is None:
        raise WatsonxError(f"unknown CroweLM brand: {brand_id!r}")
    if "text_chat" not in brand.capabilities:
        raise WatsonxError(f"brand {brand_id} does not support text_chat "
                           f"(capabilities={brand.capabilities})")
    project_id = env.get("WATSONX_PROJECT_ID")
    if not project_id:
        raise WatsonxError("WATSONX_PROJECT_ID missing from ibm.env")
    payload = {
        "model_id": brand.tuned_asset or brand.base_model,
        "project_id": project_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    return _post(_wx_url(env, "/ml/v1/text/chat"), payload, env)


def embed(
    brand_id: str,
    inputs: list[str],
    *,
    env: dict[str, str] | None = None,
) -> dict:
    env = env or _load_env()
    brand = resolve(brand_id)
    if brand is None or "embedding" not in brand.capabilities:
        raise WatsonxError(f"brand {brand_id!r} is not an embedding model")
    project_id = env.get("WATSONX_PROJECT_ID")
    if not project_id:
        raise WatsonxError("WATSONX_PROJECT_ID missing from ibm.env")
    payload = {
        "model_id": brand.base_model,
        "project_id": project_id,
        "inputs": inputs,
    }
    return _post(_wx_url(env, "/ml/v1/text/embeddings"), payload, env)


def rerank(
    brand_id: str,
    query: str,
    documents: Iterable[str],
    *,
    env: dict[str, str] | None = None,
) -> dict:
    env = env or _load_env()
    brand = resolve(brand_id)
    if brand is None or "rerank" not in brand.capabilities:
        raise WatsonxError(f"brand {brand_id!r} is not a rerank model")
    project_id = env.get("WATSONX_PROJECT_ID")
    payload = {
        "model_id": brand.base_model,
        "project_id": project_id,
        "query": query,
        "inputs": [{"text": d} for d in documents],
    }
    return _post(_wx_url(env, "/ml/v1/text/rerank"), payload, env)


def health_check(env: dict[str, str] | None = None) -> dict[str, Any]:
    """One-shot smoke test against a tiny chat brand. Returns a dict."""
    env = env or _load_env()
    out: dict[str, Any] = {"iam_ok": False, "chat_ok": False}
    try:
        get_iam_token(env)
        out["iam_ok"] = True
    except Exception as e:
        out["iam_error"] = str(e)
        return out
    try:
        r = chat("crowelm-nexus",
                 [{"role": "user", "content": "Say 'CroweLM online' exactly."}],
                 max_tokens=16, temperature=0.0, env=env)
        out["chat_ok"] = True
        out["chat_response"] = r.get("choices", [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        out["chat_error"] = str(e)
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "health":
        print(json.dumps(health_check(), indent=2))
    else:
        print("Usage: python -m config.crowelm.watsonx_adapter health")
