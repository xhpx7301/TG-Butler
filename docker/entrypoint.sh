#!/bin/sh
set -eu

STATE_DIR=/var/lib/tg-butler
DATA_DIR="$STATE_DIR/data"

umask 077
mkdir -p "$DATA_DIR"

if [ ! -f "$STATE_DIR/.env" ]; then
    cp /opt/tg-butler/.env.example "$STATE_DIR/.env"
fi
if [ ! -f "$STATE_DIR/config.yaml" ]; then
    cp /opt/tg-butler/config.example.yaml "$STATE_DIR/config.yaml"
fi
if [ ! -f "$DATA_DIR/web.json" ]; then
    printf '%s\n' '{"host":"0.0.0.0","port":15678,"secure_cookie":false}' > "$DATA_DIR/web.json"
fi
chmod 600 "$STATE_DIR/.env" "$STATE_DIR/config.yaml" "$DATA_DIR/web.json"
chown tgbutler:tgbutler "$STATE_DIR" "$DATA_DIR" "$STATE_DIR/.env" "$STATE_DIR/config.yaml" "$DATA_DIR/web.json"

# The web UI controls the monitor through supervisor. Persist the desired
# monitor state so a container restart restores a service previously started
# from the UI.
if [ -f "$DATA_DIR/monitor-enabled" ]; then
    export TG_BUTLER_MONITOR_AUTOSTART=true
else
    export TG_BUTLER_MONITOR_AUTOSTART=false
fi

exec /opt/venv/bin/supervisord -c /etc/supervisor/supervisord.conf
