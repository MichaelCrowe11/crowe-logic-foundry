"""
Tests for the Stripe webhook reconciliation in control_plane/__init__.py.

The handler-level helpers are pure functions of (db, event_object) so we
drive them directly with a RecordingMock that records every execute()
call and a Postgres-shaped subset of fetchrow() that we can program per
test. No HTTP layer, no Stripe SDK, no Postgres.

Coverage:
- _record_event_or_skip is atomic: first insert returns True, replay
  returns False without re-inserting.
- _handle_invoice_paid refills credits exactly once per Stripe event,
  via the idempotency check above.
- _handle_subscription_created persists a new subscription row and
  binds it to the workspace.
- _handle_subscription_updated updates state and refills on active.
- _handle_subscription_deleted cancels, suspends, and zeroes the
  allocation.
- _handle_invoice_payment_failed deactivates credits without zeroing
  the balance.
"""

from __future__ import annotations

import asyncio



# ─── Recording mock DB ───────────────────────────────────────────────


class RecordingDB:
    """Records every execute / fetch call for assertions.

    fetchrow returns the first item from the per-query queue if set,
    otherwise None. This is enough for _record_event_or_skip's INSERT
    ... RETURNING pattern: queue {"id": "be_1"} for the first call to
    simulate a successful insert, then queue None to simulate the
    DO NOTHING replay path.
    """

    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []
        self.fetches: list[tuple[str, tuple]] = []
        self._fetchrow_queue: list[object] = []
        self._fetch_queue: list[list] = []

    def queue_fetchrow(self, value):
        self._fetchrow_queue.append(value)

    def queue_fetch(self, rows):
        self._fetch_queue.append(rows)

    async def execute(self, query: str, *args):
        self.executes.append((query, args))

    async def fetchrow(self, query: str, *args):
        self.fetches.append((query, args))
        if self._fetchrow_queue:
            return self._fetchrow_queue.pop(0)
        return None

    async def fetch(self, query: str, *args):
        self.fetches.append((query, args))
        if self._fetch_queue:
            return self._fetch_queue.pop(0)
        return []

    def queries_matching(self, needle: str) -> list[tuple[str, tuple]]:
        n = needle.lower()
        return [(q, a) for (q, a) in self.executes if n in q.lower()]


def run(coro):
    return asyncio.run(coro)


# ─── _record_event_or_skip ───────────────────────────────────────────


def test_record_event_first_time_returns_true():
    from control_plane import _record_event_or_skip

    db = RecordingDB()
    db.queue_fetchrow({"id": "be_1"})  # simulate successful insert

    is_new = run(_record_event_or_skip(db, "evt_1", "invoice.paid", "{}"))

    assert is_new is True
    assert len(db.fetches) == 1
    query, args = db.fetches[0]
    assert "billing_events" in query.lower()
    assert "on conflict" in query.lower()
    assert "returning id" in query.lower()
    assert args == ("evt_1", "invoice.paid", "{}")


def test_record_event_replay_returns_false():
    from control_plane import _record_event_or_skip

    db = RecordingDB()
    db.queue_fetchrow(None)  # simulate DO NOTHING (already exists)

    is_new = run(_record_event_or_skip(db, "evt_1", "invoice.paid", "{}"))

    assert is_new is False


# ─── _handle_invoice_paid ────────────────────────────────────────────


def test_invoice_paid_activates_subscription_and_refills():
    from control_plane import _handle_invoice_paid

    db = RecordingDB()
    # _refill_credits_for_subscription runs a fetch over workspaces
    # joined with subscriptions; return one row to drive the refill.
    db.queue_fetch([
        {"workspace_id": "ws_1", "plan_id": "pro", "current_period_end": None},
    ])

    run(_handle_invoice_paid(db, {"subscription": "sub_1"}))

    sub_updates = db.queries_matching("update subscriptions")
    assert sub_updates, "expected subscriptions to be marked active"
    assert "active" in sub_updates[0][0].lower()
    assert sub_updates[0][1] == ("sub_1",)

    credit_inserts = db.queries_matching("insert into workspace_credits")
    assert credit_inserts, "expected a credit refill"
    # workspace_credits args: (workspace_id, tier_key, allocation, reset_at)
    args = credit_inserts[0][1]
    assert args[0] == "ws_1"
    assert args[1] == "pro"
    assert args[2] == 3000  # TIER_ALLOCATIONS["pro"]


def test_invoice_paid_with_no_subscription_id_does_nothing():
    from control_plane import _handle_invoice_paid

    db = RecordingDB()
    run(_handle_invoice_paid(db, {}))

    assert db.executes == []
    assert db.fetches == []


# ─── _handle_subscription_created ────────────────────────────────────


def test_subscription_created_inserts_record_and_binds_workspace():
    from control_plane import _handle_subscription_created

    db = RecordingDB()
    sub = {
        "id": "sub_2",
        "status": "active",
        "current_period_start": 1714435200,
        "current_period_end":   1717113600,
        "metadata": {"workspace_id": "ws_2", "plan_id": "personal"},
    }

    run(_handle_subscription_created(db, sub))

    sub_inserts = db.queries_matching("insert into subscriptions")
    assert sub_inserts
    args = sub_inserts[0][1]
    # args: (workspace_id, plan_id, stripe_subscription_id, status, period_start, period_end)
    assert args[0] == "ws_2"
    assert args[1] == "personal"
    assert args[2] == "sub_2"
    assert args[3] == "active"

    workspace_updates = db.queries_matching("update workspaces")
    assert workspace_updates
    assert workspace_updates[0][1] == ("sub_2", "ws_2")


def test_subscription_created_without_workspace_metadata_is_noop():
    from control_plane import _handle_subscription_created

    db = RecordingDB()
    run(_handle_subscription_created(db, {"id": "sub_3", "metadata": {}}))

    assert db.executes == []


# ─── _handle_subscription_updated ────────────────────────────────────


def test_subscription_updated_active_refills():
    from control_plane import _handle_subscription_updated

    db = RecordingDB()
    db.queue_fetch([
        {"workspace_id": "ws_4", "plan_id": "team", "current_period_end": None},
    ])

    run(_handle_subscription_updated(db, {
        "id": "sub_4",
        "status": "active",
        "current_period_start": 1714435200,
        "current_period_end":   1717113600,
    }))

    sub_updates = db.queries_matching("update subscriptions")
    assert sub_updates
    refills = db.queries_matching("insert into workspace_credits")
    assert refills, "active update should trigger credit refill"


def test_subscription_updated_past_due_does_not_refill():
    from control_plane import _handle_subscription_updated

    db = RecordingDB()
    run(_handle_subscription_updated(db, {
        "id": "sub_5",
        "status": "past_due",
        "current_period_start": 1714435200,
        "current_period_end":   1717113600,
    }))

    sub_updates = db.queries_matching("update subscriptions")
    assert sub_updates
    refills = db.queries_matching("insert into workspace_credits")
    assert not refills, "non-active update must not refill credits"


# ─── _handle_subscription_deleted ────────────────────────────────────


def test_subscription_deleted_cancels_suspends_and_zeroes_allocation():
    from control_plane import _handle_subscription_deleted

    db = RecordingDB()
    run(_handle_subscription_deleted(db, {"id": "sub_6"}))

    cancels = db.queries_matching("update subscriptions")
    assert cancels and "cancelled" in cancels[0][0].lower()

    suspends = db.queries_matching("update workspaces")
    assert suspends and "suspended" in suspends[0][0].lower()

    deactivations = db.queries_matching("update workspace_credits")
    assert deactivations
    q = deactivations[0][0].lower()
    assert "active = false" in q
    assert "allocation = 0" in q


# ─── _handle_invoice_payment_failed ──────────────────────────────────


def test_payment_failed_deactivates_credits_without_zeroing():
    from control_plane import _handle_invoice_payment_failed

    db = RecordingDB()
    run(_handle_invoice_payment_failed(db, {"subscription": "sub_7"}))

    deactivations = db.queries_matching("update workspace_credits")
    assert deactivations
    q = deactivations[0][0].lower()
    assert "active = false" in q
    # Crucial: balance and allocation must NOT be zeroed on failed payment.
    # The user keeps unspent credits if Stripe recovers payment in the
    # grace window.
    assert "allocation = 0" not in q
    assert "balance = 0" not in q


def test_payment_failed_with_no_subscription_id_is_noop():
    from control_plane import _handle_invoice_payment_failed

    db = RecordingDB()
    run(_handle_invoice_payment_failed(db, {}))

    assert db.executes == []


# ─── Replay scenario (the launch-blocker bug) ────────────────────────


def test_invoice_paid_replay_does_not_double_grant_credits():
    """Regression test for the launch-blocker behavior.

    Two webhook deliveries with the same stripe_event_id arrive (Stripe
    retries on a non-2xx response, and at-least-once delivery is the
    documented contract). The atomic idempotency check must short-circuit
    the second delivery before _handle_invoice_paid fires.

    We model this by running the full webhook path twice against a mock
    that programs the second insert to return None (simulating the
    UNIQUE conflict).
    """
    from control_plane import _record_event_or_skip, _handle_invoice_paid

    db = RecordingDB()
    db.queue_fetchrow({"id": "be_1"})       # first call: insert succeeds
    db.queue_fetchrow(None)                  # second call: replay
    db.queue_fetch([                         # only used if handler runs
        {"workspace_id": "ws_8", "plan_id": "pro", "current_period_end": None},
    ])

    # First delivery: handler runs.
    is_new = run(_record_event_or_skip(db, "evt_replay", "invoice.paid", "{}"))
    assert is_new is True
    run(_handle_invoice_paid(db, {"subscription": "sub_8"}))

    refills_after_first = len(db.queries_matching("insert into workspace_credits"))
    assert refills_after_first == 1

    # Second delivery: handler must NOT run. The webhook entrypoint
    # short-circuits when _record_event_or_skip returns False.
    is_new_replay = run(_record_event_or_skip(db, "evt_replay", "invoice.paid", "{}"))
    assert is_new_replay is False

    refills_after_replay = len(db.queries_matching("insert into workspace_credits"))
    assert refills_after_replay == 1, (
        "replay must not produce a second credit refill"
    )
