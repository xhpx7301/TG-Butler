#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

INSTALL_DIR=${TG_BUTLER_DOCKER_DIR:-/opt/tg-butler-docker}
BU_PATH=/usr/local/bin/bu

die() { printf '卸载失败：%s\n' "$*" >&2; exit 1; }

[[ "$EUID" -eq 0 ]] || die '请使用 root 或 sudo 运行。'
[[ "$INSTALL_DIR" == */tg-butler-docker && "$INSTALL_DIR" != / ]] \
    || die "拒绝处理不安全的 Docker 安装路径：$INSTALL_DIR"

compose() {
    [[ -d "$INSTALL_DIR" ]] || return 0
    cd "$INSTALL_DIR"
    if docker compose version >/dev/null 2>&1; then
        docker compose "$@"
    elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose "$@"
    else
        die '未找到 Docker Compose。'
    fi
}

confirm() {
    local expected="$1" prompt="$2" answer
    printf '\n%s\n' "$prompt"
    read -r -p "输入 $expected 确认（其他输入取消）：" answer || answer=
    if [[ "$answer" != "$expected" ]]; then
        printf '已取消卸载，未作任何修改。\n'
        exit 2
    fi
}

remove_tg_butler_containers() {
    local container_id container_name
    while IFS='|' read -r container_id container_name; do
        [[ -n "$container_id" && "$container_name" == *tg-butler* ]] || continue
        printf '移除容器：%s\n' "$container_name"
        docker rm -f "$container_id" >/dev/null 2>&1 || true
    done < <(docker ps -a --format '{{.ID}}|{{.Names}}' 2>/dev/null || true)
}

remove_tg_butler_images() {
    local repository
    while IFS= read -r repository; do
        [[ -n "$repository" && "$repository" == *tg-butler* ]] || continue
        printf '移除镜像：%s\n' "$repository"
        docker image rm "$repository" >/dev/null 2>&1 || true
    done < <(docker image ls --format '{{.Repository}}' 2>/dev/null | sort -u || true)
}

remove_tg_butler_networks() {
    local network_id network_name
    while IFS='|' read -r network_id network_name; do
        [[ -n "$network_id" && "$network_name" == *tg-butler* ]] || continue
        docker network rm "$network_id" >/dev/null 2>&1 || true
    done < <(docker network ls --format '{{.ID}}|{{.Name}}' 2>/dev/null || true)
}

remove_tg_butler_volumes() {
    local volume
    while IFS= read -r volume; do
        [[ -n "$volume" && "$volume" == *tg-butler-state ]] || continue
        printf '移除数据卷：%s\n' "$volume"
        docker volume rm "$volume" >/dev/null 2>&1 || true
    done < <(docker volume ls --format '{{.Name}}' 2>/dev/null || true)
}

restore_native_bu() {
    if [[ -f /usr/local/bin/bu.native ]]; then
        install -m 755 /usr/local/bin/bu.native "$BU_PATH"
        rm -f /usr/local/bin/bu.native
        printf '已恢复原生部署的 bu 命令。\n'
    else
        rm -f "$BU_PATH"
    fi
}

main() {
    local mode="keep-data"
    case "${1:-}" in
        --keep-data) mode="keep-data" ;;
        --purge) mode="purge" ;;
        --help|-h)
            printf '用法：sudo bash %s [--keep-data|--purge]\n' "$0"
            printf '卸载 Docker 容器、镜像、项目和 bu；--keep-data 保留 tg-butler-state 数据卷，--purge 同时清除数据卷。\n'
            return 0
            ;;
        *) die '用法：docker-uninstall.sh [--keep-data|--purge]' ;;
    esac

    if [[ "$mode" == purge ]]; then
        confirm DELETE '将卸载 Docker 部署并永久删除 TG-Butler 数据卷、配置、账号会话、消息数据库和备份。此操作不可撤销，请先导出备份。'
    else
        confirm REMOVE '将卸载 Docker 容器、镜像、项目目录和 bu，但保留 tg-butler-state 数据卷，之后可重新部署恢复数据。'
    fi

    compose down --remove-orphans --rmi local >/dev/null 2>&1 || true
    remove_tg_butler_containers
    remove_tg_butler_images
    remove_tg_butler_networks
    if [[ "$mode" == purge ]]; then
        remove_tg_butler_volumes
    fi
    cd /
    rm -rf --one-file-system -- "$INSTALL_DIR"
    restore_native_bu
    rm -f /run/lock/tg-butler-docker-menu.lock

    if [[ "$mode" == purge ]]; then
        printf '\nDocker 部署已卸载，容器、镜像、项目目录和数据卷已清除。\n'
    else
        printf '\nDocker 部署已卸载，tg-butler-state 数据卷已保留。\n'
    fi
}

trap 'printf "\n卸载中断（第 %s 行），可能尚有残留，请检查上方错误后重试。\n" "$LINENO" >&2' ERR
main "$@"
