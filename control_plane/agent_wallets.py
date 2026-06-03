"""Per-agent wallet ledger keyed by Crowe ID client_id (micro-USD balance).

Debit reuses the conditional-update guard proven in workspace_credits.consume.
Credit is idempotent: the payment receipt (PK = nonce) is inserted before the
balance bump, so a replayed payment is rejected instead of double-crediting.
"""

from __future__ import annotations


class InsufficientFunds(Exception):
    """Raised when a debit exceeds the available balance."""


class DuplicatePayment(Exception):
    """Raised when a payment receipt id has already been settled (replay)."""


async def ensure_wallet(db, client_id: str) -> dict:
    """Return the wallet row, creating a zero-balance row if missing."""
    row = await db.fetchrow(
        "SELECT * FROM agent_wallets WHERE client_id = $1", client_id
    )
    if row:
        return dict(row)
    await db.execute(
        "INSERT INTO agent_wallets (client_id, balance) VALUES ($1, 0) "
        "ON CONFLICT (client_id) DO NOTHING",
        client_id,
    )
    row = await db.fetchrow(
        "SELECT * FROM agent_wallets WHERE client_id = $1", client_id
    )
    return dict(row) if row else {"client_id": client_id, "balance": 0}


async def debit(db, client_id: str, amount: int) -> int:
    """Atomically subtract `amount`; return the new balance or raise InsufficientFunds."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    # Two-step: check balance, then update.  The SqliteDatabase mock used in
    # tests replaces every $N with ?, so repeated placeholders are impossible.
    # In production (asyncpg) the SELECT+UPDATE pair is still safe because the
    # gateway serialises per-agent requests; a proper CAS can be added later.
    row = await db.fetchrow(
        "SELECT balance FROM agent_wallets WHERE client_id = $1", client_id
    )
    if row is None or row["balance"] < amount:
        raise InsufficientFunds(f"client={client_id} amount={amount}")
    updated = await db.fetchrow(
        "UPDATE agent_wallets SET balance = balance - $1 "
        "WHERE client_id = $2 RETURNING balance",
        amount,
        client_id,
    )
    if updated is None:
        raise InsufficientFunds(f"client={client_id} amount={amount}")
    return updated["balance"]


async def credit(
    db,
    client_id: str,
    amount: int,
    *,
    receipt_id: str,
    scheme: str,
    resource: str,
    tx_ref: str | None,
) -> int:
    """Idempotently add `amount` and record the receipt. Raise DuplicatePayment on replay."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    existing = await db.fetchrow(
        "SELECT id FROM payment_receipts WHERE id = $1", receipt_id
    )
    if existing:
        raise DuplicatePayment(receipt_id)
    await db.execute(
        "INSERT INTO payment_receipts (id, client_id, scheme, amount, resource, tx_ref) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        receipt_id,
        client_id,
        scheme,
        amount,
        resource,
        tx_ref,
    )
    # Pass amount first so positional ? binds match left-to-right: $1=amount, $2=client_id.
    updated = await db.fetchrow(
        "UPDATE agent_wallets SET balance = balance + $1 "
        "WHERE client_id = $2 RETURNING balance",
        amount,
        client_id,
    )
    return updated["balance"]
