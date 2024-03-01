#!/usr/bin/env bash

# Read the first argument from the command line. This will specify which image we are pushing.

DOCKEROPT=${1:-""}
VERSION=$(grep '^version =' pyproject.toml | awk -F'"' '{print $2}')
DOCKERTAG="mbari/lcm-websocket-server:$VERSION"
if [ -n "$DOCKEROPT" ]; then
    DOCKERTAG="mbari/lcm-websocket-server:$DOCKEROPT-$VERSION"
fi

echo "Pushing docker image $DOCKERTAG"
docker push $DOCKERTAG