#!/usr/bin/env bash

# Read the first argument from the command line. This will specify which image we are pushing.

DOCKEROPT=${1:-""}
VERSION=$(grep '^version =' pyproject.toml | awk -F'"' '{print $2}')
DOCKERTAG="$VERSION"
if [ -n "$DOCKEROPT" ]; then
    DOCKERTAG="$DOCKERTAG-$DOCKEROPT"
fi
DOCKERIMAGE="mbari/lcm-websocket-server:$DOCKERTAG"

echo "Pushing docker image $DOCKERIMAGE"
docker push $DOCKERIMAGE