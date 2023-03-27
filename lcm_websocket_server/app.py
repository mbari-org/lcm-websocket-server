import asyncio
import queue

from websockets import serve

from lcm_websocket_server.lcmpubsub import LCMObserver, LCMRepublisher
from lcm_websocket_server.lcmtypes import LCMTypeRegistry, encode_event_json


async def run_server(lcm_type_registry: LCMTypeRegistry, lcm_republisher: LCMRepublisher):
    """
    Main coroutine for the LCM WebSocket server.
    """
    
    async def republish_lcm(websocket, path):
        """
        Connection handler (coroutine) for the LCM WebSocket server.
        
        Args:
            websocket: The WebSocket connection
            path: The path of the WebSocket connection
        """
        ip, port = websocket.remote_address
        print(f"Client connected from {ip}:{port} at {path}")

        # Create an LCM observer for this client
        observer = LCMObserver()
        
        # Subscribe the observer to the LCM republisher
        lcm_republisher.subscribe(observer)

        # Send all events to the client
        while True:
            # Block until an event is received
            try:
                channel, data = observer.get(block=False)
            except queue.Empty:
                if websocket.closed:  # Check if the client disconnected
                    print(f"Client at {ip}:{port} disconnected")
                    break
                await asyncio.sleep(0.1)
                continue
            
            # Decode the event
            event = lcm_type_registry.decode(data)
            
            # Encode the event as JSON
            event_json = encode_event_json(event)
            
            # Send the event to the client
            await websocket.send(event_json)
        
        # Unsubscribe the observer from the LCM republisher
        lcm_republisher.unsubscribe(observer)
        
        print(f"Connection to {ip}:{port} finished cleanly")
    
    print("Starting LCM WebSocket server...")
    async with serve(republish_lcm, "localhost", 8765):
        print("LCM WebSocket server started")
        await asyncio.Future()


def main():
    """
    Main function for the LCM WebSocket server.
    """
    # Initialize the LCM type registry
    registry = LCMTypeRegistry()
    registry.discover("compas_lcmtypes")
    print("Discovered LCM types:")
    for lcm_type in registry.types:
        print(f"  {lcm_type.__name__}")

    # Create an LCM republisher for all channels
    republisher = LCMRepublisher(".*")
    republisher.start()
    
    # Run the server coroutine
    asyncio.run(run_server(registry, republisher))


if __name__ == "__main__":
    asyncio.run(main())
