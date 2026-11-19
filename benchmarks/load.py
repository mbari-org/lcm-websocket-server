"""
End-to-end load harness for lcm-websocket-server.

The server runs in its own process with the real LCMWebSocketServer and
handlers. Threads stand in for the LCM thread by calling
LCMRepublisher.inject() at fixed rates (serialised through a lock, as the
single LCM thread would be); UDP and the LCM C library are bypassed.
Clients run in separate worker processes and report delivered frames,
end-to-end latency (from the header_t timestamp stamped at publish) and the
spy channel's reported rates.

Usage (requires the lcmtypes extra: uv sync --all-extras):
    uv run python benchmarks/load.py SUITE [--duration S]

Suites: latency, json-scale, json-rate, dial, slow, periodic, all
"""

import argparse
import json
import multiprocessing as mp
import os
import re
import struct
import threading
import time

TS_RE = re.compile(rb'"timestamp": ?(\d+)')
CHANNEL_RE = re.compile(r'"channel": ?"([^"]+)"')
SPY = "LWS_LCM_SPY"


# ----------------------------------------------------------------- server
def _cpu_seconds() -> float:
    t = os.times()
    return t.user + t.system


def _message_builders():
    import numpy as np
    from compas_lcmtypes.geolcm import point_t
    from compas_lcmtypes.navlcm import odometry_t
    from compas_lcmtypes.senlcm import image_t, multibeam_euclidean_t
    from compas_lcmtypes.stdlcm import header_t

    from lcm_websocket_server.lib.image import PixelFormat

    def header(ts):
        h = header_t()
        h.timestamp = ts
        h.frame_id = "base"
        return h

    def odo(ts):
        m = odometry_t()
        m.header = header(ts)
        return m.encode()

    mb_msg = multibeam_euclidean_t()
    mb_msg.n_beams = 512
    mb_msg.flags = [0] * 512
    mb_msg.range = [point_t() for _ in range(512)]
    mb_msg.intensity = [float(i) for i in range(512)]

    def mb(ts):
        mb_msg.header = header(ts)
        return mb_msg.encode()

    # Semi-realistic Bayer frame: smooth gradient plus mild noise.
    h, w = 1536, 2048
    yy, xx = np.mgrid[0:h, 0:w]
    base = ((xx // 8 + yy // 8) % 256).astype(np.uint8)
    rng = np.random.default_rng(0)
    frames = [(base + rng.integers(0, 12, (h, w), dtype=np.uint8)).tobytes() for _ in range(4)]
    counter = [0]

    def img(ts):
        m = image_t()
        m.header = header(ts)
        m.width, m.height = w, h
        m.pixelformat = PixelFormat.BAYER_RGGB.value
        m.nmetadata, m.metadata = 0, []
        m.data = frames[counter[0] % 4]
        m.size = len(m.data)
        counter[0] += 1
        return m.encode()

    return {"odo": odo, "mb": mb, "img": img}


def run_server(port, mode, server_kwargs, streams, ready, go, window_end, stop, out_q):
    import asyncio
    import logging

    import cv2
    from compas_lcmtypes.senlcm import image_t

    from lcm_websocket_server.apps.dial_proxy import (
        DialHandler,
        DownsamplingMJPEGEncoder,
        ImageMessageToJPEGHandler,
    )
    from lcm_websocket_server.apps.json_proxy import JSONHandler
    from lcm_websocket_server.lib.cli import build_registry
    from lcm_websocket_server.lib.lcm_utils import pubsub
    from lcm_websocket_server.lib.log import set_stream_handler_verbosity
    from lcm_websocket_server.lib.server import LCMWebSocketServer

    set_stream_handler_verbosity(0)
    drops = {"n": 0}

    class DropCounter(logging.Handler):
        def emit(self, record):
            drops["n"] += 1

    pubsub._logger.addHandler(DropCounter())

    registry = build_registry(["compas_lcmtypes"], logging.getLogger("bench"))
    json_handler = JSONHandler(registry)
    if mode == "dial":
        encoder = DownsamplingMJPEGEncoder(scale=1.0, params=[cv2.IMWRITE_JPEG_QUALITY, 75])
        inner = DialHandler(ImageMessageToJPEGHandler(encoder, image_t), json_handler)
    else:
        inner = json_handler

    encodes = {"image": 0, "other": 0}

    class CountingHandler(type(inner)):
        def __init__(self):
            self.__dict__.update(inner.__dict__)

        def handle(self, channel, data):
            encodes["image" if self.is_heavy(channel, data) else "other"] += 1
            return super().handle(channel, data)

    republisher = pubsub.LCMRepublisher(".*")  # never started; fed by inject()
    lcm_lock = threading.Lock()
    builders = _message_builders()
    injected = {"n": 0, "late": 0, "image": 0}

    def injector(kind, channel, hz):
        period = 1.0 / hz
        nxt = time.perf_counter()
        while not stop.is_set():
            data = builders[kind](time.time_ns() // 1000)
            with lcm_lock:
                republisher.inject(channel, data)
            injected["n"] += 1
            injected["image"] += kind == "img"
            nxt += period
            delay = nxt - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                injected["late"] += 1
                nxt = time.perf_counter()

    server = LCMWebSocketServer(
        "127.0.0.1", port, CountingHandler(), republisher, spy_registry=registry, **server_kwargs
    )

    async def main():
        lag = {"max": 0.0}

        async def lag_probe():
            while True:
                t0 = time.perf_counter()
                await asyncio.sleep(0.01)
                lag["max"] = max(lag["max"], time.perf_counter() - t0 - 0.01)

        serve_task = asyncio.create_task(server.serve())
        probe = asyncio.create_task(lag_probe())
        await asyncio.sleep(0.3)
        ready.set()
        while not go.is_set():
            await asyncio.sleep(0.01)
        for s in streams:
            threading.Thread(target=injector, args=s, daemon=True).start()
        c0, t0 = _cpu_seconds(), time.perf_counter()
        lag["max"] = 0.0
        while not window_end.is_set():
            await asyncio.sleep(0.01)
        c1, t1 = _cpu_seconds(), time.perf_counter()
        out_q.put({
            "cpu_pct": 100 * (c1 - c0) / (t1 - t0),
            "injected": injected["n"],
            "injected_images": injected["image"],
            "late": injected["late"],
            "encodes": dict(encodes),
            "drop_warnings": drops["n"],
            "loop_lag_max_ms": 1000 * lag["max"],
        })
        out_q.close()
        out_q.join_thread()
        while not stop.is_set():
            await asyncio.sleep(0.05)
        server.close()
        probe.cancel()
        serve_task.cancel()
        os._exit(0)  # skip interpreter teardown of daemon threads

    asyncio.run(main())


# ----------------------------------------------------------------- clients
def run_clients(port, n, path, duration, out_q, barrier, stall_first, compression):
    import asyncio

    import websockets

    async def one(i, start):
        stall = stall_first and i == 0
        st = {"frames": 0, "bytes": 0, "lat": [], "img": 0, "imglat": [], "chan": {}, "spyhz": {}, "stalled": stall}
        kwargs = {} if compression else {"compression": None}
        async with websockets.connect(f"ws://127.0.0.1:{port}/{path}", max_size=None, **kwargs) as ws:
            await start.wait()
            end = time.perf_counter() + duration
            while (remaining := end - time.perf_counter()) > 0:
                try:
                    m = await asyncio.wait_for(ws.recv(), remaining)
                except TimeoutError:
                    break
                now = time.time_ns() // 1000
                if stall:
                    await asyncio.sleep(1.0)  # pathological slow consumer
                st["frames"] += 1
                st["bytes"] += len(m)
                if isinstance(m, bytes):
                    st["img"] += 1
                    st["imglat"].append(now - struct.unpack(">q", m[12:20])[0])
                    continue
                channel = CHANNEL_RE.search(m[:64]).group(1)
                if channel == SPY:
                    if i == 1:
                        for c in json.loads(m)["event"]["channels"]:
                            st["spyhz"].setdefault(c["channel"], []).append(c["hz"])
                    continue
                st["chan"][channel] = st["chan"].get(channel, 0) + 1
                ts = TS_RE.search(m[:400].encode())
                if ts:
                    st["lat"].append(now - int(ts.group(1)))
        return st

    async def main():
        start = asyncio.Event()
        tasks = [asyncio.create_task(one(i, start)) for i in range(n)]
        await asyncio.sleep(1.0)  # let all connect
        await asyncio.get_running_loop().run_in_executor(None, barrier.wait)
        start.set()
        out_q.put(await asyncio.gather(*tasks))

    asyncio.run(main())


# ----------------------------------------------------------------- driver
def pct(values, p):
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(p / 100 * len(values)))]


def scenario(name, mode, streams, clients, path=".*", duration=6.0, compression=True, stall_first=False, procs=8):
    port = 20000 + (os.getpid() + int(time.time())) % 20000
    ready, go, window_end, stop = mp.Event(), mp.Event(), mp.Event(), mp.Event()
    server_q, client_q = mp.Queue(), mp.Queue()
    server_kwargs = {"compression": "deflate" if compression else None}
    sp = mp.Process(target=run_server, args=(port, mode, server_kwargs, streams, ready, go, window_end, stop, server_q))
    sp.start()
    ready.wait(60)

    procs = max(1, min(procs, clients))
    per_proc = [clients // procs + (1 if i < clients % procs else 0) for i in range(procs)]
    barrier = mp.Barrier(procs + 1)
    cps = [
        mp.Process(target=run_clients, args=(port, k, path, duration, client_q, barrier, stall_first and j == 0, compression))
        for j, k in enumerate(per_proc)
    ]
    for c in cps:
        c.start()
    barrier.wait(60)
    go.set()
    time.sleep(duration)
    window_end.set()
    srv = server_q.get(timeout=30)
    res = []
    for _ in cps:
        res.extend(client_q.get(timeout=duration + 60))
    stop.set()
    for c in cps:
        c.join()
    sp.join(10)

    healthy = [r for r in res if not r["stalled"]]
    non_image_hz = sum(h for kind, _, h in streams if kind != "img")
    expected = non_image_hz * duration
    deliv = [sum(r["chan"].values()) / expected * 100 for r in healthy]
    lat = [x for r in healthy for x in r["lat"]]
    print(
        f"{name:<40} clients={clients:<4} cmp={'on ' if compression else 'off'} "
        f"srvCPU={srv['cpu_pct']:5.0f}%  deliv avg={sum(deliv)/len(deliv):5.1f}% min={min(deliv):5.1f}%  "
        f"lat p50={pct(lat, 50)/1000:6.1f}ms p99={pct(lat, 99)/1000:6.1f}ms  "
        f"MB/s={sum(r['bytes'] for r in res)/duration/1e6:6.1f}  loopLag={srv['loop_lag_max_ms']:4.0f}ms  "
        f"injLate={srv['late']}  dropWarn={srv['drop_warnings']}",
        flush=True,
    )
    if srv["injected_images"]:
        il = [x for r in healthy for x in r["imglat"]]
        src_fps = sum(h for kind, _, h in streams if kind == "img")
        print(
            f"    image: fps/client={sum(r['img'] for r in healthy)/len(healthy)/duration:.1f} (src {src_fps})  "
            f"lat p50={pct(il, 50)/1000:.1f}ms p99={pct(il, 99)/1000:.1f}ms  "
            f"encodes/frame={srv['encodes']['image']/srv['injected_images']:.2f}",
            flush=True,
        )
    sources = {c: h for _, c, h in streams}
    chans = {}
    for r in healthy:
        for k, v in r["chan"].items():
            chans.setdefault(k, []).append(v / duration)
    shown = [k for k in ("NAV_0", "MULTIBEAM") if k in chans]
    if shown:
        print("    rcv Hz/client (source): " + ", ".join(
            f"{k}={sum(chans[k])/len(chans[k]):.1f} ({sources[k]})" for k in shown), flush=True)
    spy = next((r["spyhz"] for r in healthy if r["spyhz"]), {})
    if spy:
        print("    spy-reported Hz (last 3): " + ", ".join(
            f"{k}={[round(x) for x in spy[k][-3:]]}" for k in ("NAV_0", "MULTIBEAM", "CAM") if k in spy), flush=True)
    if stall_first:
        stalled = next(r for r in res if r["stalled"])
        print(f"    stalled client frames={stalled['frames']}", flush=True)


NAV = [("odo", f"NAV_{i}", 100) for i in range(10)] + [("mb", "MULTIBEAM", 20)]
CAM = [("img", "CAM", 10)]

SUITES = {
    "latency": lambda d: scenario("json 1 client, nav 1.2k msg/s", "json", NAV, 1, duration=d),
    "json-scale": lambda d: [
        scenario("json nav 10x100Hz + multibeam 20Hz", "json", NAV, n, duration=d) for n in (1, 10, 50, 100)
    ] + [scenario("json nav 10x100Hz + multibeam 20Hz", "json", NAV, n, duration=d, compression=False) for n in (50, 100)],
    "json-rate": lambda d: [
        scenario(f"json odo 10x{hz}Hz", "json", [("odo", f"NAV_{i}", hz) for i in range(10)], 10, duration=d, compression=False)
        for hz in (200, 500, 1000)
    ],
    "dial": lambda d: [
        scenario("dial cam 3MP 10Hz + nav", "dial", CAM + NAV, n, duration=d, compression=False) for n in (1, 3, 10)
    ] + [scenario("dial cam 3MP 10Hz + nav", "dial", CAM + NAV, 10, duration=d, compression=True)],
    "slow": lambda d: [
        scenario("json, 1 stalled client among 20", "json", NAV, 20, duration=d, stall_first=True),
        scenario("dial, 1 stalled client among 5", "dial", CAM + NAV, 5, duration=d, stall_first=True, compression=False),
    ],
    "periodic": lambda d: scenario("json periodic 500ms", "json", NAV, 10, path=".*?update_interval_ms=500", duration=d),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("suite", choices=[*SUITES, "all"])
    parser.add_argument("--duration", type=float, default=6.0, help="Measurement window per scenario (s)")
    args = parser.parse_args()
    mp.set_start_method("fork")
    for name in SUITES if args.suite == "all" else [args.suite]:
        SUITES[name](args.duration)


if __name__ == "__main__":
    main()
