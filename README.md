# lcm-websocket-server

WebSocket server for republishing LCM messages.

## :hammer: Installation

**You must have the LCM Python package installed before using `lcm-websocket-server`.** See the [LCM build instructions](http://lcm-proj.github.io/lcm/content/build-instructions.html) for more information.

### From PyPI

```bash
pip install lcm-websocket-server
```

### From source

```bash
poetry build
pip install dist/lcm_websocket_server-*-py3-none-any.whl
```

## :rocket: Usage

> [!TIP]
> The `lcm-websocket-server` commands have a log level of `ERROR` by default. To see more detailed logs, use the `-v` flag. Repeated use of the flag increases the verbosity from `ERROR` to `WARNING`, `INFO`, and `DEBUG`.


### JSON Proxy

The `lcm-websocket-json-proxy` command can be used to run a server that republishes LCM messages as JSON over a WebSocket connection.

> [!NOTE]
> The `lcm-websocket-server` command has been renamed to `lcm-websocket-json-proxy`. The old name is still available for backwards compatibility, but it is recommended to use the new name.

To run the server locally on port 8765 and republish messages on all channels:
```bash
lcm-websocket-json-proxy --host localhost --port 8765 --channel '.*' your_lcm_types_packages
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

### JPEG Proxy

The `lcm-websocket-jpeg-proxy` command can be used to run a server that republishes CoMPAS `senlcm::image_t` LCM messages as JPEG images over a WebSocket connection. The images are decoded from a variety of pixel formats and encoded as JPEG resolution as a configurable resolution and quality.

See the [CoMPAS LCM types repository](https://bitbucket.org/compas-sw/compas_lcmtypes) for more information.

To run the server locally on port 8766 and republish images for the `CAMERA` channel at 75% quality at 1.0 scale:
```bash
lcm-websocket-jpeg-proxy --host localhost --port 8766 --quality 75 --scale 1.0 --channel CAMERA
```

## :whale: Docker

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

Note that the `HOST`, `PORT`, and `CHANNEL` environment variables specified above are the defaults for the `mbari/lcm-websocket-server` image. These can be omitted if the defaults are acceptable.

The `LCM_PACKAGES` environment variable should be set to the name of the package (or comma-separated list of packages) that contains the LCM Python message definitions. The `/app` directory is included in the `PYTHONPATH` so that any packages mounted there (as shown with `-v` above) can be imported.

It's recommended to run with `--network=host` to avoid issues with LCM over UDP. This will allow the container to use the host's network stack.
