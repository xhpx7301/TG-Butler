#!/usr/bin/env bash
# Narrow privileged helper: no caller-controlled unit, path or command.
set -euo pipefail
[[ $# -eq 1 ]] || exit 2
case "$1" in
    status) /usr/bin/systemctl is-active tg-butler.service || true ;;
    start) /usr/bin/systemctl reset-failed tg-butler.service; /usr/bin/systemctl start tg-butler.service ;;
    stop) /usr/bin/systemctl stop tg-butler.service ;;
    restart) /usr/bin/systemctl restart tg-butler.service ;;
    logs) /usr/bin/journalctl -u tg-butler.service -n 80 --no-pager ;;
    *) exit 2 ;;
esac
