import asyncio
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

Payload = str | bytes | bytearray | memoryview

DEFAULT_CACHE_DEPTH = 4


class EncodeCache:
    """
    Single-flight cache of encoded payloads, confined to one event loop.

    Keyed by channel and by the identity of the raw data object: the
    republisher hands the same bytes object to every subscriber, so identity
    identifies "this exact message". The first request for a message starts
    the encode; concurrent requests await the same future, so each message is
    encoded at most once regardless of the number of clients.

    Keeps the `depth` most recent messages per channel so clients that lag a
    few messages behind still hit the cache.
    """

    def __init__(self, depth: int = DEFAULT_CACHE_DEPTH):
        if depth < 1:
            raise ValueError("depth must be >= 1")
        self._depth = depth
        # channel -> id(data) -> (data, future)
        self._entries: dict[str, OrderedDict[int, tuple[bytes, asyncio.Future]]] = {}

    async def get_or_encode(self, channel: str, data: bytes, encode: Callable[[], Awaitable[Any]]) -> Any:
        """
        Return the encoded result for (channel, data), starting `encode()` only
        if no request for this message is cached or in flight.

        Cancelling one caller does not cancel the shared encode.
        """
        per_channel = self._entries.get(channel)
        if per_channel is None:
            per_channel = self._entries[channel] = OrderedDict()

        key = id(data)
        entry = per_channel.get(key)
        if entry is not None and entry[0] is data:
            future = entry[1]
        else:
            future = asyncio.ensure_future(encode())
            per_channel[key] = (data, future)
            while len(per_channel) > self._depth:
                per_channel.popitem(last=False)

        if future.done():
            return future.result()
        return await asyncio.shield(future)


class LCMWebSocketHandler(ABC):
    """
    LCM WebSocket handler interface.

    Implementations must be synchronous and thread-safe: the server runs
    handle() in worker threads, possibly concurrently for different messages.
    The server caches results, so handle() is called at most once per message.
    """

    @abstractmethod
    def handle(self, channel: str, data: bytes) -> Payload | None:
        """
        Handle an LCM message.

        Args:
            channel: LCM channel
            data: LCM message data

        Returns:
            Response to be sent to the WebSocket client, or None to not send a response.
        """
        raise NotImplementedError

    def is_heavy(self, channel: str, data: bytes) -> bool:
        """
        Whether handling this message is CPU-heavy native work (e.g. image
        encoding) that should run on the separate heavy worker pool.
        """
        return False
