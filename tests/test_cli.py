"""
Tests for shared CLI plumbing and server transport options.
"""

import argparse
import asyncio
import json
import logging
import os
import sys

import pytest
import websockets

from lcm_websocket_server.lib.cli import (
    ServerOptions,
    add_server_args,
    build_registry,
    parse_server_options,
)
from lcm_websocket_server.lib.lcm_utils.channel_stats_list import channel_stats_list
from lcm_websocket_server.lib.server import LCMWebSocketServer
from tests.conftest import free_port, make_header_t

logger = logging.getLogger("test-cli")


def parse(argv, compression_default="deflate"):
    parser = argparse.ArgumentParser()
    add_server_args(parser, compression_default)
    return parse_server_options(parser.parse_args(argv), logger)


class TestServerOptions:
    def test_defaults(self):
        assert parse([]) == ServerOptions()

    def test_app_specific_compression_default(self):
        assert parse([], compression_default="none").compression is None

    def test_flags(self):
        opts = parse([
            "--host", "0.0.0.0", "--port", "1", "--channel", "NAV.*", "--queue-size", "8",
            "--spy-interval", "0.5", "--compression", "none", "--processes", "3", "-vv",
        ])
        assert opts == ServerOptions("0.0.0.0", 1, "NAV.*", 8, 500_000_000, None, False, 3, 2)

    def test_server_kwargs(self):
        kwargs = ServerOptions(processes=2, compression=None, queue_size=8).server_kwargs()
        assert kwargs == {
            "observer_queue_maxsize": 8,
            "spy_emit_interval_ns": 1_000_000_000,
            "compression": None,
            "reuse_port": True,
        }

    def test_poll_interval_accepted_but_deprecated(self, caplog):
        with caplog.at_level(logging.WARNING, logger="test-cli"):
            parse(["--poll-interval", "0.1"])
        assert any("deprecated" in r.message for r in caplog.records)

    def test_invalid_processes(self):
        with pytest.raises(SystemExit):
            parse(["--processes", "0"])


class TestBuildRegistry:
    def test_registers_spy_types(self):
        registry = build_registry(["stdlcm"], logger)
        assert channel_stats_list in registry.types

    def test_returns_none_when_nothing_discovered(self):
        assert build_registry(["no_such_package_xyz"], logger) is None


class TestCompression:
    @pytest.mark.parametrize("compression, negotiated", [("deflate", True), (None, False)])
    async def test_permessage_deflate_negotiation(self, republisher, json_handler, compression, negotiated):
        port = free_port()
        server = LCMWebSocketServer("localhost", port, json_handler, republisher, compression=compression)
        task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.05)
        try:
            async with websockets.connect(f"ws://localhost:{port}/") as ws:
                names = [ext.name for ext in ws.protocol.extensions]
                assert ("permessage-deflate" in names) is negotiated
                await asyncio.sleep(0.05)
                republisher.inject("CH", make_header_t(sequence=4))
                assert json.loads(await asyncio.wait_for(ws.recv(), 3))["event"]["sequence"] == 4
        finally:
            server.close()
            await asyncio.wait_for(task, 3)

    def test_rejects_unknown_compression(self, republisher, json_handler):
        with pytest.raises(ValueError):
            LCMWebSocketServer("localhost", 0, json_handler, republisher, compression="brotli")


class TestMultiProcess:
    async def test_json_proxy_serves_with_multiple_processes(self):
        """End to end: the CLI starts 2 workers sharing a port; each serves the spy channel."""
        port = free_port()
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "lcm_websocket_server.apps.json_proxy", "--port", str(port),
            "--processes", "2", "--spy-interval", "0.1", "--channel", "LWS_TEST_NO_TRAFFIC", "stdlcm",
            env=env,
        )
        try:
            for _ in range(100):
                try:
                    ws = await websockets.connect(f"ws://localhost:{port}/LWS_LCM_SPY")
                    break
                except OSError:
                    await asyncio.sleep(0.1)
            else:
                pytest.fail("server did not start")
            async with ws:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
                assert msg["channel"] == "LWS_LCM_SPY"
        finally:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), 10)
