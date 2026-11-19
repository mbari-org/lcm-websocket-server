import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

from lcmutils import LCMTypeRegistry
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.extensions.permessage_deflate import ServerPerMessageDeflateFactory
from websockets.frames import CloseCode

from lcm_websocket_server.lib.handler import (
    DEFAULT_CACHE_DEPTH,
    EncodeCache,
    LCMWebSocketHandler,
    TextPayload,
)
from lcm_websocket_server.lib.lcm_utils.pubsub import (
    OBSERVER_QUEUE_MAXSIZE,
    InvalidChannelRegex,
    LCMObserver,
    LCMRepublisher,
)
from lcm_websocket_server.lib.lcm_utils.spy import LCMSpy, SpyAccumulator
from lcm_websocket_server.lib.log import LogMixin

_TRUE_VALUES = {"1", "true", "yes", "on"}
# WebSocket close reasons are limited to 123 bytes.
_MAX_CLOSE_REASON = 120

# Light work (JSON) holds the GIL, so a few threads suffice to keep the loop free.
DEFAULT_LIGHT_WORKERS = 4
# Heavy work (OpenCV) releases the GIL and parallelises across cores.
DEFAULT_HEAVY_WORKERS = min(8, os.cpu_count() or 1)


@dataclass(frozen=True)
class ClientRequest:
    """Subscription parameters parsed from the WebSocket request path."""

    channel_regex: str
    update_interval_ms: int | None = None
    conflate: bool = False

    @classmethod
    def parse(cls, path: str) -> "ClientRequest":
        parsed = urlparse(path)
        query = parse_qs(parsed.query)

        channel_regex = unquote(parsed.path.lstrip("/")) or ".*"

        update_interval_ms = None
        if "update_interval_ms" in query:
            try:
                update_interval_ms = int(query["update_interval_ms"][0])
            except ValueError:
                raise ValueError(f"Invalid update_interval_ms value: {query['update_interval_ms'][0]!r}")
            if update_interval_ms <= 0:
                raise ValueError("update_interval_ms must be positive")

        conflate = query.get("conflate", ["0"])[0].lower() in _TRUE_VALUES
        return cls(channel_regex, update_interval_ms, conflate)


class LCMWebSocketServer(LogMixin):
    """
    LCM-WebSocket server. Subscribes to LCM and publishes data to WebSocket clients.

    Delegates LCM message handling to an LCMWebSocketHandler.
    """

    def __init__(
        self,
        host: str,
        port: int,
        handler: LCMWebSocketHandler,
        lcm_republisher: LCMRepublisher,
        spy_registry: LCMTypeRegistry | None = None,
        observer_queue_maxsize: int = OBSERVER_QUEUE_MAXSIZE,
        spy_emit_interval_ns: int = 1_000_000_000,
        light_workers: int = DEFAULT_LIGHT_WORKERS,
        heavy_workers: int = DEFAULT_HEAVY_WORKERS,
        encode_cache_depth: int = DEFAULT_CACHE_DEPTH,
        compression: str | None = "deflate",
        reuse_port: bool = False,
    ):
        self._host = host
        self._port = port
        self._handler = handler
        self._lcm_republisher = lcm_republisher
        self._spy_registry = spy_registry
        self._observer_queue_maxsize = observer_queue_maxsize
        self._spy_emit_interval_ns = spy_emit_interval_ns
        self._spy_accumulator = (
            SpyAccumulator(spy_registry, window_ns=spy_emit_interval_ns) if spy_registry is not None else None
        )

        if compression not in ("deflate", None):
            raise ValueError(f"Unsupported compression: {compression!r}")
        self._compression = compression
        self._reuse_port = reuse_port
        self._cache = EncodeCache(encode_cache_depth)
        self._light_executor = ThreadPoolExecutor(light_workers, thread_name_prefix="lws-light")
        self._heavy_executor = ThreadPoolExecutor(heavy_workers, thread_name_prefix="lws-heavy")

        self._server = None

    async def websocket_handler(self, websocket: ServerConnection):
        """
        WebSocket handler coroutine.

        Args:
            websocket: The WebSocket connection
        """
        client_host, client_port = websocket.remote_address[:2]
        try:
            request = ClientRequest.parse(websocket.request.path)
            # Periodic mode only ever sends the latest message per channel, so it always conflates.
            observer = LCMObserver(
                channel_regex=request.channel_regex,
                queue_maxsize=self._observer_queue_maxsize,
                conflate=request.conflate or request.update_interval_ms is not None,
            )
        except (ValueError, InvalidChannelRegex) as e:
            self.logger.warning(f"Rejecting client {websocket.id} from {client_host}:{client_port}: {e}")
            await websocket.close(CloseCode.POLICY_VIOLATION, str(e)[:_MAX_CLOSE_REASON])
            return

        self.logger.info(
            f"Client {websocket.id} connected from {client_host}:{client_port} at {websocket.request.path}"
        )

        self._lcm_republisher.subscribe(observer)
        tasks = []
        if request.update_interval_ms is not None:
            tasks.append(asyncio.create_task(self._periodic_loop(observer, websocket, request.update_interval_ms)))
        else:
            tasks.append(asyncio.create_task(self._stream_loop(observer, websocket)))

        if self._spy_accumulator is not None and observer.match(LCMSpy.VIRTUAL_CHANNEL):
            tasks.append(asyncio.create_task(self._spy_loop(websocket, LCMSpy(self._spy_accumulator))))
            self.logger.info(f"Enabled per-connection spy stats for client {websocket.id}")

        closed = asyncio.create_task(websocket.wait_closed())
        self.logger.debug(f"Active subscribers after connect: {self._lcm_republisher.subscriber_count}")
        try:
            done, _ = await asyncio.wait([*tasks, closed], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is closed or task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None and not isinstance(exc, ConnectionClosed):
                    self.logger.error(f"Unexpected error in client {websocket.id}: {exc!r}")
        finally:
            for task in (*tasks, closed):
                task.cancel()
            await asyncio.gather(*tasks, closed, return_exceptions=True)
            self._lcm_republisher.unsubscribe(observer)
            self.logger.info(
                f"Client {websocket.id} disconnected; "
                f"active subscribers: {self._lcm_republisher.subscriber_count}"
            )

    async def _encode(self, channel: str, data: bytes):
        """
        Encode a message for sending, at most once per message across all clients.
        Cache hits are served on the loop without a thread hop.
        """
        return await self._cache.get_or_encode(channel, data, lambda: self._run_handler(channel, data))

    async def _run_handler(self, channel: str, data: bytes):
        """
        Run handler.handle() on the appropriate worker pool so CPU-intensive work
        does not block the event loop. Errors are logged and yield None.
        """
        try:
            executor = self._heavy_executor if self._handler.is_heavy(channel, data) else self._light_executor
            return await asyncio.get_running_loop().run_in_executor(executor, self._handler.handle, channel, data)
        except Exception as e:  # noqa: BLE001 - a bad message must not end the stream
            self.logger.error(f"Error during message handling on channel {channel!r}: {e!r}")
            return None

    async def _send(self, websocket: ServerConnection, payload) -> None:
        if type(payload) is TextPayload:
            await websocket.send(payload, text=True)
        else:
            await websocket.send(payload)

    async def _stream_loop(self, observer: LCMObserver, websocket: ServerConnection):
        """
        Forward messages as fast as the client can consume them. Waits on the
        observer (no polling), so delivery latency is bounded by encode + send.
        """
        while True:
            channel, data = await observer.get()
            payload = await self._encode(channel, data)
            if payload is not None:
                await self._send(websocket, payload)

    async def _periodic_loop(self, observer: LCMObserver, websocket: ServerConnection, update_interval_ms: int):
        """
        Emit only the latest message per channel at a fixed interval.

        Deadlines are absolute so the period does not drift by the processing time.
        """
        loop = asyncio.get_running_loop()
        interval = update_interval_ms / 1000.0
        deadline = loop.time()
        while True:
            deadline += interval
            delay = deadline - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                deadline = loop.time()  # fell behind; do not burst to catch up

            for channel, data in observer.drain():
                payload = await self._encode(channel, data)
                if payload is not None:
                    await self._send(websocket, payload)

    async def _spy_loop(self, websocket: ServerConnection, spy: LCMSpy):
        """Emit the virtual spy channel at a fixed cadence."""
        loop = asyncio.get_running_loop()
        interval = self._spy_emit_interval_ns / 1e9
        deadline = loop.time()
        while True:
            deadline += interval
            await asyncio.sleep(max(0.0, deadline - loop.time()))
            response = await self._encode(LCMSpy.VIRTUAL_CHANNEL, spy.get_stats_bytes())
            if response is not None:
                await self._send(websocket, response)

    async def serve(self):
        """
        Run the server.
        """
        if self._spy_accumulator is not None:
            self._lcm_republisher.add_tap(self._spy_accumulator.record)
        try:
            async with serve(
                self.websocket_handler, self._host, self._port, **self._serve_kwargs()
            ) as self._server:
                await self._server.wait_closed()
        finally:
            if self._spy_accumulator is not None:
                self._lcm_republisher.remove_tap(self._spy_accumulator.record)
            self._light_executor.shutdown(wait=False, cancel_futures=True)
            self._heavy_executor.shutdown(wait=False, cancel_futures=True)

    def _serve_kwargs(self) -> dict:
        kwargs = {"compression": None}
        if self._compression == "deflate":
            # websockets' default deflate settings with compression level 1:
            # roughly half the CPU of the default level 6 for a similar ratio on JSON.
            kwargs["extensions"] = [
                ServerPerMessageDeflateFactory(
                    server_max_window_bits=12,
                    client_max_window_bits=12,
                    compress_settings={"memLevel": 5, "level": 1},
                )
            ]
        if self._reuse_port:
            kwargs["reuse_port"] = True
        return kwargs

    def close(self):
        """
        Close the server.
        """
        if self._server is not None:
            self._server.close()
