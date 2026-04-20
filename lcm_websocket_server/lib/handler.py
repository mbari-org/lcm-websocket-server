import threading
from abc import ABC, abstractmethod
from typing import Optional, Union


class EncodeCache:
    """
    Per-channel single-entry cache keyed by data object identity.

    Shared across all connections on the same handler instance so that when N
    clients subscribe to the same channel, the encode work is done once and the
    result is reused for the remaining N-1 clients.

    Thread-safe: handle() may be called concurrently from multiple asyncio
    to_thread workers.
    """

    def __init__(self):
        self._lock = threading.Lock()
        # channel -> (original data object, encoded result)
        self._cache: dict[str, tuple[bytes, object]] = {}

    def get(self, channel: str, data: bytes) -> Optional[object]:
        """
        Return the cached result for (channel, data) if available, else None.

        Identity comparison (`is`) is used intentionally: the same raw bytes
        object is placed into every subscriber queue by LCMRepublisher._handle,
        so object identity reliably identifies "this exact message".
        """
        with self._lock:
            entry = self._cache.get(channel)
            if entry is not None and entry[0] is data:
                return entry[1]
            return None

    def put(self, channel: str, data: bytes, result: object) -> None:
        """Store an encoded result for (channel, data)."""
        with self._lock:
            self._cache[channel] = (data, result)


class LCMWebSocketHandler(ABC):
    """
    LCM WebSocket handler interface.

    Implementations must be synchronous. The server calls handle() via
    asyncio.to_thread() to keep CPU-intensive work off the event loop.
    """

    @abstractmethod
    def handle(self, channel: str, data: bytes) -> Optional[Union[str, bytes, bytearray, memoryview]]:
        """
        Handle an LCM message.

        Args:
            channel: LCM channel
            data: LCM message data

        Returns:
            Response to be sent to the WebSocket client, or None to not send a response.
        """
        raise NotImplementedError
