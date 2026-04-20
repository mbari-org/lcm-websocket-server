"""
Tests for LCMSpy and ChannelData statistics accumulation.
"""

import time

import pytest
from stdlcm import header_t

from lcm_websocket_server.lib.lcm_utils.spy import ChannelData, LCMSpy

from tests.conftest import make_header_t


# ---------------------------------------------------------------------------
# ChannelData: per-message accounting
# ---------------------------------------------------------------------------

class TestChannelData:
    def test_message_count_increments(self):
        cd = ChannelData()
        cd.message_received("header_t", 64, True)
        cd.message_received("header_t", 64, True)
        assert cd._num_msgs == 2

    def test_undecodable_count_increments_on_failure(self):
        cd = ChannelData()
        cd.message_received("header_t", 64, decoded=True)
        cd.message_received("??", 8, decoded=False)
        assert cd._undecodable == 1

    def test_hz_estimate_after_update(self):
        cd = ChannelData()
        start = time.monotonic_ns()
        for _ in range(10):
            cd.message_received("header_t", 64, True, timestamp_ns=start)
        elapsed_ns = int(1e9)  # Pretend 1 second elapsed
        cd.update_hz_data(start + elapsed_ns)
        # 10 messages in ~1 second → ~10 Hz
        assert 5.0 < cd._hz < 20.0

    def test_bandwidth_estimate(self):
        cd = ChannelData()
        start = time.monotonic_ns()
        for _ in range(100):
            cd.message_received("header_t", 1000, True, timestamp_ns=start)
        cd.update_hz_data(start + int(1e9))
        # 100 * 1000 bytes in ~1 second = ~100 kB/s
        assert 50_000 < cd._bandwidth < 200_000

    def test_jitter_computed_from_interval_spread(self):
        cd = ChannelData()
        # Irregular spacing: 100ms, 200ms
        t0 = time.monotonic_ns()
        cd.message_received("h", 64, True, timestamp_ns=t0)
        cd.message_received("h", 64, True, timestamp_ns=t0 + 100_000_000)
        cd.message_received("h", 64, True, timestamp_ns=t0 + 300_000_000)
        cd.update_hz_data(t0 + int(1e9))
        # jitter = max_interval - min_interval = 200ms - 100ms = 100ms
        # ChannelData stores _min_interval / _max_interval in seconds after update.
        assert cd._min_interval is not None
        assert cd._max_interval is not None
        stats = cd.report("CH")
        # jitter ≈ 0.1 seconds (200ms - 100ms)
        assert 0.05 < stats.jitter < 0.2, f"Unexpected jitter: {stats.jitter}"

    def test_report_populates_channel_stats_fields(self):
        cd = ChannelData()
        cd.message_received("header_t", 64, True)
        start = time.monotonic_ns()
        cd.update_hz_data(start + int(1e9))
        stats = cd.report("MY_CHANNEL")
        assert stats.channel == "MY_CHANNEL"
        assert stats.type == "header_t"
        assert stats.num_msgs == 1


# ---------------------------------------------------------------------------
# LCMSpy: accumulation and emission
# ---------------------------------------------------------------------------

class TestLCMSpy:
    def test_handle_tracks_channel(self, registry):
        spy = LCMSpy(registry)
        spy.handle("SENSOR", make_header_t(), timestamp_ns=time.monotonic_ns())
        assert "SENSOR" in spy._channel_data

    def test_handle_ignores_virtual_spy_channel(self, registry):
        spy = LCMSpy(registry)
        spy.handle(LCMSpy.VIRTUAL_CHANNEL, b"\x00" * 8)
        assert LCMSpy.VIRTUAL_CHANNEL not in spy._channel_data

    def test_multiple_channels_tracked_independently(self, registry):
        spy = LCMSpy(registry)
        for i in range(5):
            spy.handle("IMU", make_header_t(sequence=i), timestamp_ns=time.monotonic_ns())
        spy.handle("GPS", make_header_t(), timestamp_ns=time.monotonic_ns())

        assert "IMU" in spy._channel_data
        assert "GPS" in spy._channel_data
        assert spy._channel_data["IMU"]._num_msgs == 5
        assert spy._channel_data["GPS"]._num_msgs == 1

    def test_get_stats_returns_sorted_channel_list(self, registry):
        spy = LCMSpy(registry)
        for ch in ("ZEBRA", "ALPHA", "MANGO"):
            spy.handle(ch, make_header_t(), timestamp_ns=time.monotonic_ns())

        stats = spy.get_stats()
        channels = [cs.channel for cs in stats.channels]
        assert channels == sorted(channels)

    def test_maybe_get_stats_bytes_returns_none_before_interval(self, registry):
        spy = LCMSpy(registry)
        spy.handle("CH", make_header_t(), timestamp_ns=time.monotonic_ns())

        # Immediately after construction, the interval hasn't elapsed.
        result = spy.maybe_get_stats_bytes(interval_ns=int(1e9))
        assert result is None

    def test_maybe_get_stats_bytes_emits_after_interval(self, registry):
        spy = LCMSpy(registry)
        spy.handle("CH", make_header_t(), timestamp_ns=time.monotonic_ns())

        # Use a very short interval to trigger emission immediately.
        result = spy.maybe_get_stats_bytes(interval_ns=1)
        assert result is not None
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_emitted_bytes_are_decodable_channel_stats_list(self, registry):
        from lcm_websocket_server.lib.lcm_utils.channel_stats_list import channel_stats_list
        spy = LCMSpy(registry)
        spy.handle("SENSOR", make_header_t(), timestamp_ns=time.monotonic_ns())

        payload = spy.maybe_get_stats_bytes(interval_ns=1)
        assert payload is not None

        decoded = channel_stats_list.decode(payload)
        assert decoded.num_channels == 1
        assert decoded.channels[0].channel == "SENSOR"

    def test_does_not_emit_twice_within_interval(self, registry):
        spy = LCMSpy(registry)
        spy.handle("CH", make_header_t(), timestamp_ns=time.monotonic_ns())

        first = spy.maybe_get_stats_bytes(interval_ns=1)  # emits
        second = spy.maybe_get_stats_bytes(interval_ns=int(1e9))  # interval not elapsed
        assert first is not None
        assert second is None

    # Fix 7: configurable interval
    def test_custom_interval_respected(self, registry):
        spy = LCMSpy(registry)
        spy.handle("CH", make_header_t(), timestamp_ns=time.monotonic_ns())

        # 1-nanosecond interval → always emits
        result = spy.maybe_get_stats_bytes(interval_ns=1)
        assert result is not None

    def test_long_interval_suppresses_emission(self, registry):
        spy = LCMSpy(registry)
        spy.handle("CH", make_header_t(), timestamp_ns=time.monotonic_ns())

        # 1-hour interval → never emits in a test
        result = spy.maybe_get_stats_bytes(interval_ns=3_600_000_000_000)
        assert result is None
