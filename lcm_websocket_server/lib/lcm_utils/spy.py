"""
lcm-spy style channel statistics.

SpyAccumulator keeps cumulative per-channel counters and is fed once per
message from the LCM thread (as an LCMRepublisher tap), so its counts reflect
all traffic regardless of any client's queue. LCMSpy is a cheap
per-connection view that reports statistics since that connection started.
"""

import threading
from dataclasses import dataclass
from time import monotonic_ns, time_ns

from lcmutils import LCMTypeRegistry

from lcm_websocket_server.lib.lcm_utils.channel_stats import channel_stats
from lcm_websocket_server.lib.lcm_utils.channel_stats_list import channel_stats_list

VIRTUAL_CHANNEL = "LWS_LCM_SPY"


class _ChannelCounters:
    """Mutable cumulative counters for one channel. Guarded by SpyAccumulator's lock."""

    __slots__ = (
        "jitter_s",
        "last_arrival_ns",
        "latest_wall_ns",
        "num_bytes",
        "num_msgs",
        "type_name",
        "undecodable",
        "window_max_ns",
        "window_min_ns",
        "window_start_ns",
    )

    def __init__(self, now_ns: int):
        self.type_name = ""
        self.num_msgs = 0
        self.num_bytes = 0
        self.undecodable = 0
        self.latest_wall_ns = 0
        self.last_arrival_ns: int | None = None
        self.window_start_ns = now_ns
        self.window_min_ns: int | None = None
        self.window_max_ns: int | None = None
        self.jitter_s = 0.0

    def roll_window(self, now_ns: int, window_ns: int) -> None:
        """Close the jitter window if it has elapsed: jitter = max - min inter-arrival interval."""
        if now_ns - self.window_start_ns < window_ns:
            return
        if self.window_min_ns is not None:
            self.jitter_s = (self.window_max_ns - self.window_min_ns) / 1e9
        else:
            self.jitter_s = 0.0
        self.window_min_ns = None
        self.window_max_ns = None
        self.window_start_ns = now_ns


@dataclass(frozen=True)
class ChannelSnapshot:
    """Immutable view of a channel's cumulative counters."""

    type_name: str
    num_msgs: int
    num_bytes: int
    undecodable: int
    latest_wall_ns: int
    jitter_s: float


class SpyAccumulator:
    """
    Shared, thread-safe cumulative statistics for every observed channel.

    record() matches the LCMRepublisher tap signature.
    """

    def __init__(self, registry: LCMTypeRegistry, window_ns: int = 1_000_000_000):
        """
        Args:
            registry: Registry used to identify message types.
            window_ns: Window over which inter-arrival jitter is measured.
        """
        self._registry = registry
        self._window_ns = window_ns
        self._lock = threading.Lock()
        self._channels: dict[str, _ChannelCounters] = {}

    def record(self, channel: str, data: bytes, timestamp_ns: int) -> None:
        """Account for one message. Called on the LCM thread."""
        if channel == VIRTUAL_CHANNEL:
            return
        lcm_type = self._registry.detect(data)
        type_name = lcm_type.__name__ if lcm_type is not None else data[:8].hex()
        wall_ns = time_ns()

        with self._lock:
            c = self._channels.get(channel)
            if c is None:
                c = self._channels[channel] = _ChannelCounters(timestamp_ns)
            c.roll_window(timestamp_ns, self._window_ns)
            c.type_name = type_name
            c.num_msgs += 1
            c.num_bytes += len(data)
            if lcm_type is None:
                c.undecodable += 1
            c.latest_wall_ns = wall_ns
            if c.last_arrival_ns is not None:
                interval = timestamp_ns - c.last_arrival_ns
                if c.window_min_ns is None or interval < c.window_min_ns:
                    c.window_min_ns = interval
                if c.window_max_ns is None or interval > c.window_max_ns:
                    c.window_max_ns = interval
            c.last_arrival_ns = timestamp_ns

    def snapshot(self, now_ns: int | None = None) -> dict[str, ChannelSnapshot]:
        """Return a consistent snapshot of all channels."""
        now_ns = monotonic_ns() if now_ns is None else now_ns
        with self._lock:
            result = {}
            for channel, c in self._channels.items():
                c.roll_window(now_ns, self._window_ns)
                result[channel] = ChannelSnapshot(
                    c.type_name, c.num_msgs, c.num_bytes, c.undecodable, c.latest_wall_ns, c.jitter_s
                )
            return result


class LCMSpy:
    """
    Per-connection lcm-spy view over a shared SpyAccumulator.

    Message and undecodable counts are relative to when the view was created;
    rates (Hz, bandwidth) cover the period since the previous get_stats() call.
    """

    VIRTUAL_CHANNEL = VIRTUAL_CHANNEL

    def __init__(self, accumulator: SpyAccumulator, now_ns: int | None = None):
        self._accumulator = accumulator
        now_ns = monotonic_ns() if now_ns is None else now_ns
        snap = accumulator.snapshot(now_ns)
        self._baseline = {ch: (s.num_msgs, s.undecodable) for ch, s in snap.items()}
        self._prev = {ch: (s.num_msgs, s.num_bytes) for ch, s in snap.items()}
        self._prev_ns = now_ns

    def get_stats(self, now_ns: int | None = None) -> channel_stats_list:
        """
        Compute stats for channels that received messages since this view was created.
        """
        now_ns = monotonic_ns() if now_ns is None else now_ns
        snap = self._accumulator.snapshot(now_ns)
        dt = (now_ns - self._prev_ns) / 1e9

        stats_list = channel_stats_list()
        for channel in sorted(snap):
            s = snap[channel]
            base_msgs, base_undecodable = self._baseline.get(channel, (0, 0))
            prev_msgs, prev_bytes = self._prev.get(channel, (0, 0))
            self._prev[channel] = (s.num_msgs, s.num_bytes)
            if s.num_msgs <= base_msgs:
                continue  # nothing on this channel since the view was created

            hz = (s.num_msgs - prev_msgs) / dt if dt > 0 else 0.0
            stats = channel_stats()
            stats.channel = channel
            stats.type = s.type_name
            stats.num_msgs = s.num_msgs - base_msgs
            stats.latest_msg_timestamp_ns = s.latest_wall_ns
            stats.hz = hz
            stats.inv_hz = 1.0 / hz if hz > 0 else 9999.0
            stats.jitter = s.jitter_s
            stats.bandwidth = (s.num_bytes - prev_bytes) / dt if dt > 0 else 0.0
            stats.undecodable = s.undecodable - base_undecodable
            stats_list.channels.append(stats)

        stats_list.num_channels = len(stats_list.channels)
        self._prev_ns = now_ns
        return stats_list

    def get_stats_bytes(self, now_ns: int | None = None) -> bytes:
        """Encoded `channel_stats_list` payload for the virtual channel."""
        return self.get_stats(now_ns).encode()
