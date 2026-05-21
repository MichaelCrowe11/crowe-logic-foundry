"""Tests for the JWT auth path on the gateway resolver.

A browser session authenticates with a JWT minted by `/auth/login`
(carries `sub`=user_id). The gateway resolver must accept this token
on the same `Authorization: Bearer ...` header it already uses for
workspace API keys, and resolve to the same dict shape.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jwt")

import jwt as pyjwt

import control_plane
import control_plane.gateway as gateway_mod


JWT_SECRET = control_plane.JWT_SECRET
JWT_ALGORITHM = control_plane.JWT_ALGORITHM


def _mint(user_id: str, *, expired: bool = False, bad_secret: bool = False) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": f"{user_id}@example.com",
        "iat": now - timedelta(hours=1),
        "exp": now - timedelta(minutes=1) if expired else now + timedelta(hours=1),
    }
    secret = "wrong-secret" if bad_secret else JWT_SECRET
    return pyjwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


class _FakeDb:
    """Minimal asyncpg-row stub. Each fetchrow result is a dict; the
    resolver returns dict(row) so a dict is fine.
    """
    def __init__(self, *, ws_row: dict | None = None, ak_row: dict | None = None):
        self._ws_row = ws_row
        self._ak_row = ak_row

    async def fetchrow(self, sql: str, *args, **kwargs):
        if "FROM api_keys" in sql:
            return self._ak_row
        if "FROM workspaces" in sql:
            return self._ws_row
        return None

    async def execute(self, *args, **kwargs):
        return None


# ---------------------------------------------------------------------
# _resolve_jwt_principal — direct unit tests
# ---------------------------------------------------------------------

def test_jwt_principal_resolves_default_workspace():
    import asyncio
    token = _mint("user_abc")
    db = _FakeDb(ws_row={
        "workspace_id": "ws_main",
        "plan_id": "scale",
        "ws_status": "active",
        "user_id": "user_abc",
    })
    out = asyncio.run(gateway_mod._resolve_jwt_principal(token, db))
    assert out["workspace_id"] == "ws_main"
    assert out["user_id"] == "user_abc"
    assert out["plan_id"] == "scale"


def test_jwt_principal_honors_workspace_hint():
    import asyncio
    token = _mint("user_abc")
    db = _FakeDb(ws_row={
        "workspace_id": "ws_other",
        "plan_id": "team",
        "ws_status": "active",
        "user_id": "user_abc",
    })
    out = asyncio.run(gateway_mod._resolve_jwt_principal(
        token, db, workspace_id_hint="ws_other",
    ))
    assert out["workspace_id"] == "ws_other"
    assert out["plan_id"] == "team"


def test_jwt_principal_404_when_no_workspace():
    import asyncio
    token = _mint("user_orphan")
    db = _FakeDb(ws_row=None)
    with pytest.raises(Exception) as exc_info:
        asyncio.run(gateway_mod._resolve_jwt_principal(token, db))
    # FastAPI HTTPException with status 404
    assert getattr(exc_info.value, "status_code", None) == 404


def test_jwt_principal_rejects_expired_token():
    import asyncio
    token = _mint("user_abc", expired=True)
    db = _FakeDb()
    with pytest.raises(Exception) as exc_info:
        asyncio.run(gateway_mod._resolve_jwt_principal(token, db))
    assert getattr(exc_info.value, "status_code", None) == 401


def test_jwt_principal_rejects_bad_signature():
    import asyncio
    token = _mint("user_abc", bad_secret=True)
    db = _FakeDb()
    with pytest.raises(Exception) as exc_info:
        asyncio.run(gateway_mod._resolve_jwt_principal(token, db))
    assert getattr(exc_info.value, "status_code", None) == 401


def test_jwt_principal_rejects_403_on_workspace_hint_mismatch():
    """If a user passes X-Workspace-Id but isn't a member, return 403."""
    import asyncio
    token = _mint("user_abc")
    db = _FakeDb(ws_row=None)  # JOIN returns no row → not a member
    with pytest.raises(Exception) as exc_info:
        asyncio.run(gateway_mod._resolve_jwt_principal(
            token, db, workspace_id_hint="ws_someone_else",
        ))
    assert getattr(exc_info.value, "status_code", None) == 403


# ---------------------------------------------------------------------
# _resolve_api_key — dispatch table
# ---------------------------------------------------------------------

def test_resolve_api_key_dispatches_to_jwt_when_bearer_is_not_a_key():
    """A Bearer token that doesn't match is_supported_api_key falls
    through to JWT decoding."""
    import asyncio
    token = _mint("user_abc")
    db = _FakeDb(ws_row={
        "workspace_id": "ws_main",
        "plan_id": "scale",
        "ws_status": "active",
        "user_id": "user_abc",
    })
    out = asyncio.run(gateway_mod._resolve_api_key(
        authorization=f"Bearer {token}",
        x_api_key=None,
        x_workspace_id=None,
        db=db,
    ))
    assert out["workspace_id"] == "ws_main"


def test_resolve_api_key_401_when_no_credential():
    import asyncio
    db = _FakeDb()
    with pytest.raises(Exception) as exc_info:
        asyncio.run(gateway_mod._resolve_api_key(
            authorization=None, x_api_key=None, x_workspace_id=None, db=db,
        ))
    assert getattr(exc_info.value, "status_code", None) == 401
