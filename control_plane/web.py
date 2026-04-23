"""
Crowe Logic Code public web flow: signup, login, checkout, billing status.

Minimal server-rendered pages so the pricing funnel works end-to-end without
a separate frontend project. Auth is cookie-based for the web surface; the
same JWTs work against the API surface via the Authorization header.

Design choices:

- Cookies are httpOnly + Secure + SameSite=Lax so Stripe's redirect back to
  /billing/success brings the session with it.
- Signup immediately creates user + org + workspace by reusing the existing
  /api/auth/register logic via a direct function call (not an HTTP roundtrip).
- /checkout is a single GET handler that redirects to Stripe's hosted page,
  keeping our frontend free of the Stripe.js dependency for the initial cut.
- The webhook handler already in billing.py persists subscription state, so
  the success page just needs to read from DB (or show a neutral message).

The surface is intentionally small; richer account dashboards, password
reset, email verification, and magic links are Phase 2 work.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import hashlib
import json

import jwt as pyjwt
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from .db import Database, get_db


router = APIRouter(tags=["web"])

COOKIE_NAME = "crowe_session"
COOKIE_MAX_AGE = 60 * 60 * 24  # 24 hours, matches JWT_EXPIRY_HOURS in __init__.py
JWT_SECRET = os.environ.get("CROWE_JWT_SECRET", "dev-secret-change-me-in-prod")
JWT_ALG = "HS256"
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")

PLAN_PRICE_MAP = {
    "developer":        os.environ.get("STRIPE_PRICE_DEVELOPER", ""),
    "developer_annual": os.environ.get("STRIPE_PRICE_DEVELOPER_ANNUAL", ""),
    "studio":           os.environ.get("STRIPE_PRICE_STUDIO", ""),
    "studio_annual":    os.environ.get("STRIPE_PRICE_STUDIO_ANNUAL", ""),
    "lab":              os.environ.get("STRIPE_PRICE_LAB", ""),
    "lab_annual":       os.environ.get("STRIPE_PRICE_LAB_ANNUAL", ""),
}


def _hash_password(password: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest()


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        import bcrypt
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ImportError:
        import hashlib
        return hashlib.sha256(password.encode()).hexdigest() == password_hash


def _mint_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    return pyjwt.encode(
        {"sub": user_id, "email": email, "iat": now, "exp": now + timedelta(seconds=COOKIE_MAX_AGE)},
        JWT_SECRET, algorithm=JWT_ALG,
    )


def _decode_token(token: str) -> Optional[dict]:
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except pyjwt.PyJWTError:
        return None


def _set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        key=COOKIE_NAME, value=token,
        max_age=COOKIE_MAX_AGE, httponly=True, secure=True, samesite="lax", path="/",
    )


async def _current_user(request: Request, db: Database) -> Optional[dict]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    claims = _decode_token(token)
    if not claims:
        return None
    user = await db.fetchrow("SELECT * FROM users WHERE id = $1", claims["sub"])
    return dict(user) if user else None


async def _user_default_workspace(user_id: str, db: Database) -> Optional[dict]:
    row = await db.fetchrow(
        """SELECT w.* FROM workspaces w
           JOIN org_members om ON w.org_id = om.org_id
           WHERE om.user_id = $1 AND w.status = 'active'
           ORDER BY w.created_at
           LIMIT 1""",
        user_id,
    )
    return dict(row) if row else None


def _page(title: str, body: str, subtitle: str = "") -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Crowe Logic Code</title>
<style>
  :root {{
    --gold:#bfa669;--gold-high:#d8c089;--gold-deep:#9c8451;
    --graphite:#0b0b0c;--panel:#121214;--line:#262527;
    --parchment:#e8e2cf;--muted:#948b72;--err:#d8725a;
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--graphite);color:var(--parchment);
       font:15px/1.55 -apple-system,BlinkMacSystemFont,"SF Pro Text","Inter",sans-serif;
       -webkit-font-smoothing:antialiased;min-height:100vh;display:flex;flex-direction:column}}
  a{{color:var(--gold);text-decoration:none}}
  a:hover{{color:var(--gold-high)}}
  nav{{max-width:1100px;width:100%;margin:0 auto;padding:20px 24px;display:flex;justify-content:space-between;align-items:center}}
  nav .brand{{color:var(--parchment);font-weight:600;letter-spacing:-0.01em}}
  nav .brand b{{color:var(--gold)}}
  main{{flex:1;max-width:460px;width:100%;margin:40px auto;padding:0 24px}}
  h1{{font:600 28px/1.2 "SF Pro Display",sans-serif;color:var(--parchment);margin:0 0 6px;letter-spacing:-0.015em}}
  h1 em{{color:var(--gold);font-style:normal}}
  .sub{{color:var(--muted);margin:0 0 28px}}
  form{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:28px 24px}}
  label{{display:block;font-size:13px;color:var(--muted);margin:0 0 6px}}
  input[type=text],input[type=email],input[type=password]{{
    width:100%;background:#0e0e0f;color:var(--parchment);
    border:1px solid var(--line);border-radius:8px;padding:11px 12px;
    font:14px/1.4 inherit;outline:none;transition:border-color 160ms;
    margin:0 0 14px;
  }}
  input:focus{{border-color:var(--gold-deep)}}
  button{{
    width:100%;background:var(--gold);color:var(--graphite);border:0;
    border-radius:8px;padding:12px 16px;font:600 14px/1 inherit;cursor:pointer;
    letter-spacing:0.01em;transition:background 160ms;margin-top:6px;
  }}
  button:hover{{background:var(--gold-high)}}
  .alt{{margin:18px 0 0;color:var(--muted);font-size:13px;text-align:center}}
  .err{{background:rgba(216,114,90,0.08);border:1px solid var(--err);
        color:var(--err);padding:10px 12px;border-radius:8px;margin:0 0 16px;font-size:13px}}
  .ok{{background:rgba(191,166,105,0.08);border:1px solid var(--gold-deep);
       color:var(--gold);padding:14px 16px;border-radius:8px;margin:0 0 16px}}
  footer{{max-width:1100px;width:100%;margin:0 auto;padding:24px;color:var(--muted);font-size:12px;text-align:center}}
  .eyebrow{{font-family:"SF Mono",ui-monospace,Menlo,monospace;font-size:11px;letter-spacing:0.14em;
    text-transform:uppercase;color:var(--gold);margin:0 0 10px}}
</style></head>
<body>
<nav><span class="brand">Crowe Logic <b>Code</b></span><span><a href="/pricing">Pricing</a></span></nav>
<main>
  <p class="eyebrow">{title}</p>
  <h1>{subtitle or title}</h1>
  {body}
</main>
<footer>Crowe Logic, Inc. &middot; <a href="https://crowelogic.com">crowelogic.com</a></footer>
</body></html>"""


# ─── Signup ──────────────────────────────────────────────────────────

@router.get("/signup", response_class=HTMLResponse)
async def signup_form(plan: str = Query("studio"), error: Optional[str] = None):
    err_html = f'<div class="err">{error}</div>' if error else ''
    body = f"""
<p class="sub">Pick a plan first, pay after. We provision your workspace immediately so your extension can sign in as soon as payment clears.</p>
<form method="post" action="/signup?plan={plan}" autocomplete="on">
  {err_html}
  <label for="email">Work email</label>
  <input id="email" name="email" type="email" required autocomplete="email" autofocus>
  <label for="password">Password (8+ chars)</label>
  <input id="password" name="password" type="password" required minlength="8" autocomplete="new-password">
  <label for="display_name">Display name (optional)</label>
  <input id="display_name" name="display_name" type="text" autocomplete="name">
  <button type="submit">Create account and continue</button>
  <p class="alt">Already have an account? <a href="/login?plan={plan}">Log in</a></p>
</form>"""
    return HTMLResponse(_page("Sign up", body, f"Create your <em>{plan.capitalize()}</em> account."))


@router.post("/signup")
async def signup_submit(
    email: str = Form(...),
    password: str = Form(...),
    display_name: Optional[str] = Form(None),
    plan: str = Query("studio"),
    db: Database = Depends(get_db),
):
    existing = await db.fetchrow("SELECT id FROM users WHERE email = $1", email)
    if existing:
        return RedirectResponse(f"/signup?plan={plan}&error=Email+already+registered.+Try+logging+in.", status_code=303)

    user_id = secrets.token_hex(16)
    await db.execute(
        """INSERT INTO users (id, email, display_name, password_hash)
           VALUES ($1, $2, $3, $4)""",
        user_id, email, display_name or email.split("@")[0], _hash_password(password),
    )
    org_id = secrets.token_hex(16)
    slug_base = email.split("@")[0].lower().replace(".", "-")[:40] or "user"
    await db.execute(
        """INSERT INTO organizations (id, name, slug, owner_id)
           VALUES ($1, $2, $3, $4)""",
        org_id, f"{display_name or slug_base}", slug_base, user_id,
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

    resp = RedirectResponse(f"/checkout?plan={plan}", status_code=303)
    _set_session_cookie(resp, _mint_token(user_id, email))
    return resp


# ─── Login ───────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_form(plan: Optional[str] = Query(None), error: Optional[str] = None, next: Optional[str] = Query(None)):
    err_html = f'<div class="err">{error}</div>' if error else ''
    next_param = f"&next={next}" if next else (f"&plan={plan}" if plan else "")
    body = f"""
<form method="post" action="/login?{next_param.lstrip('&')}" autocomplete="on">
  {err_html}
  <label for="email">Email</label>
  <input id="email" name="email" type="email" required autocomplete="email" autofocus>
  <label for="password">Password</label>
  <input id="password" name="password" type="password" required autocomplete="current-password">
  <button type="submit">Log in</button>
  <p class="alt">No account yet? <a href="/signup{('?plan=' + plan) if plan else ''}">Create one</a></p>
</form>"""
    return HTMLResponse(_page("Log in", body, "Welcome back."))


@router.post("/login")
async def login_submit(
    email: str = Form(...),
    password: str = Form(...),
    plan: Optional[str] = Query(None),
    next: Optional[str] = Query(None),
    db: Database = Depends(get_db),
):
    user = await db.fetchrow("SELECT * FROM users WHERE email = $1", email)
    if not user or not _verify_password(password, user["password_hash"]):
        params = f"?error=Invalid+email+or+password"
        if plan: params += f"&plan={plan}"
        if next: params += f"&next={next}"
        return RedirectResponse(f"/login{params}", status_code=303)

    if next:
        target = next
    elif plan:
        target = f"/checkout?plan={plan}"
    else:
        target = "/account"
    resp = RedirectResponse(target, status_code=303)
    _set_session_cookie(resp, _mint_token(user["id"], user["email"]))
    return resp


# ─── Logout ──────────────────────────────────────────────────────────

@router.get("/logout")
async def logout():
    resp = RedirectResponse("/pricing", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


# ─── Checkout ────────────────────────────────────────────────────────

@router.get("/checkout")
async def checkout(
    request: Request,
    plan: str = Query(...),
    interval: str = Query("month"),
    db: Database = Depends(get_db),
):
    """Create a Stripe Checkout Session and redirect to Stripe's hosted page."""
    user = await _current_user(request, db)
    if not user:
        return RedirectResponse(f"/signup?plan={plan}", status_code=303)

    if plan not in ("developer", "studio", "lab"):
        return RedirectResponse("/pricing?error=Unknown+plan", status_code=303)

    key = plan if interval == "month" else f"{plan}_annual"
    price_id = PLAN_PRICE_MAP.get(key)
    if not price_id:
        raise HTTPException(status_code=503, detail=f"No Stripe price configured for {key}")
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing not configured on server")

    workspace = await _user_default_workspace(user["id"], db)
    if not workspace:
        raise HTTPException(status_code=500, detail="No workspace for user; contact support")

    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    base = str(request.base_url).rstrip("/")
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=user["email"],
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{base}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}/billing/cancel",
        metadata={"workspace_id": workspace["id"], "plan_id": plan, "user_id": user["id"]},
        subscription_data={"metadata": {"workspace_id": workspace["id"], "plan_id": plan, "user_id": user["id"]}},
        allow_promotion_codes=True,
    )
    return RedirectResponse(session.url, status_code=303)


# ─── Billing status pages ────────────────────────────────────────────

@router.get("/billing/success", response_class=HTMLResponse)
async def billing_success(session_id: Optional[str] = None):
    sid_note = f'<p class="alt">Stripe session: <code style="color:var(--muted)">{session_id}</code></p>' if session_id else ""
    body = f"""
<div class="ok">Payment confirmed. Your workspace is being upgraded now.</div>
<p>What to do next:</p>
<ol style="color:var(--parchment);padding-left:22px">
  <li>Install the Crowe Logic extension in VS Code (search for <code style="color:var(--gold)">crowe-logic</code>).</li>
  <li>Run <code style="color:var(--gold)">Crowe Logic: Sign In</code> from the command palette.</li>
  <li>Paste your API token from <a href="/account">your account page</a>.</li>
</ol>
{sid_note}
<p class="alt"><a href="/account">Go to account</a> &middot; <a href="/pricing">Pricing</a></p>"""
    return HTMLResponse(_page("Payment confirmed", body, "<em>Welcome</em> to Crowe Logic Code."))


@router.get("/billing/cancel", response_class=HTMLResponse)
async def billing_cancel():
    body = """
<p>No charge was made. You can come back any time.</p>
<p class="alt"><a href="/pricing">Return to pricing</a></p>"""
    return HTMLResponse(_page("Checkout cancelled", body, "Checkout cancelled."))


# ─── Minimal account page ────────────────────────────────────────────

@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request, db: Database = Depends(get_db)):
    user = await _current_user(request, db)
    if not user:
        return RedirectResponse("/login?next=/account", status_code=303)
    ws = await _user_default_workspace(user["id"], db)
    sub_row = None
    if ws:
        sub_row = await db.fetchrow(
            "SELECT plan_id, status, current_period_end FROM subscriptions WHERE workspace_id = $1",
            ws["id"],
        )

    plan_label = (sub_row and sub_row["plan_id"]) or (ws and ws["plan_id"]) or "developer"
    status_label = (sub_row and sub_row["status"]) or "free"
    period_end = (sub_row and sub_row.get("current_period_end")) or ""

    # API tokens list
    token_rows = []
    if ws:
        token_rows = await db.fetch(
            """SELECT id, key_prefix, label, revoked, last_used_at
               FROM api_keys WHERE workspace_id = $1
               ORDER BY id DESC""",
            ws["id"],
        )
    tokens_html = ""
    if token_rows:
        rows_html = ""
        for t in token_rows:
            status_txt = "revoked" if t["revoked"] else "active"
            last = t.get("last_used_at") or "never used"
            revoke_btn = "" if t["revoked"] else (
                f'<form method="post" action="/account/tokens/{t["id"]}/revoke" '
                f'style="display:inline;margin:0"><button type="submit" '
                f'style="background:transparent;color:var(--err);border:1px solid var(--err);'
                f'padding:4px 10px;font-size:12px;width:auto;margin:0">Revoke</button></form>'
            )
            rows_html += (
                f'<tr><td style="color:var(--gold);font-family:ui-monospace,menlo,monospace">'
                f'{t["key_prefix"]}...</td>'
                f'<td>{t["label"]}</td>'
                f'<td style="color:var(--muted)">{status_txt}</td>'
                f'<td style="color:var(--muted);font-size:12px">{last}</td>'
                f'<td style="text-align:right">{revoke_btn}</td></tr>'
            )
        tokens_html = f"""
<table style="width:100%;border-collapse:collapse;margin:12px 0;font-size:13px">
  <thead><tr style="border-bottom:1px solid var(--line);color:var(--muted);text-align:left">
    <th style="padding:8px 0">Prefix</th><th>Label</th><th>Status</th><th>Last used</th><th></th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>"""
    else:
        tokens_html = '<p class="alt" style="text-align:left">No tokens yet.</p>'

    body = f"""
<p class="sub">Signed in as <b style="color:var(--parchment)">{user['email']}</b></p>

<h2 style="font:600 15px/1.2 inherit;color:var(--parchment);margin:24px 0 8px;letter-spacing:.01em">Subscription</h2>
<form method="get" action="#" style="margin-bottom:16px">
  <label>Current plan</label>
  <input type="text" value="{plan_label}" disabled style="opacity:.7">
  <label>Status</label>
  <input type="text" value="{status_label}" disabled style="opacity:.7">
  <label>Renews / ends</label>
  <input type="text" value="{period_end}" disabled style="opacity:.7">
</form>
<p class="alt" style="text-align:left">
  <a href="/billing/portal">Manage subscription &amp; payment method</a>
</p>

<h2 style="font:600 15px/1.2 inherit;color:var(--parchment);margin:28px 0 8px;letter-spacing:.01em">API tokens</h2>
<p class="sub" style="font-size:13px">Paste one of these into the Crowe Logic VS Code extension to sign in. Tokens are shown once at creation, so save them in your password manager.</p>
{tokens_html}
<form method="post" action="/account/tokens" style="margin-top:6px">
  <label for="label">Token label</label>
  <input id="label" name="label" type="text" placeholder="e.g. Macbook Pro" required>
  <button type="submit">Create new token</button>
</form>

<p class="alt" style="margin-top:24px"><a href="/logout">Log out</a> &middot; <a href="/pricing">Pricing</a></p>"""
    return HTMLResponse(_page("Account", body, "Your account."))


# ─── API token creation (web UI; internally same logic as /api/workspaces/{id}/keys) ──

@router.post("/account/tokens")
async def account_create_token(
    request: Request,
    label: str = Form(...),
    db: Database = Depends(get_db),
):
    user = await _current_user(request, db)
    if not user:
        return RedirectResponse("/login?next=/account", status_code=303)
    ws = await _user_default_workspace(user["id"], db)
    if not ws:
        raise HTTPException(status_code=404, detail="No workspace")

    raw_key = f"crowe_pat_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:14]
    key_id = secrets.token_hex(16)

    await db.execute(
        """INSERT INTO api_keys (id, workspace_id, user_id, key_hash, key_prefix, label, scopes)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        key_id, ws["id"], user["id"], key_hash, key_prefix, (label or "default")[:80],
        json.dumps(["chat", "vision", "agents", "ide"]),
    )

    body = f"""
<div class="ok">Token created. Copy it now; you will not see it again.</div>
<p class="sub" style="text-align:left">Label: <b style="color:var(--parchment)">{(label or 'default')[:80]}</b></p>
<label>Paste this into <code style="color:var(--gold)">Crowe Logic: Sign In</code> in VS Code</label>
<input type="text" readonly value="{raw_key}" onclick="this.select();document.execCommand('copy')"
       style="font-family:ui-monospace,menlo,monospace;font-size:13px;background:#0e0e0f;
              color:var(--gold);cursor:pointer" title="Click to copy">
<p class="alt" style="text-align:left">
  Click the field to copy. Rotate or revoke any time from the <a href="/account">account page</a>.
</p>
<p><a href="/account" style="display:inline-block;padding:10px 14px;background:var(--gold);
  color:var(--graphite);border-radius:8px;text-decoration:none;font-weight:600">Done</a></p>"""
    return HTMLResponse(_page("New token", body, "Copy your new token."))


@router.post("/account/tokens/{token_id}/revoke")
async def account_revoke_token(
    token_id: str,
    request: Request,
    db: Database = Depends(get_db),
):
    user = await _current_user(request, db)
    if not user:
        return RedirectResponse("/login?next=/account", status_code=303)
    ws = await _user_default_workspace(user["id"], db)
    if not ws:
        raise HTTPException(status_code=404, detail="No workspace")
    await db.execute(
        "UPDATE api_keys SET revoked = TRUE WHERE id = $1 AND workspace_id = $2",
        token_id, ws["id"],
    )
    return RedirectResponse("/account", status_code=303)


@router.get("/billing/portal")
async def billing_portal(request: Request, db: Database = Depends(get_db)):
    user = await _current_user(request, db)
    if not user:
        return RedirectResponse("/login?next=/account", status_code=303)
    ws = await _user_default_workspace(user["id"], db)
    if not ws:
        raise HTTPException(status_code=404, detail="No workspace")
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing not configured")
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    org = await db.fetchrow("SELECT stripe_customer_id FROM organizations WHERE id = $1", ws["org_id"])
    customer_id = (org or {}).get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=404, detail="No Stripe customer yet; complete a checkout first")

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{str(request.base_url).rstrip('/')}/account",
    )
    return RedirectResponse(session.url, status_code=303)
