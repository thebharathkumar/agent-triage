"""streaming.py - simple in-process pub/sub for Server-Sent Events.

Each connected dashboard browser subscribes via ``EventBus.subscribe()``
which yields events whenever ``EventBus.publish()`` is called from the
upload/OTLP handlers. Backpressure is handled by per-subscriber
asyncio queues with a small bound — slow clients drop events rather
than blocking publishers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """Fan-out pub/sub for dashboard SSE subscribers."""

    def __init__(self, queue_size: int = 32) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._queue_size = queue_size

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        """Push an event to every subscriber, dropping for slow ones."""
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("SSE subscriber queue full; dropping event %s", event)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_bus_singleton: EventBus | None = None


def get_bus() -> EventBus:
    global _bus_singleton
    if _bus_singleton is None:
        _bus_singleton = EventBus()
    return _bus_singleton


def reset_bus() -> EventBus:
    """Test helper: replace the singleton with a fresh bus."""
    global _bus_singleton
    _bus_singleton = EventBus()
    return _bus_singleton
