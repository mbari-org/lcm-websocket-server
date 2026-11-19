"""
Microbenchmark of JSONHandler.handle (decode + JSON encode) on the stdlib
and orjson paths.

Usage (requires stdlcm/senlcm/navlcm/geolcm on PYTHONPATH):
    uv run python benchmarks/encode.py
"""
import time

from geolcm import point_t
from lcmutils import LCMTypeRegistry
from navlcm import odometry_t
from senlcm import multibeam_euclidean_t
from stdlcm import header_t

from lcm_websocket_server.apps.json_proxy import JSONHandler
from lcm_websocket_server.lib.lcm_utils import types as T

reg = LCMTypeRegistry()
for p in ("stdlcm", "senlcm", "navlcm", "geolcm"): reg.discover(p)
hdr = header_t(); hdr.frame_id = "base"
odo = odometry_t(); odo.header = hdr
mb = multibeam_euclidean_t(); mb.header = hdr; mb.n_beams = 512
mb.flags = [0]*512; mb.range = [point_t() for _ in range(512)]; mb.intensity = [float(i) for i in range(512)]
h = JSONHandler(reg)
def bench(fn, n):
    fn(); t = time.perf_counter()
    for _ in range(n): fn()
    return (time.perf_counter() - t) / n * 1e6
orj = T.orjson
if orj is None:
    print("orjson not installed; orjson column repeats stdlib")
for name, data, n in (("odometry_t", odo.encode(), 20000), ("multibeam n=512", mb.encode(), 1000)):
    T.orjson = None
    a = bench(lambda data=data: h.handle("C", data), n)
    T.orjson = orj
    b = bench(lambda data=data: h.handle("C", data), n)
    print(f"{name:<18} stdlib {a:8.1f} us   orjson {b:8.1f} us")
