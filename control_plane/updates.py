"""
Crowe Logic Code update server.

VS Code's Electron updater queries `${updateUrl}/api/update/${platform}/${quality}/${commit}`
and expects either an HTTP 204 (no update) or a JSON body of the form:

    {
      "url":     "https://.../crowe-logic-code-darwin-arm64-1.118.0.zip",
      "name":    "1.118.0",
      "version": "<commit-sha>",
      "productVersion": "1.118.0",
      "hash":    "<sha256 of zip>",
      "timestamp": 1714000000000,
      "supportsFastUpdate": true
    }

We mirror that contract so Crowe Logic Code auto-updates from our CDN, not
Microsoft's. Every patched VS Code build we publish writes a `latest.json`
per (platform, quality) into `releases/<quality>/<platform>/latest.json`
and the binary into `releases/<quality>/<platform>/<version>.zip`.

Wire-up:
  - DNS: updates.crowelogic.com CNAME to this app
  - product.json: "updateUrl": "https://updates.crowelogic.com"
  - On every signed build: upload zip + write latest.json (see ci/upload-release.sh)

Why we run this ourselves: when Crowe Logic Code points at Microsoft's
update endpoint, every Microsoft update silently overwrites our patched
files (product.json, icons, nls.messages.json) and trips VS Code's
"appears to be corrupt" integrity check. Owning the update channel is the
only way to make the rebrand survive auto-update.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Response

router = APIRouter(prefix="/api/update", tags=["updates"])
log = logging.getLogger(__name__)

# Releases bucket. Convention:
#   {RELEASE_BASE_URL}/{quality}/{platform}/latest.json
#   {RELEASE_BASE_URL}/{quality}/{platform}/{version}.zip
# In dev we read from a local `releases/` directory; in prod we proxy from
# Cloudflare R2 / S3 / Azure Blob via a public CDN URL.
RELEASE_BASE_URL = os.environ.get(
    "CROWE_LOGIC_RELEASE_BASE_URL",
    "https://releases.crowelogic.com",
)

# Map the platform tags VS Code sends to our release directory layout.
# VS Code sends "darwin-arm64", "darwin-x64", "win32-x64", "linux-x64", etc.
SUPPORTED_PLATFORMS = {
    "darwin",
    "darwin-arm64",
    "darwin-x64",
    "win32",
    "win32-x64",
    "win32-arm64",
    "linux-x64",
    "linux-arm64",
    "linux-armhf",
}

# A tiny in-process cache: latest.json rarely changes, but the updater
# polls aggressively (every few hours). One TTL bucket per (platform, quality).
_CACHE: dict[tuple[str, str], tuple[float, Optional[dict[str, Any]]]] = {}
_CACHE_TTL_S = 60.0


async def _load_latest(platform: str, quality: str) -> Optional[dict[str, Any]]:
    """Fetch latest manifest for a (platform, quality). Returns None when no
    manifest exists (treated as "no update available")."""
    key = (platform, quality)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL_S:
        return cached[1]

    # Strategy 1: local file (dev mode + testing).
    local_path = os.path.join(
        os.path.dirname(__file__), "..", "releases", quality, platform, "latest.json"
    )
    if os.path.isfile(local_path):
        try:
            with open(local_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            _CACHE[key] = (now, manifest)
            return manifest
        except Exception as e:
            log.warning("update server: failed to read %s: %s", local_path, e)

    # Strategy 2: HTTP fetch from the releases CDN.
    try:
        import httpx

        url = f"{RELEASE_BASE_URL}/{quality}/{platform}/latest.json"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                manifest = resp.json()
                _CACHE[key] = (now, manifest)
                return manifest
            if resp.status_code == 404:
                _CACHE[key] = (now, None)
                return None
            log.warning("update server: %s returned %s", url, resp.status_code)
    except Exception as e:
        log.warning("update server: fetch failed for %s/%s: %s", platform, quality, e)

    _CACHE[key] = (now, None)
    return None


@router.get("/{platform}/{quality}/{commit}")
async def check_update(platform: str, quality: str, commit: str) -> Response:
    """VS Code update probe endpoint.

    Returns 204 (no update) when the client is already on the latest
    commit, or a JSON manifest pointing at the new build otherwise.
    """
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"unsupported platform: {platform}")
    if quality not in ("stable", "insider"):
        raise HTTPException(status_code=400, detail=f"unsupported quality: {quality}")

    manifest = await _load_latest(platform, quality)
    if manifest is None:
        # No manifest at all = no update available. 204 is the documented
        # "client is current" signal.
        return Response(status_code=204)

    latest_version = manifest.get("version") or ""
    if latest_version and latest_version == commit:
        return Response(status_code=204)

    # Minimum required fields VS Code's updater expects.
    payload = {
        "url": manifest["url"],
        "name": manifest.get("name") or manifest["productVersion"],
        "version": manifest["version"],
        "productVersion": manifest["productVersion"],
        "hash": manifest.get("hash", ""),
        "timestamp": manifest.get("timestamp", int(time.time() * 1000)),
        "supportsFastUpdate": manifest.get("supportsFastUpdate", True),
    }
    return Response(
        content=json.dumps(payload),
        media_type="application/json",
        status_code=200,
        headers={
            "cache-control": "public, max-age=60",
        },
    )


@router.get("/healthz")
async def update_healthz() -> dict[str, str]:
    """Cheap liveness probe for the updates subsystem."""
    return {"status": "ok", "service": "updates"}
