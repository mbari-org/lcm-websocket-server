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

### Dial Proxy

The `lcm-websocket-dial-proxy` command is a version of the JSON proxy tweaked for [Dial](https://github.com/mbari-org/dial). It can be used to run a server that republishes CoMPAS `senlcm::image_t` LCM messages as JPEG images and all other CoMPAS LCM messages as JSON over a WebSocket connection. All text frames sent over the WebSocket connection are encoded as JSON. Binary frames are JPEG images with a prepended header and channel name that conforms to the [LCM log file format](http://lcm-proj.github.io/lcm/content/log-file-format.html) with the following considerations:
- The *event number* is always 0. This is because the server is not reading from a log file, but rather republishing messages as they are received.
- The *timestamp* is the timestamp from the `image_t` event's contained `header_t` timestamp. The timestamp is conventionally in units of microseconds, but this is not guaranteed.
- The *data length* represents the original image data length, not the length of the JPEG image data.

Therefore, the binary frame is laid out as follows:
```
[28 byte LCM header] [channel name] [JPEG]
```

**This command requires the `image` extension to be installed.** This can be done with:
```bash
pip install lcm-websocket-server[image]
```

To run the server locally on port 8765 and republish messages on all channels (JPEG quality and scale are configured as before):
```bash
lcm-websocket-dial-proxy --host localhost --port 8765 --channel '.*' --quality 75 --scale 1.0
```

The Dial proxy depends on the `molars-lcmtypes` Python package to be installed. This package is not available on PyPI, so it must be built and installed manually; see the [MolaRS repository](https://github.com/CoMPASLab/molars/) for more info. 

The `Dockerfile.dial` file can be used to build the image with the `molars-lcmtypes` package installed. For this, the Python wheel `molars_lcmtypes-0.0.0-py3-none-any.whl` must be placed at the repository root before building the image.

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
