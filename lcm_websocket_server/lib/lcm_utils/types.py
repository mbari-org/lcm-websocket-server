"""
Utilities for working with LCM types and raw data.

JSON conventions: LCM structs become objects keyed by field name, `bytes`
become hex strings, and non-finite floats (NaN, +/-inf) become null so the
output is always valid JSON.
"""
import json
import math
from collections.abc import Iterable
from typing import Any

try:
    import orjson
except ImportError:  # optional extra
    orjson = None

# type -> tuple of field names for LCM types, or None for non-LCM types
_LCM_SLOTS: dict[type, tuple[str, ...] | None] = {}


def _lcm_slots(tp: type) -> tuple[str, ...] | None:
    """Field names of an lcm-gen class, cached per type. Cheaper than a Protocol isinstance check."""
    try:
        return _LCM_SLOTS[tp]
    except KeyError:
        slots = None
        if hasattr(tp, "_get_packed_fingerprint") and hasattr(tp, "__slots__"):
            slots = tuple(tp.__slots__)
        _LCM_SLOTS[tp] = slots
        return slots


def find_lcm_type(types: Iterable[type], name: str) -> type:
    """
    Find a type by its LCM name (e.g. "senlcm.image_t"), regardless of any
    Python package prefix it was generated with (e.g. compas_lcmtypes.senlcm.image_t).

    Raises:
        LookupError: If no type, or more than one type, matches.
    """
    matches = [t for t in types if t.__module__ == name or t.__module__.endswith("." + name)]
    if len(matches) != 1:
        found = ", ".join(f"{t.__module__}" for t in matches) or "none"
        raise LookupError(f"Expected exactly one LCM type named {name!r}, found: {found}")
    return matches[0]


def _encode_sequence(values) -> Any:
    if not values:
        return []
    first = type(values[0])
    # LCM arrays are homogeneous, so primitive arrays can skip per-element dispatch.
    if first is float:
        if all(map(math.isfinite, values)):
            return values
        return [v if math.isfinite(v) else None for v in values]
    if first is int or first is str or first is bool:
        return values
    return [encode_value(v) for v in values]


def encode_value(value: Any) -> Any:
    """
    Encode a value into JSON-serializable form.

    Args:
        value: Value to encode.

    Returns:
        Encoded value.
    """
    tp = type(value)
    if tp is float:
        return value if math.isfinite(value) else None
    if tp is int or tp is str or tp is bool or value is None:
        return value
    if tp is list or tp is tuple:
        return _encode_sequence(value)
    if tp is bytes:
        return value.hex()
    if tp is dict:
        return {key: encode_value(v) for key, v in value.items()}
    slots = _lcm_slots(tp)
    if slots is not None:
        return {slot: encode_value(getattr(value, slot)) for slot in slots}
    return value


def encode_event_dict(event: object) -> dict:
    """
    Encode an LCM event as a dictionary.

    Args:
        event: LCM event to encode.

    Returns:
        Dictionary representation of the event.
    """
    slots = _lcm_slots(type(event)) or tuple(type(event).__slots__)
    return {slot: encode_value(getattr(event, slot)) for slot in slots}


def encode_event_json(channel: str, fingerprint: str, event: object, **kwargs) -> str:
    """
    Encode an LCM event as a JSON string using the standard library.

    Args:
        channel: Channel of the event.
        fingerprint: Fingerprint of the event.
        event: LCM event to encode.
        **kwargs: Keyword arguments to pass to json.dumps.

    Returns:
        JSON string representation of the event.
    """
    return json.dumps({
        "channel": channel,
        "fingerprint": fingerprint,
        "event": encode_event_dict(event) if event is not None else {}
    }, **kwargs)


def _orjson_default(value: Any) -> Any:
    """orjson fallback for types it does not serialize natively."""
    if type(value) is bytes:
        return value.hex()
    slots = _lcm_slots(type(value))
    if slots is not None:
        return {slot: getattr(value, slot) for slot in slots}
    raise TypeError(f"Type is not JSON serializable: {type(value).__name__}")


def encode_event_json_bytes(channel: str, fingerprint: str, event: object) -> bytes:
    """
    Encode an LCM event as compact UTF-8 JSON.

    Uses orjson when installed (it walks arrays in C and maps non-finite
    floats to null natively), otherwise the standard library. Both produce
    equivalent JSON.
    """
    if orjson is not None:
        return orjson.dumps(
            {"channel": channel, "fingerprint": fingerprint, "event": event if event is not None else {}},
            default=_orjson_default,
        )
    return encode_event_json(channel, fingerprint, event, separators=(",", ":")).encode()
