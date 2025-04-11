#!/usr/bin/env bash

# Build all of the docker images

EXTS=( "dial" )

./scripts/docker_build.sh

for ext in "${EXTS[@]}"; do
    ./scripts/docker_build.sh $ext
done