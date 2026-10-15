#!/usr/bin/env bash
set -euo pipefail
[[ "$EUID" -eq 0 ]] || { printf '需要 root 权限。\n' >&2; exit 1; }
INSTALL_DIR=/opt/tg-butler
if ! command -v sudo >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y sudo
fi
install -d -m 755 /usr/local/libexec
install -m 755 "$INSTALL_DIR/deploy/control.sh" /usr/local/libexec/tg-butler-control
visudo -cf "$INSTALL_DIR/deploy/web-sudoers"
install -m 440 "$INSTALL_DIR/deploy/web-sudoers" /etc/sudoers.d/tg-butler-web
if [[ ! -f /etc/systemd/system/tg-butler-web.service ]]; then
    install -m 644 "$INSTALL_DIR/deploy/tg-butler-web.service" /etc/systemd/system/tg-butler-web.service
fi
# Shared SQLite ownership must stay with the unprivileged service account.
runuser -u tgbutler -- "$INSTALL_DIR/.venv/bin/python" -m tg_butler.admin \
    initialize --root "$INSTALL_DIR"
systemctl daemon-reload
# Enable monitoring on boot; it can be configured and started later from the website.
systemctl enable tg-butler.service
systemctl enable --now tg-butler-web.service
printf '网页后台已启动并设为开机自启：默认 127.0.0.1:15678。管理员可在网页或 bu → 1 网页后台 → 2 管理员账户 修改。\n'
