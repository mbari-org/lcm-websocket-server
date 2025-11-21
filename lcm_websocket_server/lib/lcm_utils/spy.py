from threading import Thread
from typing import Optional
from time import sleep, time_ns

from lcmutils import LCMDaemon, LCMTypeRegistry

from lcm_websocket_server.lib.lcm_utils.channel_stats import channel_stats, channel_stats_list



class ChannelData:
    """
    Maintains metrics for a specific LCM channel.
    """

    def __init__(self):
        self._last_type: Optional[str] = None
        self._num_msgs: int = 0
        self._last_timestamp: Optional[int] = None
        self._min_interval: Optional[float] = None
        self._max_interval: Optional[float] = None
        self._bandwidth: float = 0.0
        self._undecodable: int = 0

        self._hz = 0.0
        self._hz_min_interval: float = float('inf')  # Start with infinity for min
        self._hz_max_interval: float = 0.0
        self._hz_bytes: int = 0
        self._hz_last_timestamp: int = 0
        self._hz_last_nreceived: int = 0

    def message_received(self, lcm_type: str, len_data: int, decoded: bool) -> None:
        """
        Handle a received message of a given type.

        Args:
            lcm_type (str): The string representation of the LCM type.
            len_data (int): The length of the data in bytes.
            decoded (bool): Whether the message was decoded successfully.
        """
        self._num_msgs += 1
        self._last_type = lcm_type
        timestamp = time_ns()
        self._last_timestamp = timestamp
        if not decoded:
            self._undecodable += 1

        # Calculate interval from last message (skip first message)
        if self._hz_last_timestamp > 0:
            interval = timestamp - self._hz_last_timestamp
            self._hz_min_interval = min(self._hz_min_interval, interval)
            self._hz_max_interval = max(self._hz_max_interval, interval)
        else:
            # First message - initialize the timestamp
            self._hz_last_timestamp = timestamp
        
        self._hz_bytes += len_data

    def update_hz_data(self, timestamp: int) -> None:
        """
        Update the Hz data based on the last received message.
        
        Args:
            timestamp (int): The current timestamp in nanoseconds.
        """
        diff_recv = self._num_msgs - self._hz_last_nreceived
        self._hz_last_nreceived = self._num_msgs
        dt = timestamp - self._hz_last_timestamp
        self._hz_last_timestamp = timestamp
        self._hz = diff_recv / (dt / 1e9) if dt > 0 else 0.0
        
        # Store interval stats (convert from nanoseconds to seconds)
        self._min_interval = self._hz_min_interval / 1e9 if self._hz_min_interval != float('inf') else None
        self._max_interval = self._hz_max_interval / 1e9
        
        # Reset for next period
        self._hz_min_interval = float('inf')
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
        stats.hz = self._hz
        stats.inv_hz = 1.0 / self._hz if self._hz > 0 else float('inf')
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
    Subscribes to all LCM channels and maintains per-channel lcm-spy stats.
    Publishes stats at 1 Hz on the virtual channel "LWS_LCM_SPY".
    """

    VIRTUAL_CHANNEL = "LWS_LCM_SPY"

    def __init__(self, registry: LCMTypeRegistry, republisher, channel_regex: str = ".*"):
        """
        Args:
            registry (LCMTypeRegistry): Registry for detecting LCM types
            republisher (LCMRepublisher): Republisher to inject virtual channel stats into
            channel_regex (str): Channel regex to monitor (default: ".*" for all channels)
        """
        self._registry = registry
        self._republisher = republisher
        self._channel_data: dict[str, ChannelData] = {}  # Maps channel names to ChannelData objects
        
        # Start the background thread for periodic stats updates
        self._hz_thread = Thread(target=self._hz_loop, daemon=True)
        self._hz_thread.start()
        
        # Subscribe to LCM channels and start the daemon
        self._daemon = LCMDaemon()
        self._daemon.subscribe(channel_regex)(self.handle)
        self._daemon.start()

    def handle(self, channel: str, data: bytes) -> None:
        """
        Handle an LCM event and maintain per-channel lcm-spy stats.
        """
        if channel not in self._channel_data:
            self._channel_data[channel] = ChannelData()
        
        lcm_type = self._registry.detect(data)
        lcm_type_name = lcm_type.__name__ if lcm_type is not None else data[:8].hex()
        self._channel_data[channel].message_received(lcm_type_name, len(data), lcm_type is not None)

    def get_stats(self) -> channel_stats_list:
        """
        Get the current stats for all channels.

        Returns:
            channel_stats_list: A list of channel_stats objects for each channel.
        """
        stats_list = channel_stats_list()
        for channel, data in self._channel_data.items():
            stats_list.channels.append(data.report(channel))
        stats_list.num_channels = len(stats_list.channels)
        return stats_list

    def _hz_loop(self) -> None:
        """
        Periodically update the Hz data for each channel and inject stats into the republisher.
        Publishes at 1 Hz on the virtual channel "LWS_LCM_SPY".
        """
        while True:
            sleep(1)  # Sleep for 1 second
            
            timestamp = time_ns()
            for data in self._channel_data.values():
                data.update_hz_data(timestamp)

            # Compute and inject the stats as a virtual channel
            stats_list = self.get_stats()
            self._republisher.inject(self.VIRTUAL_CHANNEL, stats_list.encode())
