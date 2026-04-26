"""
Crowe Logic Foundry. Control Plane API

Auth, workspace management, plan enforcement, usage metering,
and model gateway. Extends the existing CroweLM API.
"""

import json
import os
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

from .db import get_db, Database
from .plans import (
    TIER_ALLOCATIONS,
    canonical_plan_id,
    is_self_serve_plan,
    stripe_price_id,
    stripe_price_env,
)
from .tokens import hash_api_key, is_supported_api_key, make_pat

# ─── Config ──────────────────────────────────────────────────────────

JWT_SECRET = os.environ.get("CROWE_JWT_SECRET", "dev-secret-change-me-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

app = FastAPI(
    title="Crowe Logic Foundry",
    description="Control Plane. Auth, Workspaces, Plans, Model Gateway",
    version="0.2.5",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://crowelogic.com",
        "https://www.crowelogic.com",
        "https://api.crowelogic.com",
        "https://ide.crowelogic.com",
        "https://app.crowelogic.com",
        "https://code.crowelogic.com",
        "https://crowecode.com",
        "https://www.crowecode.com",
        "https://ai.southwestmushrooms.com",
        "https://ide.southwestmushrooms.com",
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Models ──────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = JWT_EXPIRY_HOURS * 3600
    user_id: str
    email: str


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: Optional[str] = None
    role: str = "researcher"


class WorkspaceCreate(BaseModel):
    name: str
    slug: str
    ws_type: str = "personal"
    plan_id: str = "personal"


class WorkspaceResponse(BaseModel):
    id: str
    org_id: str
    name: str
    slug: str
    ws_type: str
    plan_id: str
    status: str


class ApiKeyCreate(BaseModel):
    label: str = "default"
    scopes: list[str] = ["chat", "vision", "agents"]


class ApiKeyResponse(BaseModel):
    id: str
    key: str  # only returned on creation
    key_prefix: str
    label: str
    scopes: list[str]


class ApiKeySummary(BaseModel):
    id: str
    key_prefix: str
    label: str
    scopes: list[str]
    revoked: bool = False
    last_used_at: Optional[str] = None


class UsageResponse(BaseModel):
    workspace_id: str
    period_start: str
    period_end: str
    tokens: int = 0
    tool_calls: int = 0
    vision_jobs: int = 0
    ide_hours: float = 0
    agent_jobs: int = 0


class PlanResponse(BaseModel):
    id: str
    display_name: str
    max_seats: int
    max_concurrent_sessions: int
    max_ide_hours_month: int
    vision_quota_month: int
    storage_limit_gb: int
    token_budget_month: int
    agent_jobs_month: int


class EntitlementCheck(BaseModel):
    allowed: bool
    reason: Optional[str] = None
    remaining: Optional[int] = None


# ─── Auth helpers ────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    """Hash with bcrypt if available, else SHA-256 (dev fallback)."""
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        return hashlib.sha256(password.encode()).hexdigest()


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        import bcrypt
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ImportError:
        return hashlib.sha256(password.encode()).hexdigest() == password_hash


def _mint_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Database = Depends(get_db),
) -> dict:
    """Extract and validate the Bearer token, return user row."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header")
    claims = _decode_token(authorization[7:])
    user = await db.fetchrow("SELECT * FROM users WHERE id = $1", claims["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return dict(user)


async def _resolve_workspace(workspace_id: str, user: dict, db: Database) -> dict:
    """Fetch workspace and verify the user belongs to its org."""
    ws = await db.fetchrow("SELECT * FROM workspaces WHERE id = $1", workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    member = await db.fetchrow(
        "SELECT 1 FROM org_members WHERE org_id = $1 AND user_id = $2",
        ws["org_id"], user["id"],
    )
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return dict(ws)


# ─── Health ──────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "healthy", "version": "0.2.8", "service": "control-plane"}


# ─── Public pricing ──────────────────────────────────────────────────

@app.get("/api/public/plans")
async def public_plans(db: Database = Depends(get_db)):
    """Public plan listing for the pricing page. No auth required."""
    rows = await db.fetch(
        """
        SELECT id, display_name, tagline, highlights, cta_label,
               monthly_price_cents, annual_price_cents, overage_per_1k_cents,
               max_seats, max_concurrent_sessions, max_ide_hours_month,
               vision_quota_month, storage_limit_gb, agent_jobs_month,
               token_budget_month, features, sort_order
        FROM plans
        WHERE is_public = TRUE
        ORDER BY sort_order, id
        """
    )
    plans = []
    for r in rows:
        raw = dict(r)
        # asyncpg returns JSONB as dict already; highlights may be str under mock driver.
        hi = raw.get("highlights")
        if isinstance(hi, str):
            try:
                hi = json.loads(hi)
            except json.JSONDecodeError:
                hi = []
        feat = raw.get("features")
        if isinstance(feat, str):
            try:
                feat = json.loads(feat)
            except json.JSONDecodeError:
                feat = {}
        plans.append({
            **raw,
            "highlights": hi or [],
            "features": feat or {},
        })
    return {"plans": plans, "currency": "USD"}


# ─── Pricing page (human-readable preview) ───────────────────────────

from fastapi.responses import HTMLResponse as _HTMLResponse


_PRICING_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crowe Logic Code — Pricing</title>
<style>
  :root {
    --gold: #bfa669; --gold-high: #d8c089; --gold-deep: #9c8451;
    --graphite: #0b0b0c; --panel: #121214; --line: #262527;
    --parchment: #e8e2cf; --muted: #948b72;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--graphite); color: var(--parchment);
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  header {
    max-width: 1100px; margin: 0 auto; padding: 64px 24px 24px;
  }
  .eyebrow {
    font-family: "SF Mono", ui-monospace, Menlo, monospace;
    font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--gold); margin: 0 0 12px;
  }
  h1 {
    font: 600 40px/1.1 "SF Pro Display", -apple-system, BlinkMacSystemFont, sans-serif;
    margin: 0 0 12px; color: var(--parchment); letter-spacing: -0.02em;
  }
  h1 em { color: var(--gold); font-style: normal; }
  header p { color: var(--muted); max-width: 640px; margin: 0; font-size: 16px; }
  .grid {
    max-width: 1100px; margin: 32px auto 64px; padding: 0 24px;
    display: grid; gap: 16px;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  }
  .card {
    background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
    padding: 28px 24px 24px; display: flex; flex-direction: column;
    transition: border-color 200ms, transform 200ms;
  }
  .card.featured { border-color: var(--gold-deep); box-shadow: 0 0 0 1px rgba(191,166,105,0.35); }
  .card:hover { border-color: var(--gold-deep); transform: translateY(-2px); }
  .card h2 {
    margin: 0 0 4px; font: 600 20px/1.2 inherit; color: var(--parchment);
    letter-spacing: -0.01em;
  }
  .card .tag { color: var(--gold); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; }
  .card .price {
    font: 600 38px/1 "SF Pro Display", sans-serif; color: var(--parchment);
    margin: 20px 0 4px; letter-spacing: -0.02em;
  }
  .card .price small { font-size: 14px; color: var(--muted); font-weight: 400; }
  .card .annual { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
  .card p.tagline { color: var(--parchment); opacity: 0.78; font-size: 14px; margin: 0 0 16px; min-height: 42px; }
  .card ul {
    list-style: none; padding: 0; margin: 0 0 24px; flex: 1;
    font-size: 14px; color: var(--parchment);
  }
  .card li {
    padding: 8px 0 8px 22px; position: relative; border-top: 1px solid var(--line);
    line-height: 1.4;
  }
  .card li:first-child { border-top: 0; }
  .card li::before {
    content: "+"; position: absolute; left: 0; top: 8px;
    color: var(--gold); font-weight: 600;
  }
  .card a.cta {
    display: block; text-align: center; padding: 12px 16px;
    background: var(--gold); color: var(--graphite);
    text-decoration: none; border-radius: 8px; font-weight: 600;
    letter-spacing: 0.01em; transition: background 160ms;
  }
  .card a.cta:hover { background: var(--gold-high); }
  .card.sales a.cta {
    background: transparent; color: var(--gold); border: 1px solid var(--gold-deep);
  }
  .card.sales a.cta:hover { background: var(--gold); color: var(--graphite); }
  footer {
    max-width: 1100px; margin: 0 auto 64px; padding: 24px;
    color: var(--muted); font-size: 13px; text-align: center; border-top: 1px solid var(--line);
  }
  footer a { color: var(--gold); text-decoration: none; }
  .loading { color: var(--muted); grid-column: 1 / -1; text-align: center; padding: 40px; }
</style>
</head>
<body>
  <header>
    <p class="eyebrow">Crowe Logic Code</p>
    <h1>A premium IDE for developers who <em>build with intent</em>.</h1>
    <p>Crowe Logic Code is a branded VS Code plus a hosted remote IDE, powered end-to-end by the Crowe Logic Foundry agent stack. 27 routed CroweLM models, domain-tuned for mycology, vision, research, and compound design.</p>
  </header>
  <section class="grid" id="plans"><div class="loading">Loading plans...</div></section>
  <footer>
    Billed in USD. Annual plans include roughly 20% off. Taxes calculated at checkout.
    Need a custom deployment? <a href="mailto:sales@crowelogic.com">sales@crowelogic.com</a>
  </footer>
<script>
(async () => {
  const grid = document.getElementById('plans');
  try {
    const res = await fetch('/api/public/plans', { headers: { 'Accept': 'application/json' } });
    const { plans } = await res.json();
    grid.innerHTML = '';
    for (const p of plans) {
      const featured = p.id === 'pro';
      const monthly = p.monthly_price_cents == null ? null : (p.monthly_price_cents / 100);
      const annual = p.annual_price_cents == null ? null : (p.annual_price_cents / 100);
      const priceHtml = monthly == null
        ? `<div class="price">Let's talk</div><div class="annual">Custom terms</div>`
        : `<div class="price">$${monthly.toLocaleString('en-US')}<small> / month</small></div>
           <div class="annual">or $${annual.toLocaleString('en-US')} / year</div>`;
      const items = (p.highlights || []).map(h => `<li>${h}</li>`).join('');
      const sales = p.id === 'enterprise' ? 'sales' : '';
      const href = p.id === 'enterprise'
        ? 'mailto:sales@crowelogic.com?subject=Crowe%20Logic%20Code%20Enterprise'
        : `/checkout?plan=${p.id}`;
      grid.insertAdjacentHTML('beforeend', `
        <article class="card ${featured ? 'featured' : ''} ${sales}">
          <span class="tag">${p.display_name}</span>
          <h2>${p.display_name}</h2>
          ${priceHtml}
          <p class="tagline">${p.tagline || ''}</p>
          <ul>${items}</ul>
          <a class="cta" href="${href}">${p.cta_label || 'Get started'}</a>
        </article>`);
    }
  } catch (e) {
    grid.innerHTML = '<div class="loading">Could not load pricing. Refresh in a moment.</div>';
  }
})();
</script>
</body>
</html>
"""


@app.get("/pricing", response_class=_HTMLResponse)
async def pricing_page():
    """Public pricing page. Reads from /api/public/plans; no auth."""
    return _HTMLResponse(_PRICING_HTML)


# ─── Remote IDE handoff ──────────────────────────────────────────────
#
# Called by the VS Code extension's "Open in Remote IDE" command.
# Mints a short-lived single-use JWT that the session-router at
# ide.crowelogic.com verifies and uses to spawn the user's code-server
# container. If IDE_LAUNCH_ENABLED is not truthy, returns a friendly
# message so the extension can display a useful notice instead of
# opening a broken URL.

IDE_LAUNCH_ENABLED = os.environ.get("IDE_LAUNCH_ENABLED", "").lower() in ("1", "true", "yes")
IDE_JWT_SECRET = os.environ.get("IDE_JWT_SECRET", JWT_SECRET)
IDE_PUBLIC_URL = os.environ.get("IDE_PUBLIC_URL", "https://ide.crowelogic.com")


async def _resolve_pat_or_jwt(
    authorization: Optional[str] = Header(None),
    db: Database = Depends(get_db),
) -> dict:
    """Accept either a `crowe_pat_...` PAT or a JWT bearer token.

    Returns {"user_id", "workspace_id", "plan_id"} so IDE-launch can
    mint a routing token without caring which auth method the client
    used.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header")
    token = authorization[7:]

    if is_supported_api_key(token):
        key_hash = hash_api_key(token)
        row = await db.fetchrow(
            """SELECT ak.user_id, ak.workspace_id, w.plan_id, w.status AS ws_status
               FROM api_keys ak
               JOIN workspaces w ON ak.workspace_id = w.id
               WHERE ak.key_hash = $1 AND NOT ak.revoked""",
            key_hash,
        )
        if not row:
            raise HTTPException(status_code=401, detail="Invalid or revoked API key")
        if row["ws_status"] != "active":
            raise HTTPException(status_code=403, detail="Workspace suspended")
        await db.execute(
            "UPDATE api_keys SET last_used_at = now() WHERE key_hash = $1",
            key_hash,
        )
        return {"user_id": row["user_id"], "workspace_id": row["workspace_id"], "plan_id": row["plan_id"]}

    # Otherwise treat as JWT
    claims = _decode_token(token)
    user_id = claims["sub"]
    ws = await db.fetchrow(
        """SELECT w.id, w.plan_id FROM workspaces w
           JOIN org_members om ON w.org_id = om.org_id
           WHERE om.user_id = $1 AND w.status = 'active'
           ORDER BY w.created_at LIMIT 1""",
        user_id,
    )
    if not ws:
        raise HTTPException(status_code=404, detail="No active workspace for user")
    return {"user_id": user_id, "workspace_id": ws["id"], "plan_id": ws["plan_id"]}


def _ensure_same_workspace(workspace_id: str, auth: dict) -> None:
    if auth.get("workspace_id") != workspace_id:
        raise HTTPException(status_code=403, detail="Token is not valid for this workspace")


@app.post("/api/ide/launch")
async def ide_launch(auth: dict = Depends(_resolve_pat_or_jwt)):
    """Mint a handoff URL the browser can open to drop into a remote IDE session."""
    if not IDE_LAUNCH_ENABLED:
        return {
            "url": None,
            "error": (
                "Hosted Remote IDE is not yet available (Phase 2). "
                "The Crowe Logic extension works against your local Foundry checkout today; "
                "the one-click remote IDE lands with ide.crowelogic.com going live."
            ),
            "status": "pending",
        }

    # Short-lived handoff JWT (60 s, router enforces 5 min maxTokenAge).
    # Claims match the contract in deploy/ide/session-router/auth.js:
    # iss=crowe-logic-ai, aud=crowe-ide-router, role in {admin,subscriber}.
    now = datetime.now(timezone.utc)
    role = "admin" if auth.get("plan_id") == "admin" else "subscriber"
    claims = {
        "iss": "crowe-logic-ai",
        "aud": "crowe-ide-router",
        "sub": auth["user_id"],
        "role": role,
        "workspace_id": auth["workspace_id"],
        "plan_id": auth["plan_id"],
        "iat": now,
        "exp": now + timedelta(seconds=60),
    }
    if auth.get("email"):
        claims["email"] = auth["email"]
    handoff = jwt.encode(claims, IDE_JWT_SECRET, algorithm=JWT_ALGORITHM)
    return {
        "url": f"{IDE_PUBLIC_URL.rstrip('/')}/?token={handoff}",
        "expires_in": 60,
    }


# ─── Auth endpoints ──────────────────────────────────────────────────

@app.post("/api/auth/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: Database = Depends(get_db)):
    existing = await db.fetchrow("SELECT id FROM users WHERE email = $1", req.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")

    user_id = secrets.token_hex(16)
    await db.execute(
        """INSERT INTO users (id, email, display_name, password_hash)
           VALUES ($1, $2, $3, $4)""",
        user_id, req.email, req.display_name or req.email.split("@")[0],
        _hash_password(req.password),
    )

    # Auto-create personal org + workspace
    org_id = secrets.token_hex(16)
    org_slug = req.email.split("@")[0].lower().replace(".", "-")
    await db.execute(
        """INSERT INTO organizations (id, name, slug, owner_id)
           VALUES ($1, $2, $3, $4)""",
        org_id, f"{req.display_name or org_slug}'s Org", org_slug, user_id,
    )
    await db.execute(
        "INSERT INTO org_members (org_id, user_id, role) VALUES ($1, $2, 'owner')",
        org_id, user_id,
    )
    ws_id = secrets.token_hex(16)
    await db.execute(
        """INSERT INTO workspaces (id, org_id, name, slug, plan_id)
           VALUES ($1, $2, 'Default', 'default', 'personal')""",
        ws_id, org_id,
    )

    token = _mint_token(user_id, req.email)
    return TokenResponse(access_token=token, user_id=user_id, email=req.email)


@app.post("/api/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: Database = Depends(get_db)):
    user = await db.fetchrow("SELECT * FROM users WHERE email = $1", req.email)
    if not user or not _verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = _mint_token(user["id"], user["email"])
    return TokenResponse(access_token=token, user_id=user["id"], email=user["email"])


@app.post("/api/auth/refresh", response_model=TokenResponse)
async def refresh(user: dict = Depends(get_current_user)):
    token = _mint_token(user["id"], user["email"])
    return TokenResponse(access_token=token, user_id=user["id"], email=user["email"])


@app.get("/api/auth/me", response_model=UserResponse)
async def auth_me(user: dict = Depends(get_current_user)):
    return UserResponse(
        id=user["id"],
        email=user["email"],
        display_name=user.get("display_name"),
        role=user.get("role", "researcher"),
    )


# ─── Plans ───────────────────────────────────────────────────────────

@app.get("/api/plans", response_model=list[PlanResponse])
async def list_plans(db: Database = Depends(get_db)):
    rows = await db.fetch("SELECT * FROM plans WHERE is_public = TRUE ORDER BY sort_order, max_seats")
    return [PlanResponse(**dict(r)) for r in rows]


# ─── Workspaces ──────────────────────────────────────────────────────

@app.get("/api/workspaces", response_model=list[WorkspaceResponse])
async def list_workspaces(
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    rows = await db.fetch(
        """SELECT w.* FROM workspaces w
           JOIN org_members om ON w.org_id = om.org_id
           WHERE om.user_id = $1 AND w.status = 'active'""",
        user["id"],
    )
    return [WorkspaceResponse(**dict(r)) for r in rows]


@app.post("/api/workspaces", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    req: WorkspaceCreate,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    # Find the user's org (use first owned org for now)
    org = await db.fetchrow(
        "SELECT id FROM organizations WHERE owner_id = $1 LIMIT 1", user["id"]
    )
    if not org:
        raise HTTPException(status_code=400, detail="No organization found")

    ws_id = secrets.token_hex(16)
    plan_id = canonical_plan_id(req.plan_id)
    await db.execute(
        """INSERT INTO workspaces (id, org_id, name, slug, ws_type, plan_id)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        ws_id, org["id"], req.name, req.slug, req.ws_type, plan_id,
    )
    return WorkspaceResponse(
        id=ws_id, org_id=org["id"], name=req.name, slug=req.slug,
        ws_type=req.ws_type, plan_id=plan_id, status="active",
    )


# ─── API Keys ────────────────────────────────────────────────────────

@app.post("/api/workspaces/{workspace_id}/keys", response_model=ApiKeyResponse, status_code=201)
async def create_api_key(
    workspace_id: str,
    req: ApiKeyCreate,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    await _resolve_workspace(workspace_id, user, db)

    raw_key, key_prefix, key_hash = make_pat(workspace_id)
    key_id = secrets.token_hex(16)

    await db.execute(
        """INSERT INTO api_keys (id, workspace_id, user_id, key_hash, key_prefix, label, scopes)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        key_id, workspace_id, user["id"], key_hash, key_prefix,
        req.label, json.dumps(req.scopes),
    )
    return ApiKeyResponse(
        id=key_id, key=raw_key, key_prefix=key_prefix,
        label=req.label, scopes=req.scopes,
    )


@app.get("/api/workspaces/{workspace_id}/keys", response_model=list[ApiKeySummary])
async def list_api_keys(
    workspace_id: str,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    await _resolve_workspace(workspace_id, user, db)
    rows = await db.fetch(
        """SELECT id, key_prefix, label, scopes, revoked, last_used_at
           FROM api_keys
           WHERE workspace_id = $1
           ORDER BY id DESC""",
        workspace_id,
    )
    items = []
    for row in rows:
        raw_scopes = row["scopes"]
        if isinstance(raw_scopes, str):
            try:
                scopes = json.loads(raw_scopes)
            except json.JSONDecodeError:
                scopes = [raw_scopes]
        else:
            scopes = list(raw_scopes or [])
        items.append(
            ApiKeySummary(
                id=row["id"],
                key_prefix=row["key_prefix"],
                label=row["label"],
                scopes=scopes,
                revoked=bool(row["revoked"]),
                last_used_at=row.get("last_used_at"),
            )
        )
    return items


@app.delete("/api/workspaces/{workspace_id}/keys/{key_id}", status_code=204)
async def revoke_api_key(
    workspace_id: str,
    key_id: str,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    await _resolve_workspace(workspace_id, user, db)
    await db.execute(
        "UPDATE api_keys SET revoked = TRUE WHERE id = $1 AND workspace_id = $2",
        key_id, workspace_id,
    )


# ─── Entitlements ────────────────────────────────────────────────────

@app.get("/api/workspaces/{workspace_id}/entitlements/{resource}", response_model=EntitlementCheck)
async def check_entitlement(
    workspace_id: str,
    resource: str,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Check whether a workspace can use a resource (tokens, vision, ide, agents)."""
    ws = await _resolve_workspace(workspace_id, user, db)
    plan = await db.fetchrow("SELECT * FROM plans WHERE id = $1", ws["plan_id"])
    if not plan:
        return EntitlementCheck(allowed=False, reason="No plan assigned")

    budget_map = {
        "tokens": ("token_budget_month", "tokens"),
        "vision": ("vision_quota_month", "vision_job"),
        "ide": ("max_ide_hours_month", "ide_hour"),
        "agents": ("agent_jobs_month", "agent_job"),
    }

    if resource not in budget_map:
        raise HTTPException(status_code=400, detail=f"Unknown resource: {resource}")

    plan_col, event_type = budget_map[resource]
    budget = plan[plan_col]

    if budget == -1:  # unlimited (enterprise)
        return EntitlementCheck(allowed=True, remaining=None)

    if budget == 0:
        return EntitlementCheck(allowed=False, reason=f"{resource} not included in plan")

    # Sum current month usage
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    row = await db.fetchrow(
        """SELECT COALESCE(SUM(quantity), 0) AS used
           FROM usage_events
           WHERE workspace_id = $1 AND event_type = $2 AND recorded_at >= $3""",
        workspace_id, event_type, month_start,
    )
    used = row["used"] if row else 0
    remaining = max(0, budget - used)
    return EntitlementCheck(
        allowed=remaining > 0,
        remaining=remaining,
        reason=None if remaining > 0 else f"Monthly {resource} quota exhausted",
    )


# ─── Usage ───────────────────────────────────────────────────────────

@app.get("/api/workspaces/{workspace_id}/usage", response_model=UsageResponse)
async def get_usage(
    workspace_id: str,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    await _resolve_workspace(workspace_id, user, db)

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    rows = await db.fetch(
        """SELECT event_type, COALESCE(SUM(quantity), 0) AS total
           FROM usage_events
           WHERE workspace_id = $1 AND recorded_at >= $2
           GROUP BY event_type""",
        workspace_id, month_start,
    )

    usage = {r["event_type"]: int(r["total"]) for r in rows}
    return UsageResponse(
        workspace_id=workspace_id,
        period_start=month_start.isoformat(),
        period_end=now.isoformat(),
        tokens=usage.get("tokens", 0),
        tool_calls=usage.get("tool_call", 0),
        vision_jobs=usage.get("vision_job", 0),
        ide_hours=usage.get("ide_hour", 0),
        agent_jobs=usage.get("agent_job", 0),
    )


@app.post("/api/workspaces/{workspace_id}/usage", status_code=201)
async def record_usage(
    workspace_id: str,
    event_type: str,
    quantity: int = 1,
    model: Optional[str] = None,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Record a usage event. Called by the model gateway and agent workers."""
    await _resolve_workspace(workspace_id, user, db)
    await db.execute(
        """INSERT INTO usage_events (workspace_id, user_id, event_type, quantity, model)
           VALUES ($1, $2, $3, $4, $5)""",
        workspace_id, user["id"], event_type, quantity, model,
    )
    return {"recorded": True}


# ─── Public checkout (landing page, unauthenticated) ────────────────

class PublicCheckoutRequest(BaseModel):
    email: EmailStr
    tier_key: str
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


@app.post("/api/billing/checkout/public")
async def public_checkout(req: PublicCheckoutRequest, db: Database = Depends(get_db)):
    """Create a Stripe Checkout session for a not-yet-registered visitor.

    Called by the landing page CTAs. Stores email + tier_key in the
    session metadata so the checkout.session.completed webhook can
    provision the user, workspace, and API key after payment.
    No authentication required; Stripe handles email verification.
    """
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing not configured")

    tier = canonical_plan_id(req.tier_key)
    if not is_self_serve_plan(tier):
        raise HTTPException(status_code=400, detail=f"Unknown tier '{tier}'")
    env_var = stripe_price_env(tier)
    price_id = stripe_price_id(tier)
    if not price_id:
        raise HTTPException(
            status_code=503,
            detail=f"Stripe price not configured for {tier} ({env_var} unset)",
        )

    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    success = req.success_url or os.environ.get(
        "STRIPE_SUCCESS_URL",
        "https://foundry.crowelogic.com/success.html?session_id={CHECKOUT_SESSION_ID}",
    )
    cancel = req.cancel_url or os.environ.get(
        "STRIPE_CANCEL_URL",
        "https://foundry.crowelogic.com/",
    )

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        customer_email=req.email,
        success_url=success,
        cancel_url=cancel,
        allow_promotion_codes=True,
        metadata={
            "tier_key": tier,
            "email": req.email,
            "flow": "public_landing",
        },
        subscription_data={
            "metadata": {"tier_key": tier, "email": req.email},
        },
    )
    return {"session_id": session.id, "url": session.url}


@app.get("/api/billing/checkout/session/{session_id}")
async def get_checkout_result(session_id: str, db: Database = Depends(get_db)):
    """Return the API key provisioned for a completed Checkout session.

    Called by the success page on load. Only returns the key once
    (subsequent calls return 410 Gone). Stripe session id is the
    one-time token that authorizes this read; after the first
    successful read we mark the pending row as claimed.
    """
    row = await db.fetchrow(
        "SELECT * FROM checkout_provisions WHERE stripe_session_id = $1",
        session_id,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="No provisioning record for this session (webhook may not have fired yet)",
        )
    if row["claimed"]:
        raise HTTPException(
            status_code=410,
            detail="API key already retrieved. Rotate via /account if lost.",
        )
    await db.execute(
        "UPDATE checkout_provisions SET claimed = TRUE WHERE stripe_session_id = $1",
        session_id,
    )
    return {
        "email": row["email"],
        "tier_key": row["tier_key"],
        "workspace_id": row["workspace_id"],
        "api_key": row["api_key"],
        "next_steps": [
            "1. Install the Crowe Logic VS Code extension",
            "2. Run `Crowe Logic: Sign In` from the command palette",
            "3. Paste this PAT when prompted",
            "4. Open chat and mention `@crowe-logic`",
        ],
    }


# ─── Credit accounting (Gate 1 billing) ─────────────────────────────

class CreditBalance(BaseModel):
    workspace_id: str
    tier_key: str
    balance: int
    allocation: int
    reset_at: Optional[str] = None
    active: bool = True


class CreditConsumeRequest(BaseModel):
    amount: int
    reason: str = "turn"
    model_label: Optional[str] = None
    metadata: dict = {}


class CreditRefillRequest(BaseModel):
    tier_key: str
    allocation: int
    reset_at: Optional[str] = None


async def _ensure_credit_row(workspace_id: str, db: Database) -> dict:
    """Return the workspace_credits row, creating a zero-balance row if missing.

    A workspace without an explicit credit row defaults to the 'personal'
    tier with zero balance. That means a brand-new unpaid workspace
    cannot consume credits until a refill lands (which happens on
    successful Stripe checkout webhook). BYOK users bypass this
    check entirely at the CLI layer and never hit these endpoints.
    """
    row = await db.fetchrow(
        "SELECT * FROM workspace_credits WHERE workspace_id = $1",
        workspace_id,
    )
    if row:
        return dict(row)

    await db.execute(
        """INSERT INTO workspace_credits (workspace_id, tier_key, balance, allocation)
           VALUES ($1, 'personal', 0, 0)
           ON CONFLICT (workspace_id) DO NOTHING""",
        workspace_id,
    )
    row = await db.fetchrow(
        "SELECT * FROM workspace_credits WHERE workspace_id = $1",
        workspace_id,
    )
    return dict(row) if row else {
        "workspace_id": workspace_id, "tier_key": "personal",
        "balance": 0, "allocation": 0, "reset_at": None, "active": True,
    }


@app.get(
    "/api/workspaces/{workspace_id}/credits",
    response_model=CreditBalance,
)
async def get_credits(
    workspace_id: str,
    auth: dict = Depends(_resolve_pat_or_jwt),
    db: Database = Depends(get_db),
):
    """Return the workspace's current credit balance and tier."""
    _ensure_same_workspace(workspace_id, auth)
    row = await _ensure_credit_row(workspace_id, db)
    return CreditBalance(
        workspace_id=workspace_id,
        tier_key=row["tier_key"],
        balance=row["balance"],
        allocation=row["allocation"],
        reset_at=row["reset_at"].isoformat() if row.get("reset_at") else None,
        active=row.get("active", True),
    )


@app.post(
    "/api/workspaces/{workspace_id}/credits/consume",
    status_code=200,
)
async def consume_credits(
    workspace_id: str,
    req: CreditConsumeRequest,
    auth: dict = Depends(_resolve_pat_or_jwt),
    db: Database = Depends(get_db),
):
    """Atomically decrement credits. Returns 402 if insufficient balance.

    Callers should send the full estimated credit cost for a turn
    before the turn starts. If the estimate later turns out to have
    been wrong (tool overage, synth was skipped, etc.) the CLI sends
    a corrective transaction with a negative or positive delta and
    reason='correction'.
    """
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")

    _ensure_same_workspace(workspace_id, auth)
    row = await _ensure_credit_row(workspace_id, db)
    if not row.get("active", True):
        raise HTTPException(
            status_code=402,
            detail="Workspace credit consumption paused. Check billing status.",
        )
    if row["balance"] < req.amount:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Insufficient credits: balance={row['balance']}, "
                f"requested={req.amount}, tier={row['tier_key']}"
            ),
        )

    # Conditional update so two simultaneous consumes can't oversell.
    updated = await db.fetchrow(
        """UPDATE workspace_credits
              SET balance = balance - $2, updated_at = now()
            WHERE workspace_id = $1 AND balance >= $2 AND active = TRUE
        RETURNING balance""",
        workspace_id, req.amount,
    )
    if updated is None:
        raise HTTPException(status_code=402, detail="Insufficient credits (race)")

    await db.execute(
        """INSERT INTO credit_transactions
               (workspace_id, amount, reason, model_label, metadata)
           VALUES ($1, $2, $3, $4, $5)""",
        workspace_id, -req.amount, req.reason,
        req.model_label, json.dumps(req.metadata) if req.metadata else "{}",
    )

    return {
        "workspace_id": workspace_id,
        "amount_consumed": req.amount,
        "balance": updated["balance"],
    }


@app.post(
    "/api/workspaces/{workspace_id}/credits/refill",
    status_code=200,
)
async def refill_credits(
    workspace_id: str,
    req: CreditRefillRequest,
    user: dict = Depends(get_current_user),
    db: Database = Depends(get_db),
):
    """Refill the workspace's credits to the tier allocation.

    Called by the Stripe webhook on invoice.paid and
    customer.subscription.updated so a successful payment immediately
    restores usage. Also callable by workspace owners for manual
    adjustments (audit logged as reason='manual_refill').
    """
    await _resolve_workspace(workspace_id, user, db)

    await db.execute(
        """INSERT INTO workspace_credits
               (workspace_id, tier_key, balance, allocation, reset_at, active)
           VALUES ($1, $2, $3, $3, $4, TRUE)
           ON CONFLICT (workspace_id) DO UPDATE SET
               tier_key = EXCLUDED.tier_key,
               balance = EXCLUDED.balance,
               allocation = EXCLUDED.allocation,
               reset_at = EXCLUDED.reset_at,
               active = TRUE,
               updated_at = now()""",
        workspace_id, req.tier_key, req.allocation, req.reset_at,
    )

    await db.execute(
        """INSERT INTO credit_transactions
               (workspace_id, amount, reason, metadata)
           VALUES ($1, $2, 'refill', $3)""",
        workspace_id, req.allocation,
        json.dumps({"tier": req.tier_key, "reset_at": req.reset_at}),
    )

    return {
        "workspace_id": workspace_id,
        "tier_key": req.tier_key,
        "balance": req.allocation,
        "allocation": req.allocation,
        "reset_at": req.reset_at,
    }


# ─── Stripe webhooks ─────────────────────────────────────────────────

@app.post("/api/billing/webhook")
async def stripe_webhook(request: Request, db: Database = Depends(get_db)):
    """Handle Stripe webhook events."""
    if not STRIPE_SECRET_KEY or not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Billing not configured")

    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    payload = await request.body()
    sig = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Log every event
    await db.execute(
        """INSERT INTO billing_events (stripe_event_id, event_type, payload)
           VALUES ($1, $2, $3) ON CONFLICT (stripe_event_id) DO NOTHING""",
        event["id"], event["type"], payload.decode(),
    )

    if event["type"] == "checkout.session.completed":
        await _provision_from_checkout(db, event["data"]["object"])

    elif event["type"] == "invoice.paid":
        sub_id = event["data"]["object"].get("subscription")
        if sub_id:
            await db.execute(
                """UPDATE subscriptions SET status = 'active', updated_at = now()
                   WHERE stripe_subscription_id = $1""",
                sub_id,
            )
            # Refill credits for the workspace bound to this subscription.
            await _refill_credits_for_subscription(db, sub_id)

    elif event["type"] == "customer.subscription.updated":
        sub = event["data"]["object"]
        await db.execute(
            """UPDATE subscriptions
               SET status = $1,
                   current_period_start = to_timestamp($2),
                   current_period_end = to_timestamp($3),
                   updated_at = now()
               WHERE stripe_subscription_id = $4""",
            sub["status"], sub["current_period_start"],
            sub["current_period_end"], sub["id"],
        )
        # Plan changes (upgrade/downgrade) come through this event. Refill
        # to the new tier's allocation so the upgrade takes effect
        # immediately rather than waiting for the next invoice.paid.
        if sub["status"] == "active":
            await _refill_credits_for_subscription(db, sub["id"])

    elif event["type"] == "invoice.payment_failed":
        sub_id = event["data"]["object"].get("subscription")
        if sub_id:
            # Pause credit consumption until payment is resolved. Don't
            # zero the balance - the user keeps whatever was unspent in
            # case payment recovers within the grace window.
            await db.execute(
                """UPDATE workspace_credits
                      SET active = FALSE, updated_at = now()
                    WHERE workspace_id IN (
                        SELECT id FROM workspaces
                         WHERE stripe_subscription_id = $1
                    )""",
                sub_id,
            )

    elif event["type"] == "customer.subscription.deleted":
        sub_id = event["data"]["object"]["id"]
        await db.execute(
            """UPDATE subscriptions SET status = 'cancelled', updated_at = now()
               WHERE stripe_subscription_id = $1""",
            sub_id,
        )
        await db.execute(
            """UPDATE workspaces SET status = 'suspended'
               WHERE stripe_subscription_id = $1""",
            sub_id,
        )
        # Cancellation zeroes the allocation and deactivates consumption.
        await db.execute(
            """UPDATE workspace_credits
                  SET active = FALSE, allocation = 0, updated_at = now()
                WHERE workspace_id IN (
                    SELECT id FROM workspaces
                     WHERE stripe_subscription_id = $1
                )""",
            sub_id,
        )

    return {"received": True}


# ─── Webhook helpers ────────────────────────────────────────────────

async def _provision_from_checkout(db, session_obj: dict) -> None:
    """Create user + workspace + api_key after a successful Checkout session.

    Called by the checkout.session.completed webhook. Idempotent via
    the checkout_provisions table: a repeat webhook delivery for the
    same session_id short-circuits before doing any writes.

    The API key is formatted ``crowe_pat_<workspace_id>_<secret>`` so
    the VS Code extension can accept it directly and the CLI can infer
    the workspace id without an extra round trip.
    """
    session_id = session_obj.get("id")
    if not session_id:
        return

    existing = await db.fetchrow(
        "SELECT 1 FROM checkout_provisions WHERE stripe_session_id = $1",
        session_id,
    )
    if existing:
        return   # idempotent retry

    metadata = session_obj.get("metadata") or {}
    customer_details = session_obj.get("customer_details") or {}
    email = (
        session_obj.get("customer_email")
        or customer_details.get("email")
        or metadata.get("email")
        or ""
    ).strip().lower()
    tier_key = canonical_plan_id(metadata.get("tier_key") or "personal")
    if not email:
        # No email means we can't provision. Log and bail.
        await db.execute(
            """INSERT INTO checkout_provisions
                   (stripe_session_id, email, tier_key, workspace_id, api_key,
                    claimed, error)
               VALUES ($1, '', $2, '', '', TRUE, 'no_email_in_session')""",
            session_id, tier_key,
        )
        return

    # Find or create the user by email.
    user = await db.fetchrow("SELECT * FROM users WHERE email = $1", email)
    if user is None:
        user_id = secrets.token_hex(16)
        # No password: the user will set one via password-reset on first
        # web login, or stay API-key-only forever (Crowe Logic doesn't
        # strictly require a web login for CLI-only usage).
        placeholder = secrets.token_hex(32)   # unreachable hash, forces reset
        await db.execute(
            """INSERT INTO users (id, email, display_name, password_hash)
               VALUES ($1, $2, $3, $4)""",
            user_id, email, email.split("@")[0],
            _hash_password(placeholder),
        )
    else:
        user_id = user["id"]

    customer_id = session_obj.get("customer", "") or ""
    subscription_id = session_obj.get("subscription", "") or ""

    # Find or create the user's personal org.
    org = await db.fetchrow(
        "SELECT * FROM organizations WHERE owner_id = $1 ORDER BY created_at LIMIT 1",
        user_id,
    )
    if org is None:
        org_id = secrets.token_hex(16)
        org_slug = email.split("@")[0].lower().replace(".", "-")
        await db.execute(
            """INSERT INTO organizations (id, name, slug, owner_id, stripe_customer_id)
               VALUES ($1, $2, $3, $4, $5)""",
            org_id, f"{email.split('@')[0]} Org", org_slug, user_id,
            customer_id,
        )
        await db.execute(
            "INSERT INTO org_members (org_id, user_id, role) VALUES ($1, $2, 'owner')",
            org_id, user_id,
        )
    else:
        org_id = org["id"]
        await db.execute(
            """UPDATE organizations
                  SET stripe_customer_id = COALESCE(NULLIF($2, ''), stripe_customer_id)
                WHERE id = $1""",
            org_id, customer_id,
        )

    # Find or create the default workspace.
    ws = await db.fetchrow(
        "SELECT * FROM workspaces WHERE org_id = $1 ORDER BY created_at LIMIT 1",
        org_id,
    )
    if ws is None:
        workspace_id = secrets.token_hex(16)
        await db.execute(
            """INSERT INTO workspaces (id, org_id, name, slug, plan_id,
                                        stripe_subscription_id)
               VALUES ($1, $2, 'Default', 'default', $3, $4)""",
            workspace_id, org_id, tier_key, subscription_id,
        )
    else:
        workspace_id = ws["id"]
        # Stamp the Stripe ids so the refill helper can find this workspace.
        await db.execute(
            """UPDATE workspaces
                  SET plan_id = $2,
                      stripe_subscription_id = COALESCE(NULLIF($3, ''), stripe_subscription_id)
                WHERE id = $1""",
            workspace_id, tier_key, subscription_id,
        )

    if subscription_id:
        await db.execute(
            """INSERT INTO subscriptions
                   (id, workspace_id, plan_id, stripe_subscription_id, status,
                    current_period_start, current_period_end)
               VALUES ($1, $2, $3, $4, 'active', now(), now())
               ON CONFLICT (workspace_id)
               DO UPDATE SET
                   plan_id = $3,
                   stripe_subscription_id = $4,
                   status = 'active',
                   updated_at = now()""",
            f"sub_{workspace_id}", workspace_id, tier_key, subscription_id,
        )

    api_key, key_prefix, key_hash = make_pat(workspace_id)
    key_id = secrets.token_hex(16)
    await db.execute(
        """INSERT INTO api_keys
               (id, workspace_id, user_id, key_hash, key_prefix, label, scopes)
           VALUES ($1, $2, $3, $4, $5, 'VS Code (checkout)', '["chat","vision","agents","ide"]')""",
        key_id, workspace_id, user_id, key_hash, key_prefix,
    )

    # Refill credits immediately so the user can start using the product
    # without waiting for a separate invoice.paid webhook.
    allocation = TIER_ALLOCATIONS.get(tier_key, TIER_ALLOCATIONS["personal"])
    await db.execute(
        """INSERT INTO workspace_credits
               (workspace_id, tier_key, balance, allocation, active)
           VALUES ($1, $2, $3, $3, TRUE)
           ON CONFLICT (workspace_id) DO UPDATE SET
               tier_key = EXCLUDED.tier_key,
               balance = EXCLUDED.balance,
               allocation = EXCLUDED.allocation,
               active = TRUE,
               updated_at = now()""",
        workspace_id, tier_key, allocation,
    )

    # Store the provisioned key in the one-time-retrieval table so the
    # success page can display it. The table has (claimed BOOLEAN) so
    # subsequent GETs return 410 Gone.
    await db.execute(
        """INSERT INTO checkout_provisions
               (stripe_session_id, email, tier_key, workspace_id, api_key,
                claimed, error)
           VALUES ($1, $2, $3, $4, $5, FALSE, '')""",
        session_id, email, tier_key, workspace_id, api_key,
    )


async def _refill_credits_for_subscription(db, stripe_subscription_id: str) -> None:
    """Refill credits on every workspace bound to the given Stripe subscription.

    Reads the plan_key from the subscription row (set during checkout
    completion) and looks up the tier's allocation in TIER_ALLOCATIONS.
    Quietly noops if no workspace or plan is associated, so webhook
    retries stay idempotent.
    """
    rows = await db.fetch(
        """SELECT w.id AS workspace_id, s.plan_id, s.current_period_end
             FROM workspaces w
        LEFT JOIN subscriptions s ON s.stripe_subscription_id = w.stripe_subscription_id
            WHERE w.stripe_subscription_id = $1""",
        stripe_subscription_id,
    )
    for row in rows:
        tier_key = canonical_plan_id(row["plan_id"] or "personal")
        allocation = TIER_ALLOCATIONS.get(tier_key, TIER_ALLOCATIONS["personal"])
        reset_at = row.get("current_period_end")

        await db.execute(
            """INSERT INTO workspace_credits
                   (workspace_id, tier_key, balance, allocation, reset_at, active)
               VALUES ($1, $2, $3, $3, $4, TRUE)
               ON CONFLICT (workspace_id) DO UPDATE SET
                   tier_key = EXCLUDED.tier_key,
                   balance = EXCLUDED.balance,
                   allocation = EXCLUDED.allocation,
                   reset_at = EXCLUDED.reset_at,
                   active = TRUE,
                   updated_at = now()""",
            row["workspace_id"], tier_key, allocation, reset_at,
        )
        await db.execute(
            """INSERT INTO credit_transactions
                   (workspace_id, amount, reason, metadata)
               VALUES ($1, $2, 'webhook_refill', $3)""",
            row["workspace_id"], allocation,
            json.dumps({"tier": tier_key, "stripe_sub": stripe_subscription_id}),
        )
