FROM python:3.10-slim-buster
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt update && \
    apt install -y --no-install-recommends git build-essential cmake pkg-config libglib2.0-dev && \
    rm -rf /var/lib/apt/lists/*

# Clone, build, and install LCM
RUN git clone https://github.com/lcm-proj/lcm.git && \
    cd lcm && \
    git checkout v1.5.0 && \
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

# Build and install lcm-websocket-server
RUN poetry build
RUN pip install dist/*.whl

ENV HOST=0.0.0.0
ENV PORT=8765
ENV CHANNEL=.*
ENV LCM_PACKAGES=

CMD PYTHONPATH=$PYTHONPATH:/app lcm-websocket-server --host "$HOST" --port "$PORT" --channel "$CHANNEL" "$LCM_PACKAGES"
