#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

INSTALL_DIR=/opt/tg-butler-docker
REPO_URL=https://github.com/xhpx7301/TG-Butler.git

die() { printf '错误：%s\n' "$*" >&2; exit 1; }

compose() {
    cd "$INSTALL_DIR"
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose "$@"
    else
        die '未找到 Docker Compose 插件，请重新运行安装脚本。'
    fi
}

main() {
    [[ "$EUID" -eq 0 ]] || die '请使用 root 或 sudo 运行。'
    [[ -f /etc/os-release ]] || die '无法识别操作系统。'
    # shellcheck source=/dev/null
    . /etc/os-release
    [[ "${ID:-}" == debian && "${VERSION_ID:-}" =~ ^(12|13)$ ]] \
        || die '此脚本仅支持 Debian 12 / 13。'

    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates git docker.io
    if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin \
            || DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose
    fi
    systemctl enable --now docker

    if [[ -d "$INSTALL_DIR/.git" ]]; then
        git -C "$INSTALL_DIR" fetch origin main
        git -C "$INSTALL_DIR" merge --ff-only origin/main
    elif [[ -e "$INSTALL_DIR" ]]; then
        die "$INSTALL_DIR 已存在但不是 Git 仓库，请先人工检查。"
    else
        git clone --branch main --single-branch "$REPO_URL" "$INSTALL_DIR"
    fi

    if systemctl is-active --quiet tg-butler-web.service || systemctl is-active --quiet tg-butler.service; then
        printf '检测到原生 systemd 服务，先停止以释放 15678 端口。\n'
        systemctl disable --now tg-butler-web.service tg-butler.service || true
    fi

    if [[ -e /usr/local/bin/bu ]] && ! grep -q 'TG-Butler Docker 管理' /usr/local/bin/bu 2>/dev/null; then
        install -m 755 /usr/local/bin/bu /usr/local/bin/bu.native
        printf '原有 bu 命令已备份为 /usr/local/bin/bu.native。\n'
    fi
    install -m 755 "$INSTALL_DIR/deploy/docker-menu.sh" /usr/local/bin/bu
    compose up -d --build
    printf '\nDocker 部署完成。\n'
    printf '网页地址：http://服务器IP:15678\n'
    printf '管理菜单：输入 bu\n'
    printf '首次登录后请立即修改默认管理员密码，并完成 Telegram 配置。\n'
}

trap 'printf "\nDocker 安装中断（第 %s 行）。已有配置和数据会保留。\n" "$LINENO" >&2' ERR
main "$@"
