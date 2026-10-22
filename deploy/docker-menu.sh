#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$EUID" -ne 0 ]]; then
    exec sudo /usr/local/bin/bu "$@"
fi

INSTALL_DIR=${TG_BUTLER_DOCKER_DIR:-/opt/tg-butler-docker}

die() { printf '错误：%s\n' "$*" >&2; exit 1; }
[[ -d "$INSTALL_DIR" ]] || die "未找到 Docker 项目目录：$INSTALL_DIR"

exec 9>/run/lock/tg-butler-docker-menu.lock
flock -n 9 || die '已有 Docker 管理菜单正在运行，请先退出另一个菜单。'

compose() {
    cd "$INSTALL_DIR"
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose "$@"
    else
        die '未找到 Docker Compose。'
    fi
}

update() {
    git -C "$INSTALL_DIR" fetch origin main
    git -C "$INSTALL_DIR" merge --ff-only origin/main
    compose up -d --build
}

uninstall() {
    local mode="$1"
    bash "$INSTALL_DIR/deploy/docker-uninstall.sh" "$mode"
    exit 0
}

action="${1:-menu}"
case "$action" in
    status) compose ps ;;
    start) compose up -d ;;
    stop) compose stop ;;
    restart) compose up -d --force-recreate ;;
    logs) compose logs --tail=100 -f ;;
    update) update ;;
    menu)
        while true; do
            printf '\nTG-Butler Docker 管理\n'
            printf '1. 查看状态\n2. 启动\n3. 停止\n4. 重启\n5. 查看日志\n6. 更新项目\n7. 卸载 Docker（保留数据）\n8. 卸载 Docker 并清除全部数据\n0. 退出\n'
            read -r -p '请选择：' choice
            case "$choice" in
                1) compose ps ;;
                2) compose up -d ;;
                3) compose stop ;;
                4) compose up -d --force-recreate ;;
                5) compose logs --tail=100 ;;
                6) update ;;
                7) uninstall --keep-data ;;
                8) uninstall --purge ;;
                0) exit 0 ;;
                *) printf '无效选项。\n' >&2 ;;
            esac
        done
        ;;
    uninstall-keep-data) uninstall --keep-data ;;
    uninstall) uninstall --purge ;;
    *) die "用法：bu [status|start|stop|restart|logs|update|uninstall-keep-data|uninstall]" ;;
esac
