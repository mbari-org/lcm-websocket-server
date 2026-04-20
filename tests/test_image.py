"""
Tests for image decoding, encoding, and the decoder cache.
"""

import cv2
import numpy as np
import pytest

from lcm_websocket_server.lib.image import (
    BGRDecoder,
    GrayDecoder,
    MJPEGDecoder,
    MJPEGEncoder,
    PixelFormat,
    RGBDecoder,
    UnsupportedPixelFormatError,
    get_decoder,
    get_encoder,
)
from lcm_websocket_server.apps.dial_proxy import (
    DownsamplingMJPEGEncoder,
    ImageMessageToJPEGHandler,
)

W, H = 16, 12  # small enough to be fast; large enough to be non-trivial


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def solid_bgr(w: int, h: int, color: tuple[int, int, int]) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = color
    return img


def gradient_bgr(w: int, h: int) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    for row in range(h):
        for col in range(w):
            img[row, col] = [col * 15, row * 20, 128]
    return img


# ---------------------------------------------------------------------------
# BGR / RGB round-trip
# ---------------------------------------------------------------------------

class TestBGRDecoder:
    def test_decode_returns_correct_shape(self):
        img = gradient_bgr(W, H)
        dec = BGRDecoder(W, H)
        out = dec.decode(img.tobytes())
        assert out.shape == (H, W, 3)

    def test_decode_preserves_pixel_values(self):
        img = solid_bgr(W, H, (200, 100, 50))
        dec = BGRDecoder(W, H)
        out = dec.decode(img.tobytes())
        np.testing.assert_array_equal(out, img)


class TestRGBDecoder:
    def test_decode_swaps_channels(self):
        """RGBDecoder must convert R-G-B order to B-G-R order."""
        rgb = np.zeros((H, W, 3), dtype=np.uint8)
        rgb[:] = (255, 128, 0)  # R=255, G=128, B=0

        dec = RGBDecoder(W, H)
        bgr = dec.decode(rgb.tobytes())

        # After R↔B swap: B=255, G=128, R=0
        assert bgr[0, 0, 0] == 255  # B channel
        assert bgr[0, 0, 2] == 0    # R channel


class TestGrayDecoder:
    def test_decode_returns_3_channel_bgr(self):
        gray = np.full((H, W, 1), 128, dtype=np.uint8)
        dec = GrayDecoder(W, H)
        out = dec.decode(gray.tobytes())
        assert out.shape == (H, W, 3)
        assert out[0, 0, 0] == 128  # B = G = R for gray


# ---------------------------------------------------------------------------
# Bayer decoders
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", [
    PixelFormat.BAYER_BGGR,
    PixelFormat.BAYER_GBRG,
    PixelFormat.BAYER_GRBG,
    PixelFormat.BAYER_RGGB,
])
def test_bayer_decoder_produces_correct_shape(fmt):
    bayer = np.random.randint(0, 256, (H, W, 1), dtype=np.uint8)
    decoder_cls = get_decoder(fmt)
    dec = decoder_cls(W, H)
    out = dec.decode(bayer.tobytes())
    assert out.shape == (H, W, 3), f"Bayer {fmt.name} produced wrong shape"


# ---------------------------------------------------------------------------
# MJPEG round-trip
# ---------------------------------------------------------------------------

class TestMJPEGRoundTrip:
    def test_encode_produces_valid_jpeg_bytes(self):
        enc = MJPEGEncoder(params=[cv2.IMWRITE_JPEG_QUALITY, 90])
        img = gradient_bgr(W, H)
        data = enc.encode(img)
        assert isinstance(data, bytes)
        # JPEG magic bytes: FF D8
        assert data[:2] == b"\xff\xd8"

    def test_decode_after_encode_returns_same_shape(self):
        enc = MJPEGEncoder(params=[cv2.IMWRITE_JPEG_QUALITY, 90])
        dec = MJPEGDecoder(W, H)

        img = gradient_bgr(W, H)
        data = enc.encode(img)
        recovered = dec.decode(data)

        assert recovered.shape == img.shape

    def test_encode_decode_pixel_close_to_original(self):
        """JPEG is lossy; allow a generous per-pixel tolerance."""
        enc = MJPEGEncoder(params=[cv2.IMWRITE_JPEG_QUALITY, 95])
        dec = MJPEGDecoder(W, H)

        img = solid_bgr(W, H, (200, 100, 50))
        recovered = dec.decode(enc.encode(img))

        diff = np.abs(img.astype(int) - recovered.astype(int))
        assert diff.max() < 20, f"Max pixel difference {diff.max()} too large"


class TestDownsamplingMJPEGEncoder:
    def test_scale_1_preserves_dimensions(self):
        enc = DownsamplingMJPEGEncoder(scale=1.0, params=[cv2.IMWRITE_JPEG_QUALITY, 75])
        img = gradient_bgr(W, H)
        data = enc.encode(img)
        recovered = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        assert recovered.shape == img.shape

    def test_scale_half_halves_dimensions(self):
        enc = DownsamplingMJPEGEncoder(scale=0.5, params=[cv2.IMWRITE_JPEG_QUALITY, 75])
        img = gradient_bgr(W, H)
        data = enc.encode(img)
        recovered = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        assert recovered.shape == (H // 2, W // 2, 3)


# ---------------------------------------------------------------------------
# UnsupportedPixelFormatError
# ---------------------------------------------------------------------------

def test_get_decoder_raises_for_unsupported_format():
    with pytest.raises(UnsupportedPixelFormatError):
        get_decoder(PixelFormat.INVALID)


# ---------------------------------------------------------------------------
# Fix 5: Decoder cache in ImageMessageToJPEGHandler
# ---------------------------------------------------------------------------

class TestDecoderCache:
    def test_same_geometry_returns_same_decoder_instance(self, image_handler):
        dec_a = image_handler._get_decoder(PixelFormat.BGR.value, W, H)
        dec_b = image_handler._get_decoder(PixelFormat.BGR.value, W, H)
        assert dec_a is dec_b, "Decoder should be reused for the same (format, w, h)"

    def test_different_size_returns_different_instance(self, image_handler):
        dec_small = image_handler._get_decoder(PixelFormat.BGR.value, W, H)
        dec_large = image_handler._get_decoder(PixelFormat.BGR.value, W * 2, H * 2)
        assert dec_small is not dec_large

    def test_different_format_returns_different_instance(self, image_handler):
        dec_bgr = image_handler._get_decoder(PixelFormat.BGR.value, W, H)
        dec_gray = image_handler._get_decoder(PixelFormat.GRAY.value, W, H)
        assert dec_bgr is not dec_gray

    def test_cache_grows_only_for_new_geometries(self, image_handler):
        image_handler._get_decoder(PixelFormat.BGR.value, 4, 4)
        image_handler._get_decoder(PixelFormat.BGR.value, 8, 8)
        image_handler._get_decoder(PixelFormat.BGR.value, 4, 4)  # hit
        assert len(image_handler._decoder_cache) == 2
