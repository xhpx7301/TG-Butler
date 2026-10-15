#!/usr/bin/env bash
# Remove only the fixed Debian installation; never follow external data paths.
set -Eeuo pipefail
umask 077

INSTALL_DIR=/opt/tg-butler
SERVICE_USER=tgbutler

die() { printf '卸载失败：%s\n' "$*" >&2; exit 1; }

require_environment() {
    [[ "$EUID" -eq 0 ]] || die "需要 root 或 sudo 权限。"
    [[ -d /run/systemd/system ]] || die "需要运行中的 systemd。"
}

main() {
    if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
        printf '用法：sudo bash /opt/tg-butler/deploy/uninstall.sh\n停止并移除服务、bu、专用账号及 /opt/tg-butler 内的全部配置和数据。\n需要交互输入 DELETE 确认；取消时退出码为 2。安装目录外的数据和系统日志保留。\n'
        return 0
    fi
    [[ $# -eq 0 ]] || die "不支持的参数。"
    require_environment
    [[ ! -L "$INSTALL_DIR" && "$(readlink -f -- "$INSTALL_DIR")" == "$INSTALL_DIR" ]] \
        || die "安装路径不是预期的真实目录，请人工检查。"
    # Even a bind mount could contain unrelated data. Refuse before stopping services.
    local mounts target
    mounts=$(findmnt --raw --noheadings --output TARGET)
    while IFS= read -r target; do
        case "$target" in
            "$INSTALL_DIR"|"$INSTALL_DIR"/*) die "安装目录内存在挂载点，请先卸载挂载点。" ;;
        esac
    done <<< "$mounts"
    if id "$SERVICE_USER" >/dev/null 2>&1; then
        [[ "$(id -u "$SERVICE_USER")" -ne 0 ]] || die "服务账号不能使用 UID 0。"
        [[ "$(getent passwd "$SERVICE_USER" | cut -d: -f6)" == "$INSTALL_DIR" ]] \
            || die "tgbutler 账号不属于此安装目录，请人工检查。"
        [[ "$(id -gn "$SERVICE_USER")" == "$SERVICE_USER" ]] \
            || die "tgbutler 账号主组不匹配，请人工检查。"
    fi

    printf '\n将永久删除 TG-Butler：\n'
    printf '  - 监控任务和网页后台服务、开机自启及服务覆盖配置\n'
    printf '  - bu 命令、网页提权规则和控制脚本、tgbutler 专用账号与组\n'
    printf '  - /opt/tg-butler 全部内容（含 .env、config.yaml、Telegram 登录会话、消息数据库、网页管理员及配置）\n'
    printf '安装目录外的自定义数据、符号链接目标、系统日志和系统依赖包保留。\n'
    printf '此操作不可撤销；如需保留数据，请先备份。\n'
    local confirmation
    read -r -p '输入 DELETE 确认卸载清除（其他输入取消）：' confirmation || confirmation=
    if [[ "$confirmation" != DELETE ]]; then
        printf '已取消卸载，未作任何修改。\n'
        exit 2
    fi

    # Abort on stop/disable errors rather than deleting files under running services.
    local unit state
    for unit in tg-butler-web.service tg-butler.service; do
        state=$(systemctl show --property=LoadState --value "$unit")
        if [[ "$state" != not-found ]]; then
            systemctl stop "$unit"
            systemctl disable "$unit"
            # Inactive units may be unloaded after stop/disable. Clearing their
            # failure counters is optional and must not prevent uninstallation.
            systemctl reset-failed "$unit" 2>/dev/null || true
        fi
    done
    rm -f -- /etc/systemd/system/tg-butler.service \
        /etc/systemd/system/tg-butler.service.new \
        /etc/systemd/system/tg-butler-web.service \
        /etc/systemd/system/tg-butler-web.service.new \
        /etc/sudoers.d/tg-butler-web /usr/local/libexec/tg-butler-control /usr/local/bin/bu
    rm -rf -- /etc/systemd/system/tg-butler.service.d /etc/systemd/system/tg-butler-web.service.d
    systemctl daemon-reload
    if id "$SERVICE_USER" >/dev/null 2>&1; then
        # No --remove: home cleanup is restricted to the checked fixed directory below.
        userdel "$SERVICE_USER"
    fi
    if getent group "$SERVICE_USER" >/dev/null; then
        groupdel "$SERVICE_USER"
    fi
    cd /
    rm -rf --one-file-system -- "$INSTALL_DIR"
    printf '\nTG-Butler 已卸载，安装目录内的配置和数据已清除。\n'
}

trap 'printf "\n卸载中断（第 %s 行），可能尚有残留，请检查上方错误后重试。\n" "$LINENO" >&2' ERR
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
