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

## Docker

### Build

A Docker image to run the `lcm-websocket-server` can be built with:

```bash
./scripts/docker_build_compas.sh
```

This will create the `mbari/lcm-websocket-server:compas` image.

### Run

The container can be run with:

```bash
docker run \
    --name lcm-websocket-server \
    --rm \
    -e HOST=0.0.0.0 \
    -e PORT=8765 \
    -e CHANNEL=".*" \
    -e LCM_PACKAGES=compas_lcmtypes \
    --network=host \
    -d \
    mbari/lcm-websocket-server:compas
```

Note that the environment variables specified above are the defaults for the `mbari/lcm-websocket-server:compas` image. These can be omitted if the defaults are acceptable.

It's recommended to run with `--network=host` to avoid issues with LCM over UDP. This will allow the container to use the host's network stack.
