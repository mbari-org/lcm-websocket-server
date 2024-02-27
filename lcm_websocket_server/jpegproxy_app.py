"""
LCM WebSocket JPEG proxy.
"""
from lcm_websocket_server.log import get_logger
logger = get_logger(__name__)

# Ensure LCM installed
try:
    import lcm
except ImportError:
    logger.critical("LCM Python module is not installed or could not be found, exiting.")
    exit(1)

# Ensure required extras installed
try:
    import numpy as np
    import cv2
except ImportError:
    logger.critical("Required extra (imagetrans) is not installed or could not be found, exiting.\nInstall with 'pip install lcm-websocket-server[imagetrans]'")
    exit(1)

import argparse
import asyncio
import queue
from signal import SIGINT, SIGTERM
from urllib.parse import unquote

from websockets import serve
from compas_lcmtypes.senlcm import image_t

from lcm_websocket_server.lcmpubsub import LCMObserver, LCMRepublisher
from lcm_websocket_server.image import MJPEGEncoder, PixelFormat, UnsupportedPixelFormatError, get_decoder


async def run_server(lcm_republisher: LCMRepublisher, host: str, port: int):
    """
    Main coroutine for the LCM WebSocket JPEG proxy.
    
    Args:
        lcm_republisher: LCM republisher
        host: Host to bind to
        port: Port to bind to
    """
    
    async def republish_lcm(websocket, path):
        """
        Connection handler (coroutine) for the LCM WebSocket JPEG proxy.
        
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
            try:
                image_event = image_t.decode(data)
            except Exception as e:
                logger.warning(f"Failed to decode image_t event from channel {channel}: {e}")
                continue
            
            # Decode the image from the event
            pixel_format = image_event.pixelformat
            try:
                image_decoder = get_decoder(PixelFormat(pixel_format))
            except UnsupportedPixelFormatError as e:
                logger.warning(e)
                continue
            try:
                image = image_decoder(image_event.width, image_event.height).decode(image_event.data)
            except Exception as e:
                logger.warning(f"Failed to decode image from event: {e}")
                continue
            
            # Encode the image as JPEG
            jpeg_encoder = MJPEGEncoder(image_event.width, image_event.height)
            try:
                jpeg_bytes = jpeg_encoder.encode(image)
            except Exception as e:
                logger.warning(f"Failed to encode image as JPEG: {e}")
                continue
            
            # Send the JPEG bytes to the client
            try:
                await websocket.send(jpeg_bytes)
            except Exception as e:
                logger.warning(f"Failed to send JPEG to client at {ip}:{port}: {e}")

            # Signal that the observer has finished consuming the event
            observer.task_done()
        
        # Unsubscribe the observer from the LCM republisher
        lcm_republisher.unsubscribe(observer)
        
        logger.info(f"Connection to {ip}:{port} finished cleanly")
    
    # Start the server
    logger.info("Starting LCM WebSocket JPEG proxy...")
    async with serve(republish_lcm, host, port) as server:
        logger.info(f"LCM WebSocket JPEG proxy started at ws://{host}:{port}")
        
        # Close server on SIGINT and SIGTERM
        for signal in [SIGINT, SIGTERM]:
            asyncio.get_event_loop().add_signal_handler(signal, server.close)
        
        # Wait for the server to close
        await server.wait_closed()
        logger.info("LCM WebSocket JPEG proxy closed")
        
        # Stop the LCM republisher
        lcm_republisher.stop()


def main():
    """
    Main function for the LCM WebSocket JPEG proxy.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=str, default="localhost", help="The host to listen on. Default: %(default)s")
    parser.add_argument("--port", type=int, default=8766, help="The port to listen on")
    parser.add_argument("--channel", type=str, default=".*", help="The LCM channel pattern to subscribe to. Use '.*' to subscribe to all channels.")
    args = parser.parse_args()
    
    host = args.host
    port = args.port
    channel = args.channel

    # Create an LCM republisher for the specified channel
    republisher = LCMRepublisher(channel)
    republisher.start()
    
    # Run the server coroutine
    asyncio.run(run_server(republisher, host, port))


if __name__ == "__main__":
    asyncio.run(main())
