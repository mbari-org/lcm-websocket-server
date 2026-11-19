"""
Tests for LCM -> JSON encoding (stdlib and orjson paths).
"""

import json
import math

import pytest
from stdlcm import header_t

from lcm_websocket_server.lib.lcm_utils import types as T


class Point:
    """Minimal lcm-gen-like type."""

    __slots__ = ["x", "y"]

    def __init__(self, x=0.0, y=0.0):
        self.x, self.y = x, y

    @staticmethod
    def _get_packed_fingerprint():
        return b"\x00" * 8


class Message:
    __slots__ = ["header", "values", "ints", "points", "raw", "matrix", "flag", "name", "scalar"]  # noqa: RUF023 - lcm-gen field order

    @staticmethod
    def _get_packed_fingerprint():
        return b"\x01" * 8


def make_message(scalar=1.5, values=(1.0, 2.0)):
    m = Message()
    m.header = header_t()
    m.header.frame_id = "base"
    m.values = list(values)
    m.ints = [1, 2, 3]
    m.points = [Point(1.0, 2.0), Point(float("nan"), 3.0)]
    m.raw = b"\x00\xff"
    m.matrix = [[1.0, float("inf")], [3.0, 4.0]]
    m.flag = True
    m.name = "n"
    m.scalar = scalar
    return m


EXPECTED_EVENT = {
    "header": {"sequence": 0, "timestamp": 0, "frame_id": "base"},
    "values": [1.0, None, 3.0],
    "ints": [1, 2, 3],
    "points": [{"x": 1.0, "y": 2.0}, {"x": None, "y": 3.0}],
    "raw": "00ff",
    "matrix": [[1.0, None], [3.0, 4.0]],
    "flag": True,
    "name": "n",
    "scalar": None,
}


@pytest.fixture(params=["stdlib", "orjson"])
def backend(request, monkeypatch):
    if request.param == "stdlib":
        monkeypatch.setattr(T, "orjson", None)
    elif T.orjson is None:
        pytest.skip("orjson not installed")
    return request.param


class TestEncodeValue:
    def test_non_finite_floats_become_none(self):
        assert T.encode_value(float("nan")) is None
        assert T.encode_value(float("inf")) is None
        assert T.encode_value([1.0, float("-inf")]) == [1.0, None]

    def test_finite_float_list_returned_unchanged(self):
        values = [0.5] * 100
        assert T.encode_value(values) is values

    def test_bytes_become_hex(self):
        assert T.encode_value(b"\x01\xab") == "01ab"

    def test_lcm_struct_becomes_dict(self):
        assert T.encode_value(Point(1.0, 2.0)) == {"x": 1.0, "y": 2.0}

    def test_full_message(self):
        assert T.encode_event_dict(make_message(scalar=float("nan"), values=(1.0, float("nan"), 3.0))) == EXPECTED_EVENT


class TestEncodeEventJsonBytes:
    def test_backends_produce_same_document(self, backend):
        msg = make_message(scalar=float("nan"), values=(1.0, float("nan"), 3.0))
        out = T.encode_event_json_bytes("CH", "ab" * 8, msg)
        assert isinstance(out, bytes)
        assert json.loads(out) == {"channel": "CH", "fingerprint": "ab" * 8, "event": EXPECTED_EVENT}

    def test_output_is_strict_json(self, backend):
        """No NaN/Infinity literals, which browsers' JSON.parse rejects."""
        out = T.encode_event_json_bytes("CH", "00", make_message(scalar=math.inf))
        json.loads(out, parse_constant=lambda c: pytest.fail(f"non-standard constant {c}"))

    def test_none_event(self, backend):
        assert json.loads(T.encode_event_json_bytes("CH", "00", None))["event"] == {}
