"""Anonymous plan semantics + register endpoint + gateway principal/cap."""
import pytest

from control_plane import plans


def test_anon_plan_ranks_below_everything():
    assert plans.plan_rank(plans.ANON_PLAN_ID) == -2
    assert plans.plan_rank("byok") > plans.plan_rank(plans.ANON_PLAN_ID)


def test_anon_plan_not_in_launch_plans():
    # Stripe/pricing surfaces iterate LAUNCH_PLAN_IDS; anon must stay out.
    assert plans.ANON_PLAN_ID not in plans.LAUNCH_PLAN_IDS


def test_anon_cap_constant():
    assert plans.ANON_DAILY_TURN_CAP == 20


def test_mycelium_resolves_and_is_anon_accessible():
    from config.agent_config import resolve_model_config
    from control_plane.gateway import MODEL_PLAN_ACCESS

    cfg = resolve_model_config("crowelm-mycelium")
    assert cfg is not None
    assert cfg["api_key_env"] == "CROWELM_MYCELIUM_API_KEY"
    assert MODEL_PLAN_ACCESS["crowelm-mycelium"] == plans.ANON_PLAN_ID


def test_register_mints_token(monkeypatch):
    monkeypatch.setenv("CROWE_ANON_SIGNING_SECRET", "test-secret")
    from control_plane import anonymous, tokens
    import asyncio

    class FakeClient:
        host = "203.0.113.7"

    class FakeRequest:
        client = FakeClient()
        headers = {}

    anonymous._register_log.clear()
    out = asyncio.run(anonymous.register_device(FakeRequest()))
    assert tokens.verify_device_token(out["token"]) == out["device_id"]
    assert out["free_model"] == "crowelm-mycelium"
    assert out["daily_turn_cap"] == plans.ANON_DAILY_TURN_CAP


def test_register_rate_limits_per_ip(monkeypatch):
    monkeypatch.setenv("CROWE_ANON_SIGNING_SECRET", "test-secret")
    from fastapi import HTTPException
    from control_plane import anonymous
    import asyncio

    class FakeClient:
        host = "203.0.113.8"

    class FakeRequest:
        client = FakeClient()
        headers = {}

    anonymous._register_log.clear()
    for _ in range(anonymous._REGISTER_MAX_PER_IP):
        asyncio.run(anonymous.register_device(FakeRequest()))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(anonymous.register_device(FakeRequest()))
    assert exc.value.status_code == 429


def test_register_uses_forwarded_for(monkeypatch):
    monkeypatch.setenv("CROWE_ANON_SIGNING_SECRET", "test-secret")
    from control_plane import anonymous
    import asyncio

    class FakeClient:
        host = "10.0.0.1"  # ingress IP

    class FakeRequest:
        client = FakeClient()
        headers = {"x-forwarded-for": "198.51.100.9, 10.0.0.1"}

    anonymous._register_log.clear()
    asyncio.run(anonymous.register_device(FakeRequest()))
    assert "198.51.100.9" in anonymous._register_log
    assert "10.0.0.1" not in anonymous._register_log


def test_register_log_sweeps_expired_entries(monkeypatch):
    monkeypatch.setenv("CROWE_ANON_SIGNING_SECRET", "test-secret")
    from control_plane import anonymous
    import asyncio, time

    class FakeClient:
        host = "203.0.113.99"

    class FakeRequest:
        client = FakeClient()
        headers = {}

    anonymous._register_log.clear()
    stale = time.time() - anonymous._REGISTER_WINDOW - 1
    for i in range(anonymous._REGISTER_LOG_MAX + 1):
        anonymous._register_log[f"10.9.{i // 256}.{i % 256}"] = [stale]
    asyncio.run(anonymous.register_device(FakeRequest()))
    assert len(anonymous._register_log) <= 2  # the new caller (+ at most slack)


# ── Task 11: anonymous principal + daily cap in gateway ──────────────────────


def _anon_key_info(device_id="dev123"):
    return {
        "plan_id": plans.ANON_PLAN_ID,
        "workspace_id": device_id,
        "user_id": device_id,
        "principal": "anonymous",
        "subject": f"anon:{device_id}",
    }


def test_resolve_principal_accepts_device_token(monkeypatch):
    monkeypatch.setenv("CROWE_ANON_SIGNING_SECRET", "test-secret")
    import asyncio
    from control_plane import gateway, tokens

    device_id, raw = tokens.make_device_token()
    info = asyncio.run(
        gateway._resolve_principal(authorization=f"Bearer {raw}", x_api_key=None, db=None)
    )
    assert info["principal"] == "anonymous"
    assert info["plan_id"] == plans.ANON_PLAN_ID
    assert info["user_id"] == device_id


def test_anonymous_is_not_workspace_metered():
    from control_plane.gateway import _is_metered
    assert _is_metered(_anon_key_info()) is False
    assert _is_metered({"principal": "crowe-id"}) is False
    assert _is_metered({"principal": "workspace"}) is True


class FakeDb:
    """Stub for the asyncpg-style Database helper used by gateway_chat."""

    def __init__(self, turns_today=0):
        self.turns_today = turns_today
        self.executed = []

    async def fetchrow(self, query, *args):
        if "free_usage" in query:
            return {"turns": self.turns_today}
        return None

    async def execute(self, query, *args):
        self.executed.append((query, args))


class FakeFreeDb:
    """Stub Database for the free_usage path: records the principal_id read."""

    def __init__(self, turns_today=0):
        self.turns_today = turns_today
        self.read_principal = None
        self.executed = []

    async def fetchrow(self, query, *args):
        if "free_usage" in query:
            self.read_principal = args[0]
            return {"turns": self.turns_today}
        if "plans" in query:
            return {"token_budget_month": -1}
        return None

    async def execute(self, query, *args):
        self.executed.append((query, args))


def test_anon_chat_under_cap_calls_provider(monkeypatch):
    import asyncio
    from control_plane import gateway

    async def fake_provider(**kwargs):
        return ("hello from mycelium", 5, 7)

    monkeypatch.setattr(gateway, "_call_provider", lambda **kw: fake_provider(**kw))
    req = gateway.GatewayRequest(model="crowelm-mycelium", messages=[{"role": "user", "content": "hi"}])
    resp = asyncio.run(gateway.gateway_chat(req, key_info=_anon_key_info(), db=FakeDb(turns_today=3)))
    assert resp.content == "hello from mycelium"


def test_anon_chat_cap_hit_returns_structured_402():
    import asyncio
    from fastapi import HTTPException
    from control_plane import gateway

    req = gateway.GatewayRequest(model="crowelm-mycelium", messages=[{"role": "user", "content": "hi"}])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            gateway.gateway_chat(
                req, key_info=_anon_key_info(), db=FakeDb(turns_today=plans.ANON_DAILY_TURN_CAP)
            )
        )
    assert exc.value.status_code == 402
    detail = exc.value.detail
    assert detail["code"] == "free_daily_cap"
    assert "message" in detail and "upsell" in detail


def test_anon_cannot_reach_paid_models():
    import asyncio
    from fastapi import HTTPException
    from control_plane import gateway

    req = gateway.GatewayRequest(model="gpt-5.5", messages=[{"role": "user", "content": "hi"}])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(gateway.gateway_chat(req, key_info=_anon_key_info(), db=FakeDb()))
    assert exc.value.status_code == 403


def test_hosted_openai_provider_passes_extra_headers():
    from providers.hosted_openai import HostedOpenAIProvider

    p = HostedOpenAIProvider(
        model="m",
        system_instructions="s",
        endpoint="https://example.modal.run",
        extra_headers={"Modal-Key": "k", "Modal-Secret": "sec"},
    )
    assert p.client.default_headers.get("Modal-Key") == "k"
    assert p.client.default_headers.get("Modal-Secret") == "sec"


def test_mycelium_backend_name_is_ollama_tag():
    # The Modal app fronts Ollama; the OpenAI-compat route needs the registry
    # tag, not the brand name (which would 404 model-not-found upstream).
    from config.agent_config import provider_model_name, resolve_model_config

    cfg = resolve_model_config("crowelm-mycelium")
    assert provider_model_name(cfg) == "Mcrowe1210/gemma-4-mycelium-e4b"


def test_gateway_chat_helper_spins_and_passes_through(monkeypatch):
    """On a terminal, blocking gateway turns must run under the standard
    thinking animation (free-tier backends reason before answering; an
    unwrapped call freezes the terminal) and return the response unchanged."""
    import cli.crowe_logic as cl
    from cli import gateway_client
    import cli.branding as branding

    spun = {}

    real_spinner = branding.thinking_spinner

    def _tracking_spinner(label="thinking"):
        spun["label"] = label
        return real_spinner(label)

    monkeypatch.setattr(branding, "thinking_spinner", _tracking_spinner)
    # Force the interactive path; pytest's captured stdout is not a tty.
    monkeypatch.setattr(type(cl.console), "is_terminal", property(lambda self: True))
    monkeypatch.setattr(
        gateway_client, "chat", lambda **kwargs: {"content": "OK", "echo": kwargs}
    )

    resp = cl._gateway_chat(
        model="crowelm-mycelium",
        messages=[{"role": "user", "content": "hi"}],
        bearer="crowe_anon_x",
    )

    assert resp["content"] == "OK"
    assert resp["echo"]["bearer"] == "crowe_anon_x"
    assert spun["label"] == "thinking..."


def test_gateway_chat_helper_skips_spinner_when_not_a_tty(monkeypatch):
    """Regression: when stdout is piped/redirected, the helper must call
    straight through without rich.Live. Live proxies stdout while active and,
    in non-tty mode, swallows the answer printed after it — so a spinner there
    is both pointless and harmful. (Shipped broken in 0.4.1; fixed in 0.4.2.)"""
    import cli.crowe_logic as cl
    from cli import gateway_client
    import cli.branding as branding

    spun = {"called": False}

    def _tracking_spinner(label="thinking"):
        spun["called"] = True
        return branding.thinking_spinner(label)

    monkeypatch.setattr(branding, "thinking_spinner", _tracking_spinner)
    monkeypatch.setattr(type(cl.console), "is_terminal", property(lambda self: False))
    monkeypatch.setattr(
        gateway_client, "chat", lambda **kwargs: {"content": "piped OK"}
    )

    resp = cl._gateway_chat(model="crowelm-mycelium", messages=[])

    assert resp["content"] == "piped OK"
    assert spun["called"] is False


def test_gateway_chat_helper_propagates_free_tier_capped(monkeypatch):
    """Structured gateway errors must escape the spinner unchanged — the call
    sites render the 402 upsell, not the helper."""
    import cli.crowe_logic as cl
    from cli import gateway_client

    def _capped(**kwargs):
        raise gateway_client.FreeTierCapped({"message": "cap", "upsell": "login"})

    monkeypatch.setattr(gateway_client, "chat", _capped)

    with pytest.raises(gateway_client.FreeTierCapped):
        cl._gateway_chat(model="crowelm-mycelium", messages=[])


# ── Task 3: Generalized turn-cap (free signed-in + anonymous) ────────────────


def test_free_principal_id_classifies_anon_free_and_paid():
    from control_plane import gateway

    anon = {"principal": "anonymous", "user_id": "devABC", "plan_id": "free-anonymous"}
    free = {"principal": "crowe-id", "user_id": "sub-123", "plan_id": "free"}
    paid = {"principal": "crowe-id", "user_id": "sub-999", "plan_id": "pro"}

    assert gateway._free_principal_id(anon) == "device:devABC"
    assert gateway._free_principal_id(free) == "user:sub-123"
    assert gateway._free_principal_id(paid) is None


def test_free_signed_in_user_is_turn_capped(monkeypatch):
    import asyncio
    import pytest as _pytest
    from control_plane import gateway

    async def fake_provider(**kwargs):
        return ("ok", 1, 1)

    monkeypatch.setattr(gateway, "_call_provider", lambda **kw: fake_provider(**kw))

    key_info = {
        "principal": "crowe-id",
        "user_id": "sub-123",
        "workspace_id": "sub-123",
        "plan_id": "free",
        "subject": "user@example.com",
    }
    req = gateway.GatewayRequest(
        model="crowelm-mycelium", messages=[{"role": "user", "content": "hi"}]
    )
    db = FakeFreeDb(turns_today=gateway.ANON_DAILY_TURN_CAP)
    with _pytest.raises(gateway.HTTPException) as exc:
        asyncio.run(gateway.gateway_chat(req, key_info=key_info, db=db))
    assert exc.value.status_code == 402
    assert db.read_principal == "user:sub-123"
