#!/usr/bin/env bash
# Debian installer. Credentials and Telegram login are handled on the server's TTY.
set -Eeuo pipefail
umask 077

INSTALL_DIR=/opt/tg-butler
SERVICE_USER=tgbutler
REPO_URL=https://github.com/xhpx7301/TG-Butler.git
NON_INTERACTIVE=0

die() { printf '错误：%s\n' "$*" >&2; exit 1; }
as_user() { runuser -u "$SERVICE_USER" -- "$@"; }

usage() {
    cat <<'HELP'
TG-Butler Debian 一键安装
用法：sudo bash deploy/install.sh [--non-interactive]
  默认：安装后进入 bu 中文管理菜单，填写参数、登录 Telegram 并管理服务。
  --non-interactive：安装环境并启动网页，保留现有配置，不登录或启动监控任务。
  --help：显示此帮助。
支持 Debian 12 / 13、Python 3.11+、运行中的 systemd。重复执行保留配置和 data/。
HELP
}

main() {
    for arg in "$@"; do
        case "$arg" in
            --non-interactive) NON_INTERACTIVE=1 ;;
            --help|-h) usage; return 0 ;;
            *) die "未知参数：$arg（使用 --help 查看用法）" ;;
        esac
    done
    [[ "$EUID" -eq 0 ]] || die "请使用 root 或 sudo 运行安装脚本。"
    [[ -f /etc/os-release ]] || die "无法识别操作系统。"
    # shellcheck source=/dev/null
    . /etc/os-release
    [[ "${ID:-}" == debian ]] || die "此脚本仅支持 Debian 12 / 13。"
    case "${VERSION_ID:-}" in
        12|13) ;;
        *) die "此脚本仅支持 Debian 12 / 13。" ;;
    esac
    [[ -d /run/systemd/system ]] || die "需要以 systemd 启动的 Debian 服务器。"
    if [[ "$NON_INTERACTIVE" -eq 0 ]]; then
        # Opening a separate FD keeps curl | bash's script input intact.
        { exec 3<>/dev/tty; } 2>/dev/null || die "需要交互终端；仅安装可用 --non-interactive。"
    fi
    [[ ! -L "$INSTALL_DIR" ]] || die "$INSTALL_DIR 不能是符号链接。"
    if [[ -e "$INSTALL_DIR" && ! -d "$INSTALL_DIR/.git" ]]; then
        die "$INSTALL_DIR 已存在且不是 Git 仓库，请先人工检查目录。"
    fi
    printf '\n[1/5] 安装 Debian 依赖\n'
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        ca-certificates curl git python3 python3-venv tzdata nano util-linux passwd sudo
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
        || die "需要 Python 3.11 或更新版本。"

    printf '\n[2/5] 准备专用账号和代码\n'
    if id "$SERVICE_USER" >/dev/null 2>&1; then
        [[ "$(id -u "$SERVICE_USER")" -ne 0 ]] || die "服务账号不能使用 UID 0。"
        [[ "$(getent passwd "$SERVICE_USER" | cut -d: -f6)" == "$INSTALL_DIR" ]] \
            || die "已有 tgbutler 用户的 home 不是 $INSTALL_DIR，请人工检查。"
        [[ "$(id -gn "$SERVICE_USER")" == "$SERVICE_USER" ]] \
            || die "已有 tgbutler 用户的主组必须为 tgbutler。"
    else
        useradd --system --user-group --home-dir "$INSTALL_DIR" \
            --shell /usr/sbin/nologin "$SERVICE_USER"
    fi
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        [[ ! -L "$INSTALL_DIR/.git" ]] || die ".git 不能是符号链接。"
        [[ "$(as_user git -C "$INSTALL_DIR" remote get-url origin)" == "$REPO_URL" ]] \
            || die "现有仓库的 origin 与官方仓库不一致。"
        [[ "$(as_user git -C "$INSTALL_DIR" branch --show-current)" == main ]] \
            || die "现有仓库必须位于 main 分支。"
        if ! as_user git -C "$INSTALL_DIR" diff --quiet \
            || ! as_user git -C "$INSTALL_DIR" diff --cached --quiet; then
            die "仓库有未提交的修改，请先人工处理。"
        fi
        as_user git -C "$INSTALL_DIR" pull --ff-only origin main
    else
        git clone --branch main --single-branch "$REPO_URL" "$INSTALL_DIR"
        chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
    fi

    printf '\n[3/5] 安装 Python 环境并保留个人配置\n'
    # Stop before replacing the environment or allowing an interactive session login.
    if systemctl is-active --quiet tg-butler.service; then
        systemctl stop tg-butler.service
    fi
    as_user python3 -m venv "$INSTALL_DIR/.venv"
    as_user "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip wheel
    as_user "$INSTALL_DIR/.venv/bin/pip" install --upgrade "$INSTALL_DIR"
    if [[ ! -e "$INSTALL_DIR/.env" ]]; then
        as_user cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    fi
    if [[ ! -e "$INSTALL_DIR/config.yaml" ]]; then
        as_user cp "$INSTALL_DIR/config.example.yaml" "$INSTALL_DIR/config.yaml"
    fi
    as_user mkdir -p "$INSTALL_DIR/data"
    chmod 600 "$INSTALL_DIR/.env" "$INSTALL_DIR/config.yaml"
    chmod 700 "$INSTALL_DIR/data"

    printf '\n[4/5] 注册 systemd 服务\n'
    install -m 644 "$INSTALL_DIR/deploy/tg-butler.service" \
        /etc/systemd/system/tg-butler.service
    install -m 755 "$INSTALL_DIR/deploy/bu" /usr/local/bin/bu
    systemctl daemon-reload
    bash "$INSTALL_DIR/deploy/setup-web.sh"
    if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
        printf '\n环境已安装，网页已启动，监控任务尚未启动。现有配置与 data/ 已保留。\n'
        printf '通过 SSH 隧道打开网页；首次默认账号 pingzi / pingzi，已有管理员保留。\n'
        return 0
    fi

    printf '\n[5/5] 安装完成，进入 bu 中文管理菜单\n' >&3
    printf '网页后台已自动开启。首次默认账号 pingzi / pingzi，已有管理员保留；通过 SSH 隧道访问本机 15678 完成配置。\n' >&3
    printf '推荐先在网页配置；菜单按网页后台、Telegram 配置、监控任务、服务与日志分组，6 更新脚本。\n' >&3
    printf '以后直接输入 bu 即可管理；选择 0 退出不会停止正在运行的服务。\n' >&3
    /usr/local/bin/bu <&3 >&3 2>&3
}

trap 'printf "\n安装中断（第 %s 行）。已存在的凭据和数据会保留；若服务已停止，排查后重新运行脚本。\n" "$LINENO" >&2' ERR
main "$@"
