VERSION=$(grep '^version =' pyproject.toml | awk -F'"' '{print $2}')

EXTS=( "dial" )

docker tag mbari/lcm-websocket-server:$VERSION mbari/lcm-websocket-server:latest
docker push mbari/lcm-websocket-server:latest

for EXT in "${EXTS[@]}"; do
    docker tag mbari/lcm-websocket-server:$VERSION-$EXT mbari/lcm-websocket-server:latest-$EXT
    docker push mbari/lcm-websocket-server:latest-$EXT
done