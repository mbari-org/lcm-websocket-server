#!/bin/sh
# Start the proxy selected by $PROXY (json or dial), configured from the
# environment. Any other command (e.g. "bash") is executed as given.
set -e

if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ]; then
    exec "$@"
fi

case "$PROXY" in
    json | dial) ;;
    *)
        echo "Unknown PROXY '$PROXY' (expected 'json' or 'dial')" >&2
        exit 2
        ;;
esac

# ARGS is intentionally unquoted so it splits into separate flags
# shellcheck disable=SC2086
exec "lcm-websocket-$PROXY-proxy" \
    --host "$HOST" \
    --port "$PORT" \
    --channel "$CHANNEL" \
    --lcm-packages "$LCM_PACKAGES" \
    $ARGS "$@"
