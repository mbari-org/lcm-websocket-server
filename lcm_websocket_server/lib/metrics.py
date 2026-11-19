"""
Server health metrics, exposed in the Prometheus text format.

Counters are updated from the event loop, the LCM thread and encode workers.
Plain int increments are effectively atomic under the GIL; float
accumulation from worker threads takes a lock.
"""

import asyncio
import threading
import time

from lcm_websocket_server.lib.lcm_utils.pubsub import LCMObserver

LOOP_PROBE_INTERVAL_S = 0.25


class ServerMetrics:
    """Counters and gauges describing server load and health."""

    def __init__(self):
        self.messages_received = 0
        self.frames_sent = 0
        self.bytes_sent = 0
        self.encode_requests = 0
        self.encodes = 0
        self.encode_errors = 0
        self.connections = 0
        self.rejected_connections = 0
        self._encode_seconds = 0.0
        self._encode_lock = threading.Lock()
        self._active: set[LCMObserver] = set()
        self._dropped_closed = 0
        self._loop_lag_max_s = 0.0

    # ------------------------------------------------------------ updates
    def record_message(self, channel: str, data: bytes, timestamp_ns: int) -> None:
        """LCMRepublisher tap: count every received message."""
        self.messages_received += 1

    def record_encode(self, seconds: float, error: bool) -> None:
        with self._encode_lock:
            self.encodes += 1
            self.encode_errors += error
            self._encode_seconds += seconds

    def record_sent(self, num_bytes: int) -> None:
        self.frames_sent += 1
        self.bytes_sent += num_bytes

    def client_connected(self, observer: LCMObserver) -> None:
        self.connections += 1
        self._active.add(observer)

    def client_disconnected(self, observer: LCMObserver) -> None:
        self._active.discard(observer)
        self._dropped_closed += observer.drop_count

    async def probe_loop_lag(self) -> None:
        """Track the worst event-loop scheduling delay; run as a task."""
        while True:
            start = time.perf_counter()
            await asyncio.sleep(LOOP_PROBE_INTERVAL_S)
            lag = time.perf_counter() - start - LOOP_PROBE_INTERVAL_S
            self._loop_lag_max_s = max(self._loop_lag_max_s, lag)

    # ------------------------------------------------------------ views
    @property
    def clients(self) -> int:
        return len(self._active)

    @property
    def dropped(self) -> int:
        return self._dropped_closed + sum(o.drop_count for o in self._active)

    @property
    def encode_seconds(self) -> float:
        with self._encode_lock:
            return self._encode_seconds

    def render(self) -> str:
        """Prometheus text exposition. Resets the loop-lag maximum."""
        lag, self._loop_lag_max_s = self._loop_lag_max_s, 0.0
        rows = [
            ("lws_clients", "gauge", "Connected WebSocket clients", self.clients),
            ("lws_connections_total", "counter", "Accepted WebSocket connections", self.connections),
            ("lws_rejected_connections_total", "counter", "Connections closed for invalid requests",
             self.rejected_connections),
            ("lws_messages_received_total", "counter", "LCM messages received", self.messages_received),
            ("lws_frames_sent_total", "counter", "WebSocket frames sent", self.frames_sent),
            ("lws_bytes_sent_total", "counter", "Payload bytes sent (before compression)", self.bytes_sent),
            ("lws_dropped_total", "counter", "Messages dropped from full client queues", self.dropped),
            ("lws_encode_requests_total", "counter", "Encoded payload requests (hits + misses)",
             self.encode_requests),
            ("lws_encodes_total", "counter", "Handler encodes performed (cache misses)", self.encodes),
            ("lws_encode_errors_total", "counter", "Handler encodes that raised", self.encode_errors),
            ("lws_encode_seconds_total", "counter", "Time spent in handler encodes", self.encode_seconds),
            ("lws_event_loop_lag_max_seconds", "gauge", "Worst event-loop delay since the last scrape", lag),
        ]
        lines = []
        for name, kind, help_text, value in rows:
            lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}", f"{name} {value}"]
        return "\n".join(lines) + "\n"
