#!/usr/bin/env bash

# Build all of the docker images

EXTS=( "compas-lcmtypes" "dial" )

./scripts/docker_build.sh

for ext in "${EXTS[@]}"; do
    ./scripts/docker_build.sh $ext
done