"""
Tests for ImageMessageToJPEGHandler and DialHandler.
"""

import inspect
import json
import struct

import cv2
import numpy as np
import pytest
from senlcm import image_t
from stdlcm import header_t

from lcm_websocket_server.apps.dial_proxy import (
    DialHandler,
    DownsamplingMJPEGEncoder,
    ImageMessageToJPEGHandler,
)
from lcm_websocket_server.lib.image import PixelFormat
from lcm_websocket_server.lib.lcm_utils.pubsub import LCMObserver, LCMRepublisher

from tests.conftest import make_bgr_image_t, make_header_t


# ---------------------------------------------------------------------------
# Synchronous interface (Fix 2)
# ---------------------------------------------------------------------------

class TestHandlerInterface:
    def test_image_handler_is_not_coroutine(self, image_handler):
        assert not inspect.iscoroutinefunction(image_handler.handle)

    def test_dial_handler_is_not_coroutine(self, dial_handler):
        assert not inspect.iscoroutinefunction(dial_handler.handle)


# ---------------------------------------------------------------------------
# ImageMessageToJPEGHandler
# ---------------------------------------------------------------------------

class TestImageMessageToJPEGHandler:
    def test_returns_jpeg_bytes_for_valid_bgr_image(self, image_handler):
        result = image_handler.handle("CAM", make_bgr_image_t())
        assert isinstance(result, bytes)
        assert result[:2] == b"\xff\xd8", "Expected JPEG magic bytes"

    def test_returns_none_for_garbage_data(self, image_handler):
        result = image_handler.handle("CAM", b"\x00\x01\x02\x03")
        assert result is None

    def test_accepts_pre_decoded_image_t_object(self, image_handler):
        raw = make_bgr_image_t()
        event = image_t.decode(raw)
        result = image_handler.handle("CAM", event)
        assert result is not None
        assert result[:2] == b"\xff\xd8"

    def test_returned_jpeg_is_decodable(self, image_handler):
        result = image_handler.handle("CAM", make_bgr_image_t(width=8, height=6))
        assert result is not None
        arr = cv2.imdecode(np.frombuffer(result, np.uint8), cv2.IMREAD_COLOR)
        assert arr is not None
        assert arr.shape == (6, 8, 3)

    def test_downsampling_encoder_reduces_dimensions(self, registry):
        enc = DownsamplingMJPEGEncoder(scale=0.5, params=[cv2.IMWRITE_JPEG_QUALITY, 75])
        handler = ImageMessageToJPEGHandler(enc)
        jpeg = handler.handle("CAM", make_bgr_image_t(width=16, height=12))
        arr = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        assert arr.shape == (6, 8, 3)


# ---------------------------------------------------------------------------
# DialHandler: routing
# ---------------------------------------------------------------------------

class TestDialHandlerRouting:
    def test_image_t_produces_bytes_frame(self, dial_handler):
        result = dial_handler.handle("CAM", make_bgr_image_t())
        assert isinstance(result, bytes)

    def test_non_image_produces_str_json_frame(self, dial_handler):
        result = dial_handler.handle("STATE", make_header_t())
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed["channel"] == "STATE"

    def test_image_t_frame_starts_with_jpeg(self, dial_handler):
        """
        The binary frame layout is: LCM-log-header (magic + timestamps etc.)
        + channel-name bytes + JPEG bytes.  We verify the JPEG magic is present
        after the header and channel-name portion.
        """
        channel = "CAM"
        raw = make_bgr_image_t()
        frame = dial_handler.handle(channel, raw)
        assert frame is not None

        # LCM log header: 4-byte magic (0xa3 0x54 0x21 0x4c 0x2d 0x6c 0x67 0x21)
        # followed by various fields.  Rather than parse the full header, just
        # verify that the JPEG magic (0xff 0xd8) appears somewhere after the
        # first 8 bytes.
        assert b"\xff\xd8" in frame[8:]

    def test_channel_name_embedded_in_image_frame(self, dial_handler):
        channel = "FRONT_CAMERA"
        frame = dial_handler.handle(channel, make_bgr_image_t())
        assert channel.encode("utf-8") in frame

    def test_unknown_type_returns_none(self, dial_handler):
        garbage = b"\x00" * 16
        result = dial_handler.handle("CH", garbage)
        assert result is None

    def test_fingerprint_routing_matches_image_t(self, dial_handler):
        """Verify DialHandler.IMAGE_T_FINGERPRINT matches the real type."""
        raw = make_bgr_image_t()
        fingerprint_in_data = raw[:8]
        assert fingerprint_in_data == DialHandler.IMAGE_T_FINGERPRINT


# ---------------------------------------------------------------------------
# Fix 3: EncodeCache on DialHandler
# ---------------------------------------------------------------------------

class TestDialHandlerEncodeCache:
    def _make_counting_handler(self, image_handler, json_handler):
        encode_calls: list[str] = []

        class CountingDialHandler(DialHandler):
            def _encode_image_t(self_inner, channel, data):
                encode_calls.append("image")
                return super()._encode_image_t(channel, data)

        return CountingDialHandler(image_handler, json_handler), encode_calls

    def test_second_image_call_with_same_object_hits_cache(
        self, image_handler, json_handler
    ):
        handler, calls = self._make_counting_handler(image_handler, json_handler)
        data = make_bgr_image_t()

        r1 = handler.handle("CAM", data)
        r2 = handler.handle("CAM", data)  # same object → cache hit

        assert r1 == r2
        assert len(calls) == 1, f"Expected 1 encode, got {len(calls)}"

    def test_multiple_subscribers_share_same_data_object(self):
        """
        When two connections subscribe to the same channel, inject() puts the
        same data object into both queues, so the second call hits the cache.
        """
        r = LCMRepublisher(".*")
        obs1, obs2 = LCMObserver(), LCMObserver()
        r.subscribe(obs1)
        r.subscribe(obs2)

        r.inject("CAM", make_bgr_image_t())

        _, data1 = obs1.get(block=False)
        _, data2 = obs2.get(block=False)

        assert data1 is data2, "Both queues must hold the same bytes object"
