FROM python:3.10-slim-buster AS builder
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt update && \
    apt install -y --no-install-recommends libglib2.0-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir poetry

# Copy app into container
COPY . /app
WORKDIR /app

# Build lcm_websocket_server*.whl
RUN poetry build

FROM python:3.10-slim-buster

# Copy glib-2.0 shared libraries
COPY --from=builder /usr/lib/x86_64-linux-gnu/libglib-2.0.so.0 /usr/lib/x86_64-linux-gnu/libglib-2.0.so.0

# Install lcm-websocket-server:
COPY --from=builder /app/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl

ENV HOST=0.0.0.0
ENV PORT=8765
ENV CHANNEL=.*
ENV LCM_PACKAGES=

CMD ["sh", "-c", "PYTHONPATH=$PYTHONPATH:/app lcm-websocket-server --host \"$HOST\" --port \"$PORT\" --channel \"$CHANNEL\" \"$LCM_PACKAGES\""]
