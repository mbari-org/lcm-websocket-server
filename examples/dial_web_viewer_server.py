#!/usr/bin/env python3
"""
Simple web server example for viewing data from lcm-websocket-dial-proxy.

This script serves a minimal web page that:
1) Connects to a WebSocket URL built from host/port/path arguments.
2) Displays basic per-channel stats for all incoming messages.
3) Renders JPEG image streams carried in Dial binary frames.

Usage:
    python examples/dial_web_viewer_server.py --ws-path '/.*'
"""

from __future__ import annotations

import argparse
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>LCM Dial Stream Viewer</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #111318;
      --panel: #1a1f27;
      --panel2: #202734;
      --text: #e8ecf3;
      --muted: #9aa4b3;
      --ok: #64d97c;
      --warn: #f6c453;
      --err: #ff7070;
      --line: #2f3745;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    header {
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 16px;
      background: var(--panel2);
      border: 1px solid var(--line);
      font-size: 13px;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--warn);
      box-shadow: 0 0 8px var(--warn);
    }
    .dot.open { background: var(--ok); box-shadow: 0 0 8px var(--ok); }
    .dot.closed { background: var(--err); box-shadow: 0 0 8px var(--err); }
    main {
      display: grid;
      grid-template-columns: minmax(340px, 1fr) minmax(420px, 2fr);
      gap: 12px;
      padding: 12px;
    }
    @media (max-width: 1000px) {
      main { grid-template-columns: 1fr; }
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: hidden;
    }
    .card h2 {
      margin: 0;
      padding: 10px 12px;
      font-size: 14px;
      letter-spacing: 0.03em;
      color: var(--muted);
      border-bottom: 1px solid var(--line);
      text-transform: uppercase;
    }
    .table-wrap {
      max-height: 60vh;
      overflow: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    thead th {
      position: sticky;
      top: 0;
      background: var(--panel2);
      color: var(--muted);
      text-align: left;
      border-bottom: 1px solid var(--line);
      padding: 8px;
      white-space: nowrap;
    }
    tbody td {
      padding: 8px;
      border-bottom: 1px solid var(--line);
      white-space: nowrap;
      text-overflow: ellipsis;
      overflow: hidden;
      max-width: 220px;
    }
    tbody tr {
      cursor: pointer;
    }
    tbody tr:hover {
      background: #253044;
    }
    tbody tr.selected {
      background: #2b3a57;
    }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }
    .images {
      padding: 10px;
      display: grid;
      gap: 10px;
      max-height: 76vh;
      overflow: auto;
    }
    .img-panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: #0f1218;
    }
    .img-meta {
      padding: 8px 10px;
      font-size: 12px;
      color: var(--muted);
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      gap: 8px;
    }
    .img-panel img {
      width: 100%;
      display: block;
      background: #000;
      min-height: 100px;
      object-fit: contain;
    }
    .log {
      padding: 8px 10px;
      border-top: 1px solid var(--line);
      background: #0f1218;
      color: var(--muted);
      max-height: 120px;
      overflow: auto;
      font-size: 12px;
    }
    .details-wrap {
      padding: 0 12px 12px;
    }
    .details {
      padding: 10px;
      color: var(--text);
      min-height: 120px;
    }
    .details pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
  </style>
</head>
<body>
  <header>
    <div class="pill"><span id="ws-dot" class="dot"></span><strong id="ws-state">Connecting...</strong></div>
    <div class="pill">URL: <span id="ws-url" class="mono"></span></div>
    <div class="pill">Messages: <strong id="total-msgs">0</strong></div>
    <div class="pill">Channels: <strong id="total-channels">0</strong></div>
  </header>

  <main>
    <section class="card">
      <h2>Channels</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Channel</th>
              <th>Kind</th>
              <th>Msgs</th>
              <th>Bytes</th>
              <th>Rate (Hz)</th>
              <th>Last Seen</th>
            </tr>
          </thead>
          <tbody id="channels-body"></tbody>
        </table>
      </div>
      <div class="log" id="log"></div>
    </section>

    <section class="card">
      <h2>JPEG Streams</h2>
      <div class="images" id="images"></div>
    </section>
  </main>

  <section class="details-wrap">
    <section class="card">
      <h2>Selected Channel Content</h2>
      <div class="details" id="channel-details">Click a channel row to inspect its latest content.</div>
    </section>
  </section>

  <script>
    const WS_URL = __WS_URL_JSON__;

    const wsDot = document.getElementById("ws-dot");
    const wsState = document.getElementById("ws-state");
    const wsUrlEl = document.getElementById("ws-url");
    const totalMsgsEl = document.getElementById("total-msgs");
    const totalChannelsEl = document.getElementById("total-channels");
    const channelsBody = document.getElementById("channels-body");
    const imagesEl = document.getElementById("images");
    const logEl = document.getElementById("log");
    const channelDetailsEl = document.getElementById("channel-details");

    wsUrlEl.textContent = WS_URL;

    const channelStats = new Map();
    const channelPayloads = new Map();
    const imagePanels = new Map();
    let totalMessages = 0;
    let renderQueued = false;
    let lastRenderTime = 0;
    let selectedChannel = null;
    const RENDER_THROTTLE_MS = 250; // Only render table updates every 250ms

    // Event delegation for channel selection
    channelsBody.addEventListener("click", (e) => {
      const row = e.target.closest("tr");
      if (!row) return;
      const channel = row.dataset.channel;
      if (!channel) return;
      selectedChannel = channel;
      updateSelectedRowStyles();
      renderSelectedChannel();
    });

    function log(msg) {
      const at = new Date().toLocaleTimeString();
      const line = document.createElement("div");
      line.textContent = `[${at}] ${msg}`;
      logEl.prepend(line);
      while (logEl.childElementCount > 80) {
        logEl.removeChild(logEl.lastChild);
      }
    }

    function getOrInitStat(channel) {
      let s = channelStats.get(channel);
      if (!s) {
        s = {
          channel,
          kind: "-",
          count: 0,
          bytes: 0,
          firstMs: performance.now(),
          lastMs: performance.now(),
          lastSeenIso: new Date().toISOString()
        };
        channelStats.set(channel, s);
      }
      return s;
    }

    function touchStat(channel, kind, bytes) {
      const nowMs = performance.now();
      const s = getOrInitStat(channel);
      s.kind = kind;
      s.count += 1;
      s.bytes += bytes;
      s.lastMs = nowMs;
      s.lastSeenIso = new Date().toISOString();
      totalMessages += 1;
      totalMsgsEl.textContent = String(totalMessages);
      totalChannelsEl.textContent = String(channelStats.size);
      queueRender();
    }

    function queueRender() {
      if (renderQueued) return;
      
      const now = performance.now();
      const timeSinceLastRender = now - lastRenderTime;
      
      if (timeSinceLastRender < RENDER_THROTTLE_MS) {
        // Schedule render for later
        renderQueued = true;
        setTimeout(() => {
          renderQueued = false;
          lastRenderTime = performance.now();
          renderTable();
        }, RENDER_THROTTLE_MS - timeSinceLastRender);
      } else {
        // Render immediately
        renderQueued = true;
        requestAnimationFrame(() => {
          renderQueued = false;
          lastRenderTime = performance.now();
          renderTable();
        });
      }
    }

    function renderTable() {
      const rows = Array.from(channelStats.values()).sort((a, b) => a.channel.localeCompare(b.channel));
      channelsBody.innerHTML = "";
      for (const s of rows) {
        const elapsed = Math.max((s.lastMs - s.firstMs) / 1000, 0.001);
        const hz = s.count / elapsed;
        const tr = document.createElement("tr");
        tr.dataset.channel = s.channel;
        if (selectedChannel === s.channel) {
          tr.classList.add("selected");
        }
        tr.innerHTML = `
          <td class="mono" title="${escapeHtml(s.channel)}">${escapeHtml(s.channel)}</td>
          <td>${escapeHtml(s.kind)}</td>
          <td>${s.count}</td>
          <td>${s.bytes}</td>
          <td>${hz.toFixed(2)}</td>
          <td title="${s.lastSeenIso}">${new Date(s.lastSeenIso).toLocaleTimeString()}</td>
        `;
        channelsBody.appendChild(tr);
      }
    }

    function updateSelectedRowStyles() {
      const allRows = channelsBody.querySelectorAll("tr");
      for (const row of allRows) {
        if (row.dataset.channel === selectedChannel) {
          row.classList.add("selected");
        } else {
          row.classList.remove("selected");
        }
      }
    }

    function renderSelectedChannel() {
      if (!selectedChannel) {
        channelDetailsEl.textContent = "Click a channel row to inspect its latest content.";
        return;
      }

      const payload = channelPayloads.get(selectedChannel);
      if (!payload) {
        channelDetailsEl.textContent = `No payload captured yet for ${selectedChannel}.`;
        return;
      }

      if (payload.type === "json") {
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify(payload.data, null, 2);
        channelDetailsEl.replaceChildren(pre);
        return;
      }

      if (payload.type === "jpeg") {
        const pre = document.createElement("pre");
        pre.textContent = [
          `channel: ${payload.channel}`,
          `payload_type: jpeg`,
          `jpeg_bytes: ${payload.jpegBytes}`,
          `timestamp_micros: ${payload.timestampMicros !== null ? payload.timestampMicros.toString() : "unknown"}`,
          `header_data_length: ${payload.dataLen}`,
          `header_endianness: ${payload.littleEndian ? "little" : "big"}`,
        ].join("\\n");
        channelDetailsEl.replaceChildren(pre);
      }
    }

    function escapeHtml(text) {
      return String(text)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
    }

    function getOrCreateImagePanel(channel) {
      let panel = imagePanels.get(channel);
      if (panel) return panel;

      const root = document.createElement("div");
      root.className = "img-panel";

      const meta = document.createElement("div");
      meta.className = "img-meta";
      const left = document.createElement("span");
      left.className = "mono";
      left.textContent = channel;
      const right = document.createElement("span");
      right.textContent = "waiting...";
      meta.append(left, right);

      const img = document.createElement("img");
      img.alt = `JPEG stream for ${channel}`;

      root.append(meta, img);
      imagesEl.prepend(root);

      panel = { root, right, img, objectUrl: null };
      imagePanels.set(channel, panel);
      return panel;
    }

    function updateImage(channel, jpegBytes, timestampMicros) {
      const panel = getOrCreateImagePanel(channel);
      if (panel.objectUrl) {
        URL.revokeObjectURL(panel.objectUrl);
      }
      const blob = new Blob([jpegBytes], { type: "image/jpeg" });
      const objectUrl = URL.createObjectURL(blob);
      panel.objectUrl = objectUrl;
      panel.img.src = objectUrl;
      const tsText = timestampMicros !== null
        ? `${new Date(Number(timestampMicros / 1000n)).toLocaleTimeString()} (${timestampMicros} μs)`
        : "timestamp unavailable";
      panel.right.textContent = tsText;
    }

    function parseDialBinaryFrame(buffer) {
      if (!(buffer instanceof ArrayBuffer)) {
        throw new Error("Expected ArrayBuffer for binary frame");
      }
      if (buffer.byteLength < 30) {
        throw new Error(`Binary frame too small: ${buffer.byteLength} bytes`);
      }

      const view = new DataView(buffer);
      const bytes = new Uint8Array(buffer);

      const attempts = [false, true];
      for (const littleEndian of attempts) {
        const channelLen = view.getUint32(20, littleEndian);
        const dataLen = view.getUint32(24, littleEndian);
        const jpegOffset = 28 + channelLen;

        if (channelLen === 0 || channelLen > 4096) continue;
        if (jpegOffset >= bytes.length) continue;

        const channelBytes = bytes.slice(28, jpegOffset);
        let channel;
        try {
          channel = new TextDecoder("utf-8", { fatal: true }).decode(channelBytes);
        } catch {
          continue;
        }

        const jpegBytes = bytes.slice(jpegOffset);
        if (jpegBytes.length < 2) continue;
        if (!(jpegBytes[0] === 0xff && jpegBytes[1] === 0xd8)) continue;

        const timestampMicros = view.getBigUint64(12, littleEndian);
        return {
          channel,
          jpegBytes,
          timestampMicros,
          dataLen,
          littleEndian,
        };
      }

      throw new Error("Unable to parse Dial binary frame header");
    }

    function handleJsonMessage(rawText) {
      let obj;
      try {
        obj = JSON.parse(rawText);
      } catch (err) {
        log(`Invalid JSON text frame: ${err}`);
        return;
      }

      const channel = typeof obj.channel === "string" ? obj.channel : "<unknown-json-channel>";
      const kind = obj?.event && typeof obj.event === "object" && obj.event !== null
        ? (obj.event.type || obj.fingerprint || "json")
        : "json";

      const bytes = new TextEncoder().encode(rawText).length;
      touchStat(channel, String(kind), bytes);
      channelPayloads.set(channel, { type: "json", data: obj });
      if (selectedChannel === channel) {
        renderSelectedChannel();
      }
    }

    function handleBinaryMessage(buffer) {
      try {
        const frame = parseDialBinaryFrame(buffer);
        touchStat(frame.channel, "jpeg", frame.jpegBytes.length);
        updateImage(frame.channel, frame.jpegBytes, frame.timestampMicros);
        channelPayloads.set(frame.channel, {
          type: "jpeg",
          channel: frame.channel,
          jpegBytes: frame.jpegBytes.length,
          timestampMicros: frame.timestampMicros,
          dataLen: frame.dataLen,
          littleEndian: frame.littleEndian,
        });
        if (selectedChannel === frame.channel) {
          renderSelectedChannel();
        }
      } catch (err) {
        log(`Failed to parse binary frame: ${err}`);
      }
    }

    function setConnectionState(state) {
      wsState.textContent = state;
      wsDot.classList.remove("open", "closed");
      if (state === "Open") wsDot.classList.add("open");
      if (state === "Closed" || state === "Error") wsDot.classList.add("closed");
    }

    function connect() {
      setConnectionState("Connecting...");
      const ws = new WebSocket(WS_URL);
      ws.binaryType = "arraybuffer";

      ws.addEventListener("open", () => {
        setConnectionState("Open");
        log("WebSocket connected");
      });

      ws.addEventListener("message", (event) => {
        if (typeof event.data === "string") {
          handleJsonMessage(event.data);
          return;
        }
        if (event.data instanceof ArrayBuffer) {
          handleBinaryMessage(event.data);
          return;
        }
        if (event.data instanceof Blob) {
          event.data.arrayBuffer().then(handleBinaryMessage).catch((err) => {
            log(`Failed reading blob frame: ${err}`);
          });
          return;
        }
        log(`Unsupported message type: ${typeof event.data}`);
      });

      ws.addEventListener("close", (ev) => {
        setConnectionState("Closed");
        log(`WebSocket closed (code=${ev.code})`);
      });

      ws.addEventListener("error", () => {
        setConnectionState("Error");
        log("WebSocket error");
      });
    }

    connect();
  </script>
</body>
</html>
"""


class ViewerHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the inlined viewer page."""

    ws_url = ""

    def do_GET(self):  # noqa: N802 (http.server naming convention)
        parsed = urlsplit(self.path)
        if parsed.path != "/":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not found\n")
            return

        ws_url_json = html.escape(repr(self.ws_url), quote=False)
        page = HTML_TEMPLATE.replace("__WS_URL_JSON__", ws_url_json)

        payload = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        # Keep output concise and useful for an example script.
        print(f"[http] {self.address_string()} - {fmt % args}")


def build_ws_url(ws_url: str | None, ws_host: str, ws_port: int, ws_path: str, ws_secure: bool) -> str:
    """Build the WebSocket URL from explicit URL or host/port/path parts."""
    if ws_url:
        return ws_url
    scheme = "wss" if ws_secure else "ws"
    if not ws_path.startswith("/"):
        ws_path = "/" + ws_path
    return f"{scheme}://{ws_host}:{ws_port}{ws_path}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host to bind (default: %(default)s)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port to bind (default: %(default)s)")
    parser.add_argument("--ws-url", default=None, help="Full WebSocket URL, e.g., ws://localhost:8765/.*")
    parser.add_argument("--ws-host", default="localhost", help="WebSocket host (used when --ws-url is unset)")
    parser.add_argument("--ws-port", type=int, default=8765, help="WebSocket port (used when --ws-url is unset)")
    parser.add_argument(
        "--ws-path",
        default="/.*",
        help="WebSocket path/regex (used when --ws-url is unset), e.g., '/.*' or '/CAMERA_.*'",
    )
    parser.add_argument(
        "--ws-secure",
        action="store_true",
        help="Use wss:// when building URL from --ws-host/--ws-port/--ws-path",
    )
    args = parser.parse_args()

    ws_url = build_ws_url(args.ws_url, args.ws_host, args.ws_port, args.ws_path, args.ws_secure)
    ViewerHandler.ws_url = ws_url

    server = ThreadingHTTPServer((args.host, args.port), ViewerHandler)
    print(f"Serving viewer at http://{args.host}:{args.port}/")
    print(f"Connecting browser client to: {ws_url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("Stopped.")


if __name__ == "__main__":
    main()