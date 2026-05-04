"""Tests for triage.streaming."""

from __future__ import annotations

import asyncio

import pytest

from triage.streaming import EventBus, get_bus, reset_bus


@pytest.fixture(autouse=True)
def fresh_bus():
    reset_bus()
    yield
    reset_bus()


class TestEventBus:
    def test_starts_with_zero_subscribers(self):
        bus = EventBus()
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_subscribe_increments_count(self):
        bus = EventBus()

        async def consume():
            agen = bus.subscribe()
            # Pull just one event then exit
            return await asyncio.wait_for(agen.__anext__(), timeout=0.5)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)  # let subscriber register
        assert bus.subscriber_count == 1
        await bus.publish({"type": "test"})
        result = await task
        assert result == {"type": "test"}

    @pytest.mark.asyncio
    async def test_publish_to_no_subscribers_is_safe(self):
        bus = EventBus()
        await bus.publish({"type": "noop"})  # should not raise

    @pytest.mark.asyncio
    async def test_publish_fans_out_to_all_subscribers(self):
        bus = EventBus()

        async def consume():
            agen = bus.subscribe()
            return await asyncio.wait_for(agen.__anext__(), timeout=0.5)

        t1 = asyncio.create_task(consume())
        t2 = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        assert bus.subscriber_count == 2

        await bus.publish({"type": "fanout"})
        r1, r2 = await asyncio.gather(t1, t2)
        assert r1 == {"type": "fanout"}
        assert r2 == {"type": "fanout"}

    @pytest.mark.asyncio
    async def test_full_queue_drops_event_for_slow_subscriber(self):
        bus = EventBus(queue_size=2)

        # Subscribe but never consume
        agen = bus.subscribe()
        # Force registration by getting one waiting
        consumer = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0.05)

        # Fill the queue beyond capacity
        for i in range(10):
            await bus.publish({"i": i})

        # Should not have raised; consumer can read what fit
        first = await asyncio.wait_for(consumer, timeout=0.5)
        assert first["i"] == 0


class TestSingleton:
    def test_get_bus_returns_same_instance(self):
        a = get_bus()
        b = get_bus()
        assert a is b

    def test_reset_replaces_instance(self):
        a = get_bus()
        b = reset_bus()
        assert a is not b
