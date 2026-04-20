"""
LCM WebSocket JSON proxy server.
"""
import argparse
import asyncio
from typing import List, Optional

from lcmutils import LCMType, LCMTypeRegistry

from lcm_websocket_server.lib.server import LCMWebSocketServer
from lcm_websocket_server.lib.handler import EncodeCache, LCMWebSocketHandler
from lcm_websocket_server.lib.lcm_utils.pubsub import LCMRepublisher, OBSERVER_QUEUE_MAXSIZE
from lcm_websocket_server.lib.lcm_utils.channel_stats import channel_stats
from lcm_websocket_server.lib.lcm_utils.channel_stats_list import channel_stats_list
from lcm_websocket_server.lib.lcm_utils.types import encode_event_json
from lcm_websocket_server.lib.log import LogMixin, get_logger, set_stream_handler_verbosity


logger = get_logger("lcm-websocket-json-proxy")


class JSONHandler(LCMWebSocketHandler, LogMixin):
    """
    Handler that converts LCM messages to JSON.

    Results are cached per channel keyed by data object identity so that when
    multiple clients subscribe to the same channel the encode work is done once.
    """

    def __init__(self, lcm_type_registry: LCMTypeRegistry):
        self._lcm_type_registry = lcm_type_registry
        self._cache = EncodeCache()

    def _decode(self, data: bytes) -> Optional[LCMType]:
        """
        Decode an LCM message.

        Args:
            data: LCM message data

        Returns:
            The decoded LCM message, or None if the message could not be decoded.
        """
        try:
            message = self._lcm_type_registry.decode(data)
            if message is None:
                return None
            for slot in message.__slots__:
                if isinstance(getattr(message, slot), bytes):
                    # Attempt to decode bytes as another LCM message
                    nested_message = self._decode(getattr(message, slot))
                    if nested_message is not None:
                        setattr(message, slot, nested_message)
            return message
        except Exception as e:
            self.logger.debug(f"Failed to decode LCM data: {e}")
            return None

    def handle(self, channel: str, data: bytes) -> Optional[str]:
        cached = self._cache.get(channel, data)
        if cached is not None:
            return cached

        # Decode the LCM message
        event = self._decode(data)
        if event is None:
            return None

        # Get fingerprint hex
        fingerprint_hex = data[:8].hex()

        # Encode the event as JSON
        result = encode_event_json(channel, fingerprint_hex, event)
        self._cache.put(channel, data, result)
        return result


async def run(
    host: str,
    port: int,
    channel: str,
    lcm_packages: List[str],
    empty_wait_seconds: float = 0.1,
    observer_queue_maxsize: int = OBSERVER_QUEUE_MAXSIZE,
    spy_emit_interval_ns: int = 1_000_000_000,
):
    """
    Run the LCM WebSocket JSON proxy server.

    Args:
        host: Host to bind to
        port: Port to bind to
        channel: LCM channel to subscribe to
        lcm_packages: LCM packages to discover types from
        empty_wait_seconds: Poll sleep when the queue is empty (seconds)
        observer_queue_maxsize: Max messages buffered per connection
        spy_emit_interval_ns: LCM spy stats emission interval (nanoseconds)
    """
    # Create an LCM republisher
    logger.debug(f"Creating LCM republisher for channel '{channel}'")
    lcm_republisher = LCMRepublisher(channel)
    lcm_republisher.start()

    # Initialize the LCM type registry
    registry = LCMTypeRegistry()
    for package in lcm_packages:
        try:
            registry.discover(package)
        except ModuleNotFoundError:
            logger.error(f"Failed to discover LCM types in package '{package}'")

    # Register the channel_stats LCM types for the virtual spy channel
    registry.register(channel_stats)
    registry.register(channel_stats_list)
    logger.info(f"Registered virtual channel stats types: {channel_stats.__name__}, {channel_stats_list.__name__}")

    if not registry.types:
        logger.critical("No LCM types discovered, exiting.")
        return
    logger.info(f"Discovered LCM types: {', '.join([t.__name__ for t in registry.types])}")

    # Create an LCM WebSocket server
    handler = JSONHandler(registry)
    server = LCMWebSocketServer(
        host,
        port,
        handler,
        lcm_republisher,
        empty_wait_seconds=empty_wait_seconds,
        spy_registry=registry,
        observer_queue_maxsize=observer_queue_maxsize,
        spy_emit_interval_ns=spy_emit_interval_ns,
    )

    # Start the server
    logger.debug("Starting LCM WebSocket server")
    await server.serve()

    # Stop the LCM republisher
    lcm_republisher.stop()


def main():
    """
    Entry point for the LCM WebSocket JSON proxy server.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=str, default="localhost", help="The host to listen on. Default: %(default)s")
    parser.add_argument("--port", type=int, default=8765, help="The port to listen on. Default: %(default)s")
    parser.add_argument("--channel", type=str, default=".*", help="The LCM channel to subscribe to. Use '.*' to subscribe to all channels.")
    parser.add_argument("--queue-size", type=int, default=OBSERVER_QUEUE_MAXSIZE, help="Max messages buffered per connection before oldest are dropped. Default: %(default)s")
    parser.add_argument("--poll-interval", type=float, default=0.1, help="Sleep duration (seconds) when the message queue is empty. Default: %(default)s")
    parser.add_argument("--spy-interval", type=float, default=1.0, help="LCM spy stats emission interval (seconds). Default: %(default)s")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity level. 0=ERROR, 1=WARNING, 2=INFO, 3=DEBUG. Default: %(default)s")
    parser.add_argument("lcm_packages", type=str, help="The LCM packages to discover LCM types from. Separate multiple packages with a comma.")
    args = parser.parse_args()

    host = args.host
    port = args.port
    channel = args.channel
    verbosity = args.verbose
    lcm_packages = args.lcm_packages.split(",")
    empty_wait_seconds = args.poll_interval
    observer_queue_maxsize = args.queue_size
    spy_emit_interval_ns = int(args.spy_interval * 1_000_000_000)

    # Set the verbosity level
    set_stream_handler_verbosity(verbosity)

    # Run the server coroutine
    logger.info(f"Starting LCM WebSocket JSON proxy at ws://{host}:{port}")
    try:
        asyncio.run(run(
            host,
            port,
            channel,
            lcm_packages,
            empty_wait_seconds=empty_wait_seconds,
            observer_queue_maxsize=observer_queue_maxsize,
            spy_emit_interval_ns=spy_emit_interval_ns,
        ))
    except KeyboardInterrupt:
        logger.info("Stopped")


if __name__ == "__main__":
    asyncio.run(main())
