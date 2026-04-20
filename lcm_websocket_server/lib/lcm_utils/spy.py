from typing import Optional
from time import monotonic_ns, time_ns

from lcmutils import LCMTypeRegistry

from lcm_websocket_server.lib.lcm_utils.channel_stats import channel_stats
from lcm_websocket_server.lib.lcm_utils.channel_stats_list import channel_stats_list



class ChannelData:
    """
    Maintains metrics for a specific LCM channel.
    """

    def __init__(self):
        self._last_type: Optional[str] = None
        self._num_msgs: int = 0
        self._min_interval: Optional[float] = None
        self._max_interval: Optional[float] = None
        self._bandwidth: float = 0.0
        self._undecodable: int = 0

        self._hz = 0.0
        self._hz_min_interval: float = float("inf")
        self._hz_max_interval: float = 0.0
        self._hz_bytes: int = 0
        self._hz_last_update_timestamp: int = monotonic_ns()
        self._last_msg_timestamp: Optional[int] = None
        self._latest_msg_timestamp_ns: int = 0
        self._hz_last_nreceived: int = 0

    def message_received(self, lcm_type: str, len_data: int, decoded: bool, timestamp_ns: Optional[int] = None) -> None:
        """
        Handle a received message of a given type.

        Args:
            lcm_type (str): The string representation of the LCM type.
            len_data (int): The length of the data in bytes.
            decoded (bool): Whether the message was decoded successfully.
            timestamp_ns (Optional[int]): Arrival timestamp in nanoseconds.
        """
        self._num_msgs += 1
        self._last_type = lcm_type
        timestamp = timestamp_ns if timestamp_ns is not None else monotonic_ns()
        self._latest_msg_timestamp_ns = time_ns()
        if not decoded:
            self._undecodable += 1

        # Track inter-message timing for jitter using consecutive message deltas.
        if self._last_msg_timestamp is not None:
            interval = timestamp - self._last_msg_timestamp
            self._hz_min_interval = min(self._hz_min_interval, interval)
            self._hz_max_interval = max(self._hz_max_interval, interval)
        self._last_msg_timestamp = timestamp
        
        self._hz_bytes += len_data

    def update_hz_data(self, timestamp: int) -> None:
        """
        Update the Hz data based on the last received message.
        
        Args:
            timestamp (int): The current timestamp in nanoseconds.
        """
        diff_recv = self._num_msgs - self._hz_last_nreceived
        self._hz_last_nreceived = self._num_msgs
        dt = timestamp - self._hz_last_update_timestamp
        self._hz_last_update_timestamp = timestamp
        self._hz = diff_recv / (dt / 1e9) if dt > 0 else 0.0
        
        # Store interval stats (convert from nanoseconds to seconds)
        if self._hz_min_interval != float("inf") and self._hz_max_interval > 0.0:
            self._min_interval = self._hz_min_interval / 1e9
            self._max_interval = self._hz_max_interval / 1e9
        else:
            self._min_interval = None
            self._max_interval = None
        
        # Reset for next period
        self._hz_min_interval = float("inf")
        self._hz_max_interval = 0.0
        
        self._bandwidth = self._hz_bytes / (dt / 1e9) if dt > 0 else 0.0
        self._hz_bytes = 0

    def report(self, channel: str) -> channel_stats:
        """
        Generate a report of the channel's stats.
        
        Returns:
            channel_stats: A channel_stats object containing the stats for this channel.
        """
        stats = channel_stats()
        stats.channel = channel
        stats.type = self._last_type or ""
        stats.num_msgs = self._num_msgs
        stats.latest_msg_timestamp_ns = self._latest_msg_timestamp_ns
        stats.hz = self._hz
        stats.inv_hz = 1.0 / self._hz if self._hz > 0 else 9999.0
        # Jitter is already in seconds (converted in update_hz_data)
        if self._min_interval is not None and self._max_interval is not None:
            stats.jitter = self._max_interval - self._min_interval
        else:
            stats.jitter = 0.0
        stats.bandwidth = self._bandwidth
        stats.undecodable = self._undecodable
        return stats


class LCMSpy:
    """
    Per-connection lcm-spy stats accumulator.

    Call `handle()` for each observed LCM event and `maybe_get_stats_bytes()`
    periodically to emit the virtual `LWS_LCM_SPY` payload at a fixed cadence.
    """

    VIRTUAL_CHANNEL = "LWS_LCM_SPY"

    def __init__(self, registry: LCMTypeRegistry):
        """
        Args:
            registry (LCMTypeRegistry): Registry for detecting LCM types
        """
        self._registry = registry
        self._channel_data: dict[str, ChannelData] = {}
        self._last_emit_ts_ns = monotonic_ns()

    def handle(self, channel: str, data: bytes, timestamp_ns: Optional[int] = None) -> None:
        """
        Handle an LCM event and maintain per-channel lcm-spy stats.
        """
        if channel == self.VIRTUAL_CHANNEL:
            return

        if channel not in self._channel_data:
            self._channel_data[channel] = ChannelData()
        
        lcm_type = self._registry.detect(data)
        lcm_type_name = lcm_type.__name__ if lcm_type is not None else data[:8].hex()
        self._channel_data[channel].message_received(
            lcm_type_name,
            len(data),
            lcm_type is not None,
            timestamp_ns=timestamp_ns,
        )

    def get_stats(self) -> channel_stats_list:
        """
        Get the current stats for all channels.

        Returns:
            channel_stats_list: A list of channel_stats objects for each channel.
        """
        stats_list = channel_stats_list()
        for channel in sorted(self._channel_data):
            data = self._channel_data[channel]
            stats_list.channels.append(data.report(channel))
        stats_list.num_channels = len(stats_list.channels)
        return stats_list

    def maybe_get_stats_bytes(self, interval_ns: int = 1_000_000_000) -> Optional[bytes]:
        """
        Update internal rates and emit encoded stats if the interval has elapsed.

        Args:
            interval_ns (int): Emission interval in nanoseconds. Default is 1 second.

        Returns:
            Optional[bytes]: Encoded `channel_stats_list` payload when due, else None.
        """
        timestamp = monotonic_ns()
        if timestamp - self._last_emit_ts_ns < interval_ns:
            return None

        self._last_emit_ts_ns = timestamp
        for data in self._channel_data.values():
            data.update_hz_data(timestamp)

        stats_list = self.get_stats()
        return stats_list.encode()
