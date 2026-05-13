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

from .db import Database, get_db
from .plans import canonical_plan_id, plan_rank
from .tokens import hash_api_key, is_supported_api_key

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
    "FW-MiniMax-M2.7": "pro",
    "crowe-synapse": "pro",
    # New Azure account (crowelm-prod-eastus2, mike@southwestmushrooms.com)
    # deployed 2026-05-13 as the v2 tier floor.
    "gpt-5.5": "team",
    "gpt-4o": "personal",
    "DeepSeek-R1-0528": "pro",
    "DeepSeek-V3.1": "personal",
    "cohere-command-a": "pro",
    "Llama-3.3-70B-Instruct": "personal",
    "Codestral-2501": "pro",
    # Utility tiers (not customer-selectable; serve embedding + routing surfaces)
    "text-embedding-3-large": "personal",
    "embed-v-4-0": "personal",
    "model-router": "pro",
    "sora-2": "team",
    "claude-opus-4-6-2": "pro",
    "claude-opus-4-6": "pro",
    "gpt-5.4": "pro",
    # Team tier
    "gpt-5.4-pro": "team",
    "grok-4-20-reasoning": "team",
    "claude-opus-4-5": "team",
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
    "FW-MiniMax-M2.7": {
        "name": "CroweLM Vega",
        "description": "Next-generation mid-tier model. Improved reasoning and instruction-following over Atlas.",
    },
    # ── New Azure account (crowelm-prod-eastus2) tier floor ──────────────
    # Deployed 2026-05-13 to the mike@southwestmushrooms.com subscription.
    # Names are additive; the older entries above keep routing to the old
    # crowelogicos-* account until those env vars get repointed.
    "gpt-5.5": {
        "name": "CroweLM Aurora",
        "description": "Top-tier general-purpose flagship. Newest OpenAI deployment, broadest knowledge and tool-use surface.",
    },
    "gpt-4o": {
        "name": "CroweLM Sight",
        "description": "Vision-capable legacy daily driver. Used for image analysis and multimodal tasks.",
    },
    "DeepSeek-R1-0528": {
        "name": "CroweLM Reason Pro",
        "description": "Frontier reasoning model with explicit chain-of-thought. Successor to CroweLM Reason.",
    },
    "DeepSeek-V3.1": {
        "name": "CroweLM Vector Pro",
        "description": "General-purpose reasoning with strong cost-efficiency. Upgrade from CroweLM Vector.",
    },
    "cohere-command-a": {
        "name": "CroweLM Continental",
        "description": "Multilingual flagship with strong European and Asian language coverage. Backed by Cohere Command-A.",
    },
    "Llama-3.3-70B-Instruct": {
        "name": "CroweLM Forge Pro",
        "description": "Open-weights workhorse, latest Meta release in the 70B family.",
    },
    "Codestral-2501": {
        "name": "CroweLM Codex",
        "description": "Code generation and analysis specialist. Mistral Codestral tuned for software engineering.",
    },
    "text-embedding-3-large": {
        "name": "CroweLM Embed",
        "description": "High-quality semantic embeddings for retrieval and clustering.",
    },
    "embed-v-4-0": {
        "name": "CroweLM Embed Multilingual",
        "description": "Cohere embed-v-4-0; multilingual semantic embeddings.",
    },
    "model-router": {
        "name": "CroweLM Conductor",
        "description": "Azure-managed router that auto-selects the cheapest model meeting the request's quality bar.",
    },
    "sora-2": {
        "name": "CroweLM Cinema",
        "description": "Video generation surface. Used by the Crowe Studio Scene Engine for short-form synthesis.",
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
    "crowe-synapse": {
        "name": "CroweLM Synapse",
        "description": "Autonomous CLI executor. Tool-use specialist for code generation, file edits, and multi-step execution under AICL discipline.",
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
        resolve_model_config,
        MODEL_CHAIN,
        provider_model_name,
    )

    cfg = resolve_model_config(model)
    if cfg is None:
        # Fall back to first model in chain
        cfg = list(MODEL_CHAIN)[0] if MODEL_CHAIN else None
    if cfg is None:
        raise HTTPException(
            status_code=400, detail=f"Model '{model}' not found in MODEL_CHAIN"
        )

    provider_kind = cfg.get("provider", "azure_openai")
    name = provider_model_name(cfg)

    # Azure-hosted models deployed on the /v1/responses surface need a
    # different SDK call than /v1/chat/completions. Tracked separately so
    # _sync_call can branch on the API shape, not just the provider kind.
    use_responses_api = (
        provider_kind == "azure_openai" and cfg.get("surface") == "responses"
    )

    def _sync_call():
        """Execute the provider call synchronously (OpenAI SDK is not async)."""
        if provider_kind == "azure_openai":
            from providers.azure_openai import (
                AzureOpenAIProvider,
                AzureResponsesProvider,
            )

            endpoint_var = cfg.get("endpoint_env", "AZURE_CORE_ENDPOINT")
            api_key_var = cfg.get("api_key_env", "AZURE_CORE_API_KEY")
            endpoint = os.environ.get(endpoint_var, "")
            api_key = os.environ.get(api_key_var, "")
            if not endpoint or not api_key:
                raise HTTPException(
                    status_code=503,
                    detail=f"Missing credentials for {name} ({endpoint_var}/{api_key_var})",
                )

            if use_responses_api:
                # Responses-surface deployments do NOT respond on
                # /chat/completions, so we must use the Responses API.
                # Previously this branch wrongly constructed an
                # AzureOpenAIProvider and called chat.completions, which
                # 400s with "Invalid model" against responses-only
                # deployments (e.g. gpt-5.4-pro on AZURE_CORE).
                provider = AzureResponsesProvider(
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
                    detail=f"Missing endpoint for {name} ({endpoint_var})",
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
                system_instructions="You are a helpful assistant.",
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
                system_instructions="You are a helpful assistant.",
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
                system_instructions="You are a helpful assistant.",
                endpoint=endpoint,
                api_key=api_key,
                label=cfg.get("label", "CroweLM"),
            )
        else:
            raise HTTPException(
                status_code=400, detail=f"Unsupported provider: {provider_kind}"
            )

        if use_responses_api:
            # Responses API call: uses `input` (list of role/content items)
            # and `instructions` (system prompt as a top-level field).
            # Token usage fields are `input_tokens` / `output_tokens` rather
            # than `prompt_tokens` / `completion_tokens`.
            input_items: list[dict] = []
            for m in messages:
                role = m.get("role", "user")
                if role not in ("user", "system", "developer", "assistant"):
                    continue
                content_text = str(m.get("content", ""))
                if role == "assistant":
                    # Responses input items can't carry a bare assistant role;
                    # surface prior assistant turns as developer context.
                    input_items.append(
                        {
                            "type": "message",
                            "role": "developer",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": f"Previous assistant reply:\n{content_text}",
                                }
                            ],
                        }
                    )
                else:
                    input_items.append(
                        {
                            "type": "message",
                            "role": role,
                            "content": [{"type": "input_text", "text": content_text}],
                        }
                    )

            kwargs: dict = {
                "model": provider.model,
                "input": input_items,
                "instructions": "You are a helpful assistant.",
            }
            if max_tokens is not None:
                kwargs["max_output_tokens"] = max_tokens
            if temperature is not None:
                kwargs["temperature"] = temperature

            response = provider.client.responses.create(**kwargs)
            # `output_text` is the SDK-provided concatenation of all text
            # output items. Fall back to walking response.output if the
            # SDK version doesn't expose the helper.
            content = getattr(response, "output_text", "") or ""
            if not content:
                for item in getattr(response, "output", []) or []:
                    if getattr(item, "type", None) != "message":
                        continue
                    for piece in getattr(item, "content", []) or []:
                        text = getattr(piece, "text", "") or ""
                        if text:
                            content += text
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
            output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
            return content, input_tokens, output_tokens

        # Chat Completions path (default for everything that isn't a
        # responses-surface Azure deployment).
        sdk_messages = [{"role": "system", "content": "You are a helpful assistant."}]
        for m in messages:
            sdk_messages.append(
                {"role": m.get("role", "user"), "content": m.get("content", "")}
            )

        kwargs = {"model": provider.model, "messages": sdk_messages, "stream": False}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature

        response = provider.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content or ""
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


async def _resolve_api_key(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: Database = Depends(get_db),
) -> dict:
    """Resolve an API key to workspace + user + plan."""
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


@router.post("/chat", response_model=GatewayResponse)
async def gateway_chat(
    req: GatewayRequest,
    key_info: dict = Depends(_resolve_api_key),
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

    # ── Record usage ──
    if token_count > 0:
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
    key_info: dict = Depends(_resolve_api_key),
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
            if recorded_tokens > 0:
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
    key_info: dict = Depends(_resolve_api_key),
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


def _required_envs(cfg: dict) -> list[str]:
    """Return the env var names a single model config requires to dispatch.

    Used by the /health endpoint to surface "this tier is blocked because
    NVIDIA_API_KEY isn't set" without the caller having to read the
    provider source. Defaults match those used inside ``_call_provider``
    and ``cli.headless._build_provider``.
    """
    provider_kind = cfg.get("provider", "")
    if provider_kind == "azure_openai":
        return [
            cfg.get("endpoint_env", "AZURE_CORE_ENDPOINT"),
            cfg.get("api_key_env", "AZURE_CORE_API_KEY"),
        ]
    if provider_kind == "anthropic":
        return [
            cfg.get("endpoint_env", "AZURE_ANTHROPIC_ENDPOINT"),
            cfg.get("api_key_env", "AZURE_ANTHROPIC_API_KEY"),
        ]
    if provider_kind == "openai_compat":
        return [
            cfg.get("endpoint_env", "CROWE_OPEN_ENDPOINT"),
            cfg.get("api_key_env", "CROWE_OPEN_API_KEY"),
        ]
    if provider_kind == "nvidia":
        return ["NVIDIA_NIM_ENDPOINT", "NVIDIA_API_KEY"]
    if provider_kind == "openrouter":
        return ["OPENROUTER_API_KEY"]
    if provider_kind == "ollama":
        return [cfg.get("base_url_env", "OLLAMA_BASE_URL")]
    return []


@router.get("/health")
async def gateway_health(
    key_info: dict = Depends(_resolve_api_key),
):
    """Per-tier credential and stream-flag status.

    Auth-gated like /models because it surfaces which provider env vars
    are set or missing. The shape lets a caller see exactly why a
    specific tier (e.g. CroweLM Vega, CroweLM Reason) is failing without
    reading Railway env vars one by one.

    Response shape::

        {
          "stream_enabled": bool,
          "tiers_ok": int,
          "tiers_blocked": int,
          "tiers": [
            {
              "model": "FW-MiniMax-M2.7",
              "display_name": "CroweLM Vega",
              "provider": "openai_compat",
              "required_env": ["CROWE_OPEN_ENDPOINT", "CROWE_OPEN_API_KEY"],
              "missing_env": ["CROWE_OPEN_API_KEY"],
              "ok": false,
            },
            ...
          ]
        }
    """
    from config.agent_config import MODEL_CHAIN

    tiers: list[dict] = []
    for cfg in MODEL_CHAIN:
        if cfg.get("provider") == "auto":
            # Meta-models like "crowelm-auto" don't have provider creds
            # of their own; their status is determined by what they
            # resolve to. Skip from the health report.
            continue
        required = _required_envs(cfg)
        missing = [name for name in required if not os.environ.get(name, "").strip()]
        display = MODEL_DISPLAY.get(cfg["name"], {}).get(
            "name", cfg.get("label", cfg["name"])
        )
        tiers.append(
            {
                "model": cfg["name"],
                "display_name": display,
                "provider": cfg.get("provider", ""),
                "surface": cfg.get("surface"),
                "required_env": required,
                "missing_env": missing,
                "ok": len(missing) == 0,
            }
        )

    return {
        "stream_enabled": CROWE_STREAM_ENABLED,
        "tiers_ok": sum(1 for t in tiers if t["ok"]),
        "tiers_blocked": sum(1 for t in tiers if not t["ok"]),
        "tiers": tiers,
    }
