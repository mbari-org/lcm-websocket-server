"""
LCM WebSocket Proxy Server for the Dial visualization webapp.
"""

import argparse
import threading
from io import BytesIO
from time import monotonic_ns

import cv2
from lcmlog.event import Header
from numpy import ndarray
from senlcm import image_t
from stdlcm import header_t

from lcm_websocket_server.apps.json_proxy import JSONHandler
from lcm_websocket_server.lib.cli import (
    ServerOptions,
    add_server_args,
    app_factory,
    build_registry,
    parse_server_options,
    run_app,
    serve_until_stopped,
)
from lcm_websocket_server.lib.handler import LCMWebSocketHandler
from lcm_websocket_server.lib.image import (
    MJPEGEncoder,
    PixelFormat,
    UnsupportedPixelFormatError,
    get_decoder,
)
from lcm_websocket_server.lib.lcm_utils.pubsub import LCMRepublisher
from lcm_websocket_server.lib.log import LogMixin, get_logger
from lcm_websocket_server.lib.server import DEFAULT_HEAVY_WORKERS, LCMWebSocketServer

logger = get_logger("lcm-websocket-dial-proxy")


class ImageMessageToJPEGHandler(LCMWebSocketHandler, LogMixin):
    """
    Handler that converts image_t LCM messages to JPEG.

    Decoder instances are cached by (pixelformat, width, height) so a new
    object is not allocated on every frame.
    """

    def __init__(self, encoder: MJPEGEncoder):
        self._encoder = encoder
        self._decoder_cache: dict[tuple, object] = {}

    def _get_decoder(self, pixelformat: int, width: int, height: int):
        """Return a cached decoder instance for the given image geometry."""
        key = (pixelformat, width, height)
        decoder = self._decoder_cache.get(key)
        if decoder is None:
            decoder_cls = get_decoder(PixelFormat(pixelformat))
            decoder = decoder_cls(width, height)
            self._decoder_cache[key] = decoder
        return decoder

    def is_heavy(self, channel: str, data: bytes) -> bool:
        return True

    def handle(self, channel: str, data: bytes | image_t) -> bytes | None:
        # Accept either raw bytes or a pre-decoded image_t object.
        if isinstance(data, image_t):
            image_event = data
        else:
            try:
                image_event = image_t.decode(data)
            except Exception as e:  # noqa: BLE001 - malformed input must not propagate
                self.logger.warning(f"Failed to decode image_t event from channel {channel}: {e}")
                return None

        # Retrieve (or create) a cached decoder for this image geometry.
        try:
            decoder = self._get_decoder(image_event.pixelformat, image_event.width, image_event.height)
        except (UnsupportedPixelFormatError, ValueError) as e:
            self.logger.warning(str(e))
            return None

        # Decode the contained image.
        try:
            image = decoder.decode(image_event.data)
        except Exception as e:  # noqa: BLE001 - malformed input must not propagate
            self.logger.warning(f"Failed to decode image from channel {channel}: {e}")
            return None

        # Convert the image to JPEG.
        try:
            jpeg = self._encoder.encode(image)
        except Exception as e:  # noqa: BLE001 - malformed input must not propagate
            self.logger.warning(f"Failed to encode image as JPEG: {e}")
            return None

        return jpeg


class DownsamplingMJPEGEncoder(MJPEGEncoder):
    """
    MJPEG encoder that downsamples the image before encoding it.
    """
    def __init__(self, scale: float, params: list | None = None):
        super().__init__(params)
        self._scale = scale

    def encode(self, image: ndarray) -> bytes:
        # Downsample the image (skipped at scale 1.0, where resize is a full-frame copy)
        if self._scale != 1.0:
            image = cv2.resize(image, (0, 0), fx=self._scale, fy=self._scale, interpolation=cv2.INTER_AREA)

        # Encode the image
        return super().encode(image)


class DialHandler(LCMWebSocketHandler, LogMixin):
    """
    Handler for messages as preferred by Dial.

    This handler is a combination of the JSON and JPEG handlers in order to push both types of messages to the Dial webapp over a single WebSocket connection.
    The image handler is invoked for `image_t` messages, and the JSON handler is invoked for all other messages.

    The image handler generates a JPEG image from the `image_t` message, prepends the original LCM message header and channel name, and sends the result as a binary frame over the WebSocket.
    The JSON handler generates UTF-8 JSON from the LCM message, and sends the result as a text frame over the WebSocket.
    """

    IMAGE_T_FINGERPRINT = image_t._get_packed_fingerprint()

    def __init__(
        self,
        image_handler: ImageMessageToJPEGHandler,
        json_handler: JSONHandler,
        max_image_fps: float | None = None,
    ):
        """
        Args:
            image_handler: Converts image_t to JPEG.
            json_handler: Converts all other messages to JSON.
            max_image_fps: Optional per-channel cap on transcoded image frames.
                Frames over the cap are skipped for all clients.
        """
        if max_image_fps is not None and max_image_fps <= 0:
            raise ValueError("max_image_fps must be positive")
        self._image_handler = image_handler
        self._json_handler = json_handler
        self._image_interval_ns = int(1e9 / max_image_fps) if max_image_fps else 0
        self._next_image_ns: dict[str, int] = {}
        self._rate_lock = threading.Lock()

    def _image_due(self, channel: str, now_ns: int) -> bool:
        """
        Deadline-based rate limit per channel. Tolerates 10% early arrival so
        source jitter does not halve the rate when the cap matches the source.
        """
        interval = self._image_interval_ns
        if not interval:
            return True
        with self._rate_lock:
            deadline = self._next_image_ns.get(channel)
            if deadline is not None and now_ns < deadline - interval // 10:
                return False
            # Resume from now after a long gap rather than bursting to catch up.
            base = now_ns if deadline is None or now_ns - deadline > interval else deadline
            self._next_image_ns[channel] = base + interval
            return True

    def _encode_image_t(self, channel: str, data: bytes) -> bytes | None:
        """
        Encode an image_t message as a binary frame.

        Args:
            channel: LCM channel name
            data: LCM message data (image_t)

        Returns:
            The encoded binary frame, or None if the message could not be encoded.
        """
        # Decode the image_t to get the payload header timestamp.
        image_event = image_t.decode(data)
        payload_header: header_t = image_event.header

        # Encode the image as JPEG (pass pre-decoded object to skip a second decode).
        jpeg_bytes = self._image_handler.handle(channel, image_event)
        if jpeg_bytes is None:
            return None

        # Construct the LCM log-format header.
        channel_name_utf8 = channel.encode("utf-8")
        header = Header(
            0,
            payload_header.timestamp,
            len(channel_name_utf8),
            len(data)
        )

        header_byte_io = BytesIO()
        header.write_to(header_byte_io)
        header_bytes = header_byte_io.getvalue()

        return header_bytes + channel_name_utf8 + jpeg_bytes

    def is_heavy(self, channel: str, data: bytes) -> bool:
        return data[:8] == DialHandler.IMAGE_T_FINGERPRINT

    def handle(self, channel: str, data: bytes) -> bytes | str | None:
        if data[:8] == DialHandler.IMAGE_T_FINGERPRINT:
            if not self._image_due(channel, monotonic_ns()):
                return None
            return self._encode_image_t(channel, data)
        return self._json_handler.handle(channel, data)


DIAL_LCM_PACKAGES = (
    "stdlcm",
    "senlcm",
    "navlcm",
    "geolcm",
    "lcm_action_server",
    "mission_server",
    "nav_server",
    "control_server",
)


async def run(
    options: ServerOptions,
    scale: float = 1.0,
    quality: int = 75,
    image_workers: int = DEFAULT_HEAVY_WORKERS,
    max_fps: float | None = None,
) -> None:
    """
    Run the LCM WebSocket Dial proxy server.

    Args:
        options: Common server options
        scale: The scale factor to resize the image by.
        quality: The JPEG quality level. Clamped to the range [0, 100].
        image_workers: Threads for image transcoding.
        max_fps: Optional per-channel cap on transcoded image frames per second.
    """
    registry = build_registry(DIAL_LCM_PACKAGES, logger)
    if registry is None:
        return

    quality = round(max(0, min(quality, 100)))
    jpeg_encoder = DownsamplingMJPEGEncoder(scale=scale, params=[cv2.IMWRITE_JPEG_QUALITY, quality])
    handler = DialHandler(ImageMessageToJPEGHandler(jpeg_encoder), JSONHandler(registry), max_image_fps=max_fps)

    logger.debug(f"Creating LCM republisher for channel '{options.channel}'")
    republisher = LCMRepublisher(options.channel)
    server = LCMWebSocketServer(
        options.host,
        options.port,
        handler,
        republisher,
        spy_registry=registry,
        heavy_workers=image_workers,
        **options.server_kwargs(),
    )
    await serve_until_stopped(server, republisher)


def main():
    """
    Entry point for the LCM WebSocket Dial proxy server.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    add_server_args(parser, compression_default="none")
    parser.add_argument("--scale", type=float, default=1.0, help="The scale factor to resize the image by. Default: %(default)s")
    parser.add_argument("--quality", type=int, default=75, help="The JPEG quality level, 0-100. Default: %(default)s")
    parser.add_argument("--max-fps", type=float, default=None, help="Cap on transcoded frames per second per image channel. Default: no cap")
    parser.add_argument("--image-workers", type=int, default=DEFAULT_HEAVY_WORKERS, help="Threads for image transcoding. Default: %(default)s")
    args = parser.parse_args()

    options = parse_server_options(args, logger)
    run_app(
        app_factory(
            run,
            options,
            scale=args.scale,
            quality=args.quality,
            image_workers=args.image_workers,
            max_fps=args.max_fps,
        ),
        options,
        logger,
    )


if __name__ == "__main__":
    main()
