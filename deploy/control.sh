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
    update)
        if /usr/bin/systemctl is-active --quiet tg-butler-update.service; then
            printf '更新已在进行中，请稍后刷新页面查看结果。\n'
            exit 0
        fi
        /usr/bin/systemctl reset-failed tg-butler-update.service >/dev/null 2>&1 || true
        /usr/bin/systemd-run --unit=tg-butler-update.service --collect --no-block \
            /usr/bin/bash /opt/tg-butler/deploy/update.sh
        printf '更新已开始，网页会短暂断开；完成后请刷新页面确认版本和服务状态。\n'
        ;;
    *) exit 2 ;;
esac
