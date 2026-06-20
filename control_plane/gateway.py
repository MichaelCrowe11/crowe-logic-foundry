"""
Model Gateway — metered proxy for CroweLM model tiers.

Sits between the client and the existing provider layer. For every request:
1. Validates the API key or JWT
2. Checks entitlements (plan allows the requested model?)
3. Forwards to the correct provider
4. Records usage (tokens consumed)

This module is imported by the Control Plane API; it doesn't run standalone.
"""

import json
import os
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import oidc
from .db import Database, get_db
from .plans import ANON_PLAN_ID, ANON_DAILY_TURN_CAP, canonical_plan_id, plan_rank
from .tokens import hash_api_key, is_supported_api_key, verify_device_token

router = APIRouter(prefix="/api/gateway", tags=["gateway"])

# Reversible flag for the SSE streaming endpoint. Off by default so a
# misconfigured deploy can't accidentally expose it; flip to "1" once
# the surface is dogfood-ready. See docs/protocols/crowe-stream-v0.md.
CROWE_STREAM_ENABLED = os.environ.get("CROWE_STREAM_ENABLED", "").lower() in (
    "1",
    "true",
    "yes",
)

# Model tier → plan minimum. Models not listed are enterprise-only.
MODEL_PLAN_ACCESS = {
    # Personal tier
    "gpt-5.4-nano": "personal",
    "Llama-3-3-70B": "personal",
    "FW-GLM-5": "personal",
    "crowelm-kernel": "personal",
    "crowelm-grower": "personal",
    # Pro tier
    "Kimi-K2.5": "pro",
    "DeepSeek-R1": "pro",
    "DeepSeek-V3-1": "pro",
    "Mistral-Large-3": "pro",
    "FW-MiniMax-M2.5": "pro",
    "claude-opus-4-6-2": "pro",
    "claude-opus-4-6": "pro",
    "gpt-5.4": "pro",
    # Team tier
    "gpt-5.4-pro": "team",
    "grok-4-20-reasoning": "team",
    "claude-opus-4-5": "team",
    # Free / anonymous tier
    "crowelm-mycelium": ANON_PLAN_ID,
}

# Customer-facing display layer. Keys are routing IDs from MODEL_PLAN_ACCESS.
# Values are CroweLM-branded names + descriptions surfaced via /api/gateway/models.
# Vendor names (OpenAI, Anthropic, Claude, GPT, DeepSeek, Mistral, etc.) MUST NOT
# appear in any value here. The routing ID stays unchanged so /chat and
# /chat/stream keep dispatching to the right provider.
MODEL_DISPLAY = {
    "gpt-5.4-nano": {
        "name": "CroweLM Nano",
        "description": "Fastest and cheapest. Best for high-volume tasks.",
    },
    "Llama-3-3-70B": {
        "name": "CroweLM Forge",
        "description": "Open-weight workhorse. Reliable for general writing and summarization.",
    },
    "FW-GLM-5": {
        "name": "CroweLM Dense",
        "description": "Dense general-purpose model. Balanced speed and quality.",
    },
    "Kimi-K2.5": {
        "name": "CroweLM Lunar",
        "description": "Long-context specialist. Use for large documents or extended threads.",
    },
    "DeepSeek-R1": {
        "name": "CroweLM Reason",
        "description": "Reasoning-tuned. Use for math, code logic, and multi-step problems.",
    },
    "DeepSeek-V3-1": {
        "name": "CroweLM Vector",
        "description": "Cost-efficient general model. Strong on technical writing.",
    },
    "Mistral-Large-3": {
        "name": "CroweLM Edge",
        "description": "Multilingual generalist with strong European-language coverage.",
    },
    "FW-MiniMax-M2.5": {
        "name": "CroweLM Atlas",
        "description": "Versatile mid-tier model. Solid default for routine work.",
    },
    "claude-opus-4-6": {
        "name": "CroweLM Prime",
        "description": "Deep analysis flagship. Careful, thorough, vision-capable.",
    },
    "claude-opus-4-6-2": {
        "name": "CroweLM Sovereign",
        "description": "Premium analytical tier. The most thorough option for high-stakes answers.",
    },
    "gpt-5.4": {
        "name": "CroweLM Titan",
        "description": "Default daily driver. Broad knowledge, fast enough for most tasks.",
    },
    "gpt-5.4-pro": {
        "name": "CroweLM Apex",
        "description": "Top-tier reasoning. Use when answers must be exhaustive and rigorous.",
    },
    "grok-4-20-reasoning": {
        "name": "CroweLM Oracle",
        "description": "Realtime-aware reasoning. Use for current-events analysis.",
    },
    "claude-opus-4-5": {
        "name": "CroweLM Classic",
        "description": "Mature analytical model. Reliable for deep document review.",
    },
    "crowelm-kernel": {
        "name": "CroweLM Kernel",
        "description": "Crowe Logic's cultivation-tuned fast tier. Operational guidance with specific numbers and ratios.",
    },
    "crowelm-grower": {
        "name": "CroweLM Grower",
        "description": "Cultivation operations specialist. Domain-tuned for commercial mycology, substrate prep, sterilization, and yield analysis.",
    },
}


def _model_entry(model: str, min_plan: str) -> dict:
    """Build a single catalog row with CroweLM display fields applied."""
    display = MODEL_DISPLAY.get(model, {})
    return {
        "model": model,
        "name": display.get("name", model),
        "description": display.get("description", ""),
        "min_plan": min_plan,
    }


def _build_model_catalog() -> list[dict]:
    catalog = [_model_entry(m, p) for m, p in MODEL_PLAN_ACCESS.items()]
    return sorted(catalog, key=lambda item: (plan_rank(item["min_plan"]), item["name"]))


_CONTEXT_LENGTH_MARKERS = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "context window",
    "too many tokens",
    "string too long",
    "reduce the length",
)


def _is_context_length_error(exc: Exception) -> bool:
    """True if a provider/SDK exception is a context-window / oversize-input
    rejection rather than a generic tier failure.

    Matches on the OpenAI-style ``code`` attribute (``context_length_exceeded``)
    when present, otherwise falls back to substring markers in the message —
    Azure/OpenAI/OpenRouter phrase this differently, so we keep the net wide.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and "context_length" in code.lower():
        return True
    # The OpenAI SDK nests the API code under .body / .error in some versions.
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        body_code = str(body.get("code", "")).lower()
        if "context_length" in body_code:
            return True
    text = str(exc).lower()
    return any(marker in text for marker in _CONTEXT_LENGTH_MARKERS)


async def _call_provider(
    model: str,
    messages: list[dict],
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> tuple[str, int, int]:
    """Call a CroweLM provider and return (content, prompt_tokens, completion_tokens).

    Runs the synchronous OpenAI SDK call in a thread pool so the event loop
    stays responsive.
    """
    import asyncio
    import os
    import functools

    from config.agent_config import (
        MODEL_CHAIN,
        azure_openai_runtime_config,
        build_system_instructions,
        provider_model_name,
        resolve_model_config,
    )

    cfg = resolve_model_config(model)
    if cfg is None:
        # Fall back to first model in chain
        cfg = list(MODEL_CHAIN)[0] if MODEL_CHAIN else None
    if cfg is None:
        raise HTTPException(
            status_code=400, detail=f"Model '{model}' not found in MODEL_CHAIN"
        )

    # Per-model persona (config/system_prompts/<slug>.md + brand policy). A
    # generic placeholder here leaks foundation-model identity to end users.
    # include_agent_tools=False: this is a toolless chat turn — the gateway
    # cannot execute tools, so the agent tool catalog must be omitted or the
    # model emits <tool_code> calls instead of answering (empty/broken content).
    system_instructions = build_system_instructions(cfg, include_agent_tools=False)

    provider_kind = cfg.get("provider", "azure_openai")
    name = provider_model_name(cfg)

    def _sync_call():
        """Execute the provider call synchronously (OpenAI SDK is not async)."""
        if provider_kind == "azure_openai":
            from providers.azure_openai import (
                AzureOpenAIProvider,
                AzureResponsesProvider,
            )

            runtime = azure_openai_runtime_config(cfg)
            if runtime["missing"]:
                raise HTTPException(
                    status_code=503,
                    detail=f"Missing credentials for {name} ({'/'.join(runtime['missing'])})",
                )
            runtime_model = runtime["model"]

            if cfg.get("surface") == "responses":
                # Responses API — no direct non-streaming token count; use chat fallback
                provider = AzureOpenAIProvider(
                    model=runtime_model,
                    system_instructions=system_instructions,
                    endpoint=runtime["endpoint"],
                    api_key=runtime["api_key"],
                    label=cfg.get("label", "CroweLM"),
                )
            else:
                provider = AzureOpenAIProvider(
                    model=runtime_model,
                    system_instructions=system_instructions,
                    endpoint=runtime["endpoint"],
                    api_key=runtime["api_key"],
                    label=cfg.get("label", "CroweLM"),
                )
        elif provider_kind == "openai_compat":
            from providers.hosted_openai import HostedOpenAIProvider

            endpoint_var = cfg.get("endpoint_env", "CROWE_OPEN_ENDPOINT")
            api_key_var = cfg.get("api_key_env", "CROWE_OPEN_API_KEY")
            endpoint = os.environ.get(endpoint_var, "")
            api_key = os.environ.get(api_key_var, "")
            if not endpoint:
                raise HTTPException(
                    status_code=503,
                    detail=f"Missing endpoint for {name} ({endpoint_var})",
                )
            # Modal proxy-auth (e.g. crowelm-mycelium): Modal authenticates at
            # its edge via Modal-Key/Modal-Secret headers, not the bearer key.
            # Convention: <PREFIX>_MODAL_KEY/_MODAL_SECRET beside <PREFIX>_ENDPOINT.
            env_prefix = endpoint_var.removesuffix("_ENDPOINT")
            modal_key = os.environ.get(f"{env_prefix}_MODAL_KEY", "")
            modal_secret = os.environ.get(f"{env_prefix}_MODAL_SECRET", "")
            extra_headers = (
                {"Modal-Key": modal_key, "Modal-Secret": modal_secret}
                if modal_key and modal_secret
                else None
            )
            provider = HostedOpenAIProvider(
                model=name,
                system_instructions=system_instructions,
                endpoint=endpoint,
                api_key=api_key,
                label=cfg.get("label", "CroweLM"),
                extra_headers=extra_headers,
            )
        elif provider_kind == "openrouter":
            from providers.openrouter import OpenRouterProvider

            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            base_url = os.environ.get(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            )
            if not api_key:
                raise HTTPException(
                    status_code=503, detail="OPENROUTER_API_KEY not set"
                )
            provider = OpenRouterProvider(
                api_key=api_key,
                base_url=base_url,
                model=name,
                system_instructions=system_instructions,
                label=cfg.get("label", "CroweLM"),
            )
        elif provider_kind == "nvidia":
            endpoint = os.environ.get("NVIDIA_NIM_ENDPOINT", "")
            api_key = os.environ.get("NVIDIA_API_KEY", "")
            if not endpoint or not api_key:
                raise HTTPException(
                    status_code=503, detail="NVIDIA credentials not set"
                )
            from providers.nvidia import NvidiaProvider

            provider = NvidiaProvider(
                model=name,
                system_instructions=system_instructions,
                endpoint=endpoint,
                api_key=api_key,
                label=cfg.get("label", "CroweLM"),
            )
        elif provider_kind == "anthropic":
            from providers.anthropic import AnthropicProvider

            endpoint_var = cfg.get("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT")
            api_key_var = cfg.get("api_key_env", "AZURE_ANTHROPIC_API_KEY")
            endpoint = os.environ.get(endpoint_var, "")
            api_key = os.environ.get(api_key_var, "")
            if not endpoint or not api_key:
                raise HTTPException(
                    status_code=503, detail=f"Missing credentials for {name}"
                )
            provider = AnthropicProvider(
                model=name,
                system_instructions=system_instructions,
                endpoint=endpoint,
                api_key=api_key,
                label=cfg.get("label", "CroweLM"),
            )
        else:
            raise HTTPException(
                status_code=400, detail=f"Unsupported provider: {provider_kind}"
            )

        # Build messages for the OpenAI-compatible SDK call
        sdk_messages = [{"role": "system", "content": system_instructions}]
        for m in messages:
            sdk_messages.append(
                {"role": m.get("role", "user"), "content": m.get("content", "")}
            )

        kwargs = {"model": provider.model, "messages": sdk_messages, "stream": False}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature

        try:
            response = provider.client.chat.completions.create(**kwargs)
        except HTTPException:
            # Credential/unsupported errors raised before/around the call
            # (e.g. "missing credentials" 503) already carry the right status —
            # don't swallow and re-wrap them.
            raise
        except Exception as exc:  # noqa: BLE001 - any provider/SDK failure
            if _is_context_length_error(exc):
                # An over-budget prompt (e.g. a multi-thousand-line paste) is
                # rejected by EVERY tier, so "retry another tier" (503) is
                # misleading. The honest, actionable answer is 413: your input
                # is too large — trim it.
                raise HTTPException(
                    status_code=413,
                    detail={
                        "error": "input_too_large",
                        "tier": name,
                        "message": (
                            "Your input is too large for this model's context "
                            "window and was rejected before any tokens were "
                            "generated."
                        ),
                        "hint": (
                            "Trim or reduce the input (e.g. paste fewer lines, "
                            "split it into smaller requests) and try again."
                        ),
                    },
                ) from exc
            # A misconfigured or unreachable tier must surface as a clean,
            # client-recoverable 503, never a bare 500 that reads as a gateway
            # bug. The client-side fallback (WS-A) hops from this cleanly.
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "tier_unavailable",
                    "tier": name,
                    "message": f"{name} is not currently servable ({type(exc).__name__})",
                    "hint": "retry with a different model tier",
                },
            ) from exc
        # Defensive: a provider can return an empty choices list (e.g. a
        # content-filter block or upstream truncation). Accessing choices[0]
        # then raises IndexError -> bare 500. Treat it as clean empty content.
        choices = getattr(response, "choices", None) or []
        content = (choices[0].message.content or "") if choices else ""
        usage = response.usage
        return (
            content,
            (usage.prompt_tokens if usage else 0),
            (usage.completion_tokens if usage else 0),
        )

    # Run the blocking SDK call in a thread
    return await asyncio.get_event_loop().run_in_executor(None, _sync_call)


class GatewayRequest(BaseModel):
    model: str
    messages: list[dict]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    tools: Optional[list[dict]] = None


class GatewayResponse(BaseModel):
    id: str
    model: str
    content: str
    usage: dict
    latency_ms: int


@router.get("/catalog")
async def list_model_catalog():
    """Return the public model catalog without requiring an API key."""
    return {"models": _build_model_catalog()}


async def _resolve_principal(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: Database = Depends(get_db),
) -> dict:
    """Resolve the caller to a principal: a Crowe ID token OR a workspace API key.

    Crowe ID bearer tokens (verified against the configured issuer's JWKS) yield a
    token principal whose plan is derived from ``crowe_tier``; these are not backed
    by a ``workspaces`` row, so downstream metering is skipped (see ``_is_metered``).
    API keys keep their existing workspace-scoped behaviour unchanged.
    """
    # ── Anonymous device token path (free tier) ──
    # Must be FIRST so anon tokens never fall through to JWT verification or
    # API-key lookup. verify_device_token is fail-closed (returns None on any
    # error including missing signing secret).
    if authorization and authorization.startswith("Bearer "):
        device_id = verify_device_token(authorization[7:])
        if device_id:
            return {
                "plan_id": ANON_PLAN_ID,
                "workspace_id": device_id,
                "user_id": device_id,
                "principal": "anonymous",
                "subject": f"anon:{device_id}",
            }

    # ── Crowe ID bearer token path (alternative to API keys) ──
    if authorization and authorization.startswith("Bearer "):
        candidate = authorization[7:]
        if oidc.looks_like_jwt(candidate) and not is_supported_api_key(candidate):
            try:
                claims = oidc.verify_token(candidate)
            except Exception as exc:  # noqa: BLE001 - surface any verify failure as 401
                raise HTTPException(
                    status_code=401, detail=f"Invalid Crowe ID token: {exc}"
                )
            return {
                "plan_id": oidc.tier_to_plan(claims.get("crowe_tier")),
                "workspace_id": claims["sub"],
                "user_id": claims["sub"],
                "principal": "crowe-id",
                "subject": claims.get("preferred_username"),
            }

    raw_key = None
    if x_api_key:
        raw_key = x_api_key
    elif authorization and authorization.startswith("Bearer "):
        candidate = authorization[7:]
        # Accept launch PATs and legacy `cl_`/`clk_` keys.
        if is_supported_api_key(candidate):
            raw_key = candidate

    if not raw_key:
        raise HTTPException(status_code=401, detail="API key required")

    key_hash = hash_api_key(raw_key)
    row = await db.fetchrow(
        """SELECT ak.*, w.plan_id, w.status AS ws_status
           FROM api_keys ak
           JOIN workspaces w ON ak.workspace_id = w.id
           WHERE ak.key_hash = $1 AND NOT ak.revoked""",
        key_hash,
    )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key")
    if row["ws_status"] != "active":
        raise HTTPException(status_code=403, detail="Workspace suspended")

    # Update last_used_at
    await db.execute(
        "UPDATE api_keys SET last_used_at = now() WHERE id = $1", row["id"]
    )
    return dict(row)


def _is_metered(key_info: dict) -> bool:
    """Workspace-scoped budget/usage only applies to real workspace principals.

    Crowe ID token principals carry a Keycloak ``sub`` rather than a ``workspaces``
    row, so the plan-budget lookup and the usage INSERT (FK to workspaces/users)
    must be skipped for them. Metering token principals is a deliberate follow-up.

    Anonymous principals are also excluded: the ``free-anonymous`` plan has no
    row in the ``plans`` table (it is deliberately not a Stripe-managed plan), so
    reaching the token-budget path would crash on ``plan["token_budget_month"]``.
    Anonymous principals are metered instead via the ``anon_usage`` table and the
    daily-turn-cap check that runs immediately after the plan-access gate.
    """
    return key_info.get("principal") not in ("crowe-id", "anonymous")


@router.post("/chat", response_model=GatewayResponse)
async def gateway_chat(
    req: GatewayRequest,
    key_info: dict = Depends(_resolve_principal),
    db: Database = Depends(get_db),
):
    """Metered model gateway. Enforces plan-based model access and records usage."""
    model = req.model
    plan_id = canonical_plan_id(key_info["plan_id"])
    workspace_id = key_info["workspace_id"]
    user_id = key_info["user_id"]

    # ── Plan-based model access check ──
    required_plan = MODEL_PLAN_ACCESS.get(model, "enterprise")
    if plan_rank(plan_id) < plan_rank(required_plan):
        raise HTTPException(
            status_code=403,
            detail=f"Model '{model}' requires {required_plan} plan or higher",
        )

    # ── Anonymous daily turn cap (deny-by-default; increment before call) ──
    if key_info.get("principal") == "anonymous":
        from datetime import date

        today = date.today()
        row = await db.fetchrow(
            "SELECT turns FROM anon_usage WHERE device_id = $1 AND day = $2",
            user_id,
            today,
        )
        if row and row["turns"] >= ANON_DAILY_TURN_CAP:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "anon_daily_cap",
                    "message": f"Free daily limit reached ({ANON_DAILY_TURN_CAP} turns).",
                    "upsell": "Sign in for full CroweLM tiers: run `crowe-logic login` or visit https://crowelogic.com/pricing",
                },
            )
        await db.execute(
            """INSERT INTO anon_usage (device_id, day, turns) VALUES ($1, $2, 1)
               ON CONFLICT (device_id, day) DO UPDATE SET turns = anon_usage.turns + 1""",
            user_id,
            today,
        )

    # ── Token budget check (workspace principals only) ──
    if _is_metered(key_info):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        plan = await db.fetchrow("SELECT * FROM plans WHERE id = $1", plan_id)
        budget = plan["token_budget_month"]

        if budget != -1:  # not unlimited
            used_row = await db.fetchrow(
                """SELECT COALESCE(SUM(quantity), 0) AS used
                   FROM usage_events
                   WHERE workspace_id = $1 AND event_type = 'tokens' AND recorded_at >= $2""",
                workspace_id,
                month_start,
            )
            if used_row and used_row["used"] >= budget:
                raise HTTPException(
                    status_code=429, detail="Monthly token budget exhausted"
                )

    # ── Forward to provider ──
    start = time.monotonic()

    content, prompt_tokens, completion_tokens = await _call_provider(
        model=model,
        messages=req.messages,
        max_tokens=req.max_tokens,
        temperature=req.temperature,
    )

    elapsed_ms = int((time.monotonic() - start) * 1000)
    token_count = prompt_tokens + completion_tokens

    # ── Record usage (workspace principals only) ──
    if _is_metered(key_info) and token_count > 0:
        await db.execute(
            """INSERT INTO usage_events (workspace_id, user_id, event_type, quantity, model)
               VALUES ($1, $2, 'tokens', $3, $4)""",
            workspace_id,
            user_id,
            token_count,
            model,
        )

    return GatewayResponse(
        id=f"gw_{int(time.time())}",
        model=model,
        content=content,
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": token_count,
        },
        latency_ms=elapsed_ms,
    )


@router.post("/chat/stream")
async def gateway_chat_stream(
    req: GatewayRequest,
    key_info: dict = Depends(_resolve_principal),
    db: Database = Depends(get_db),
):
    """Streaming model gateway. Emits crowe-stream v0 events as SSE.

    Behind CROWE_STREAM_ENABLED so the rollout is reversible without a
    redeploy. Plan gating mirrors /chat exactly so streamed access can
    never exceed the user's tier.

    Token accounting note: the v0 renderer counts SDK content deltas
    (one increment per feed call), which is approximately the
    completion_tokens count but does not include prompt_tokens. We
    record what we have on the done event; v1 of the protocol adds the
    real provider usage block (gap #3 in the spec) at which point this
    becomes accurate. The endpoint is dogfood-only until that lands.
    """
    if not CROWE_STREAM_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Streaming endpoint is disabled (set CROWE_STREAM_ENABLED=1)",
        )

    if key_info.get("principal") == "anonymous":
        raise HTTPException(
            status_code=403,
            detail="Streaming requires a signed-in account; the free tier uses /chat.",
        )

    from .streaming import stream_agent_events, sse_frame

    model = req.model
    plan_id = canonical_plan_id(key_info["plan_id"])
    workspace_id = key_info["workspace_id"]
    user_id = key_info["user_id"]

    required_plan = MODEL_PLAN_ACCESS.get(model, "enterprise")
    if plan_rank(plan_id) < plan_rank(required_plan):
        raise HTTPException(
            status_code=403,
            detail=f"Model '{model}' requires {required_plan} plan or higher",
        )

    # Pre-stream budget check, identical to /chat. We can't know final
    # cost up front for a streamed turn, so this only guards against
    # users who are already over budget when the request arrives.
    # Workspace principals only — Crowe ID token principals have no workspaces row.
    if _is_metered(key_info):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        plan = await db.fetchrow("SELECT * FROM plans WHERE id = $1", plan_id)
        if plan and plan["token_budget_month"] != -1:
            used_row = await db.fetchrow(
                """SELECT COALESCE(SUM(quantity), 0) AS used
                   FROM usage_events
                   WHERE workspace_id = $1 AND event_type = 'tokens' AND recorded_at >= $2""",
                workspace_id,
                month_start,
            )
            if used_row and used_row["used"] >= plan["token_budget_month"]:
                raise HTTPException(
                    status_code=429, detail="Monthly token budget exhausted"
                )

    session_id = f"http-{workspace_id[:12]}"
    messages = req.messages or []
    if not messages or messages[-1].get("role") != "user":
        raise HTTPException(
            status_code=400, detail="messages must end with a user turn"
        )

    async def _sse() -> "AsyncIterator[str]":
        recorded_tokens = 0
        try:
            async for event in stream_agent_events(
                messages=messages,
                model_id=model,
                session_id=session_id,
            ):
                if event.get("type") == "done":
                    recorded_tokens = int(event.get("tokens") or 0) + int(
                        event.get("reasoning_tokens") or 0
                    )
                yield sse_frame(event)
        finally:
            # Record usage even if the client disconnected mid-stream;
            # the model has already produced (and billed) the tokens.
            # Workspace principals only — token principals are not metered yet.
            if _is_metered(key_info) and recorded_tokens > 0:
                try:
                    await db.execute(
                        """INSERT INTO usage_events
                               (workspace_id, user_id, event_type, quantity, model)
                           VALUES ($1, $2, 'tokens', $3, $4)""",
                        workspace_id,
                        user_id,
                        recorded_tokens,
                        model,
                    )
                except Exception:
                    # Don't crash the response on a usage-write failure;
                    # billing reconciliation is a separate batch job.
                    pass

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable proxy buffering so events flush in real time.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/models")
async def list_available_models(
    key_info: dict = Depends(_resolve_principal),
):
    """Return models available for this API key's plan, with CroweLM display fields."""
    plan_id = canonical_plan_id(key_info["plan_id"])
    rank = plan_rank(plan_id)
    available = [
        entry
        for entry in _build_model_catalog()
        if plan_rank(entry["min_plan"]) <= rank
    ]
    return {"plan": plan_id, "models": available}
