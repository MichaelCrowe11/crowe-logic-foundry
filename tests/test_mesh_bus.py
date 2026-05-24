"""Tests for the Redis-backed mesh broadcast bus (B3). Uses fakeredis."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fakeredis")

import fakeredis.aioredis

from control_plane.mesh_bus import MeshBus


def _bus() -> MeshBus:
    return MeshBus(client=fakeredis.aioredis.FakeRedis())


def test_publish_subscribe_roundtrip():
    async def go():
        bus = _bus()
        received: list[dict] = []
        sub = bus.subscribe("s1")

        async def reader():
            async for ev in sub:
                received.append(ev)
                if len(received) >= 2:
                    return

        task = asyncio.create_task(reader())
        await asyncio.sleep(0.05)  # let the subscription register
        await bus.publish("s1", {"type": "token", "delta": "a", "session_id": "s1"})
        await bus.publish("s1", {"type": "token", "delta": "b", "session_id": "s1"})
        await asyncio.wait_for(task, timeout=2.0)
        assert [e["delta"] for e in received] == ["a", "b"]

    asyncio.run(go())


def test_publish_does_not_raise_when_client_errors():
    async def go():
        class _Boom:
            async def publish(self, *a, **k):
                raise RuntimeError("redis down")

        bus = MeshBus(client=_Boom())
        # Must degrade gracefully, never propagate.
        await bus.publish("s1", {"type": "token"})

    asyncio.run(go())


def test_other_session_not_received():
    async def go():
        bus = _bus()
        received: list[dict] = []
        sub = bus.subscribe("s1")

        async def reader():
            async for ev in sub:
                received.append(ev)
                return

        task = asyncio.create_task(reader())
        await asyncio.sleep(0.05)
        await bus.publish("s2", {"type": "token", "delta": "x"})
        await bus.publish("s1", {"type": "token", "delta": "mine"})
        await asyncio.wait_for(task, timeout=2.0)
        assert received[0]["delta"] == "mine"

    asyncio.run(go())
