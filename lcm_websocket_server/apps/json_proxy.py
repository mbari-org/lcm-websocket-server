"""
LCM WebSocket JSON proxy server.
"""
import argparse

from lcmutils import LCMType, LCMTypeRegistry

from lcm_websocket_server.lib.cli import (
    ServerOptions,
    add_server_args,
    app_factory,
    build_registry,
    parse_server_options,
    run_app,
    serve_until_stopped,
)
from lcm_websocket_server.lib.handler import LCMWebSocketHandler, TextPayload
from lcm_websocket_server.lib.lcm_utils.pubsub import LCMRepublisher
from lcm_websocket_server.lib.lcm_utils.types import encode_event_json_bytes
from lcm_websocket_server.lib.log import LogMixin, get_logger
from lcm_websocket_server.lib.server import LCMWebSocketServer

logger = get_logger("lcm-websocket-json-proxy")


class JSONHandler(LCMWebSocketHandler, LogMixin):
    """
    Handler that converts LCM messages to JSON. Stateless and thread-safe.
    """

    def __init__(self, lcm_type_registry: LCMTypeRegistry):
        self._lcm_type_registry = lcm_type_registry

    def _decode(self, data: bytes) -> LCMType | None:
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

    def handle(self, channel: str, data: bytes) -> TextPayload | None:
        # Decode the LCM message
        event = self._decode(data)
        if event is None:
            return None

        # Get fingerprint hex
        fingerprint_hex = data[:8].hex()

        # Encode the event as JSON
        return TextPayload(encode_event_json_bytes(channel, fingerprint_hex, event))


async def run(options: ServerOptions, lcm_packages: list[str]) -> None:
    """
    Run the LCM WebSocket JSON proxy server.

    Args:
        options: Common server options
        lcm_packages: LCM packages to discover types from
    """
    registry = build_registry(lcm_packages, logger)
    if registry is None:
        return

    logger.debug(f"Creating LCM republisher for channel '{options.channel}'")
    republisher = LCMRepublisher(options.channel)
    server = LCMWebSocketServer(
        options.host,
        options.port,
        JSONHandler(registry),
        republisher,
        spy_registry=registry,
        **options.server_kwargs(),
    )
    await serve_until_stopped(server, republisher)


def main():
    """
    Entry point for the LCM WebSocket JSON proxy server.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    add_server_args(parser, compression_default="deflate")
    parser.add_argument("lcm_packages", type=str, help="The LCM packages to discover LCM types from. Separate multiple packages with a comma.")
    args = parser.parse_args()

    options = parse_server_options(args, logger)
    run_app(app_factory(run, options, args.lcm_packages.split(",")), options, logger)


if __name__ == "__main__":
    main()
