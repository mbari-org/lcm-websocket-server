"""
Tests for JSONHandler.
"""

import inspect
import json

from stdlcm import header_t


from lcm_websocket_server.lib.handler import TextPayload
from lcm_websocket_server.lib.lcm_utils.pubsub import LCMObserver, LCMRepublisher

from tests.conftest import make_header_t


# ---------------------------------------------------------------------------
# Synchronous interface (Fix 2)
# ---------------------------------------------------------------------------

class TestJSONHandlerInterface:
    def test_handle_is_not_a_coroutine_function(self, json_handler):
        """handle() must be a plain def so asyncio.to_thread() can run it."""
        assert not inspect.iscoroutinefunction(json_handler.handle)

    def test_handle_returns_text_payload(self, json_handler):
        result = json_handler.handle("TEST", make_header_t())
        assert isinstance(result, TextPayload)

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


# ---------------------------------------------------------------------------
# Data identity (relied on by the server EncodeCache)
# ---------------------------------------------------------------------------

class TestDataIdentity:
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

        items = [obs.get_nowait() for obs in observers]
        data_objects = [item[1] for item in items]

        first = data_objects[0]
        for other in data_objects[1:]:
            assert other is first, "All observers must share the same data object"
