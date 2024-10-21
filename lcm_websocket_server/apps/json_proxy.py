"""
LCM WebSocket JSON proxy server.
"""
import argparse
import asyncio
from typing import List, Optional

from lcmutils import LCMTypeRegistry

from lcm_websocket_server.lib.server import LCMWebSocketServer
from lcm_websocket_server.lib.handler import LCMWebSocketHandler
from lcm_websocket_server.lib.lcm_utils.pubsub import LCMRepublisher
from lcm_websocket_server.lib.lcm_utils.types import encode_event_json
from lcm_websocket_server.lib.log import LogMixin, get_logger, set_stream_handler_verbosity


logger = get_logger("lcm-websocket-json-proxy")


class JSONHandler(LCMWebSocketHandler, LogMixin):
    """
    Handler that converts LCM messages to JSON.
    """
    
    def __init__(self, lcm_type_registry: LCMTypeRegistry):
        self._lcm_type_registry = lcm_type_registry
    
    def handle(self, channel: str, data: bytes) -> Optional[str]:
        # Decode the LCM message
        try:
            event = self._lcm_type_registry.decode(data)
        except Exception as e:
            self.logger.debug(f"Failed to decode LCM event from channel {channel}: {e}")
            return None
        
        # Get fingerprint hex
        fingerprint = data[:8]
        fingerprint_hex = fingerprint.hex()
        
        # Encode the event as JSON
        event_json = encode_event_json(channel, fingerprint_hex, event)
        return event_json


async def run(host: str, port: int, channel: str, lcm_packages: List[str]):
    """
    Run the LCM WebSocket JSON proxy server.
    
    Args:
        host: Host to bind to
        port: Port to bind to
        channel: LCM channel to subscribe to
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
    if not registry.types:
        logger.critical("No LCM types discovered, exiting.")
        return
    logger.info(f"Discovered LCM types: {', '.join([t.__name__ for t in registry.types])}")

    # Create an LCM WebSocket server
    handler = JSONHandler(registry)
    server = LCMWebSocketServer(host, port, handler, lcm_republisher)

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
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity level. 0=ERROR, 1=WARNING, 2=INFO, 3=DEBUG. Default: %(default)s")
    parser.add_argument("lcm_packages", type=str, help="The LCM packages to discover LCM types from. Separate multiple packages with a comma.")
    args = parser.parse_args()
    
    host = args.host
    port = args.port
    channel = args.channel
    verbosity = args.verbose
    lcm_packages = args.lcm_packages.split(",")
    
    # Set the verbosity level
    set_stream_handler_verbosity(verbosity)
    
    # Run the server coroutine
    logger.info(f"Starting LCM WebSocket JSON proxy at ws://{host}:{port}")
    try:
        asyncio.run(run(host, port, channel, lcm_packages))
    except KeyboardInterrupt:
        logger.info("Stopped")


if __name__ == "__main__":
    asyncio.run(main())
