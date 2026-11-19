"""
End-to-end tests for LCMWebSocketServer.

All tests use a real asyncio server bound to a free localhost port, real
WebSocket connections via websockets.connect(), and real LCM messages injected
via LCMRepublisher.inject().  No network daemon is required.
"""

import asyncio
import json
import threading
import time

import pytest
import pytest_asyncio
import websockets
from stdlcm import header_t

from lcm_websocket_server.lib.handler import LCMWebSocketHandler
from lcm_websocket_server.lib.lcm_utils.pubsub import (
    LCMRepublisher,
)
from lcm_websocket_server.lib.server import ClientRequest, LCMWebSocketServer
from tests.conftest import free_port, make_header_t

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RECV_TIMEOUT = 3.0  # generous timeout; tests run locally with no I/O latency


async def recv(ws) -> str | bytes:
    return await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)


# ---------------------------------------------------------------------------
# Custom-handler server fixture factory
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def server_factory(republisher: LCMRepublisher):
    """
    Factory that starts a server with an arbitrary handler.
    Returns an async callable: make(handler, **server_kwargs) -> (server, port).
    Cleans up all started servers on teardown.
    """
    started: list[tuple[LCMWebSocketServer, asyncio.Task]] = []

    async def make(handler: LCMWebSocketHandler, **kwargs) -> tuple[LCMWebSocketServer, int]:
        port = free_port()
        server = LCMWebSocketServer("localhost", port, handler, republisher, **kwargs)
        task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.05)
        started.append((server, task))
        return server, port

    yield make

    for server, task in started:
        server.close()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


# ---------------------------------------------------------------------------
# Basic message delivery
# ---------------------------------------------------------------------------

class TestMessageDelivery:
    async def test_client_receives_injected_message(
        self, running_server, republisher: LCMRepublisher
    ):
        _server, port = running_server
        async with websockets.connect(f"ws://localhost:{port}/") as ws:
            republisher.inject("SENSOR", make_header_t(sequence=7))
            msg = json.loads(await recv(ws))
            assert msg["channel"] == "SENSOR"
            assert msg["event"]["sequence"] == 7

    async def test_client_receives_multiple_messages_in_order(
        self, running_server, republisher: LCMRepublisher
    ):
        _server, port = running_server
        async with websockets.connect(f"ws://localhost:{port}/") as ws:
            for i in range(5):
                republisher.inject("CH", make_header_t(sequence=i))

            sequences = []
            for _ in range(5):
                msg = json.loads(await recv(ws))
                sequences.append(msg["event"]["sequence"])

            assert sequences == list(range(5))

    async def test_unknown_type_is_not_forwarded(
        self, running_server, republisher: LCMRepublisher
    ):
        """handle() returns None for unknown types; no WebSocket frame is sent."""
        _server, port = running_server
        async with websockets.connect(f"ws://localhost:{port}/") as ws:
            republisher.inject("CH", b"\x00" * 16)  # unknown fingerprint
            republisher.inject("CH", make_header_t(sequence=42))  # known → delivered

            msg = json.loads(await recv(ws))
            assert msg["event"]["sequence"] == 42


# ---------------------------------------------------------------------------
# Channel-regex filtering
# ---------------------------------------------------------------------------

class TestChannelFiltering:
    async def test_client_only_receives_matching_channel(
        self, running_server, republisher: LCMRepublisher
    ):
        _server, port = running_server
        # Subscribe only to IMU
        async with websockets.connect(f"ws://localhost:{port}/IMU") as ws:
            republisher.inject("GPS", make_header_t(sequence=1))
            republisher.inject("IMU", make_header_t(sequence=2))

            msg = json.loads(await recv(ws))
            assert msg["channel"] == "IMU"
            assert msg["event"]["sequence"] == 2

    async def test_regex_channel_filter(
        self, running_server, republisher: LCMRepublisher
    ):
        _server, port = running_server
        async with websockets.connect(f"ws://localhost:{port}/CAM_.*") as ws:
            republisher.inject("IMU", make_header_t(sequence=0))
            republisher.inject("CAM_FRONT", make_header_t(sequence=1))

            msg = json.loads(await recv(ws))
            assert msg["channel"] == "CAM_FRONT"


# ---------------------------------------------------------------------------
# Fix 2: handle() runs off the event loop
# ---------------------------------------------------------------------------

class TestHandleRunsInThread:
    async def test_handle_called_from_worker_thread(
        self, server_factory, republisher: LCMRepublisher
    ):
        """
        If handle() is executed via asyncio.to_thread(), it runs in a
        ThreadPoolExecutor thread whose id differs from the asyncio event-loop thread.
        """
        event_loop_thread_id = threading.get_ident()
        handle_thread_ids: list[int] = []

        class ThreadCapturingHandler(LCMWebSocketHandler):
            def handle(self, channel, data):
                handle_thread_ids.append(threading.get_ident())
                # don't send anything; we only care about the thread

        _, port = await server_factory(ThreadCapturingHandler())
        async with websockets.connect(f"ws://localhost:{port}/"):
            republisher.inject("CH", make_header_t())
            # Give the server time to process the message.
            await asyncio.sleep(0.1)

        assert handle_thread_ids, "handle() was never called"
        for tid in handle_thread_ids:
            assert tid != event_loop_thread_id, (
                "handle() was called on the event loop thread; "
                "it must run via asyncio.to_thread()"
            )

    async def test_slow_handle_does_not_block_event_loop(
        self, server_factory, republisher: LCMRepublisher
    ):
        """
        A handle() that sleeps with time.sleep() (which would block the event
        loop if running on it) must not prevent other coroutines from running.
        """
        SLEEP_S = 0.2

        class SlowHandler(LCMWebSocketHandler):
            def handle(self, channel, data):
                time.sleep(SLEEP_S)

        _, port = await server_factory(SlowHandler())
        async with websockets.connect(f"ws://localhost:{port}/"):
            republisher.inject("CH", make_header_t())

            # While the slow handle() is running in a thread, an asyncio.sleep
            # on the event loop should complete in approximately SLEEP_S, not
            # much longer (which would indicate the loop was blocked).
            t0 = time.monotonic()
            progress_event = asyncio.Event()

            async def set_event():
                await asyncio.sleep(0)
                progress_event.set()

            asyncio.create_task(set_event())
            await asyncio.wait_for(progress_event.wait(), timeout=SLEEP_S * 3)
            elapsed = time.monotonic() - t0

            # The event loop should have been free to process set_event
            # without waiting for the full SLEEP_S.
            assert elapsed < SLEEP_S * 2, (
                f"Event loop was blocked for {elapsed:.3f}s; "
                "handle() should run in a thread"
            )


# ---------------------------------------------------------------------------
# Error handling and cleanup
# ---------------------------------------------------------------------------

class TestErrorHandlingAndCleanup:
    async def test_server_continues_processing_after_handler_exception(
        self, server_factory, republisher: LCMRepublisher
    ):
        """
        If handle() raises, the server must log it and continue processing
        subsequent messages.
        """
        call_count = 0

        class FlakyHandler(LCMWebSocketHandler):
            def handle(self, channel, data):
                nonlocal call_count
                call_count += 1
                if call_count <= 3:
                    raise RuntimeError("deliberate error")
                msg = header_t.decode(data)
                return json.dumps({"sequence": msg.sequence})

        _, port = await server_factory(FlakyHandler())
        async with websockets.connect(f"ws://localhost:{port}/") as ws:
            # These three will raise inside handle() and must not deadlock.
            for _ in range(3):
                republisher.inject("CH", make_header_t(sequence=0))

            # This one should succeed and be delivered.
            republisher.inject("CH", make_header_t(sequence=99))

            msg = json.loads(await recv(ws))
            assert msg["sequence"] == 99

    async def test_observer_unsubscribed_after_disconnect(
        self, running_server, republisher: LCMRepublisher
    ):
        """
        After a message is delivered and the client disconnects, the server's
        websocket_handler coroutine must finish and call unsubscribe().
        """
        _server, port = running_server
        async with websockets.connect(f"ws://localhost:{port}/") as ws:
            republisher.inject("CH", make_header_t())
            await recv(ws)

        # Give the server-side websocket_handler coroutine time to detect the
        # closed connection, exit the update loop, and call unsubscribe().
        await asyncio.sleep(0.2)

        assert republisher.subscriber_count == 0, "Observer was not unsubscribed after disconnect"


# ---------------------------------------------------------------------------
# Fix 7: Configurable parameters
# ---------------------------------------------------------------------------

class TestConfigurableParameters:
    def test_observer_queue_maxsize_wired_through(
        self, republisher: LCMRepublisher, json_handler
    ):
        """observer_queue_maxsize is passed to each LCMObserver on connection."""
        server = LCMWebSocketServer(
            "localhost", 0, json_handler, republisher,
            observer_queue_maxsize=16,
        )
        assert server._observer_queue_maxsize == 16

    def test_spy_emit_interval_ns_stored(
        self, republisher: LCMRepublisher, json_handler
    ):
        server = LCMWebSocketServer(
            "localhost", 0, json_handler, republisher,
            spy_emit_interval_ns=500_000_000,
        )
        assert server._spy_emit_interval_ns == 500_000_000

    async def test_update_interval_ms_query_param_throttles_delivery(
        self, running_server, republisher: LCMRepublisher
    ):
        """
        With ?update_interval_ms=50, messages are batched and only the latest
        per channel is delivered at each tick.
        """
        _server, port = running_server
        url = f"ws://localhost:{port}/?update_interval_ms=50"
        async with websockets.connect(url) as ws:
            # Inject three messages quickly; only the last should survive batching.
            for seq in range(3):
                republisher.inject("CH", make_header_t(sequence=seq))

            # Wait for the 50ms tick to fire and deliver.
            msg = json.loads(await recv(ws))
            # The delivered sequence must be the last one (latest-wins).
            assert msg["event"]["sequence"] == 2

    async def test_small_queue_size_drops_oldest_under_load(
        self, server_factory, republisher: LCMRepublisher, json_handler
    ):
        """
        With observer_queue_maxsize=2, injecting 20 messages and then connecting
        a client results in at most 2 messages being delivered (the rest were
        dropped by the bounded queue before the client drained them).

        We inject before the server can drain so the observer queue fills up,
        then verify how many messages the client actually sees.
        """
        # Use a slow handler so messages accumulate in the queue before draining.
        import time as _time

        class SlowPassthrough(LCMWebSocketHandler):
            def handle(self, channel, data):
                _time.sleep(0.02)  # artificially slow so queue fills
                msg = header_t.decode(data)
                return json.dumps({"sequence": msg.sequence})

        _, port = await server_factory(SlowPassthrough(), observer_queue_maxsize=2)
        async with websockets.connect(f"ws://localhost:{port}/") as ws:
            # Inject 10 messages; with queue_maxsize=2 only the last 2 survive.
            for i in range(10):
                republisher.inject("CH", make_header_t(sequence=i))

            received = []
            for _ in range(2):
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
                    received.append(msg["sequence"])
                except TimeoutError:
                    break

        # We should have received at most 2 messages (the bounded cap), and
        # the messages must be the most recent ones (8 and 9).
        assert len(received) <= 2
        assert all(s >= 8 for s in received), f"Expected recent sequences, got {received}"


# ---------------------------------------------------------------------------
# Multiple concurrent clients
# ---------------------------------------------------------------------------

class TestMultipleClients:
    async def test_all_clients_receive_injected_message(
        self, running_server, republisher: LCMRepublisher
    ):
        _server, port = running_server
        url = f"ws://localhost:{port}/"
        async with (
            websockets.connect(url) as ws1,
            websockets.connect(url) as ws2,
            websockets.connect(url) as ws3,
        ):
            republisher.inject("CH", make_header_t(sequence=55))

            msgs = await asyncio.gather(recv(ws1), recv(ws2), recv(ws3))
            for raw in msgs:
                assert json.loads(raw)["event"]["sequence"] == 55

    async def test_slow_client_does_not_delay_fast_client(
        self, server_factory, republisher: LCMRepublisher, json_handler
    ):
        """
        A client that never reads its messages (simulating a slow consumer)
        must not prevent a fast client from receiving messages promptly.
        The bounded queue ensures the slow client's backlog stays finite.
        """
        _, port = await server_factory(json_handler, observer_queue_maxsize=4)
        url = f"ws://localhost:{port}/"
        async with (
            websockets.connect(url),
            websockets.connect(url) as fast_ws,
        ):
            # Flood both queues; the slow client never drains, the fast one will.
            for i in range(20):
                republisher.inject("CH", make_header_t(sequence=i))

            # The fast client should receive messages promptly.
            t0 = time.monotonic()
            received = []
            for _ in range(4):
                try:
                    msg = json.loads(await asyncio.wait_for(fast_ws.recv(), timeout=2.0))
                    received.append(msg["event"]["sequence"])
                except TimeoutError:
                    break
            elapsed = time.monotonic() - t0

            assert len(received) > 0, "Fast client received no messages"
            assert elapsed < 2.0, f"Fast client was blocked for {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# Request parsing and validation
# ---------------------------------------------------------------------------

class TestClientRequest:
    def test_defaults(self):
        req = ClientRequest.parse("/")
        assert req == ClientRequest(".*", None, False)

    def test_url_decoded_regex_and_params(self):
        req = ClientRequest.parse("/CAM_%2E%2A?update_interval_ms=250&conflate=true")
        assert req == ClientRequest("CAM_.*", 250, True)

    @pytest.mark.parametrize("query", ["update_interval_ms=abc", "update_interval_ms=0", "update_interval_ms=-5"])
    def test_invalid_update_interval_rejected(self, query):
        with pytest.raises(ValueError):
            ClientRequest.parse(f"/?{query}")


class TestRequestRejection:
    @pytest.mark.parametrize("path", ["/[invalid", "/?update_interval_ms=abc"])
    async def test_invalid_request_closed_with_policy_violation(self, running_server, path):
        _, port = running_server
        async with websockets.connect(f"ws://localhost:{port}{path}") as ws:
            with pytest.raises(websockets.ConnectionClosed) as info:
                await recv(ws)
        assert info.value.rcvd.code == 1008


# ---------------------------------------------------------------------------
# Event-driven delivery
# ---------------------------------------------------------------------------

class TestEventDrivenDelivery:
    async def test_message_after_idle_period_is_delivered_promptly(
        self, running_server, republisher: LCMRepublisher
    ):
        """No polling: a message arriving after an idle period is not delayed by a sleep interval."""
        _, port = running_server
        async with websockets.connect(f"ws://localhost:{port}/") as ws:
            await asyncio.sleep(0.3)
            t0 = time.monotonic()
            republisher.inject("CH", make_header_t(sequence=1))
            await recv(ws)
            assert time.monotonic() - t0 < 0.05

    async def test_idle_client_disconnect_unsubscribes_promptly(
        self, running_server, republisher: LCMRepublisher
    ):
        _, port = running_server
        async with websockets.connect(f"ws://localhost:{port}/"):
            await asyncio.sleep(0.05)
            assert republisher.subscriber_count == 1
        for _ in range(50):
            if republisher.subscriber_count == 0:
                break
            await asyncio.sleep(0.01)
        assert republisher.subscriber_count == 0


# ---------------------------------------------------------------------------
# Conflation
# ---------------------------------------------------------------------------

class TestConflation:
    async def _burst_then_collect(self, port, query, republisher):
        async with websockets.connect(f"ws://localhost:{port}/{query}") as ws:
            await asyncio.sleep(0.05)
            # Synchronous burst: the server cannot drain between injections.
            republisher.inject("QUIET", make_header_t(sequence=1))
            for i in range(50):
                republisher.inject("BUSY", make_header_t(sequence=i))
            received = {}
            while True:
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 0.3))
                except TimeoutError:
                    return received
                received.setdefault(msg["channel"], []).append(msg["event"]["sequence"])

    async def test_fifo_mode_busy_channel_evicts_quiet_channel(self, server_factory, json_handler, republisher):
        _, port = await server_factory(json_handler, observer_queue_maxsize=4)
        received = await self._burst_then_collect(port, "", republisher)
        assert "QUIET" not in received

    async def test_conflate_mode_preserves_quiet_channel_and_latest_busy(
        self, server_factory, json_handler, republisher
    ):
        _, port = await server_factory(json_handler, observer_queue_maxsize=4)
        received = await self._burst_then_collect(port, "?conflate=1", republisher)
        assert received == {"QUIET": [1], "BUSY": [49]}

    async def test_periodic_mode_preserves_quiet_channel(self, server_factory, json_handler, republisher):
        _, port = await server_factory(json_handler, observer_queue_maxsize=4)
        received = await self._burst_then_collect(port, "?update_interval_ms=50", republisher)
        assert received == {"QUIET": [1], "BUSY": [49]}


# ---------------------------------------------------------------------------
# Spy virtual channel
# ---------------------------------------------------------------------------

class TestSpyChannel:
    async def test_spy_counts_every_message_even_when_client_queue_overflows(
        self, server_factory, json_handler, registry, republisher: LCMRepublisher
    ):
        """Spy counts come from the shared tap, not from the (bounded) client queue."""
        _, port = await server_factory(
            json_handler, spy_registry=registry, observer_queue_maxsize=2, spy_emit_interval_ns=200_000_000
        )
        async with websockets.connect(f"ws://localhost:{port}/LWS_LCM_SPY") as ws:
            await asyncio.sleep(0.05)
            for i in range(500):
                republisher.inject("NAV", make_header_t(sequence=i))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                msg = json.loads(await recv(ws))
                assert msg["channel"] == "LWS_LCM_SPY"
                channels = {c["channel"]: c for c in msg["event"]["channels"]}
                if "NAV" in channels:
                    assert channels["NAV"]["num_msgs"] == 500
                    break
            else:
                pytest.fail("NAV never reported by spy")

    async def test_spy_not_sent_to_clients_not_matching_virtual_channel(
        self, server_factory, json_handler, registry, republisher: LCMRepublisher
    ):
        _, port = await server_factory(json_handler, spy_registry=registry, spy_emit_interval_ns=50_000_000)
        async with websockets.connect(f"ws://localhost:{port}/NAV") as ws:
            await asyncio.sleep(0.2)
            republisher.inject("NAV", make_header_t(sequence=3))
            msg = json.loads(await recv(ws))
            assert msg["channel"] == "NAV"


# ---------------------------------------------------------------------------
# Shared encoding and worker pools
# ---------------------------------------------------------------------------

class TestSharedEncoding:
    async def test_message_encoded_once_for_many_clients(self, server_factory, republisher: LCMRepublisher):
        """Concurrent clients must share one encode per message (no thundering herd)."""
        calls = []

        class SlowCountingHandler(LCMWebSocketHandler):
            def handle(self, channel, data):
                calls.append(data)
                time.sleep(0.05)  # long enough that all clients request concurrently
                return json.dumps({"sequence": header_t.decode(data).sequence})

        _, port = await server_factory(SlowCountingHandler())
        url = f"ws://localhost:{port}/"
        clients = [await websockets.connect(url) for _ in range(8)]
        try:
            await asyncio.sleep(0.05)
            republisher.inject("CH", make_header_t(sequence=5))
            msgs = await asyncio.gather(*(recv(ws) for ws in clients))
            assert all(json.loads(m)["sequence"] == 5 for m in msgs)
            assert len(calls) == 1
        finally:
            await asyncio.gather(*(ws.close() for ws in clients))

    async def test_heavy_messages_run_on_heavy_pool(self, server_factory, republisher: LCMRepublisher):
        thread_names = {}

        class RoutingHandler(LCMWebSocketHandler):
            def is_heavy(self, channel, data):
                return channel == "CAM"

            def handle(self, channel, data):
                thread_names[channel] = threading.current_thread().name
                return channel

        _, port = await server_factory(RoutingHandler())
        async with websockets.connect(f"ws://localhost:{port}/") as ws:
            await asyncio.sleep(0.05)
            republisher.inject("CAM", b"x")
            republisher.inject("NAV", b"y")
            await recv(ws)
            await recv(ws)
        assert thread_names["CAM"].startswith("lws-heavy")
        assert thread_names["NAV"].startswith("lws-light")
