# syntax=docker/dockerfile:1

# Build a virtual environment from the locked dependencies
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /app
ARG EXTRAS="--extra image --extra orjson --extra uvloop --extra lcmtypes"

# Dependencies first so they are cached across source changes
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project $EXTRAS

COPY . /src
RUN --mount=type=cache,target=/root/.cache/uv \
    cd /src && UV_PROJECT_ENVIRONMENT=/app/.venv uv sync --locked --no-dev --no-editable $EXTRAS

# Runtime image: same Python as the builder, without uv or sources
FROM python:3.13-slim-bookworm

# libglib is required by the lcm Python bindings
RUN apt-get update && \
    apt-get install -y --no-install-recommends libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY docker/entrypoint.sh /usr/local/bin/lws-entrypoint

# Packages mounted under /app (e.g. -v ./my_types:/app/my_types) are importable
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    PROXY=json \
    HOST=0.0.0.0 \
    PORT=8765 \
    CHANNEL=.* \
    LCM_PACKAGES=compas_lcmtypes,molars_lcmtypes \
    ARGS=

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['PORT'], timeout=3)"]

ENTRYPOINT ["lws-entrypoint"]
