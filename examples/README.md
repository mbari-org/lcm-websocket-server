# Examples

This directory contains example scripts demonstrating how to use the LCM WebSocket Server.

## spy_client.py

A Python client that subscribes to the LCM spy virtual channel (`LWS_LCM_SPY`) and displays channel statistics in real-time.

### Prerequisites

```bash
pip install websockets
```

### Usage

First, start the LCM WebSocket server (replace `your_lcm_types` with your actual LCM types package):

```bash
lcm-websocket-json-proxy --host localhost --port 8765 --channel '.*' your_lcm_types
```

Then, in another terminal, run the example client:

```bash
python examples/spy_client.py --host localhost --port 8765
```

### Example Output

```
Connecting to ws://localhost:8765/LWS_LCM_SPY...
Connected! Receiving channel stats every 1 second...

=== Channel Statistics (3 channels) ===
Channel                        Type                     Hz     Msgs    BW (KB/s)      Jitter
----------------------------------------------------------------------------------------------------
SENSOR_CAMERA                  image_t               15.20      150     1220.70    0.002000
NAV_POSITION                   pose_t                10.00      100        4.88    0.001000
```

### Notes

- The ⚠ marker indicates channels with undecodable messages
- Press Ctrl+C to stop the client
