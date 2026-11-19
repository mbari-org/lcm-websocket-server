"""
Tests for SpyAccumulator (shared counters) and LCMSpy (per-connection view).
"""

import pytest

from lcm_websocket_server.lib.lcm_utils.channel_stats_list import channel_stats_list
from lcm_websocket_server.lib.lcm_utils.pubsub import LCMRepublisher
from lcm_websocket_server.lib.lcm_utils.spy import (
    VIRTUAL_CHANNEL,
    LCMSpy,
    SpyAccumulator,
)
from tests.conftest import make_header_t

S = 1_000_000_000  # one second in ns
T0 = 10 * S  # arbitrary monotonic origin


@pytest.fixture
def acc(registry) -> SpyAccumulator:
    return SpyAccumulator(registry, window_ns=S)


def stats_by_channel(stats_list: channel_stats_list) -> dict:
    return {cs.channel: cs for cs in stats_list.channels}


# ---------------------------------------------------------------------------
# SpyAccumulator
# ---------------------------------------------------------------------------

class TestSpyAccumulator:
    def test_counts_messages_bytes_and_type(self, acc):
        data = make_header_t()
        for i in range(3):
            acc.record("IMU", data, T0 + i)
        snap = acc.snapshot(T0 + 10)["IMU"]
        assert snap.num_msgs == 3
        assert snap.num_bytes == 3 * len(data)
        assert snap.type_name == "header_t"
        assert snap.undecodable == 0

    def test_unknown_type_counted_as_undecodable(self, acc):
        acc.record("RAW", b"\x01" * 16, T0)
        snap = acc.snapshot(T0)["RAW"]
        assert snap.undecodable == 1
        assert snap.type_name == ("01" * 8)

    def test_ignores_virtual_channel(self, acc):
        acc.record(VIRTUAL_CHANNEL, make_header_t(), T0)
        assert acc.snapshot(T0) == {}

    def test_jitter_is_interval_spread_over_window(self, acc):
        data = make_header_t()
        acc.record("CH", data, T0)
        acc.record("CH", data, T0 + 100_000_000)  # 100 ms
        acc.record("CH", data, T0 + 300_000_000)  # 200 ms
        snap = acc.snapshot(T0 + S)  # window closes
        assert snap["CH"].jitter_s == pytest.approx(0.1)

    def test_jitter_zero_with_single_message(self, acc):
        acc.record("CH", make_header_t(), T0)
        assert acc.snapshot(T0 + S)["CH"].jitter_s == 0.0

    def test_works_as_republisher_tap(self, acc):
        r = LCMRepublisher(".*")
        r.add_tap(acc.record)
        for _ in range(5):
            r.inject("CH", make_header_t())
        assert acc.snapshot()["CH"].num_msgs == 5


# ---------------------------------------------------------------------------
# LCMSpy (per-connection view)
# ---------------------------------------------------------------------------

class TestLCMSpy:
    def test_counts_are_relative_to_view_creation(self, acc):
        data = make_header_t()
        for i in range(7):
            acc.record("CH", data, T0 + i)
        spy = LCMSpy(acc, now_ns=T0 + 10)
        for i in range(3):
            acc.record("CH", data, T0 + 20 + i)
        cs = stats_by_channel(spy.get_stats(now_ns=T0 + S))["CH"]
        assert cs.num_msgs == 3

    def test_channels_without_new_messages_are_omitted(self, acc):
        acc.record("OLD", make_header_t(), T0)
        spy = LCMSpy(acc, now_ns=T0 + 1)
        acc.record("NEW", make_header_t(), T0 + 2)
        assert list(stats_by_channel(spy.get_stats(now_ns=T0 + S))) == ["NEW"]

    def test_hz_and_bandwidth_over_emission_period(self, acc):
        data = make_header_t()
        spy = LCMSpy(acc, now_ns=T0)
        for i in range(10):
            acc.record("CH", data, T0 + i * 100_000_000)
        cs = stats_by_channel(spy.get_stats(now_ns=T0 + S))["CH"]
        assert cs.hz == pytest.approx(10.0)
        assert cs.inv_hz == pytest.approx(0.1)
        assert cs.bandwidth == pytest.approx(10 * len(data))

        # Next period: no new messages -> 0 Hz, but cumulative count persists
        cs = stats_by_channel(spy.get_stats(now_ns=T0 + 2 * S))["CH"]
        assert cs.hz == 0.0
        assert cs.inv_hz == 9999.0
        assert cs.num_msgs == 10

    def test_views_are_independent(self, acc):
        data = make_header_t()
        early = LCMSpy(acc, now_ns=T0)
        acc.record("CH", data, T0 + 1)
        late = LCMSpy(acc, now_ns=T0 + 2)
        acc.record("CH", data, T0 + 3)
        assert stats_by_channel(early.get_stats(now_ns=T0 + S))["CH"].num_msgs == 2
        assert stats_by_channel(late.get_stats(now_ns=T0 + S))["CH"].num_msgs == 1

    def test_channels_sorted(self, acc):
        spy = LCMSpy(acc, now_ns=T0)
        for ch in ("ZEBRA", "ALPHA", "MANGO"):
            acc.record(ch, make_header_t(), T0 + 1)
        assert [cs.channel for cs in spy.get_stats(now_ns=T0 + S).channels] == ["ALPHA", "MANGO", "ZEBRA"]

    def test_stats_bytes_decode_as_channel_stats_list(self, acc):
        spy = LCMSpy(acc, now_ns=T0)
        acc.record("SENSOR", make_header_t(), T0 + 1)
        decoded = channel_stats_list.decode(spy.get_stats_bytes(now_ns=T0 + S))
        assert decoded.num_channels == 1
        assert decoded.channels[0].channel == "SENSOR"
