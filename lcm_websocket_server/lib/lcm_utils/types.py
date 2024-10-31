"""
Utilities for working with LCM types and raw data.
"""
import json
from typing import Any

from lcmutils import LCMType


def encode_value(value: Any) -> Any:
    """
    Encode a value. 
    
    If the value is an LCM event, encode it as a dictionary. If the value is a list, encode each element. Otherwise, return the value.
    
    Args:
        value: Value to encode.
    
    Returns:
        Encoded value.
    """
    if isinstance(value, LCMType):
        return encode_event_dict(value)
    elif isinstance(value, list):
        return list(map(encode_value, value))
    elif isinstance(value, dict):
        return {key: encode_value(value) for key, value in value.items()}
    elif isinstance(value, bytes):
        return value.hex()
    elif isinstance(value, float) and value != value:
        return None
    return value


def encode_event_dict(event: object) -> dict:
    """
    Encode an LCM event as a dictionary.
    
    Args:
        event: LCM event to encode.
    
    Returns:
        Dictionary representation of the event.
    """
    event_type = type(event)
    event_dict = {}
    
    for slot in event_type.__slots__:
        value = getattr(event, slot)
        value = encode_value(value)
        
        event_dict[slot] = value

    return event_dict


def encode_event_json(channel: str, fingerprint: str, event: object, **kwargs) -> str:
    """
    Encode an LCM event as a JSON string.
    
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
