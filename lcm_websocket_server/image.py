"""
Image encoding and decoding utilities.
"""


from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional, Sequence, Type, TypeVar

import cv2
import numpy as np


class ImageDecoder(ABC):
    """
    Abstract class for image decoders.
    """

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height

    @abstractmethod
    def decode(self, data: bytes) -> np.ndarray:
        """
        Decode the image data from a byte array into 8-bit BGR.

        Args:
            data: The image data to decode.

        Returns:
            The decoded image.
        """
        raise NotImplementedError


class ImageEncoder(ABC):
    """
    Abstract class for image encoders.
    """

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height

    @abstractmethod
    def encode(self, image: np.ndarray) -> bytes:
        """
        Encode the image from 8-bit BGR into a byte array.

        Args:
            image: The image to encode.

        Returns:
            The encoded image data.
        """
        raise NotImplementedError


ImageEncoderClass = TypeVar("ImageEncoderClass", bound=ImageEncoder)
ImageDecoderClass = TypeVar("ImageDecoderClass", bound=ImageDecoder)


class PixelFormat(Enum):
    """
    Enumeration of known pixel formats.
    """

    UYVY = 1498831189
    YUYV = 1448695129
    IYU1 = 827677001
    IYU2 = 844454217
    YUV420 = 842093913
    YUV411P = 1345401140
    I420 = 808596553
    NV12 = 842094158
    GRAY = 1497715271
    RGB = 859981650
    BGR = 861030210
    RGBA = 876758866
    BGRA = 877807426
    BAYER_BGGR = 825770306
    BAYER_GBRG = 844650584
    BAYER_GRBG = 861427800
    BAYER_RGGB = 878205016
    BE_BAYER16_BGGR = 826360386
    BE_BAYER16_GBRG = 843137602
    BE_BAYER16_GRBG = 859914818
    BE_BAYER16_RGGB = 876692034
    LE_BAYER16_BGGR = 826360396
    LE_BAYER16_GBRG = 843137612
    LE_BAYER16_GRBG = 859914828
    LE_BAYER16_RGGB = 876692044
    MJPEG = 1196444237
    BE_GRAY16 = 357
    LE_GRAY16 = 909199180
    BE_RGB16 = 358
    LE_RGB16 = 1279412050
    BE_SIGNED_GRAY16 = 359
    BE_SIGNED_RGB16 = 360
    FLOAT_GRAY32 = 842221382
    INVALID = -2
    ANY = -1


_ENCODER_MAP = {}
_DECODER_MAP = {}


def _register(
    format: PixelFormat,
    *,
    decoder: Type[ImageDecoderClass],
    encoder: Optional[Type[ImageEncoderClass]] = None,
):
    global _ENCODER_MAP, _DECODER_MAP
    _ENCODER_MAP[format] = encoder
    _DECODER_MAP[format] = decoder


class UnsupportedPixelFormatError(Exception):
    """
    Exception raised when an unsupported pixel format is encountered.
    """

    def __init__(self, format: PixelFormat):
        super().__init__(f"Unsupported pixel format: {format} ({format.name})")


def get_encoder(format: PixelFormat) -> Type[ImageEncoderClass]:
    """
    Get the image encoder class for the given pixel format.

    Returns:
        The image encoder class.

    Raises:
        UnsupportedPixelFormatError: If the pixel format is not supported.
    """
    try:
        return _ENCODER_MAP[format]
    except KeyError:
        raise UnsupportedPixelFormatError(format)


def get_decoder(format: PixelFormat) -> Type[ImageDecoderClass]:
    """
    Get the image decoder class for the given pixel format.

    Returns:
        The image decoder class.

    Raises:
        UnsupportedPixelFormatError: If the pixel format is not supported.
    """
    try:
        return _DECODER_MAP[format]
    except KeyError:
        raise UnsupportedPixelFormatError(format)


###############################################################################


class BGREncoder(ImageEncoder):
    def encode(self, image: np.ndarray) -> bytes:
        return image.tobytes()


class BGRDecoder(ImageDecoder):
    def decode(self, data: bytes) -> np.ndarray:
        return np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 3)


class RGBEncoder(ImageEncoder):
    def encode(self, image: np.ndarray) -> bytes:
        return image[:, :, ::-1].tobytes()


class RGBDecoder(ImageDecoder):
    def decode(self, data: bytes) -> np.ndarray:
        return np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 3)[
            :, ::-1
        ]


class GrayEncoder(ImageEncoder):
    def encode(self, image: np.ndarray) -> bytes:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR).tobytes()


class GrayDecoder(ImageDecoder):
    def decode(self, data: bytes) -> np.ndarray:
        return cv2.cvtColor(
            np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 1),
            cv2.COLOR_BGR2GRAY,
        )


class BayerBGGRDecoder(ImageDecoder):
    def decode(self, data: bytes) -> np.ndarray:
        return cv2.cvtColor(
            np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 1),
            cv2.COLOR_BAYER_RG2BGR,
        )


class BayerGBRGDecoder(ImageDecoder):
    def decode(self, data: bytes) -> np.ndarray:
        return cv2.cvtColor(
            np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 1),
            cv2.COLOR_BAYER_GR2BGR,
        )


class BayerGRBGDecoder(ImageDecoder):
    def decode(self, data: bytes) -> np.ndarray:
        return cv2.cvtColor(
            np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 1),
            cv2.COLOR_BAYER_GB2BGR,
        )


class BayerRGGBDecoder(ImageDecoder):
    def decode(self, data: bytes) -> np.ndarray:
        return cv2.cvtColor(
            np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 1),
            cv2.COLOR_BAYER_BG2BGR,
        )


class MJPEGEncoder(ImageEncoder):
    def __init__(self, width: int, height: int, params: Optional[Sequence[int]] = None):
        super().__init__(width, height)
        self.params = params or [cv2.IMWRITE_JPEG_QUALITY, 90]

    def encode(self, image: np.ndarray) -> bytes:
        return cv2.imencode(".jpg", image, params=self.params)[1].tobytes()


class MJPEGDecoder(ImageDecoder):
    def decode(self, data: bytes) -> np.ndarray:
        return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


_register(PixelFormat.BGR, encoder=BGREncoder, decoder=BGRDecoder)
_register(PixelFormat.RGB, encoder=RGBEncoder, decoder=RGBDecoder)
_register(PixelFormat.GRAY, encoder=GrayEncoder, decoder=GrayDecoder)
_register(PixelFormat.BAYER_BGGR, decoder=BayerBGGRDecoder)
_register(PixelFormat.BAYER_GBRG, decoder=BayerGBRGDecoder)
_register(PixelFormat.BAYER_GRBG, decoder=BayerGRBGDecoder)
_register(PixelFormat.BAYER_RGGB, decoder=BayerRGGBDecoder)
_register(PixelFormat.MJPEG, encoder=MJPEGEncoder, decoder=MJPEGDecoder)
