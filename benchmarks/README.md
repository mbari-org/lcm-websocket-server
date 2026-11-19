# Benchmarks

Load and microbenchmarks used to characterize the server under load.

Both scripts use the CoMPAS LCM types from the `lcmtypes` extra.

```bash
uv sync --all-extras
uv run python benchmarks/load.py all        # or: latency, json-scale, json-rate, dial, slow, periodic
uv run python benchmarks/encode.py
```

## `load.py`

Runs the real `LCMWebSocketServer` in one process. Threads stand in for the LCM thread by calling `LCMRepublisher.inject()` at fixed rates, so UDP and the LCM C library are not exercised. Clients run in 8 worker processes over loopback.

Default workload: 10 `odometry_t` channels at 100 Hz plus a 512-beam `multibeam_euclidean_t` at 20 Hz (1.22 k msg/s). Dial scenarios add a 2048x1536 Bayer `image_t` at 10 Hz.

Reported per scenario:

| Field | Meaning |
|---|---|
| `srvCPU` | Server process CPU over the window (100% = one core) |
| `deliv` | Non-image messages received per client as % of published |
| `lat` | Publish-to-receive latency from the `header_t` timestamp |
| `loopLag` | Worst event-loop stall seen by a 10 ms probe |
| `injLate` | Times the simulated LCM thread fell behind schedule |
| `dropWarn` | Queue-overflow warnings logged |
| `encodes/frame` | JPEG encodes per published image (1.00 = fully shared) |
| `spy-reported Hz` | Rates from `LWS_LCM_SPY`, to check against the source |

## Results

Deucalion (24 cores), Python 3.13, websockets 17.4, orjson installed. Baseline is v0.8.0 with default flags.

| Scenario | v0.8.0 | Now |
|---|---|---|
| JSON, 1 client | 66% delivered, p50 32 ms | 100%, p50 0.5 ms |
| JSON, 50 clients | 36%, CPU 112% | 99.9%, CPU 76% |
| JSON, 100 clients, compression on | 9% | 92% |
| JSON, 100 clients, compression off | 12% | 100%, p50 4.4 ms |
| JSON, 10 clients at 10 k msg/s | 2.8% | 38% (one core saturated) |
| Dial, 10 clients, image encodes per frame | 9.3 | 1.00 |
| Dial, 10 clients, CPU (compression off) | 162% | 42% |
| Spy Hz for a 100 Hz channel (streaming / periodic) | 64 / 12 | 100 / 100 |

Beyond about 100 JSON clients or 10 k msg/s, one process is GIL-bound; use `--processes N`.
