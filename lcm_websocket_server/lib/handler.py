from abc import ABC, abstractmethod
from typing import Optional, Union


class LCMWebSocketHandler(ABC):
    """
    LCM WebSocket handler interface.
    """
    
    @abstractmethod
    async def handle(self, channel: str, data: bytes) -> Optional[Union[str, bytes, bytearray, memoryview]]:
        """
        Handle an LCM message.
        
        Args:
            channel: LCM channel
            data: LCM message data
        
        Returns:
            Response to be sent to the WebSocket client, or None to not send a response.
        """
        raise NotImplementedError

        