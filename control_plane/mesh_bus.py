"""Redis-backed broadcast bus for the Crowe Agent Mesh (B3).

Producers (the /mesh/stream turn loop) publish CMP events keyed by session_id;
WS /mesh/attach subscribers for that session receive them. Backed by Redis
pub/sub so it works across multiple control_plane workers/processes.

Graceful degradation is a hard requirement: a turn must never fail because the
bus is down. `publish` swallows connection errors (logged once), and
`subscribe` ends cleanly if the connection drops — the attach handshake and the
caller's own SSE stream keep working without Redis.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger("control_plane.mesh_bus")

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def _channel(session_id: str) -> str:
    return f"mesh:session:{session_id}"


class MeshBus:
    def __init__(self, client: Any = None, url: str | None = None):
        if client is None:
            import redis.asyncio as aioredis

            url = url or os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
            client = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
        self._client = client

    async def publish(self, session_id: str, event: dict) -> None:
        """Publish a CMP event to a session channel. Never raises — degrades."""
        try:
            await self._client.publish(_channel(session_id), json.dumps(event))
        except Exception as exc:  # noqa: BLE001 — bus failure must not break a turn
            logger.warning("mesh_bus publish failed (degraded): %s", exc)

    async def subscribe(self, session_id: str) -> AsyncIterator[dict]:
        """Yield CMP event dicts published to this session until disconnect."""
        pubsub = self._client.pubsub()
        try:
            await pubsub.subscribe(_channel(session_id))
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", "replace")
                try:
                    yield json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    continue
        except Exception as exc:  # noqa: BLE001 — degrade: end the stream cleanly
            logger.warning("mesh_bus subscribe ended (degraded): %s", exc)
        finally:
            try:
                await pubsub.unsubscribe(_channel(session_id))
                await pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception:  # noqa: BLE001
            return False


_BUS: MeshBus | None = None


def get_bus() -> MeshBus:
    """Module-level singleton reading REDIS_URL on first use."""
    global _BUS
    if _BUS is None:
        _BUS = MeshBus()
    return _BUS
