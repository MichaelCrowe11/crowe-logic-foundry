"""
Crowe Logic Foundry — Control Plane API

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

# ─── Config ──────────────────────────────────────────────────────────

JWT_SECRET = os.environ.get("CROWE_JWT_SECRET", "dev-secret-change-me-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

app = FastAPI(
    title="Crowe Logic Foundry",
    description="Control Plane — Auth, Workspaces, Plans, Model Gateway",
    version="0.2.5",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://crowelogic.com",
        "https://ai.southwestmushrooms.com",
        "https://ide.southwestmushrooms.com",
        "http://localhost:3000",
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
    plan_id: str = "developer"


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
    return {"status": "healthy", "version": "0.2.5", "service": "control-plane"}


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
           VALUES ($1, $2, 'Default', 'default', 'developer')""",
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
    rows = await db.fetch("SELECT * FROM plans ORDER BY max_seats")
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
    await db.execute(
        """INSERT INTO workspaces (id, org_id, name, slug, ws_type, plan_id)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        ws_id, org["id"], req.name, req.slug, req.ws_type, req.plan_id,
    )
    return WorkspaceResponse(
        id=ws_id, org_id=org["id"], name=req.name, slug=req.slug,
        ws_type=req.ws_type, plan_id=req.plan_id, status="active",
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

    raw_key = f"cl_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:11]
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

    if event["type"] == "invoice.paid":
        sub_id = event["data"]["object"].get("subscription")
        if sub_id:
            await db.execute(
                """UPDATE subscriptions SET status = 'active', updated_at = now()
                   WHERE stripe_subscription_id = $1""",
                sub_id,
            )

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

    return {"received": True}
