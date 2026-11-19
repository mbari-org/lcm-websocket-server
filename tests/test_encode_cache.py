"""
Tests for the loop-confined, single-flight EncodeCache.
"""

import asyncio

import pytest

from lcm_websocket_server.lib.handler import EncodeCache


class Encoder:
    """Counts calls; optionally blocks until released to simulate a slow encode."""

    def __init__(self, result="encoded", gate: asyncio.Event | None = None):
        self.calls = 0
        self.result = result
        self.gate = gate

    async def __call__(self):
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return self.result


class TestHitAndMiss:
    async def test_second_request_for_same_object_is_cached(self):
        cache, enc = EncodeCache(), Encoder()
        data = b"x"
        assert await cache.get_or_encode("CH", data, enc) == "encoded"
        assert await cache.get_or_encode("CH", data, enc) == "encoded"
        assert enc.calls == 1

    async def test_identity_not_equality(self):
        cache, enc = EncodeCache(), Encoder()
        a, b = bytes(bytearray(b"same")), bytes(bytearray(b"same"))
        assert a == b and a is not b
        await cache.get_or_encode("CH", a, enc)
        await cache.get_or_encode("CH", b, enc)
        assert enc.calls == 2

    async def test_per_channel(self):
        cache, enc = EncodeCache(), Encoder()
        data = b"x"
        await cache.get_or_encode("A", data, enc)
        await cache.get_or_encode("B", data, enc)
        await cache.get_or_encode("A", data, enc)
        assert enc.calls == 2

    async def test_none_result_is_cached(self):
        """Unencodable messages are not retried for every client."""
        cache, enc = EncodeCache(), Encoder(result=None)
        data = b"x"
        assert await cache.get_or_encode("CH", data, enc) is None
        assert await cache.get_or_encode("CH", data, enc) is None
        assert enc.calls == 1


class TestDepth:
    async def test_keeps_recent_messages_per_channel(self):
        cache, enc = EncodeCache(depth=2), Encoder()
        msgs = [bytes([i]) for i in range(3)]
        for m in msgs:
            await cache.get_or_encode("CH", m, enc)
        assert enc.calls == 3
        await cache.get_or_encode("CH", msgs[1], enc)  # still cached
        await cache.get_or_encode("CH", msgs[2], enc)  # still cached
        assert enc.calls == 3
        await cache.get_or_encode("CH", msgs[0], enc)  # evicted
        assert enc.calls == 4

    def test_invalid_depth(self):
        with pytest.raises(ValueError):
            EncodeCache(depth=0)


class TestSingleFlight:
    async def test_concurrent_requests_share_one_encode(self):
        gate = asyncio.Event()
        cache, enc = EncodeCache(), Encoder(gate=gate)
        data = b"x"
        waiters = [asyncio.create_task(cache.get_or_encode("CH", data, enc)) for _ in range(10)]
        await asyncio.sleep(0.01)
        gate.set()
        assert await asyncio.gather(*waiters) == ["encoded"] * 10
        assert enc.calls == 1

    async def test_cancelling_one_waiter_does_not_cancel_the_encode(self):
        gate = asyncio.Event()
        cache, enc = EncodeCache(), Encoder(gate=gate)
        data = b"x"
        first = asyncio.create_task(cache.get_or_encode("CH", data, enc))
        second = asyncio.create_task(cache.get_or_encode("CH", data, enc))
        await asyncio.sleep(0.01)
        first.cancel()
        gate.set()
        assert await second == "encoded"
        assert enc.calls == 1
