"""HTTP-layer tests for the ops router via FastAPI TestClient."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from crowe_synapse_engine.ops.router import build_ops_router  # noqa: E402
from crowe_synapse_engine.ops.store import InMemoryStore  # noqa: E402


@pytest.fixture()
def client():
    store = InMemoryStore()
    aicl_log = []
    app = FastAPI()
    app.include_router(
        build_ops_router(store=store, aicl_sink=aicl_log.append),
        prefix="/ops",
    )
    return TestClient(app), store, aicl_log


def _make_strain(client: TestClient) -> dict:
    r = client.post("/ops/strains", json={"name": "Lions Mane", "species": "He"})
    assert r.status_code == 200, r.text
    return r.json()


def _make_batch(client: TestClient, strain_id: str, code: str = "B-001") -> dict:
    r = client.post(
        "/ops/batches",
        json={
            "code": code,
            "strain_id": strain_id,
            "location": "Room B",
            "operator_id": "op-1",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_strain_create_and_get(client) -> None:
    c, _, _ = client
    strain = _make_strain(c)
    r = c.get(f"/ops/strains/{strain['id']}")
    assert r.status_code == 200
    assert r.json()["name"] == "Lions Mane"


def test_strain_get_missing_404(client) -> None:
    c, _, _ = client
    from uuid import uuid4

    r = c.get(f"/ops/strains/{uuid4()}")
    assert r.status_code == 404


def test_batch_create_get_list(client) -> None:
    c, _, _ = client
    strain = _make_strain(c)
    batch = _make_batch(c, strain["id"])
    r = c.get(f"/ops/batches/{batch['id']}")
    assert r.status_code == 200
    assert r.json()["code"] == "B-001"
    r = c.get("/ops/batches")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_batch_duplicate_code_returns_409(client) -> None:
    c, _, _ = client
    strain = _make_strain(c)
    _make_batch(c, strain["id"], code="B-DUP")
    r = c.post(
        "/ops/batches",
        json={
            "code": "B-DUP",
            "strain_id": strain["id"],
            "location": "L",
        },
    )
    assert r.status_code == 409


def test_batch_with_missing_strain_returns_409(client) -> None:
    c, _, _ = client
    from uuid import uuid4

    r = c.post(
        "/ops/batches",
        json={"code": "B-X", "strain_id": str(uuid4()), "location": "L"},
    )
    assert r.status_code == 409


def test_stage_transition_happy_path(client) -> None:
    c, _, _ = client
    strain = _make_strain(c)
    batch = _make_batch(c, strain["id"])
    r = c.post(
        f"/ops/batches/{batch['id']}/stage",
        json={"target": "colonization", "actor_id": "op-1"},
    )
    assert r.status_code == 200
    assert r.json()["stage"] == "colonization"


def test_illegal_stage_transition_returns_422(client) -> None:
    c, _, _ = client
    strain = _make_strain(c)
    batch = _make_batch(c, strain["id"])
    r = c.post(
        f"/ops/batches/{batch['id']}/stage",
        json={"target": "harvested"},
    )
    assert r.status_code == 422


def test_haccp_pass_then_recall_includes_check(client) -> None:
    c, _, _ = client
    strain = _make_strain(c)
    batch = _make_batch(c, strain["id"])
    r = c.post(
        f"/ops/batches/{batch['id']}/haccp",
        json={
            "ccp_name": "autoclave",
            "target": "121C/15m",
            "actual": "122C/17m",
            "result": "pass",
            "operator_id": "op-1",
        },
    )
    assert r.status_code == 200
    r = c.get(f"/ops/batches/{batch['id']}/recall")
    assert r.status_code == 200
    body = r.json()
    assert len(body["haccp_checks"]) == 1
    assert body["haccp_checks"][0]["ccp_name"] == "autoclave"


def test_haccp_fail_without_corrective_action_returns_422(client) -> None:
    c, _, _ = client
    strain = _make_strain(c)
    batch = _make_batch(c, strain["id"])
    r = c.post(
        f"/ops/batches/{batch['id']}/haccp",
        json={
            "ccp_name": "autoclave",
            "target": "121C/15m",
            "actual": "100C/5m",
            "result": "fail",
            "operator_id": "op-1",
        },
    )
    assert r.status_code == 422


def test_full_lifecycle_to_ship_and_recall(client) -> None:
    c, _, aicl_log = client
    strain = _make_strain(c)
    batch = _make_batch(c, strain["id"], code="LM-W19-003")

    # Move stages.
    for stage in ("colonization", "fruiting"):
        r = c.post(f"/ops/batches/{batch['id']}/stage", json={"target": stage})
        assert r.status_code == 200

    # Record environmental + contamination snapshots.
    c.post(
        f"/ops/batches/{batch['id']}/environmental",
        json={"temp_c": 22.0, "humidity_pct": 90.0, "source": "Room B"},
    )
    c.post(
        f"/ops/batches/{batch['id']}/contamination",
        json={
            "contamination_type": "trichoderma",
            "severity": "low",
            "operator_id": "op-1",
        },
    )

    # Harvest then ship.
    r = c.post(
        f"/ops/batches/{batch['id']}/harvest",
        json={"code": "LM-W19-003-F1", "weight_kg": 1.2, "grade": "A"},
    )
    assert r.status_code == 200, r.text
    lot = r.json()

    r = c.post(
        f"/ops/lots/{lot['id']}/ship",
        json={
            "lot_id": lot["id"],
            "recipient": "Whole Foods AZ",
            "destination_location": "Phoenix, AZ",
        },
    )
    assert r.status_code == 200, r.text

    # Recall trace returns everything.
    r = c.get(f"/ops/batches/{batch['id']}/recall")
    assert r.status_code == 200
    body = r.json()
    assert body["batch"]["code"] == "LM-W19-003"
    assert len(body["lots"]) == 1
    assert len(body["contamination_events"]) == 1
    types = [e["event_type"] for e in body["tracking_events"]]
    assert "receive" in types
    assert "harvest" in types
    assert "ship" in types
    # AICL log accumulated through the whole flow.
    assert len(aicl_log) >= 7


def test_ship_lot_id_mismatch_returns_400(client) -> None:
    c, _, _ = client
    strain = _make_strain(c)
    batch = _make_batch(c, strain["id"])
    c.post(f"/ops/batches/{batch['id']}/stage", json={"target": "colonization"})
    c.post(f"/ops/batches/{batch['id']}/stage", json={"target": "fruiting"})
    r = c.post(
        f"/ops/batches/{batch['id']}/harvest",
        json={"code": "X-F1", "weight_kg": 1.0},
    )
    lot = r.json()
    from uuid import uuid4

    r = c.post(
        f"/ops/lots/{lot['id']}/ship",
        json={
            "lot_id": str(uuid4()),
            "recipient": "X",
            "destination_location": "Y",
        },
    )
    assert r.status_code == 400
