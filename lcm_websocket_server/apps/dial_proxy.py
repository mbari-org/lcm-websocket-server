"""
LCM WebSocket Proxy Server for the Dial visualization webapp.
"""

import argparse
import asyncio
from io import BytesIO
from typing import Optional, Union

import cv2
from numpy import ndarray
from lcmlog.event import Header
from lcmutils import LCMTypeRegistry
from senlcm import image_t
from stdlcm import header_t

from lcm_websocket_server.lib.lcm_utils.pubsub import LCMRepublisher
from lcm_websocket_server.lib.lcm_utils.spy import LCMSpy
from lcm_websocket_server.lib.lcm_utils.channel_stats import channel_stats, channel_stats_list
from lcm_websocket_server.lib.handler import LCMWebSocketHandler
from lcm_websocket_server.lib.image import MJPEGEncoder, PixelFormat, UnsupportedPixelFormatError, get_decoder
from lcm_websocket_server.lib.log import LogMixin
from lcm_websocket_server.lib.server import LCMWebSocketServer
from lcm_websocket_server.apps.json_proxy import JSONHandler
from lcm_websocket_server.lib.log import get_logger, set_stream_handler_verbosity


logger = get_logger("lcm-websocket-dial-proxy")


class ImageMessageToJPEGHandler(LCMWebSocketHandler, LogMixin):
    """
    Handler that converts image_t LCM messages to JPEG.
    """
    
    def __init__(self, encoder: MJPEGEncoder):
        self._encoder = encoder
    
    def handle(self, channel: str, data: Union[bytes, image_t]) -> Optional[bytes]:
        # Check if the data is already an image_t. If so, use it directly
        if isinstance(data, image_t):
            image_event = data
        else:
            # Decode the LCM message
            try:
                image_event = image_t.decode(data)
            except Exception as e:
                self.logger.warning(f"Failed to decode image_t event from channel {channel}: {e}")
                return None

        # Create a decoder
        try:
            decoder_cls = get_decoder(PixelFormat(image_event.pixelformat))
        except UnsupportedPixelFormatError as e:
            self.logger.warning(str(e))
            return None
        decoder = decoder_cls(image_event.width, image_event.height)

        # Decode the contained image
        try:
            image = decoder.decode(image_event.data)
        except Exception as e:
            self.logger.warning(f"Failed to decode image from channel {channel}: {e}")
            return None

        # Convert the image to JPEG
        try:
            jpeg = self._encoder.encode(image)
        except Exception as e:
            self.logger.warning(f"Failed to encode image as JPEG: {e}")
            return None

        return jpeg


class DownsamplingMJPEGEncoder(MJPEGEncoder):
    """
    MJPEG encoder that downsamples the image before encoding it.
    """
    def __init__(self, scale: float, params: Optional[list] = None):
        super().__init__(params)
        self._scale = scale
    
    def encode(self, image: ndarray) -> bytes:
        # Downsample the image
        image = cv2.resize(image, (0, 0), fx=self._scale, fy=self._scale, interpolation=cv2.INTER_AREA)
        
        # Encode the image
        return super().encode(image)


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
    package_names = [
        "stdlcm",
        "senlcm",
        "navlcm",
        "geolcm",
        "lcm_action_server",
        "mission_server",
        "nav_server",
        "control_server",
    ]
    logger.info(f"Discovering LCM types in packages: {', '.join(package_names)}")
    for package_name in package_names:
        try:
            registry.discover(package_name)
        except ModuleNotFoundError:
            logger.error(f"Could not discover types in MolaRS package '{package_name}'")
    
    # Register the channel_stats LCM types for the virtual spy channel
    registry.register(channel_stats)
    registry.register(channel_stats_list)
    logger.info(f"Registered virtual channel stats types: {channel_stats.__name__}, {channel_stats_list.__name__}")
    
    if not registry.types:
        logger.critical("No LCM types discovered, exiting.")
        return
    logger.info(f"Discovered LCM types: {', '.join([t.__name__ for t in registry.types])}")

    # Initialize the LCM spy to track channel statistics
    # The spy will publish stats at 1 Hz on the virtual channel "LWS_LCM_SPY"
    spy = LCMSpy(registry, lcm_republisher, channel_regex=channel)
    logger.info(f"Initialized LCM spy - stats available on virtual channel '{LCMSpy.VIRTUAL_CHANNEL}'")

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
    logger.debug("Starting LCM WebSocket server")
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
        logger.info("Stopped")


if __name__ == "__main__":
    asyncio.run(main())