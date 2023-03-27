# lcm-websocket-server

WebSocket server for republishing LCM messages.

## Installation

```bash
poetry build
pip install dist/lcm_websocket_server-0.1.0-py3-none-any.whl
```

## Usage

For a list of available options, run:
```bash
lcm-websocket-server --help
```

To run the server locally on port 8765 and republish messages on all channels:
```bash
lcm-websocket-server --host localhost --port 8765 --channel ".*" compas_lcmtypes
```

The `lcm_packages` argument is the name of the package (or comma-separated list of packages) that contains the LCM Python message definitions. Submodules are scanned recursively and registered so they can be automatically identified, decoded, and republished. 

For example, the `compas_lcmtypes` package contains LCM types for the CoMPAS lab. These can be installed with:
```bash
pip install compas-lcmtypes
```
