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


def parse(argv, compression_default="deflate", lcm_packages_default="pkg"):
    parser = argparse.ArgumentParser()
    add_server_args(parser, compression_default, lcm_packages_default)
    parser.add_argument("lcm_packages_positional", nargs="?")
    return parse_server_options(parser.parse_args(argv), logger)


class TestServerOptions:
    def test_defaults(self):
        assert parse([]) == ServerOptions(lcm_packages=("pkg",))

    def test_lcm_packages_flag(self):
        assert parse(["--lcm-packages", "a, b"]).lcm_packages == ("a", "b")

    def test_positional_lcm_packages_take_precedence(self):
        assert parse(["--lcm-packages", "a", "c,d"]).lcm_packages == ("c", "d")

    def test_missing_lcm_packages_exits(self):
        with pytest.raises(SystemExit):
            parse([], lcm_packages_default=None)

    def test_app_specific_compression_default(self):
        assert parse([], compression_default="none").compression is None

    def test_flags(self):
        opts = parse([
            "--host", "0.0.0.0", "--port", "1", "--channel", "NAV.*", "--queue-size", "8",
            "--spy-interval", "0.5", "--compression", "none", "--processes", "3", "-vv",
        ])
        assert opts == ServerOptions(
            lcm_packages=("pkg",), host="0.0.0.0", port=1, channel="NAV.*", queue_size=8,
            spy_interval_ns=500_000_000, compression=None, uvloop=False, processes=3, verbosity=2,
        )

    def test_server_kwargs(self):
        kwargs = ServerOptions(processes=2, compression=None, queue_size=8).server_kwargs()
        assert kwargs == {
            "observer_queue_maxsize": 8,
            "spy_emit_interval_ns": 1_000_000_000,
            "compression": None,
            "reuse_port": True,
            "ping_interval": 20.0,
            "ping_timeout": 20.0,
        }

    def test_zero_disables_keepalive(self):
        opts = parse(["--ping-interval", "0", "--ping-timeout", "0"])
        assert opts.ping_interval is None and opts.ping_timeout is None

    def test_poll_interval_accepted_but_deprecated(self, caplog):
        with caplog.at_level(logging.WARNING, logger="test-cli"):
            parse(["--poll-interval", "0.1"])
        assert any("deprecated" in r.message for r in caplog.records)

    def test_invalid_processes(self):
        with pytest.raises(SystemExit):
            parse(["--processes", "0"])


class TestBuildRegistry:
    def test_registers_spy_types(self):
        registry = build_registry(["compas_lcmtypes"], logger)
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


async def spy_message_from_cli(module: str, *args: str) -> dict:
    """Start a proxy CLI as a subprocess and return its first spy message."""
    port = free_port()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", module, "--port", str(port), "--spy-interval", "0.1",
        "--channel", "LWS_TEST_NO_TRAFFIC", *args,
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
            return json.loads(await asyncio.wait_for(ws.recv(), 5))
    finally:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), 10)


class TestCliEndToEnd:
    async def test_json_proxy_serves_with_multiple_processes(self):
        """The CLI starts 2 workers sharing a port; each serves the spy channel."""
        msg = await spy_message_from_cli(
            "lcm_websocket_server.apps.json_proxy", "--processes", "2", "compas_lcmtypes"
        )
        assert msg["channel"] == "LWS_LCM_SPY"

    async def test_dial_proxy_starts_with_default_type_packages(self):
        """Defaults discover compas_lcmtypes + molars_lcmtypes and resolve senlcm.image_t."""
        pytest.importorskip("molars_lcmtypes")
        msg = await spy_message_from_cli("lcm_websocket_server.apps.dial_proxy")
        assert msg["channel"] == "LWS_LCM_SPY"


class TestExitCodes:
    @pytest.mark.parametrize(
        "args",
        [
            ["lcm_websocket_server.apps.dial_proxy", "--image-type", "nope.image_t"],
            ["lcm_websocket_server.apps.json_proxy", "no_such_package_xyz"],
            ["lcm_websocket_server.apps.json_proxy", "--processes", "2", "no_such_package_xyz"],
        ],
    )
    async def test_configuration_errors_exit_nonzero(self, args):
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", *args, env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        assert await asyncio.wait_for(proc.wait(), 60) != 0
