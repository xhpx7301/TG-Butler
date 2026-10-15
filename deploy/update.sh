#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

INSTALL_DIR=/opt/tg-butler
WAS_ACTIVE=0
STOPPED=0
as_user() { runuser -u tgbutler -- "$@"; }
die() { printf '更新失败：%s\n' "$*" >&2; exit 1; }

on_error() {
    printf '\n更新中断。配置和数据已保留。\n' >&2
    if [[ "$STOPPED" -eq 1 ]]; then
        printf '服务已停止。请排查上方错误后重新运行更新，再手动启动服务。\n' >&2
    fi
}

main() {
    if [[ "${1:-}" == --help ]]; then
        printf '用法：sudo bash /opt/tg-butler/deploy/update.sh\n更新 main、依赖、bu 菜单和 systemd 服务；保留个人配置及数据。\n'
        return 0
    fi
    [[ $# -eq 0 ]] || die "不支持的参数。"
    [[ "$EUID" -eq 0 ]] || die "需要 root 或 sudo 权限。"
    [[ -d /run/systemd/system ]] || die "需要运行中的 systemd。"
    [[ ! -L "$INSTALL_DIR" && -d "$INSTALL_DIR/.git" && ! -L "$INSTALL_DIR/.git" ]] \
        || die "安装目录无效。"
    [[ "$(as_user git -C "$INSTALL_DIR" remote get-url origin)" == \
        https://github.com/xhpx7301/TG-Butler.git ]] || die "仓库来源不匹配。"
    [[ "$(as_user git -C "$INSTALL_DIR" branch --show-current)" == main ]] \
        || die "必须在 main 分支。"
    if ! as_user git -C "$INSTALL_DIR" diff --quiet \
        || ! as_user git -C "$INSTALL_DIR" diff --cached --quiet; then
        die "存在未提交的代码修改，请先人工处理。"
    fi
    as_user git -C "$INSTALL_DIR" fetch origin main
    as_user git -C "$INSTALL_DIR" merge-base --is-ancestor HEAD origin/main \
        || die "本地代码与远程已分叉，未覆盖本地修改。"
    if systemctl is-active --quiet tg-butler.service; then
        WAS_ACTIVE=1
        systemctl stop tg-butler.service
        STOPPED=1
    fi
    if systemctl is-active --quiet tg-butler-web.service; then
        systemctl stop tg-butler-web.service
    fi
    as_user git -C "$INSTALL_DIR" merge --ff-only origin/main
    as_user "$INSTALL_DIR/.venv/bin/pip" install --upgrade "$INSTALL_DIR"
    install -m 755 "$INSTALL_DIR/deploy/bu" /usr/local/bin/bu
    # Preserve locally customized service paths/hardening, particularly data_dir.
    if ! cmp -s "$INSTALL_DIR/deploy/tg-butler.service" /etc/systemd/system/tg-butler.service; then
        install -m 644 "$INSTALL_DIR/deploy/tg-butler.service" \
            /etc/systemd/system/tg-butler.service.new
        printf '新版服务模板保存为 /etc/systemd/system/tg-butler.service.new；现有服务文件保留。\n'
    fi
    systemctl daemon-reload
    bash "$INSTALL_DIR/deploy/setup-web.sh"
    systemctl restart tg-butler-web.service
    if [[ "$WAS_ACTIVE" -eq 1 ]]; then
        systemctl reset-failed tg-butler.service
        systemctl start tg-butler.service
        STOPPED=0
    fi
    printf '更新成功。原运行状态已恢复，配置和数据未覆盖。请输入 bu 打开新版菜单。\n'
}

trap on_error ERR
main "$@"
