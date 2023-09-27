"""
LCM WebSocket server.
"""
from lcm_websocket_server.log import get_logger, set_stream_handler_level
logger = get_logger(__name__)

# Ensure LCM installed
try:
    import lcm
except ImportError:
    logger.critical("LCM Python module is not installed or could not be found, exiting.")
    exit(1)

import argparse
import logging
import asyncio
import queue
from signal import SIGINT, SIGTERM

from websockets import serve

from lcm_websocket_server.lcmpubsub import LCMObserver, LCMRepublisher
from lcm_websocket_server.lcmtypes import LCMTypeRegistry, encode_event_json
from lcm_websocket_server.http_server import LCMWebSocketHTTPServer


async def run_server(lcm_type_registry: LCMTypeRegistry, lcm_republisher: LCMRepublisher, host: str, ws_port: int):
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

        # Create an LCM observer for this client
        observer = LCMObserver()
        
        # Subscribe the observer to the LCM republisher
        lcm_republisher.subscribe(observer)
        
        # Add the client to the HTTP server's client list
        ws_clients.append(websocket)

        # Send all events to the client
        try:
            while True:
                # Block until an event is received
                try:
                    channel, data = observer.get(block=False)
                except queue.Empty:
                    if websocket.closed:  # Check if the client disconnected
                        logger.info(f"WebSocket client at {ip}:{port} disconnected")
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
            
            logger.info(f"Connection to {ip}:{port} closed")
        
        except Exception as e:
            logger.error(f"Connection to {ip}:{port} closed with exception: {e}")
        
        finally:
            # Remove the client from the HTTP server's client list
            ws_clients.remove(websocket)
            
            # Unsubscribe the observer from the LCM republisher
            lcm_republisher.unsubscribe(observer)
            
    
    # Start the HTTP server daemon
    http_port = ws_port + 1
    ws_clients = []
    http_server = LCMWebSocketHTTPServer(host, http_port, ws_clients)
    http_server.start()
    
    # Start the WebSocket server
    logger.debug("Starting LCM WebSocket server...")
    async with serve(republish_lcm, host, ws_port) as server:
        logger.info(f"LCM WebSocket server started at ws://{host}:{ws_port}")
        
        # Close server on SIGINT and SIGTERM
        for signal in [SIGINT, SIGTERM]:
            asyncio.get_event_loop().add_signal_handler(signal, server.close)
        
        # Wait for the server to close
        await server.wait_closed()
        logger.debug("Shutting down LCM WebSocket server...")
        
        # Stop the LCM republisher
        lcm_republisher.stop()
        
        logger.info("LCM WebSocket server closed")
    
    # Stop the HTTP server daemon
    http_server.stop()


def main():
    """
    Main function for the LCM WebSocket server.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=str, default="localhost", help="The host to listen on. Default: %(default)s")
    parser.add_argument("--port", type=int, default=8765, help="The port to listen on")
    parser.add_argument("--channel", type=str, default=".*", help="The LCM channel to subscribe to. Use '.*' to subscribe to all channels.")
    parser.add_argument("-v", action="count", default=0, help="Increase verbosity. Use -v for INFO, -vv for DEBUG. Defaults to WARNING.")
    parser.add_argument("lcm_packages", type=str, help="The LCM packages to discover LCM types from. Separate multiple packages with a comma.")
    args = parser.parse_args()
    
    host = args.host
    port = args.port
    channel = args.channel
    verbosity = {0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG}.get(args.v, logging.DEBUG)
    lcm_packages = args.lcm_packages.split(",")
    
    # Set up logging
    set_stream_handler_level(verbosity)
    
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
