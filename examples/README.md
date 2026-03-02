# Examples

This directory contains example scripts demonstrating how to use the LCM WebSocket Server.

## dial_web_viewer_server.py

A simple local web server that hosts a browser UI for `lcm-websocket-dial-proxy` streams.

The page connects to a configurable WebSocket path, shows basic per-channel live stats for all incoming channels, and renders JPEG image streams from Dial binary frames.

### Prerequisites

No additional Python packages are required (standard library only).

### Usage

Start the Dial proxy:

```bash
lcm-websocket-dial-proxy --host localhost --port 8765 --channel '.*' --quality 75 --scale 1.0 -v
```

Then run the example web server:

```bash
python examples/dial_web_viewer_server.py --host 127.0.0.1 --port 8080 --ws-path '/.*'
```

Open the viewer at:

```text
http://127.0.0.1:8080/
```

Optional: provide a full WebSocket URL directly:

```bash
python examples/dial_web_viewer_server.py --ws-url 'ws://localhost:8765/CAMERA_.*'
```

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
