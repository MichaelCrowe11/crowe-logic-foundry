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
    # Single atomic conditional UPDATE: the `balance >= ...` guard makes this
    # race-safe in Postgres (two concurrent debits cannot both match). Placeholders
    # are DISTINCT ($1,$2,$3) and ordered ascending by their position in the string
    # so this is correct under both asyncpg (numbered) and the SqliteDatabase mock
    # (which strips $N->? and binds positionally in string order). $1 and $3 are
    # both `amount`; $2 is the client_id.
    updated = await db.fetchrow(
        "UPDATE agent_wallets SET balance = balance - $1 "
        "WHERE client_id = $2 AND balance >= $3 RETURNING balance",
        amount,
        client_id,
        amount,
    )
    if updated is None:
        raise InsufficientFunds(f"client={client_id} amount={amount}")
    return updated["balance"]


async def refund(db, client_id: str, amount: int) -> int:
    """Return `amount` to the wallet — reverses a debit for a call that never
    delivered (e.g. the upstream provider failed after we charged).

    A refund is NOT a payment, so it records no receipt; it only restores balance.
    Amount first so positional ? binds left-to-right: $1=amount, $2=client_id."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    updated = await db.fetchrow(
        "UPDATE agent_wallets SET balance = balance + $1 "
        "WHERE client_id = $2 RETURNING balance",
        amount,
        client_id,
    )
    return updated["balance"] if updated else 0


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
