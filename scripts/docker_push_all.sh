#!/usr/bin/env bash

# Push all of the docker images

EXTS=( "compas-lcmtypes" "dial" )

./scripts/docker_push.sh

for ext in "${EXTS[@]}"; do
    ./scripts/docker_push.sh $ext
done