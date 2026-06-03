import httpx
import pytest

from control_plane import settlement


def _transport(verify_json, settle_json, *, verify_status=200, settle_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/verify"):
            return httpx.Response(verify_status, json=verify_json)
        if request.url.path.endswith("/settle"):
            return httpx.Response(settle_status, json=settle_json)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


REQS = {
    "scheme": "exact",
    "network": "base",
    "asset": "USDC",
    "maxAmountRequired": "50",
    "resource": "/api/agent/v1/chat",
    "payTo": "0xabc",
}
PAYMENT = {
    "scheme": "exact",
    "nonce": "n1",
    "amount": 50,
    "resource": "/api/agent/v1/chat",
    "payload": "0xsigned",
}


@pytest.mark.asyncio
async def test_real_facilitator_verify_and_settle_success():
    fac = settlement.Facilitator(
        "https://fac.example",
        transport=_transport(
            {"isValid": True}, {"success": True, "txHash": "0xdeadbeef"}
        ),
    )
    receipt = await fac.verify_and_settle(PAYMENT, REQS, price=50)
    assert receipt.scheme == "exact"
    assert receipt.amount == 50
    assert receipt.tx_ref == "0xdeadbeef"
    assert receipt.id == "n1"


@pytest.mark.asyncio
async def test_facilitator_rejects_invalid_payment():
    fac = settlement.Facilitator(
        "https://fac.example",
        transport=_transport(
            {"isValid": False, "invalidReason": "bad sig"}, {"success": True}
        ),
    )
    with pytest.raises(settlement.PaymentError):
        await fac.verify_and_settle(PAYMENT, REQS, price=50)


@pytest.mark.asyncio
async def test_settlement_failure_raises():
    fac = settlement.Facilitator(
        "https://fac.example",
        transport=_transport(
            {"isValid": True}, {"success": False, "errorReason": "reverted"}
        ),
    )
    with pytest.raises(settlement.PaymentError):
        await fac.verify_and_settle(PAYMENT, REQS, price=50)


def test_get_facilitator_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("X402_FACILITATOR_URL", raising=False)
    assert settlement.get_facilitator() is None


def test_get_facilitator_built_when_configured(monkeypatch):
    monkeypatch.setenv("X402_FACILITATOR_URL", "https://fac.example")
    fac = settlement.get_facilitator()
    assert isinstance(fac, settlement.Facilitator)
