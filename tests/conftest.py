"""
Shared fixtures for the lcm-websocket-server test suite.

All LCM interaction uses LCMRepublisher.inject() which bypasses the UDP
multicast network entirely, so no LCM daemon or network is required.
"""

import asyncio
import socket
from typing import AsyncIterator

import cv2
import numpy as np
import pytest
import pytest_asyncio
from senlcm import image_t
from stdlcm import header_t

from lcmutils import LCMTypeRegistry

from lcm_websocket_server.apps.dial_proxy import (
    DialHandler,
    DownsamplingMJPEGEncoder,
    ImageMessageToJPEGHandler,
)
from lcm_websocket_server.apps.json_proxy import JSONHandler
from lcm_websocket_server.lib.image import PixelFormat
from lcm_websocket_server.lib.lcm_utils.channel_stats import channel_stats
from lcm_websocket_server.lib.lcm_utils.channel_stats_list import channel_stats_list
from lcm_websocket_server.lib.lcm_utils.pubsub import LCMRepublisher
from lcm_websocket_server.lib.server import LCMWebSocketServer


# ---------------------------------------------------------------------------
# Port helpers
# ---------------------------------------------------------------------------

def free_port() -> int:
    """Return an OS-assigned free TCP port (best-effort; TOCTOU is acceptable in tests)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# LCM message factories
# ---------------------------------------------------------------------------

def make_header_t(sequence: int = 0, timestamp: int = 1_000_000, frame_id: str = "test") -> bytes:
    """Encode a header_t message."""
    msg = header_t()
    msg.sequence = sequence
    msg.timestamp = timestamp
    msg.frame_id = frame_id
    return msg.encode()


def make_bgr_image_t(width: int = 8, height: int = 6, timestamp: int = 0) -> bytes:
    """
    Encode an image_t message backed by a small synthetic BGR image.

    The image is real numpy data so the full encode/decode/JPEG pipeline runs
    against actual pixel values.
    """
    hdr = header_t()
    hdr.timestamp = timestamp
    hdr.sequence = 0
    hdr.frame_id = "cam"

    # Gradient: blue channel increases left→right, green channel top→bottom
    bgr = np.zeros((height, width, 3), dtype=np.uint8)
    for row in range(height):
        for col in range(width):
            bgr[row, col] = [col * (255 // max(width - 1, 1)),
                              row * (255 // max(height - 1, 1)),
                              128]

    msg = image_t()
    msg.header = hdr
    msg.width = width
    msg.height = height
    msg.pixelformat = PixelFormat.BGR.value
    msg.nmetadata = 0
    msg.metadata = []
    msg.data = bgr.tobytes()
    msg.size = len(msg.data)
    return msg.encode()


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def registry() -> LCMTypeRegistry:
    """Shared LCMTypeRegistry with stdlcm and senlcm types (plus internal spy types)."""
    reg = LCMTypeRegistry()
    for pkg in ("stdlcm", "senlcm"):
        try:
            reg.discover(pkg)
        except ModuleNotFoundError:
            pass
    reg.register(channel_stats)
    reg.register(channel_stats_list)
    return reg


@pytest.fixture(scope="session")
def jpeg_encoder() -> DownsamplingMJPEGEncoder:
    """Stateless JPEG encoder; safe to share across all tests."""
    return DownsamplingMJPEGEncoder(scale=1.0, params=[cv2.IMWRITE_JPEG_QUALITY, 75])


@pytest.fixture
def republisher() -> LCMRepublisher:
    """Fresh LCMRepublisher per test (no daemon started; use inject())."""
    return LCMRepublisher(".*")


@pytest.fixture
def json_handler(registry: LCMTypeRegistry) -> JSONHandler:
    """Fresh JSONHandler per test so EncodeCache state doesn't bleed between tests."""
    return JSONHandler(registry)


@pytest.fixture
def image_handler(jpeg_encoder: DownsamplingMJPEGEncoder) -> ImageMessageToJPEGHandler:
    """Fresh ImageMessageToJPEGHandler per test (fresh decoder cache)."""
    return ImageMessageToJPEGHandler(jpeg_encoder)


@pytest.fixture
def dial_handler(image_handler: ImageMessageToJPEGHandler, json_handler: JSONHandler) -> DialHandler:
    """Fresh DialHandler per test (fresh encode cache)."""
    return DialHandler(image_handler, json_handler)


# ---------------------------------------------------------------------------
# Running-server fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def running_server(
    republisher: LCMRepublisher,
    json_handler: JSONHandler,
) -> AsyncIterator[tuple[LCMWebSocketServer, int]]:
    """
    Start a real LCMWebSocketServer on a free port and tear it down after each test.

    Uses empty_wait_seconds=0.001 so tests don't spend 100 ms waiting on each
    empty-queue poll.
    """
    port = free_port()
    server = LCMWebSocketServer(
        "localhost",
        port,
        json_handler,
        republisher,
        empty_wait_seconds=0.001,
    )
    serve_task = asyncio.create_task(server.serve())
    # Give the server time to bind and start accepting connections.
    await asyncio.sleep(0.05)

    yield server, port

    server.close()
    try:
        await asyncio.wait_for(serve_task, timeout=2.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        serve_task.cancel()
        await asyncio.gather(serve_task, return_exceptions=True)
