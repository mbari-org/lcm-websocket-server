FROM python:3.10-slim-buster as builder
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt update && \
    apt install -y --no-install-recommends git build-essential cmake pkg-config libglib2.0-dev && \
    rm -rf /var/lib/apt/lists/*

# Clone, build, and install LCM
RUN git clone --depth=1 --branch=v1.5.0 https://github.com/lcm-proj/lcm.git && \
    cd lcm && \
    mkdir build && \
    cd build && \
    cmake \
        -DCMAKE_BUILD_TYPE=Release \
        -DLCM_ENABLE_EXAMPLES=OFF \
        -DLCM_ENABLE_JAVA=OFF \
        -DLCM_ENABLE_LUA=OFF \
        -DLCM_ENABLE_GO=OFF \
        -DLCM_ENABLE_TESTS=OFF \
        .. && \
    make -j $(nproc) && \
    make install

# Install Python dependencies
RUN pip install poetry

# Copy app into container
COPY . /app
WORKDIR /app

# Build lcm_websocket_server*.whl
RUN poetry build

FROM python:3.10-slim-buster

# pkg-config seems still needed
RUN apt update && \
    apt install -y --no-install-recommends pkg-config && \
    rm -rf /var/lib/apt/lists/*

# Copy lcm artifacts:
COPY --from=builder /usr/local/lib/liblcm* /usr/lib/
COPY --from=builder /usr/local/lib/lcm /usr/lib/lcm
COPY --from=builder /usr/local/lib/pkgconfig/lcm.pc /usr/lib/pkgconfig/
COPY --from=builder /usr/local/include/lcm /usr/include/lcm
COPY --from=builder /usr/local/share/aclocal /usr/share/aclocal
COPY --from=builder /usr/local/bin/lcm* /usr/bin/
# lcm python bindings:
COPY --from=builder /usr/local/lib/python3.10/site-packages/lcm /usr/local/lib/python3.10/site-packages/lcm

# Install lcm-websocket-server:
COPY --from=builder /app/dist/*.whl /tmp/
RUN pip install /tmp/*.whl

ENV HOST=0.0.0.0
ENV PORT=8765
ENV CHANNEL=.*
ENV LCM_PACKAGES=

CMD PYTHONPATH=$PYTHONPATH:/app lcm-websocket-server --host "$HOST" --port "$PORT" --channel "$CHANNEL" "$LCM_PACKAGES"
