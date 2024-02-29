"""
LCM WebSocket JPEG proxy server.
"""

from lcm_websocket_server.lib.log import get_logger, set_stream_handler_verbosity
logger = get_logger("lcm-websocket-jpeg-proxy")

# Ensure LCM installed
try:
    import lcm
except ImportError:
    logger.critical("LCM Python module is not installed or could not be found, exiting.")
    exit(1)

import argparse
import asyncio
from typing import Optional, Union

from compas_lcmtypes.senlcm import image_t
import cv2
from numpy import ndarray

from lcm_websocket_server.lib.lcm_utils.pubsub import LCMRepublisher
from lcm_websocket_server.lib.image import MJPEGEncoder, PixelFormat, UnsupportedPixelFormatError, get_decoder
from lcm_websocket_server.lib.log import LogMixin
from lcm_websocket_server.lib.server import LCMWebSocketServer
from lcm_websocket_server.lib.handler import LCMWebSocketHandler


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
                self.logger.debug(f"Failed to decode image_t event from channel {channel}: {e}")
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


async def run(host: str, port: int, channel: str, scale: float = 1.0, quality: int = 75):
    """
    Run the LCM WebSocket JPEG proxy server.
    
    Args:
        host: The host to listen on.
        port: The port to listen on.
        channel: The LCM channel pattern to subscribe to.
        scale: The scale factor to resize the image by.
        quality: The JPEG quality level. Clamped to the range [0, 100].
    """
    # Create an LCM republisher
    logger.debug(f"Creating LCM republisher for channel '{channel}'")
    lcm_republisher = LCMRepublisher(channel)
    lcm_republisher.start()

    # Create an image encoder
    quality = round(max(0, min(quality, 100)))
    jpeg_encoder = DownsamplingMJPEGEncoder(scale=scale, params=[cv2.IMWRITE_JPEG_QUALITY, quality])

    # Create an LCM WebSocket server
    handler = ImageMessageToJPEGHandler(jpeg_encoder)
    server = LCMWebSocketServer(host, port, handler, lcm_republisher)

    # Start the server
    logger.debug(f"Starting LCM WebSocket server")
    await server.serve()
    
    # Stop the LCM republisher
    lcm_republisher.stop()


def main():
    """
    Entry point for the LCM WebSocket JPEG proxy server.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=str, default="localhost", help="The host to listen on. Default: %(default)s")
    parser.add_argument("--port", type=int, default=8766, help="The port to listen on. Default: %(default)s")
    parser.add_argument("--channel", type=str, default=".*", help="The LCM channel pattern to subscribe to. Use '.*' to subscribe to all channels.")
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
    logger.info(f"Starting LCM WebSocket JPEG proxy at ws://{host}:{port}")
    try:
        asyncio.run(run(host, port, channel, scale=scale, quality=quality))
    except KeyboardInterrupt:
        logger.info(f"Stopped")


if __name__ == "__main__":
    main()