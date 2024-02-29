"""
LCM WebSocket Proxy Server for the Dial visualization webapp.
"""

from io import BytesIO
import time
from typing import Optional, Union

import cv2
from lcm_websocket_server.lib.log import get_logger, set_stream_handler_verbosity
logger = get_logger("lcm-websocket-dial-proxy")

# Ensure LCM installed
try:
    import lcm
except ImportError:
    logger.critical("LCM Python module is not installed or could not be found, exiting.")
    exit(1)

import argparse
import asyncio

from compas_lcmtypes.senlcm import image_t
from compas_lcmtypes.stdlcm import header_t
from lcmlog.event import Header

from lcm_websocket_server.lib.lcm_utils.types import LCMTypeRegistry
from lcm_websocket_server.lib.lcm_utils.pubsub import LCMRepublisher
from lcm_websocket_server.lib.log import LogMixin
from lcm_websocket_server.lib.server import LCMWebSocketServer
from lcm_websocket_server.apps.jpeg_proxy import DownsamplingMJPEGEncoder, ImageMessageToJPEGHandler
from lcm_websocket_server.apps.json_proxy import JSONHandler


class DialHandler(LogMixin):
    """
    Handler for messages as preferred by Dial.
    
    This handler is a combination of the JSON and JPEG handlers in order to push both types of messages to the Dial webapp over a single WebSocket connection.
    The image handler is invoked for `image_t` messages, and the JSON handler is invoked for all other messages.
    
    The image handler generates a JPEG image from the `image_t` message, prepends the original LCM message header and channel name, and sends the result as a binary frame over the WebSocket.
    The JSON handler generates a JSON string from the LCM message, and sends the result as a text frame over the WebSocket.
    """
    
    IMAGE_T_FINGERPRINT = image_t._get_packed_fingerprint()
    
    def __init__(self, image_handler: ImageMessageToJPEGHandler, json_handler: JSONHandler):
        self._image_handler = image_handler
        self._json_handler = json_handler
    
    def _encode_image_t(self, channel: str, data: bytes) -> Optional[bytes]:
        """
        Encode an image_t message as a binary frame.
        
        Args:
            channel: LCM channel name
            data: LCM message data (image_t)
        
        Returns:
            The encoded binary frame, or None if the message could not be encoded.
        """
        # Decode the image_t to get the payload header timestamp
        image_event = image_t.decode(data)
        payload_header: header_t = image_event.header
        
        # Encode the image as JPEG
        jpeg_bytes = self._image_handler.handle(channel, image_event)
        if jpeg_bytes is None:
            return None
        
        # Construct the header
        channel_name_utf8 = channel.encode("utf-8")
        header = Header(
            0, 
            payload_header.timestamp,
            len(channel_name_utf8), 
            len(data)
        )
        
        # Encode the header as bytes
        header_byte_io = BytesIO()
        header.write_to(header_byte_io)
        header_bytes = header_byte_io.getvalue()
        
        # Construct the frame
        frame = header_bytes + channel_name_utf8 + jpeg_bytes
        
        return frame
    
    def handle(self, channel: str, data: bytes) -> Optional[Union[bytes, str]]:
        # Check if the message is an image_t message and encode the response
        response = None
        fingerprint = data[:8]
        if fingerprint == DialHandler.IMAGE_T_FINGERPRINT:
            response = self._encode_image_t(channel, data)
        else:
            response = self._json_handler.handle(channel, data)
        
        return response


async def run(host: str, port: int, channel: str, scale: float = 1.0, quality: int = 75):
    """
    Run the LCM WebSocket Dial proxy server.
    
    Args:
        host: Host to bind to
        port: Port to bind to
        channel: LCM channel to subscribe to
        scale: The scale factor to resize the image by.
        quality: The JPEG quality level. Clamped to the range [0, 100].
    """
    # Create an LCM republisher
    logger.debug(f"Creating LCM republisher for channel '{channel}'")
    lcm_republisher = LCMRepublisher(channel)
    lcm_republisher.start()
    
    # Initialize the LCM type registry
    registry = LCMTypeRegistry()
    registry.discover("compas_lcmtypes")
    if not registry.types:
        logger.critical("No LCM types discovered, exiting.")
        return
    logger.info(f"Discovered LCM types: {', '.join([t.__name__ for t in registry.types])}")

    # Create an image encoder
    quality = round(max(0, min(quality, 100)))
    jpeg_encoder = DownsamplingMJPEGEncoder(scale=scale, params=[cv2.IMWRITE_JPEG_QUALITY, quality])

    # Create the sub-handlers
    image_handler = ImageMessageToJPEGHandler(jpeg_encoder)
    json_handler = JSONHandler(registry)

    # Create an LCM WebSocket server
    handler = DialHandler(image_handler, json_handler)
    server = LCMWebSocketServer(host, port, handler, lcm_republisher)

    # Start the server
    logger.debug(f"Starting LCM WebSocket server")
    await server.serve()
    
    # Stop the LCM republisher
    lcm_republisher.stop()


def main():
    """
    Entry point for the LCM WebSocket Dial proxy server.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=str, default="localhost", help="The host to listen on. Default: %(default)s")
    parser.add_argument("--port", type=int, default=8765, help="The port to listen on. Default: %(default)s")
    parser.add_argument("--channel", type=str, default=".*", help="The LCM channel to subscribe to. Use '.*' to subscribe to all channels.")
    parser.add_argument("--scale", type=float, default=1.0, help="The scale factor to resize the image by. Default: %(default)s")
    parser.add_argument("--quality", type=int, default=75, help="The JPEG quality level, 0-100. Default: %(default)s")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity level. 0=ERROR, 1=WARNING, 2=INFO, 3=DEBUG. Default: %(default)s")
    args = parser.parse_args()
    
    host = args.host
    port = args.port
    channel = args.channel
    scale = args.scale
    quality = args.quality
    verbosity = args.verbose
    
    # Set the verbosity level
    set_stream_handler_verbosity(verbosity)
    
    # Run the server coroutine
    logger.info(f"Starting LCM WebSocket Dial proxy at ws://{host}:{port}")
    try:
        asyncio.run(run(host, port, channel, scale=scale, quality=quality))
    except KeyboardInterrupt:
        logger.info(f"Stopped")


if __name__ == "__main__":
    asyncio.run(main())