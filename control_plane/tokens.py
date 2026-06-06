"""API token helpers shared by web, checkout, gateway, and tests."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets


PAT_PREFIX = "crowe_pat_"
LEGACY_KEY_PREFIXES = ("cl_", "clk_")


def make_pat(workspace_id: str) -> tuple[str, str, str]:
    """Return ``(raw_key, key_prefix, key_hash)`` for a workspace-scoped PAT."""
    raw_key = f"{PAT_PREFIX}{workspace_id}_{secrets.token_hex(24)}"
    return raw_key, raw_key[:18], hash_api_key(raw_key)


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def is_supported_api_key(raw_key: str) -> bool:
    return raw_key.startswith(PAT_PREFIX) or raw_key.startswith(LEGACY_KEY_PREFIXES)


def workspace_id_from_api_key(raw_key: str) -> str | None:
    """Extract workspace id from keys that encode it.

    Legacy ``clk_<workspace_id>_<secret>`` and launch ``crowe_pat_<workspace_id>_<secret>``
    both encode the workspace id. Older ``crowe_pat_<randomhex>`` keys do not.
    """
    if raw_key.startswith("clk_"):
        parts = raw_key.split("_", 2)
        return parts[1] if len(parts) >= 3 and parts[1] else None
    if raw_key.startswith(PAT_PREFIX):
        body = raw_key[len(PAT_PREFIX):]
        parts = body.split("_", 1)
        return parts[0] if len(parts) == 2 and parts[0] else None
    return None


ANON_PREFIX = "crowe_anon_"


def _anon_sig(device_id: str) -> str:
    secret = os.environ["CROWE_ANON_SIGNING_SECRET"]
    return hmac.new(secret.encode(), device_id.encode(), hashlib.sha256).hexdigest()[:32]


def make_device_token() -> tuple[str, str]:
    """Mint an anonymous device token. Returns (device_id, raw_token).

    Stateless HMAC: the gateway verifies without a DB row, so registration is
    cheap and revocation is by daily cap rather than by token.
    """
    device_id = secrets.token_hex(12)
    return device_id, f"{ANON_PREFIX}{device_id}.{_anon_sig(device_id)}"


def verify_device_token(raw: str) -> str | None:
    """Return the device_id for a valid anonymous token, else None."""
    if not raw or not raw.startswith(ANON_PREFIX):
        return None
    device_id, _, sig = raw[len(ANON_PREFIX):].partition(".")
    if not device_id or not sig:
        return None
    if not hmac.compare_digest(sig, _anon_sig(device_id)):
        return None
    return device_id
