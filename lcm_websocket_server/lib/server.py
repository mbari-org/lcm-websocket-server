import asyncio
import queue
from urllib.parse import unquote

from websockets.server import WebSocketServerProtocol, serve

from lcm_websocket_server.lib.handler import LCMWebSocketHandler
from lcm_websocket_server.lib.lcm_utils.pubsub import LCMObserver, LCMRepublisher
from lcm_websocket_server.lib.log import LogMixin


class LCMWebSocketServer(LogMixin):
    """
    LCM-WebSocket server. Subscribes to LCM and publishes data to WebSocket clients.
    
    Delegates LCM message handling to an LCMWebSocketHandler.
    """
    
    def __init__(self, host: str, port: int, handler: LCMWebSocketHandler, lcm_republisher: LCMRepublisher, empty_wait_seconds: float = 0.1):
        self._host = host
        self._port = port
        self._handler = handler
        self._lcm_republisher = lcm_republisher
        self._empty_wait_seconds = empty_wait_seconds
        
        self._server = None
    
    async def websocket_handler(self, websocket: WebSocketServerProtocol, path: str):
        """
        WebSocket handler coroutine.
        
        Args:
            websocket: The WebSocket connection
            path: The path of the WebSocket connection
        """
        client_host, client_port = websocket.remote_address[:2]
        self.logger.info(f"Client {websocket.id} connected from {client_host}:{client_port} at {path}")
        
        channel_regex = unquote(path.lstrip('/'))
        if not channel_regex:  # empty path -> subscribe to all channels
            channel_regex = '.*'
        
        # Subscribe to the LCM republisher
        observer = LCMObserver(channel_regex=channel_regex)
        self._lcm_republisher.subscribe(observer)
        
        try:
            while True:
                # Busy wait until a message is received
                try:
                    channel, data = observer.get(block=False)
                except queue.Empty:
                    if websocket.closed:
                        break
                    await asyncio.sleep(self._empty_wait_seconds)
                    continue
                
                # Handle the LCM message
                try:
                    response = self._handler.handle(channel, data)
                except Exception as e:
                    self.logger.error(f"Error during message handling: {e}")
                    continue
                
                # Send the response to the client (if any)
                if response is not None:
                    try:
                        await websocket.send(response)
                    except Exception as e:
                        self.logger.debug(f"Error while sending response to client {websocket.id}: {e}")
                        continue
                
                # Indicate that the message has been handled
                observer.task_done()
        except Exception as e:
            self.logger.error(f"Unexpected error in client {websocket.id}: {e}")
        
        self._lcm_republisher.unsubscribe(observer)
        self.logger.info(f"Client {websocket.id} disconnected")
    
    async def serve(self):
        """
        Run the server.
        """
        async with serve(self.websocket_handler, self._host, self._port) as self._server:
            await self._server.wait_closed()
    
    def close(self):
        """
        Close the server.
        """
        if self._server is not None:
            self._server.close()