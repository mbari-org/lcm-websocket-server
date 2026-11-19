"""
Shared command-line and process plumbing for the proxy apps.
"""

import argparse
import asyncio
import functools
import logging
import multiprocessing
import signal
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass
from typing import Any

from lcmutils import LCMTypeRegistry

from lcm_websocket_server.lib.lcm_utils.channel_stats import channel_stats
from lcm_websocket_server.lib.lcm_utils.channel_stats_list import channel_stats_list
from lcm_websocket_server.lib.lcm_utils.pubsub import (
    OBSERVER_QUEUE_MAXSIZE,
    LCMRepublisher,
)
from lcm_websocket_server.lib.log import set_stream_handler_verbosity
from lcm_websocket_server.lib.server import LCMWebSocketServer

COMPRESSION_CHOICES = ("deflate", "none")


@dataclass(frozen=True)
class ServerOptions:
    """Options common to all proxy apps."""

    host: str = "localhost"
    port: int = 8765
    channel: str = ".*"
    queue_size: int = OBSERVER_QUEUE_MAXSIZE
    spy_interval_ns: int = 1_000_000_000
    compression: str | None = "deflate"
    uvloop: bool = False
    processes: int = 1
    verbosity: int = 0
    ping_interval: float | None = 20.0
    ping_timeout: float | None = 20.0

    def server_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for LCMWebSocketServer derived from these options."""
        return {
            "observer_queue_maxsize": self.queue_size,
            "spy_emit_interval_ns": self.spy_interval_ns,
            "compression": self.compression,
            "reuse_port": self.processes > 1,
            "ping_interval": self.ping_interval,
            "ping_timeout": self.ping_timeout,
        }


def add_server_args(parser: argparse.ArgumentParser, compression_default: str) -> None:
    """Add the common server arguments to a parser."""
    parser.add_argument("--host", type=str, default="localhost", help="The host to listen on. Default: %(default)s")
    parser.add_argument("--port", type=int, default=8765, help="The port to listen on. Default: %(default)s")
    parser.add_argument("--channel", type=str, default=".*", help="The LCM channel to subscribe to. Use '.*' to subscribe to all channels.")
    parser.add_argument("--queue-size", type=int, default=OBSERVER_QUEUE_MAXSIZE, help="Max messages buffered per connection before oldest are dropped. Default: %(default)s")
    parser.add_argument("--poll-interval", type=float, default=None, help=argparse.SUPPRESS)  # deprecated no-op
    parser.add_argument("--spy-interval", type=float, default=1.0, help="LCM spy stats emission interval (seconds). Default: %(default)s")
    parser.add_argument("--compression", choices=COMPRESSION_CHOICES, default=compression_default, help="WebSocket permessage-deflate compression. Saves bandwidth for JSON at a CPU cost per client; useless for JPEG. Default: %(default)s")
    parser.add_argument("--uvloop", action="store_true", help="Use the uvloop event loop (requires the 'uvloop' extra).")
    parser.add_argument("--processes", type=int, default=1, help="Number of server processes sharing the port (SO_REUSEPORT). Each has its own LCM subscription, encode cache and spy stats. Default: %(default)s")
    parser.add_argument("--ping-interval", type=float, default=20.0, help="Seconds between keepalive pings; 0 disables. Default: %(default)s")
    parser.add_argument("--ping-timeout", type=float, default=20.0, help="Seconds to wait for a pong before closing; 0 disables. Raise for slow links carrying large frames. Default: %(default)s")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity level. 0=ERROR, 1=WARNING, 2=INFO, 3=DEBUG. Default: %(default)s")


def parse_server_options(args: argparse.Namespace, logger: logging.Logger) -> ServerOptions:
    """Build ServerOptions from parsed arguments and apply logging verbosity."""
    set_stream_handler_verbosity(args.verbose)
    if args.poll_interval is not None:
        logger.warning("--poll-interval is deprecated and ignored; delivery is now event-driven")
    if args.processes < 1:
        raise SystemExit("--processes must be >= 1")
    return ServerOptions(
        host=args.host,
        port=args.port,
        channel=args.channel,
        queue_size=args.queue_size,
        spy_interval_ns=int(args.spy_interval * 1_000_000_000),
        compression=None if args.compression == "none" else args.compression,
        uvloop=args.uvloop,
        processes=args.processes,
        verbosity=args.verbose,
        ping_interval=args.ping_interval or None,
        ping_timeout=args.ping_timeout or None,
    )


def build_registry(packages: Iterable[str], logger: logging.Logger) -> LCMTypeRegistry | None:
    """
    Discover LCM types from packages and register the spy types.

    Returns:
        The registry, or None if no types were discovered from the packages.
    """
    registry = LCMTypeRegistry()
    for package in packages:
        try:
            registry.discover(package)
        except ModuleNotFoundError:
            logger.error(f"Failed to discover LCM types in package '{package}'")

    if not registry.types:
        logger.critical("No LCM types discovered, exiting.")
        return None

    registry.register(channel_stats)
    registry.register(channel_stats_list)
    logger.info(f"Discovered LCM types: {', '.join(t.__name__ for t in registry.types)}")
    return registry


async def serve_until_stopped(server: LCMWebSocketServer, republisher: LCMRepublisher) -> None:
    """Start the republisher, serve until closed or SIGTERM, then stop the republisher."""
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, server.close)
    except (NotImplementedError, RuntimeError):
        pass  # unsupported platform or not on the main thread
    republisher.start()
    try:
        await server.serve()
    finally:
        republisher.stop()


AppFactory = Callable[[], Coroutine[Any, Any, None]]


def _run_one(app: AppFactory, use_uvloop: bool) -> None:
    try:
        if use_uvloop:
            import uvloop

            uvloop.run(app())
        else:
            asyncio.run(app())
    except KeyboardInterrupt:
        pass


def run_app(app: AppFactory, options: ServerOptions, logger: logging.Logger) -> None:
    """
    Run an app coroutine factory in one or more processes.

    `app` must be picklable (e.g. functools.partial of a module-level coroutine
    function) when options.processes > 1.
    """
    if options.uvloop:
        try:
            import uvloop  # noqa: F401
        except ImportError:
            raise SystemExit("--uvloop requires the 'uvloop' extra: uv add 'lcm-websocket-server[uvloop]'")

    logger.info(f"Starting at ws://{options.host}:{options.port} ({options.processes} process(es))")
    if options.processes == 1:
        _run_one(app, options.uvloop)
        logger.info("Stopped")
        return

    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_run_worker, args=(app, options.uvloop, options.verbosity), name=f"lws-worker-{i}")
        for i in range(options.processes)
    ]
    for p in procs:
        p.start()

    def terminate(*_):
        for p in procs:
            if p.is_alive():
                p.terminate()

    signal.signal(signal.SIGTERM, terminate)
    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        pass  # workers receive SIGINT from the terminal too
    finally:
        terminate()
        for p in procs:
            p.join(timeout=5)
    logger.info("Stopped")


def _run_worker(app: AppFactory, use_uvloop: bool, verbosity: int) -> None:
    set_stream_handler_verbosity(verbosity)  # spawned processes start with default logging
    _run_one(app, use_uvloop)


def app_factory(fn: Callable[..., Coroutine[Any, Any, None]], *args, **kwargs) -> AppFactory:
    """Picklable zero-argument coroutine factory."""
    return functools.partial(fn, *args, **kwargs)
