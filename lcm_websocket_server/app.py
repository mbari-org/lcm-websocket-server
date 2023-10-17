"""
LCM WebSocket server.
"""
from lcm_websocket_server.log import get_logger
logger = get_logger(__name__)

# Ensure LCM installed
try:
    import lcm
except ImportError:
    logger.critical("LCM Python module is not installed or could not be found, exiting.")
    exit(1)

import argparse
import asyncio
import queue
from signal import SIGINT, SIGTERM
from urllib.parse import unquote

from websockets import serve

from lcm_websocket_server.lcmpubsub import LCMObserver, LCMRepublisher
from lcm_websocket_server.lcmtypes import LCMTypeRegistry, encode_event_json


async def run_server(lcm_type_registry: LCMTypeRegistry, lcm_republisher: LCMRepublisher, host: str, port: int):
    """
    Main coroutine for the LCM WebSocket server.
    
    Args:
        lcm_type_registry: LCM type registry
        lcm_republisher: LCM republisher
        host: Host to bind to
        port: Port to bind to
    """
    
    async def republish_lcm(websocket, path):
        """
        Connection handler (coroutine) for the LCM WebSocket server.
        
        Args:
            websocket: The WebSocket connection
            path: The path of the WebSocket connection
        """
        ip, port = websocket.remote_address[:2]
        logger.info(f"Client connected from {ip}:{port} at {path}")

        # Extract channel regex
        channel_regex = unquote(path.lstrip('/'))
        if not channel_regex:  # empty path -> subscribe to all channels
            channel_regex = '.*'

        # Create an LCM observer for this client
        observer = LCMObserver(channel_regex=channel_regex)
        logger.debug(f"Created LCM observer with channel regex {channel_regex}")
        
        # Subscribe the observer to the LCM republisher
        lcm_republisher.subscribe(observer)

        # Send all events to the client
        while True:
            # Block until an event is received
            try:
                channel, data = observer.get(block=False)
            except queue.Empty:
                if websocket.closed:  # Check if the client disconnected
                    logger.info(f"Client at {ip}:{port} disconnected")
                    break
                await asyncio.sleep(0.1)
                continue
            
            # Decode the event
            event = lcm_type_registry.decode(data)
            
            # Get fingerprint hex
            fingerprint = data[:8]
            fingerprint_hex = fingerprint.hex()
            
            # Encode the event as JSON
            event_json = encode_event_json(channel, fingerprint_hex, event)
            
            # Send the event to the client
            await websocket.send(event_json)

            # Signal that the observer has finished consuming the event
            observer.task_done()
        
        # Unsubscribe the observer from the LCM republisher
        lcm_republisher.unsubscribe(observer)
        
        logger.info(f"Connection to {ip}:{port} finished cleanly")
    
    # Start the server
    logger.info("Starting LCM WebSocket server...")
    async with serve(republish_lcm, host, port) as server:
        logger.info(f"LCM WebSocket server started at ws://{host}:{port}")
        
        # Close server on SIGINT and SIGTERM
        for signal in [SIGINT, SIGTERM]:
            asyncio.get_event_loop().add_signal_handler(signal, server.close)
        
        # Wait for the server to close
        await server.wait_closed()
        logger.info("LCM WebSocket server closed")
        
        # Stop the LCM republisher
        lcm_republisher.stop()


def main():
    """
    Main function for the LCM WebSocket server.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=str, default="localhost", help="The host to listen on. Default: %(default)s")
    parser.add_argument("--port", type=int, default=8765, help="The port to listen on")
    parser.add_argument("--channel", type=str, default=".*", help="The LCM channel to subscribe to. Use '.*' to subscribe to all channels.")
    parser.add_argument("lcm_packages", type=str, help="The LCM packages to discover LCM types from. Separate multiple packages with a comma.")
    args = parser.parse_args()
    
    host = args.host
    port = args.port
    channel = args.channel
    lcm_packages = args.lcm_packages.split(",")
    
    # Initialize the LCM type registry
    registry = LCMTypeRegistry()
    registry.discover(*lcm_packages)
    if not registry.types:
        logger.critical("No LCM types discovered, exiting.")
        return
    logger.info(f"Discovered LCM types: {', '.join([t.__name__ for t in registry.types])}")

    # Create an LCM republisher for the specified channel
    republisher = LCMRepublisher(channel)
    republisher.start()
    
    # Run the server coroutine
    asyncio.run(run_server(registry, republisher, host, port))


if __name__ == "__main__":
    asyncio.run(main())
