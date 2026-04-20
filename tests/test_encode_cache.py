"""
Tests for EncodeCache.
"""

import threading

import pytest

from lcm_websocket_server.lib.handler import EncodeCache


class TestEncodeCacheHitAndMiss:
    def test_cache_miss_on_first_call(self):
        cache = EncodeCache()
        data = b"hello"
        assert cache.get("CH", data) is None

    def test_cache_hit_after_put(self):
        cache = EncodeCache()
        data = b"hello"
        result = b"encoded"
        cache.put("CH", data, result)
        assert cache.get("CH", data) is result  # same object

    def test_cache_hit_uses_identity_not_equality(self):
        """Two bytes objects with identical content but different identities are
        different cache keys.

        LCM's encode() always allocates a new bytes object, making it a reliable
        source of same-content, different-identity pairs.
        """
        from stdlcm import header_t

        msg = header_t()
        msg.sequence = 42
        data_a = msg.encode()
        data_b = msg.encode()  # same content, new allocation
        assert data_a == data_b, "Sanity: content must be equal"
        assert data_a is not data_b, "LCM encode() must return fresh bytes each call"

        result = b"encoded"
        cache = EncodeCache()
        cache.put("CH", data_a, result)

        # data_a → hit
        assert cache.get("CH", data_a) is result
        # data_b has equal content but a different identity → miss
        assert cache.get("CH", data_b) is None

    def test_cache_is_per_channel(self):
        cache = EncodeCache()
        data = b"payload"
        result_a = b"result_for_A"
        result_b = b"result_for_B"

        cache.put("CHAN_A", data, result_a)
        cache.put("CHAN_B", data, result_b)

        assert cache.get("CHAN_A", data) is result_a
        assert cache.get("CHAN_B", data) is result_b

    def test_put_overwrites_previous_entry_for_channel(self):
        cache = EncodeCache()
        old_data = b"old"
        new_data = b"new"
        old_result = b"old_result"
        new_result = b"new_result"

        cache.put("CH", old_data, old_result)
        cache.put("CH", new_data, new_result)

        # The old entry is gone; only the new one remains.
        assert cache.get("CH", old_data) is None
        assert cache.get("CH", new_data) is new_result

    def test_unknown_channel_returns_none(self):
        cache = EncodeCache()
        assert cache.get("NONEXISTENT", b"data") is None


class TestEncodeCacheThreadSafety:
    def test_concurrent_get_and_put_do_not_corrupt(self):
        """
        Many threads doing get/put on the same cache should never raise or
        return a result belonging to a different channel.
        """
        cache = EncodeCache()
        errors: list[Exception] = []
        iterations = 500

        def worker(channel: str, data: bytes, result: bytes):
            for _ in range(iterations):
                try:
                    cache.put(channel, data, result)
                    got = cache.get(channel, data)
                    # If we get a hit it must be OUR result (not another channel's).
                    if got is not None and got is not result:
                        errors.append(ValueError(
                            f"Cache returned wrong result for {channel}: {got!r}"
                        ))
                except Exception as exc:
                    errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(f"CH{i}", f"data{i}".encode(), f"res{i}".encode()))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not any(t.is_alive() for t in threads), "Worker thread hung"
        assert not errors, f"Errors during concurrent cache access: {errors}"
