"""
Utilities for working with LCM types and raw data.
"""
import json
import pkgutil
from importlib import import_module
from typing import Optional


class LCMTypeRegistry:
    """
    Registry of LCM types. Key-value pairs are stored as (fingerprint, class).
    """

    def __init__(self, *classes):
        self._registry = {}
        
        for cls in classes:
            self.register(cls)

    def register(self, cls):
        """
        Register an LCM class.
        
        Args:
            cls: LCM class to register.
        """
        if not hasattr(cls, "_get_packed_fingerprint"):
            raise ValueError("Class must have a _get_packed_fingerprint method")
        
        self._registry[cls._get_packed_fingerprint()] = cls

    @property
    def types(self):
        """
        Get the list of registered LCM types.
        """
        return list(self._registry.values())

    def clear(self):
        """
        Clear the registry.
        """
        self._registry.clear()

    def get(self, fingerprint) -> Optional[type]:
        """
        Get the LCM class associated with a fingerprint.
        
        Args:
            fingerprint: Fingerprint to look up.
        
        Returns:
            LCM class associated with the fingerprint, or None if no class is registered for the fingerprint.
        """
        return self._registry.get(fingerprint, None)
    
    def decode(self, event: bytes) -> Optional[object]:
        """
        Decode an LCM event into an object, if its class is registered.
        
        Args:
            event: LCM event to decode.
        
        Returns:
            Decoded object, or None if the class is not registered.
        """
        fingerprint = event[:8]
        cls = self.get(fingerprint)
        
        if cls is None:
            return None
        
        return cls.decode(event)
    
    def discover(self, *package_name: str):
        """
        Discover LCM classes in a package.
        
        Args:
            *package_name: Package to discover.
        """
        packages = []
        for package_name in package_name:
            try:
                package = import_module(package_name)
            except ModuleNotFoundError:
                print(f"Package {package_name} not found, skipping.")
                continue
            
            packages.append(package)
        
        for package in packages:
            for loader, module_name, is_pkg in pkgutil.walk_packages(package.__path__):
                module = loader.find_module(module_name).load_module(module_name)
                
                for name in dir(module):
                    cls = getattr(module, name)
                    
                    if hasattr(cls, "_get_packed_fingerprint"):
                        self.register(cls)


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
    
    for slot, dimension in zip(event_type.__slots__, event_type.__dimensions__):
        value = getattr(event, slot)
        
        if dimension is None:
            event_dict[slot] = value
        else:
            event_dict[slot] = list(map(encode_event_json, value))

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
        "event": encode_event_dict(event)
    }, **kwargs)
