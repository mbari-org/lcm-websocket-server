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
pip install .
```

Or with optional extras:

```bash
pip install ".[image]"            # Dial proxy (OpenCV)
pip install ".[orjson,uvloop]"    # faster JSON encoding and event loop
```

See [Development](#wrench-development) for working on the project itself.

## :rocket: Usage

> [!TIP]
> The `lcm-websocket-server` commands have a log level of `ERROR` by default. To see more detailed logs, use the `-v` flag. Repeated use of the flag increases the verbosity from `ERROR` to `WARNING`, `INFO`, and `DEBUG`.

### Running the Server

#### JSON Proxy

The `lcm-websocket-json-proxy` command can be used to run a server that republishes LCM messages as JSON over a WebSocket connection.

> [!NOTE]
> The `lcm-websocket-server` command has been renamed to `lcm-websocket-json-proxy`. The old name is still available for backwards compatibility, but it is recommended to use the new name.

To run the server locally on port 8765 and republish messages on all channels:
```bash
lcm-websocket-json-proxy --host localhost --port 8765 --channel '.*' your_lcm_types_packages
```

The `lcm_packages` argument is the name of the package (or comma-separated list of packages) that contains the LCM Python message definitions. Submodules are scanned recursively and registered so they can be automatically identified, decoded, and republished.

#### Tuning Options

Both proxy commands accept these options:

| Flag | Default | Description |
|------|---------|-------------|
| `--queue-size N` | 64 | Max messages buffered per connection before the oldest are dropped |
| `--spy-interval S` | 1.0 | LCM spy stats emission interval (seconds) |
| `--compression {deflate,none}` | `deflate` (JSON), `none` (Dial) | WebSocket permessage-deflate. Saves bandwidth for JSON on slow links at a CPU cost per client; useless for JPEG |
| `--uvloop` | off | Use the uvloop event loop (requires the `uvloop` extra) |
| `--processes N` | 1 | Run N server processes on the same port (`SO_REUSEPORT`) to use more than one core |
| `--ping-interval S` | 20 | Seconds between WebSocket keepalive pings (0 disables) |
| `--ping-timeout S` | 20 | Seconds to wait for a pong before closing (0 disables) |

The Dial proxy also accepts:

| Flag | Default | Description |
|------|---------|-------------|
| `--max-fps F` | no cap | Max transcoded frames per second per image channel |
| `--image-workers N` | min(8, cores) | Threads for image transcoding |

Delivery is event-driven; `--poll-interval` is accepted for compatibility but ignored.

If drop warnings appear in the logs, the client is not keeping up. Options: connect with `?conflate=1` (see below), increase `--queue-size`, or subscribe to fewer channels.

Each message is encoded once and shared by all clients. Encoding runs in worker threads: JSON on a small pool and images on a separate pool, so image bursts do not delay JSON. One process is bound to roughly one core for JSON work; with `--processes N` each process has its own LCM subscription, encode cache and spy statistics.

For faster JSON encoding, install the `orjson` extra (about 2x faster than the standard library path).

**Example: `compas_lcmtypes`**

For example, the `compas_lcmtypes` package contains LCM types for the CoMPAS lab. These can be installed with:
```bash
pip install compas-lcmtypes==0.1.0
```

Then, the server can be run with:
```bash
lcm-websocket-json-proxy compas_lcmtypes
```

#### Dial Proxy

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

### Connecting to the Server

Clients can connect to the server using a WebSocket connection. The channel(s) to subscribe to are specified in the WebSocket path as a regular expression. For example, to subscribe to all channels:
```javascript
const ws = new WebSocket('ws://localhost:8765/.*');
```

To subscribe to a specific channel, such as `EXAMPLE_CHANNEL`:
```javascript
const ws = new WebSocket('ws://localhost:8765/EXAMPLE_CHANNEL');
```

#### Update Interval

Clients can specify an optional `update_interval_ms` query parameter in the WebSocket URL to control how often they receive messages. This is useful for clients that want to limit the rate of incoming messages. For example, to receive updates every 500 milliseconds:
```javascript
const ws = new WebSocket('ws://localhost:8765/.*?update_interval_ms=500');
```

This will cause the latest message for each subscribed channel to be sent to the client every 500 milliseconds. Between ticks only the latest message per channel is kept, so low-rate channels are never crowded out by high-rate ones.

#### Conflation

By default each connection buffers up to `--queue-size` messages and drops the oldest when full, regardless of channel. A client that cannot keep up with every message can instead request only the latest message per channel:
```javascript
const ws = new WebSocket('ws://localhost:8765/.*?conflate=1');
```

Messages are still delivered as soon as possible, but a busy channel can no longer evict a quiet one.

#### Invalid Requests

A connection with an invalid channel regex (or one longer than 1024 characters) or an invalid `update_interval_ms` is closed with code 1008 (policy violation) and a reason describing the problem.

## :bar_chart: LCM Spy Virtual Channel

The server provides a **virtual channel** called `LWS_LCM_SPY` that publishes per-channel statistics at 1 Hz (`--spy-interval`) to every client whose channel regex matches it. Statistics are counted once per message for all traffic the server receives, independent of any client's queue. Each client's view starts when it connects: message counts are since connect, and channels appear once they have received a message since then.

### Channel Statistics

The virtual channel publishes a `channel_stats_list` message containing statistics for each active channel:
- **Hz**: Message frequency
- **Bandwidth**: Bytes per second
- **Jitter**: Spread (max - min) of inter-arrival intervals over the last spy interval
- **Message count**: Total messages received
- **Undecodable count**: Messages that couldn't be decoded

### Subscribing to Stats

Connect to the spy channel using the channel name in the WebSocket path:

```javascript
// Subscribe to spy stats only
const ws = new WebSocket('ws://localhost:8765/LWS_LCM_SPY');

ws.onmessage = (ev) => {
    const stats = JSON.parse(ev.data).event;
    console.log('Channel stats:', stats.channels);
};
```

Or subscribe to all channels including the spy:

```javascript
// Subscribe to all channels (real + virtual)
const ws = new WebSocket('ws://localhost:8765/.*');
```

### Example Client

A Python example client is provided in `examples/spy_client.py`:

```bash
python examples/spy_client.py --host localhost --port 8765
```

## :satellite: Deployment

### Monitoring

Plain HTTP requests to the server port answer two endpoints (WebSocket connections on any path are unaffected):

- `GET /healthz` returns `ok`.
- `GET /metrics` returns Prometheus text: connected clients, LCM messages received, frames and bytes sent, messages dropped from full client queues, encode counts, errors and time, and worst event-loop lag since the last scrape.

A rising `lws_dropped_total` means clients are not keeping up. A sustained `lws_event_loop_lag_max_seconds` above roughly 50 ms means the process is saturated; use `--processes` or reduce load.

### LCM receive buffers

LCM messages dropped by the kernel never reach the server, so they appear in neither the logs, `/metrics`, nor the spy channel. Large or bursty traffic (especially images) needs larger UDP receive buffers than Linux defaults:

```bash
sudo sysctl -w net.core.rmem_max=16777216 net.core.rmem_default=16777216
export LCM_DEFAULT_URL="udpm://239.255.76.67:7667?ttl=0&recv_buf_size=16777216"
```

Persist the `sysctl` settings in `/etc/sysctl.d/`. Check for drops with `netstat -su` (`RcvbufErrors`).

### Matching the link

- **Local network**: compression off for best CPU efficiency.
- **Constrained links (e.g. ship to shore)**: keep `--compression deflate` for JSON; for Dial use `--scale`, `--quality` and `--max-fps` to cut image bandwidth, and raise `--ping-timeout` if large frames can take longer than 20 s to send. Clients that do not need every message should connect with `?conflate=1` or `?update_interval_ms=`.

### Scaling

One process handles about 100 JSON clients at 1.2 k msg/s (see [benchmarks](benchmarks/README.md)). Beyond that, `--processes N` adds cores. Each process has its own LCM subscription and encode cache, so image transcoding is repeated per process: keep the Dial proxy at one process and run a separate JSON proxy for large numbers of JSON clients.

### Security

The server has no authentication or encryption. When it is reachable beyond a trusted vehicle network, put it behind a reverse proxy that terminates TLS (`wss://`) and enforces access control.

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

Extra command-line flags can be passed with the `ARGS` environment variable, e.g. `-e ARGS="--uvloop --processes 4 -vv"`. The image includes the `orjson` and `uvloop` extras and a `HEALTHCHECK` that polls `/healthz`; if `HOST` is set to an address other than `0.0.0.0` or `127.0.0.1`, the health check cannot reach it.

The `LCM_PACKAGES` environment variable should be set to the name of the package (or comma-separated list of packages) that contains the LCM Python message definitions. The `/app` directory is included in the `PYTHONPATH` so that any packages mounted there (as shown with `-v` above) can be imported.

It's recommended to run with `--network=host` to avoid issues with LCM over UDP. This will allow the container to use the host's network stack.

## :wrench: Development

```bash
uv sync --all-extras
uv run pre-commit install   # ruff and file hygiene hooks on commit
uv run pytest
uv run ruff check .
```

The tests are self-contained: they use LCM types vendored in `tests/lcmtypes` and the in-process `memq://` LCM provider, so no LCM network or MolaRS install is needed. CI runs lint and tests on Python 3.11 to 3.14.

Before a release, run the load benchmarks and compare against [benchmarks/README.md](benchmarks/README.md) to catch performance regressions. They need the full CoMPAS LCM types on `PYTHONPATH`:

```bash
uv run python benchmarks/load.py all
```
