#!/usr/bin/env bash
# Smoke-test an image: start each proxy, wait for /healthz, check /metrics.
# Usage: docker/smoke_test.sh IMAGE
set -euo pipefail

IMAGE=${1:?usage: $0 IMAGE}

for proxy in json dial; do
    name="lws-smoke-$proxy-$$"
    # memq:// keeps LCM in-process, so no multicast route is needed
    docker run -d --rm --name "$name" -p 127.0.0.1::8765 \
        -e PROXY="$proxy" -e LCM_DEFAULT_URL=memq:// -e ARGS="-v" "$IMAGE" >/dev/null
    trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
    port=$(docker port "$name" 8765/tcp | head -n1 | cut -d: -f2)

    ok=""
    for _ in $(seq 1 30); do
        if curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
            ok=1
            break
        fi
        if ! docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -q true; then
            break
        fi
        sleep 1
    done
    if [ -z "$ok" ]; then
        echo "FAIL: $proxy proxy did not become healthy" >&2
        docker logs "$name" >&2 || true
        exit 1
    fi
    curl -fsS "http://127.0.0.1:$port/metrics" | grep -q '^lws_clients 0$'
    echo "OK: $proxy proxy healthy on port $port"
    docker rm -f "$name" >/dev/null
done
