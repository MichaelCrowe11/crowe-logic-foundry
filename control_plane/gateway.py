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
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Depends
from pydantic import BaseModel

from .db import Database, get_db

router = APIRouter(prefix="/api/gateway", tags=["gateway"])

# Model tier → plan minimum. Models not listed are enterprise-only.
MODEL_PLAN_ACCESS = {
    # Developer tier (BYOK models + Nano/Forge)
    "gpt-5.4-nano": "developer",
    "Llama-3-3-70B": "developer",
    "FW-GLM-5": "developer",
    # Studio tier
    "Kimi-K2.5": "studio",
    "DeepSeek-R1": "studio",
    "DeepSeek-V3-1": "studio",
    "Mistral-Large-3": "studio",
    "FW-MiniMax-M2.5": "studio",
    # Lab tier
    "claude-opus-4-6-2": "lab",
    "claude-opus-4-6": "lab",
    "gpt-5.4": "lab",
    # Enterprise only
    "gpt-5.4-pro": "enterprise",
    "grok-4-20-reasoning": "enterprise",
    "claude-opus-4-5": "enterprise",
}

PLAN_RANK = {"developer": 0, "studio": 1, "lab": 2, "enterprise": 3}


def _build_model_catalog() -> list[dict]:
    from config.agent_config import resolve_model_config

    catalog = []
    for model, min_plan in MODEL_PLAN_ACCESS.items():
        cfg = resolve_model_config(model) or {}
        catalog.append(
            {
                "model": model,
                "provider": cfg.get("provider", "unknown"),
                "surface": cfg.get("surface", "chat"),
                "label": cfg.get("label"),
                "min_plan": min_plan,
            }
        )
    return sorted(catalog, key=lambda item: (PLAN_RANK.get(item["min_plan"], 99), item["model"]))


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

    from config.agent_config import resolve_model_config, MODEL_CHAIN, provider_model_name

    cfg = resolve_model_config(model)
    if cfg is None:
        # Fall back to first model in chain
        cfg = list(MODEL_CHAIN)[0] if MODEL_CHAIN else None
    if cfg is None:
        raise HTTPException(status_code=400, detail=f"Model '{model}' not found in MODEL_CHAIN")

    provider_kind = cfg.get("provider", "azure_openai")
    name = provider_model_name(cfg)

    def _sync_call():
        """Execute the provider call synchronously (OpenAI SDK is not async)."""
        if provider_kind == "azure_openai":
            from providers.azure_openai import AzureOpenAIProvider, AzureResponsesProvider
            endpoint_var = cfg.get("endpoint_env", "AZURE_CORE_ENDPOINT")
            api_key_var = cfg.get("api_key_env", "AZURE_CORE_API_KEY")
            endpoint = os.environ.get(endpoint_var, "")
            api_key = os.environ.get(api_key_var, "")
            if not endpoint or not api_key:
                raise HTTPException(
                    status_code=503,
                    detail=f"Missing credentials for {name} ({endpoint_var}/{api_key_var})"
                )

            if cfg.get("surface") == "responses":
                # Responses API — no direct non-streaming token count; use chat fallback
                provider = AzureOpenAIProvider(
                    model=name,
                    system_instructions="You are a helpful assistant.",
                    endpoint=endpoint,
                    api_key=api_key,
                    label=cfg.get("label", "CroweLM"),
                )
            else:
                provider = AzureOpenAIProvider(
                    model=name,
                    system_instructions="You are a helpful assistant.",
                    endpoint=endpoint,
                    api_key=api_key,
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
                    detail=f"Missing endpoint for {name} ({endpoint_var})"
                )
            provider = HostedOpenAIProvider(
                model=name,
                system_instructions="You are a helpful assistant.",
                endpoint=endpoint,
                api_key=api_key,
                label=cfg.get("label", "CroweLM"),
            )
        elif provider_kind == "openrouter":
            from providers.openrouter import OpenRouterProvider
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            if not api_key:
                raise HTTPException(status_code=503, detail="OPENROUTER_API_KEY not set")
            provider = OpenRouterProvider(
                api_key=api_key, base_url=base_url, model=name,
                system_instructions="You are a helpful assistant.",
                label=cfg.get("label", "CroweLM"),
            )
        elif provider_kind == "nvidia":
            endpoint = os.environ.get("NVIDIA_NIM_ENDPOINT", "")
            api_key = os.environ.get("NVIDIA_API_KEY", "")
            if not endpoint or not api_key:
                raise HTTPException(status_code=503, detail="NVIDIA credentials not set")
            from providers.nvidia import NvidiaProvider
            provider = NvidiaProvider(
                model=name, system_instructions="You are a helpful assistant.",
                endpoint=endpoint, api_key=api_key,
                label=cfg.get("label", "CroweLM"),
            )
        elif provider_kind == "anthropic":
            from providers.anthropic import AnthropicProvider
            endpoint_var = cfg.get("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT")
            api_key_var = cfg.get("api_key_env", "AZURE_ANTHROPIC_API_KEY")
            endpoint = os.environ.get(endpoint_var, "")
            api_key = os.environ.get(api_key_var, "")
            if not endpoint or not api_key:
                raise HTTPException(status_code=503, detail=f"Missing credentials for {name}")
            provider = AnthropicProvider(
                model=name, system_instructions="You are a helpful assistant.",
                endpoint=endpoint, api_key=api_key,
                label=cfg.get("label", "CroweLM"),
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider_kind}")

        # Build messages for the OpenAI-compatible SDK call
        sdk_messages = [{"role": "system", "content": "You are a helpful assistant."}]
        for m in messages:
            sdk_messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})

        kwargs = {"model": provider.model, "messages": sdk_messages, "stream": False}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature

        response = provider.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
        usage = response.usage
        return content, (usage.prompt_tokens if usage else 0), (usage.completion_tokens if usage else 0)

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


async def _resolve_api_key(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: Database = Depends(get_db),
) -> dict:
    """Resolve an API key to workspace + user + plan."""
    import hashlib

    raw_key = None
    if x_api_key:
        raw_key = x_api_key
    elif authorization and authorization.startswith("Bearer cl_"):
        raw_key = authorization[7:]

    if not raw_key:
        raise HTTPException(status_code=401, detail="API key required")

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
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


@router.post("/chat", response_model=GatewayResponse)
async def gateway_chat(
    req: GatewayRequest,
    key_info: dict = Depends(_resolve_api_key),
    db: Database = Depends(get_db),
):
    """Metered model gateway. Enforces plan-based model access and records usage."""
    model = req.model
    plan_id = key_info["plan_id"]
    workspace_id = key_info["workspace_id"]
    user_id = key_info["user_id"]

    # ── Plan-based model access check ──
    required_plan = MODEL_PLAN_ACCESS.get(model, "enterprise")
    if PLAN_RANK.get(plan_id, 0) < PLAN_RANK.get(required_plan, 3):
        raise HTTPException(
            status_code=403,
            detail=f"Model '{model}' requires {required_plan} plan or higher",
        )

    # ── Token budget check ──
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
            workspace_id, month_start,
        )
        if used_row and used_row["used"] >= budget:
            raise HTTPException(status_code=429, detail="Monthly token budget exhausted")

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

    # ── Record usage ──
    if token_count > 0:
        await db.execute(
            """INSERT INTO usage_events (workspace_id, user_id, event_type, quantity, model)
               VALUES ($1, $2, 'tokens', $3, $4)""",
            workspace_id, user_id, token_count, model,
        )

    return GatewayResponse(
        id=f"gw_{int(time.time())}",
        model=model,
        content=content,
        usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": token_count},
        latency_ms=elapsed_ms,
    )


@router.get("/models")
async def list_available_models(
    key_info: dict = Depends(_resolve_api_key),
):
    """Return models available for this API key's plan."""
    plan_id = key_info["plan_id"]
    plan_rank = PLAN_RANK.get(plan_id, 0)
    available = [
        {"model": m, "min_plan": p}
        for m, p in MODEL_PLAN_ACCESS.items()
        if PLAN_RANK.get(p, 3) <= plan_rank
    ]
    return {"plan": plan_id, "models": available}
