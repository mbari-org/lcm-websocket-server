#!/usr/bin/env bash

# Read the first argument from the command line. This will specify which image we are building:
# - empty: docker/Dockerfile
# - x: docker/Dockerfile.x

# If no argument is provided, use the default Dockerfile

DOCKEROPT=${1:-""}
DOCKERFILE="Dockerfile"
if [ -n "$DOCKEROPT" ]; then
    DOCKERFILE="Dockerfile.$DOCKEROPT"
fi
# Check that the Dockerfile exists
if [ ! -f "docker/$DOCKERFILE" ]; then
    echo "Dockerfile $DOCKERFILE does not exist"
    echo "Usage: $0 [DOCKERFILE EXTENSION]"
    exit 1
fi

VERSION=$(grep '^version =' pyproject.toml | awk -F'"' '{print $2}')
DOCKERTAG="$VERSION"
if [ -n "$DOCKEROPT" ]; then
    DOCKERTAG="$DOCKEROPT-$VERSION"
fi

DOCKERIMAGE="mbari/lcm-websocket-server:$DOCKERTAG"

echo "Building docker image $DOCKERIMAGE"
docker build -t $DOCKERIMAGE -f docker/$DOCKERFILE --build-arg VERSION=$VERSION .