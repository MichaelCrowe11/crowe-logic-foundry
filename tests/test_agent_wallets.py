import pytest

from control_plane import agent_wallets as w
from control_plane.preview import SqliteDatabase


@pytest.fixture
def db():
    # Fresh in-memory SQLite; agent_wallets + payment_receipts come from _bootstrap (Task 0).
    return SqliteDatabase(":memory:")


@pytest.mark.asyncio
async def test_new_wallet_starts_at_zero(db):
    row = await w.ensure_wallet(db, "agent-1")
    assert row["balance"] == 0


@pytest.mark.asyncio
async def test_credit_then_debit(db):
    await w.ensure_wallet(db, "agent-1")
    bal = await w.credit(
        db,
        "agent-1",
        100,
        receipt_id="r1",
        scheme="crowe-credit",
        resource="/api/agent/v1/chat",
        tx_ref=None,
    )
    assert bal == 100
    bal2 = await w.debit(db, "agent-1", 30)
    assert bal2 == 70


@pytest.mark.asyncio
async def test_debit_insufficient_raises(db):
    await w.ensure_wallet(db, "agent-1")
    with pytest.raises(w.InsufficientFunds):
        await w.debit(db, "agent-1", 5)


@pytest.mark.asyncio
async def test_credit_is_idempotent_on_replayed_receipt(db):
    await w.ensure_wallet(db, "agent-1")
    await w.credit(
        db,
        "agent-1",
        100,
        receipt_id="r1",
        scheme="crowe-credit",
        resource="/api/agent/v1/chat",
        tx_ref=None,
    )
    with pytest.raises(w.DuplicatePayment):
        await w.credit(
            db,
            "agent-1",
            100,
            receipt_id="r1",
            scheme="crowe-credit",
            resource="/api/agent/v1/chat",
            tx_ref=None,
        )
    row = await w.ensure_wallet(db, "agent-1")
    assert row["balance"] == 100  # balance moved exactly once
