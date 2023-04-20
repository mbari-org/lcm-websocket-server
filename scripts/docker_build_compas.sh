#!/usr/bin/env bash

docker build \
    -t mbari/lcm-websocket-server:compas \
    -f docker/Dockerfile.compas \
    .