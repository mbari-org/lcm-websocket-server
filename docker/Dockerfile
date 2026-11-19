FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt update && \
    apt install -y --no-install-recommends libglib2.0-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy app into container
COPY . /app
WORKDIR /app

# Build lcm_websocket_server*.whl
RUN uv build --wheel

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Copy glib-2.0 shared libraries
COPY --from=builder /usr/lib/x86_64-linux-gnu/libglib-2.0.so.0 /usr/lib/x86_64-linux-gnu/libglib-2.0.so.0

# Install lcm-websocket-server with the performance extras
COPY --from=builder /app/dist/*.whl /tmp/
RUN uv pip install --system --no-cache "$(ls /tmp/lcm_websocket_server-*.whl)[orjson,uvloop]"

ENV HOST=0.0.0.0
ENV PORT=8765
ENV CHANNEL=.*
ENV LCM_PACKAGES=
# Extra command-line flags, e.g. "--uvloop --processes 4 -vv"
ENV ARGS=

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['PORT'], timeout=3)"]

CMD ["sh", "-c", "PYTHONPATH=$PYTHONPATH:/app lcm-websocket-json-proxy --host \"$HOST\" --port \"$PORT\" --channel \"$CHANNEL\" $ARGS \"$LCM_PACKAGES\""]
