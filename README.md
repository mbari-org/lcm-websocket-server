# lcm-websocket-server

WebSocket server for republishing LCM messages.

## Installation

```bash
poetry build
pip install dist/lcm_websocket_server-*-py3-none-any.whl
```

## Usage

For a list of available options, run:
```bash
lcm-websocket-server --help
```

To run the server locally on port 8765 and republish messages on all channels:
```bash
lcm-websocket-server --host localhost --port 8765 --channel ".*" your_lcm_types_package
```

The `lcm_packages` argument is the name of the package (or comma-separated list of packages) that contains the LCM Python message definitions. Submodules are scanned recursively and registered so they can be automatically identified, decoded, and republished. 

### Example: `compas_lcmtypes`

For example, the `compas_lcmtypes` package contains LCM types for the CoMPAS lab. These can be installed with:
```bash
pip install compas-lcmtypes==0.1.0
```

Then, the server can be run with:
```bash
lcm-websocket-server compas_lcmtypes
```

## Docker

### Build

A Docker image to run the `lcm-websocket-server` can be built with:

```bash
./scripts/docker_build.sh
```

This will create the `mbari/lcm-websocket-server` image.

### Run

The container can be run with:

```bash
docker run \
    --name lcm-websocket-server \
    --rm \
    -e HOST=0.0.0.0 \
    -e PORT=8765 \
    -e CHANNEL=".*" \
    -v /path/to/your_lcm_types_package:/app/your_lcm_types_package \
    -e LCM_PACKAGES=your_lcm_types_package \
    --network=host \
    -d \
    mbari/lcm-websocket-server
```

Note that the `HOST`, `PORT`, and `CHANNEL` environment variables specified above are the defaults for the `mbari/lcm-websocket-server:compas` image. These can be omitted if the defaults are acceptable.

The `LCM_PACKAGES` environment variable should be set to the name of the package (or comma-separated list of packages) that contains the LCM Python message definitions. The `/app` directory is included in the `PYTHONPATH` so that any packages mounted there (as shown with `-v` above) can be imported.

It's recommended to run with `--network=host` to avoid issues with LCM over UDP. This will allow the container to use the host's network stack.
