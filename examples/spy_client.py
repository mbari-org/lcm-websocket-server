#!/usr/bin/env python3
"""
Example client that subscribes to the LCM spy virtual channel.

This demonstrates how to connect to the WebSocket server and receive
channel statistics at 1 Hz.

Usage:
    python examples/spy_client.py --host localhost --port 8765
"""

import argparse
import asyncio
import json
import websockets


async def subscribe_to_spy(host: str, port: int):
    """
    Subscribe to the LWS_LCM_SPY virtual channel and print stats.
    
    Args:
        host: WebSocket server host
        port: WebSocket server port
    """
    # Connect to the spy channel
    uri = f"ws://{host}:{port}/LWS_LCM_SPY"
    print(f"Connecting to {uri}...")
    
    async with websockets.connect(uri) as websocket:
        print("Connected! Receiving channel stats...\n")
        
        while True:
            try:
                # Receive a message
                message = await websocket.recv()
                
                # Parse JSON
                data = json.loads(message)
                
                # Extract stats
                channel_list = data.get("event", {})
                num_channels = channel_list.get("num_channels", 0)
                channels = channel_list.get("channels", [])
                
                print(f"=== Channel Statistics ({num_channels} channels) ===")
                print(f"{'Channel':<30} {'Type':<20} {'Hz':>8} {'Msgs':>8} {'BW (KB/s)':>12} {'Jitter':>10}")
                print("-" * 100)
                
                for channel in channels:
                    ch_name = channel.get("channel", "")
                    ch_type = channel.get("type", "")
                    hz = channel.get("hz", 0.0)
                    num_msgs = channel.get("num_msgs", 0)
                    bandwidth = channel.get("bandwidth", 0.0) / 1024  # Convert to KB/s
                    jitter = channel.get("jitter", 0.0)
                    undecodable = channel.get("undecodable", 0)
                    
                    # Truncate long names
                    ch_name_short = ch_name[:28] + ".." if len(ch_name) > 30 else ch_name
                    ch_type_short = ch_type[:18] + ".." if len(ch_type) > 20 else ch_type
                    
                    marker = " ⚠" if undecodable > 0 else ""
                    print(f"{ch_name_short:<30} {ch_type_short:<20} {hz:8.2f} {num_msgs:8d} {bandwidth:12.2f} {jitter:10.6f}{marker}")
                
                print()
            except json.JSONDecodeError as e:
                print(f"Failed to parse JSON: {e}")
            except websockets.exceptions.ConnectionClosed:
                print("Connection closed by server")
                break
            except KeyboardInterrupt:
                print("\nShutting down...")
                break


def main():
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__, 
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", type=str, default="localhost", 
                       help="WebSocket server host (default: localhost)")
    parser.add_argument("--port", type=int, default=8765,
                       help="WebSocket server port (default: 8765)")
    args = parser.parse_args()
    
    try:
        asyncio.run(subscribe_to_spy(args.host, args.port))
    except KeyboardInterrupt:
        print("\nStopped")


if __name__ == "__main__":
    main()
