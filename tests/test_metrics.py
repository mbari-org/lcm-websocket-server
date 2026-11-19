"""
Tests for ServerMetrics and the /healthz and /metrics HTTP endpoints.
"""

import asyncio
import urllib.error
import urllib.request

import pytest
import websockets

from lcm_websocket_server.lib.handler import LCMWebSocketHandler
from lcm_websocket_server.lib.lcm_utils.pubsub import LCMObserver
from lcm_websocket_server.lib.metrics import ServerMetrics
from tests.conftest import make_header_t


def parse_metrics(text: str) -> dict[str, float]:
    return {
        line.split()[0]: float(line.split()[1])
        for line in text.splitlines()
        if line and not line.startswith("#")
    }


def http_get(port: int, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=3) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ""


class TestServerMetrics:
    def test_render_is_prometheus_text(self):
        m = ServerMetrics()
        m.record_message("CH", b"x", 0)
        m.record_encode(0.5, error=True)
        m.record_sent(10)
        values = parse_metrics(m.render())
        assert values["lws_messages_received_total"] == 1
        assert values["lws_encodes_total"] == 1
        assert values["lws_encode_errors_total"] == 1
        assert values["lws_encode_seconds_total"] == 0.5
        assert values["lws_frames_sent_total"] == 1
        assert values["lws_bytes_sent_total"] == 10

    def test_drops_counted_for_active_and_closed_clients(self):
        m = ServerMetrics()
        a, b = LCMObserver(queue_maxsize=1), LCMObserver(queue_maxsize=1)
        m.client_connected(a)
        m.client_connected(b)
        for obs in (a, b):
            for _ in range(3):
                obs.handle(("CH", b"x"))  # 2 drops each
        m.client_disconnected(a)
        assert m.clients == 1
        assert m.dropped == 4


class TestHttpEndpoints:
    async def test_healthz(self, running_server):
        _, port = running_server
        assert await asyncio.to_thread(http_get, port, "/healthz") == (200, "ok\n")

    async def test_metrics_reflect_traffic(self, server_factory, republisher):
        class Echo(LCMWebSocketHandler):
            def handle(self, channel, data):
                return data

        _, port = await server_factory(Echo())
        async with websockets.connect(f"ws://localhost:{port}/") as ws1, websockets.connect(
            f"ws://localhost:{port}/"
        ) as ws2:
            await asyncio.sleep(0.05)
            republisher.inject("CH", make_header_t())
            await asyncio.wait_for(asyncio.gather(ws1.recv(), ws2.recv()), 3)
            status, body = await asyncio.to_thread(http_get, port, "/metrics")
        assert status == 200
        values = parse_metrics(body)
        assert values["lws_clients"] == 2
        assert values["lws_messages_received_total"] == 1
        assert values["lws_encode_requests_total"] == 2
        assert values["lws_encodes_total"] == 1  # shared across both clients
        assert values["lws_frames_sent_total"] == 2

    async def test_websocket_on_metrics_path_is_still_a_subscription(self, running_server, republisher):
        """Only plain HTTP requests are intercepted; a WebSocket may subscribe to any channel regex."""
        _, port = running_server
        async with websockets.connect(f"ws://localhost:{port}/metrics") as ws:
            await asyncio.sleep(0.05)
            republisher.inject("metrics", make_header_t(sequence=9))
            assert "metrics" in await asyncio.wait_for(ws.recv(), 3)

    @pytest.mark.parametrize("path", ["/", "/other"])
    async def test_other_plain_http_paths_rejected(self, running_server, path):
        _, port = running_server
        status, _ = await asyncio.to_thread(http_get, port, path)
        assert status >= 400
