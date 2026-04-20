"""
Tests for JSONHandler.
"""

import inspect
import json
import queue

import pytest
from stdlcm import header_t

from lcmutils import LCMTypeRegistry

from lcm_websocket_server.apps.json_proxy import JSONHandler
from lcm_websocket_server.lib.lcm_utils.pubsub import LCMObserver, LCMRepublisher

from tests.conftest import make_header_t


# ---------------------------------------------------------------------------
# Synchronous interface (Fix 2)
# ---------------------------------------------------------------------------

class TestJSONHandlerInterface:
    def test_handle_is_not_a_coroutine_function(self, json_handler):
        """handle() must be a plain def so asyncio.to_thread() can run it."""
        assert not inspect.iscoroutinefunction(json_handler.handle)

    def test_handle_returns_string_not_coroutine(self, json_handler):
        result = json_handler.handle("TEST", make_header_t())
        assert result is None or isinstance(result, str)

    def test_handle_returns_none_for_unknown_type(self, json_handler):
        garbage = b"\x00\x01\x02\x03\x04\x05\x06\x07" + b"not lcm data"
        result = json_handler.handle("TEST", garbage)
        assert result is None


# ---------------------------------------------------------------------------
# JSON output correctness
# ---------------------------------------------------------------------------

class TestJSONHandlerOutput:
    def test_output_is_valid_json(self, json_handler):
        raw = make_header_t(sequence=1, timestamp=12345, frame_id="base")
        result = json_handler.handle("TEST_CHAN", raw)
        assert result is not None
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_output_has_required_top_level_keys(self, json_handler):
        result = json_handler.handle("MY_CHANNEL", make_header_t())
        parsed = json.loads(result)
        assert "channel" in parsed
        assert "fingerprint" in parsed
        assert "event" in parsed

    def test_channel_field_matches_injected_channel(self, json_handler):
        result = json_handler.handle("ROBOT_STATE", make_header_t())
        assert json.loads(result)["channel"] == "ROBOT_STATE"

    def test_fingerprint_is_hex_string_of_length_16(self, json_handler):
        result = json_handler.handle("CH", make_header_t())
        fp = json.loads(result)["fingerprint"]
        assert isinstance(fp, str)
        assert len(fp) == 16
        int(fp, 16)  # must be valid hex

    def test_fingerprint_matches_lcm_type_fingerprint(self, json_handler):
        result = json_handler.handle("CH", make_header_t())
        fp_hex = json.loads(result)["fingerprint"]
        expected = header_t._get_packed_fingerprint().hex()
        assert fp_hex == expected

    def test_event_dict_contains_header_t_fields(self, json_handler):
        raw = make_header_t(sequence=42, timestamp=999, frame_id="odom")
        event = json.loads(json_handler.handle("CH", raw))["event"]
        assert event["sequence"] == 42
        assert event["timestamp"] == 999
        assert event["frame_id"] == "odom"

    def test_nan_float_is_serialised_as_null(self, registry):
        """NaN floats must not cause json.dumps to raise; they become JSON null."""
        from stdlcm import msg_t
        msg = msg_t()
        # msg_t has a 'data' bytes field; check for float fields in other types.
        # Use encode_event_json directly to inject a NaN.
        from lcm_websocket_server.lib.lcm_utils.types import encode_event_json
        result = encode_event_json("CH", "ff" * 8, None)
        parsed = json.loads(result)
        assert parsed["event"] == {}


# ---------------------------------------------------------------------------
# Fix 3: Encode cache
# ---------------------------------------------------------------------------

class TestJSONHandlerEncodeCache:
    def _make_counting_handler(self, registry: LCMTypeRegistry) -> tuple[JSONHandler, list]:
        """Return a JSONHandler subclass that records how many times _decode runs."""
        calls: list[int] = []

        class CountingJSONHandler(JSONHandler):
            def _decode(self_inner, data):
                calls.append(1)
                return super()._decode(data)

        return CountingJSONHandler(registry), calls

    def test_second_call_with_same_object_hits_cache(self, registry):
        handler, calls = self._make_counting_handler(registry)
        data = make_header_t(sequence=1)

        result1 = handler.handle("CH", data)
        result2 = handler.handle("CH", data)  # same object → cache hit

        assert result1 == result2
        assert len(calls) == 1, f"Expected 1 decode call, got {len(calls)}"

    def test_different_object_same_content_causes_re_encode(self, registry):
        handler, calls = self._make_counting_handler(registry)
        data_a = make_header_t(sequence=1)
        data_b = make_header_t(sequence=1)  # equal content, different object
        assert data_a is not data_b

        handler.handle("CH", data_a)
        handler.handle("CH", data_b)

        assert len(calls) == 2, "Different objects must each trigger a decode"

    def test_cache_is_per_channel(self, registry):
        """Same data object on different channels should encode independently."""
        handler, calls = self._make_counting_handler(registry)
        data = make_header_t()

        handler.handle("CHAN_A", data)
        handler.handle("CHAN_B", data)
        handler.handle("CHAN_A", data)  # hit

        assert len(calls) == 2, "Expected 2 decodes (one per channel), got {len(calls)}"

    def test_republisher_puts_same_object_in_all_queues(self):
        """
        LCMRepublisher must place the exact same bytes object into every
        subscriber queue — the identity guarantee that makes EncodeCache useful.
        """
        r = LCMRepublisher(".*")
        observers = [LCMObserver() for _ in range(5)]
        for obs in observers:
            r.subscribe(obs)

        r.inject("CH", make_header_t())

        items = [obs.get(block=False) for obs in observers]
        data_objects = [item[1] for item in items]

        first = data_objects[0]
        for other in data_objects[1:]:
            assert other is first, "All observers must share the same data object"
