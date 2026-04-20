import asyncio
import queue
from urllib.parse import unquote, urlparse, parse_qs
from typing import Optional

from lcmutils import LCMTypeRegistry
from websockets.asyncio.server import ServerConnection, serve
from websockets.protocol import OPEN

from lcm_websocket_server.lib.handler import LCMWebSocketHandler
from lcm_websocket_server.lib.lcm_utils.pubsub import (
    LCMObserver,
    LCMRepublisher,
    LCMTimedObserver,
    OBSERVER_QUEUE_MAXSIZE,
)
from lcm_websocket_server.lib.lcm_utils.spy import LCMSpy
from lcm_websocket_server.lib.log import LogMixin


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
        empty_wait_seconds: float = 0.1,
        spy_registry: Optional[LCMTypeRegistry] = None,
        observer_queue_maxsize: int = OBSERVER_QUEUE_MAXSIZE,
        spy_emit_interval_ns: int = 1_000_000_000,
    ):
        self._host = host
        self._port = port
        self._handler = handler
        self._lcm_republisher = lcm_republisher
        self._empty_wait_seconds = empty_wait_seconds
        self._spy_registry = spy_registry
        self._observer_queue_maxsize = observer_queue_maxsize
        self._spy_emit_interval_ns = spy_emit_interval_ns

        self._server = None

    async def websocket_handler(self, websocket: ServerConnection):
        """
        WebSocket handler coroutine.

        Args:
            websocket: The WebSocket connection
        """
        # Parse the path and query
        parsed_url = urlparse(websocket.request.path)
        query_params = parse_qs(parsed_url.query)

        # Parse the update interval in ms, if present in query params
        update_interval_ms = None
        if 'update_interval_ms' in query_params:
            try:
                update_interval_ms = int(query_params['update_interval_ms'][0])
                self.logger.info(f"Using update interval of {update_interval_ms} ms")
            except ValueError:
                self.logger.warning(f"Invalid update_interval_ms value: {query_params['update_interval_ms'][0]}")

        client_host, client_port = websocket.remote_address[:2]
        self.logger.info(f"Client {websocket.id} connected from {client_host}:{client_port} at {parsed_url.path}")

        channel_regex = unquote(parsed_url.path.lstrip('/'))
        if not channel_regex:  # empty path -> subscribe to all channels
            channel_regex = '.*'

        # Subscribe to the LCM republisher
        observer = LCMObserver(channel_regex=channel_regex, queue_maxsize=self._observer_queue_maxsize)
        self._lcm_republisher.subscribe(observer)

        spy = None
        spy_observer = None
        if self._spy_registry is not None and observer.match(LCMSpy.VIRTUAL_CHANNEL):
            spy = LCMSpy(self._spy_registry)
            spy_observer = LCMTimedObserver(channel_regex=".*", queue_maxsize=self._observer_queue_maxsize)
            self._lcm_republisher.subscribe(spy_observer)
            self.logger.info(f"Enabled per-connection spy stats for client {websocket.id}")

        self.logger.debug(f"Active subscribers after connect: {self._lcm_republisher.subscriber_count}")
        try:
            if update_interval_ms is not None:
                await self._periodic_update_loop(observer, websocket, update_interval_ms, spy, spy_observer)
            else:
                await self._update_loop(observer, websocket, spy, spy_observer)
        except Exception as e:
            self.logger.error(f"Unexpected error in client {websocket.id}: {e}")
        finally:
            self._lcm_republisher.unsubscribe(observer)
            if spy_observer is not None:
                self._lcm_republisher.unsubscribe(spy_observer)
            self.logger.info(
                f"Client {websocket.id} disconnected; "
                f"active subscribers: {self._lcm_republisher.subscriber_count}"
            )

    async def _send_virtual_spy_if_due(self, websocket: ServerConnection, spy: Optional[LCMSpy]) -> None:
        if spy is None:
            return

        payload = spy.maybe_get_stats_bytes(self._spy_emit_interval_ns)
        if payload is None:
            return

        response = await asyncio.to_thread(self._handler.handle, LCMSpy.VIRTUAL_CHANNEL, payload)
        if response is None:
            return

        await websocket.send(response)

    def _drain_spy_observer(self, spy_observer: Optional[LCMTimedObserver], spy: Optional[LCMSpy]) -> None:
        if spy_observer is None or spy is None:
            return

        while True:
            try:
                channel, data, timestamp_ns = spy_observer.get(block=False)
            except queue.Empty:
                break

            spy.handle(channel, data, timestamp_ns=timestamp_ns)
            spy_observer.task_done()

    async def _update_loop(
        self,
        observer: LCMObserver,
        websocket: ServerConnection,
        spy: Optional[LCMSpy],
        spy_observer: Optional[LCMTimedObserver],
    ):
        """
        Update loop: dequeue and forward LCM messages as fast as the client can consume them.

        handler.handle() is offloaded to a thread so CPU-intensive work (image
        encoding, JSON serialisation) does not block the event loop.

        task_done() is guaranteed to be called for every successful get() via a
        try/finally block, regardless of whether handle() or send() raises.
        """
        while True:
            self._drain_spy_observer(spy_observer, spy)

            try:
                channel, data = observer.get(block=False)
            except queue.Empty:
                if websocket.state is not OPEN:
                    break
                try:
                    await self._send_virtual_spy_if_due(websocket, spy)
                except Exception as e:
                    self.logger.debug(f"Error while sending spy response to client {websocket.id}: {e}")
                    if websocket.state is not OPEN:
                        break
                await asyncio.sleep(self._empty_wait_seconds)
                continue

            # task_done() is called in the finally so it is never skipped,
            # even when handle() raises or send() fails.
            try:
                try:
                    response = await asyncio.to_thread(self._handler.handle, channel, data)
                except Exception as e:
                    self.logger.error(f"Error during message handling: {e}")
                    continue  # finally still runs

                if response is not None:
                    try:
                        await websocket.send(response)
                    except Exception as e:
                        self.logger.debug(f"Error while sending response to client {websocket.id}: {e}")
                        continue  # finally still runs; skip spy on broken connection
            finally:
                observer.task_done()

            try:
                await self._send_virtual_spy_if_due(websocket, spy)
            except Exception as e:
                self.logger.debug(f"Error while sending spy response to client {websocket.id}: {e}")
                if websocket.state is not OPEN:
                    break

    async def _periodic_update_loop(
        self,
        observer: LCMObserver,
        websocket: ServerConnection,
        update_interval_ms: int,
        spy: Optional[LCMSpy],
        spy_observer: Optional[LCMTimedObserver],
    ):
        """
        Periodic update loop: emit only the latest message per channel at a fixed interval.

        handler.handle() is offloaded to a thread (same as _update_loop).
        """
        interval_sec = update_interval_ms / 1000.0
        channel_latest_data: dict[str, bytes | None] = {}
        while True:
            if websocket.state is not OPEN:
                break

            await asyncio.sleep(interval_sec)

            self._drain_spy_observer(spy_observer, spy)

            # Drain the observer queue, keeping only the latest message per channel.
            while True:
                try:
                    channel, data = observer.get(block=False)
                    channel_latest_data[channel] = data
                    observer.task_done()
                except queue.Empty:
                    break

            self._drain_spy_observer(spy_observer, spy)

            # Handle and send the latest message for each channel.
            for channel, data in channel_latest_data.items():
                if data is None:
                    continue

                try:
                    response = await asyncio.to_thread(self._handler.handle, channel, data)
                except Exception as e:
                    self.logger.error(f"Error during message handling: {e}")
                    continue
                finally:
                    channel_latest_data[channel] = None

                if response is not None:
                    try:
                        await websocket.send(response)
                    except Exception as e:
                        self.logger.debug(f"Error while sending response to client {websocket.id}: {e}")

            try:
                await self._send_virtual_spy_if_due(websocket, spy)
            except Exception as e:
                self.logger.debug(f"Error while sending spy response to client {websocket.id}: {e}")
                if websocket.state is not OPEN:
                    break

    async def serve(self):
        """
        Run the server.
        """
        async with serve(self.websocket_handler, self._host, self._port) as self._server:
            await self._server.wait_closed()

    def close(self):
        """
        Close the server.
        """
        if self._server is not None:
            self._server.close()
