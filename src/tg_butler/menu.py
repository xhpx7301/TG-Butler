"""Chinese terminal management menu for the Debian systemd installation."""

from __future__ import annotations

import argparse
import copy
import getpass
import hashlib
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import yaml
from dotenv import dotenv_values, set_key
from filelock import FileLock, Timeout
from pydantic import ValidationError

from tg_butler.config import (
    ApplicationCredentials,
    Checkin,
    Credentials,
    Group,
    NotificationCredentials,
    Settings,
)

SERVICE = "tg-butler.service"


def atomic_write(path: Path, content: str) -> None:
    """Replace privately, retaining ownership so root's menu never breaks the service."""
    owner = path.stat() if path.exists() else path.parent.stat()
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        if os.name != "nt":
            os.chown(temporary, owner.st_uid, owner.st_gid)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def configuration_lock(path: Path):
    directory = path.parent / "data"
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "config.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
        if os.name != "nt":
            owner = path.stat() if path.exists() else path.parent.stat()
            os.chown(lock_path, owner.st_uid, owner.st_gid)
    with FileLock(str(lock_path), timeout=2):
        yield


def save_settings(path: Path, data: dict, *, expected_revision=None, locked=False) -> None:
    if not locked:
        with configuration_lock(path):
            return save_settings(path, data, expected_revision=expected_revision, locked=True)
    if expected_revision and hashlib.sha256(path.read_bytes()).hexdigest() != expected_revision:
        raise ValueError("配置已被网页或其他菜单修改，请重新进入此项后再保存")
    Settings.model_validate(data)
    # Validate without serializing the model: keep relative data_dir and optional defaults.
    atomic_write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def save_credentials(
    path: Path, values: dict[str, str], section: str = "all", *, locked=False
) -> None:
    if not locked:
        with configuration_lock(path):
            return save_credentials(path, values, section, locked=True)
    if section not in {"all", "application", "notification", "ai"}:
        raise ValueError("Unknown credential section")
    if section == "application":
        ApplicationCredentials(
            api_id=values.get("TG_API_ID", ""), api_hash=values.get("TG_API_HASH", "")
        )
    elif section == "notification":
        NotificationCredentials(
            bot_token=values.get("TG_BOT_TOKEN", ""),
            notify_chat_id=values.get("TG_NOTIFY_CHAT_ID", ""),
        )
    elif section == "all":
        Credentials(
            api_id=values.get("TG_API_ID", ""),
            api_hash=values.get("TG_API_HASH", ""),
            bot_token=values.get("TG_BOT_TOKEN", ""),
            notify_chat_id=values.get("TG_NOTIFY_CHAT_ID", ""),
            summary_api_key=values.get("SUMMARY_API_KEY", ""),
        )
    # Use dotenv's escaping rules, preserving unrelated entries and comments.
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    os.close(fd)
    try:
        staging = Path(temporary)
        if path.exists():
            staging.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        for key, value in values.items():
            set_key(staging, key, value, quote_mode="always")
        atomic_write(path, staging.read_text(encoding="utf-8"))
    finally:
        Path(temporary).unlink(missing_ok=True)


def ask(label: str, default="", *, secret: bool = False, clear: bool = False) -> str:
    shown = "已设置，回车保留" if secret and default else str(default)
    suffix = "；输入 - 清空" if clear else ""
    prompt = f"{label} [{shown}{suffix}]："
    value = (getpass.getpass(prompt) if secret else input(prompt)).strip()
    if clear and value == "-":
        return ""
    return value if value else str(default)


def ask_bool(label: str, default: bool) -> bool:
    while True:
        value = ask(f"{label}（y/n）", "y" if default else "n").lower()
        if value in {"y", "yes", "是", "1"}:
            return True
        if value in {"n", "no", "否", "0"}:
            return False
        print("请输入 y 或 n。")


def show_validation(exc: ValidationError) -> None:
    for error in exc.errors(include_input=False, include_url=False, include_context=False):
        location = ".".join(map(str, error["loc"])) or "配置"
        print(f"  {location}: {error['msg']}")


class Menu:
    def __init__(self, config: Path):
        self.config = config.resolve()
        self.root = self.config.parent

    def read(self) -> dict:
        content = self.config.read_bytes()
        self._config_revision = hashlib.sha256(content).hexdigest()
        data = yaml.safe_load(content.decode("utf-8")) or {}
        Settings.model_validate(data)
        return data

    def save(self, data):
        save_settings(self.config, data, expected_revision=self._config_revision)

    def command(self, args: list[str], *, check: bool = True):
        return subprocess.run(args, check=check)  # No shell; typed values stay arguments.

    def active(self, service: str = SERVICE) -> bool:
        return (
            subprocess.run(
                ["systemctl", "is-active", "--quiet", service],
                check=False,
            ).returncode
            == 0
        )

    def apply(self) -> None:
        if self.active():
            self.command(["systemctl", "restart", SERVICE])
            print("已保存并重启运行中的服务。请确认启动通知，或使用菜单查看日志。")
        else:
            print("已保存。服务当前未运行，完成配置后选择“启动服务”。")

    @contextmanager
    def paused(self):
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        lock_path = self.root / "data/telegram.lock"
        lock_path.touch(exist_ok=True)
        if os.name != "nt":
            owner = self.config.stat()
            os.chown(lock_path, owner.st_uid, owner.st_gid)
        with FileLock(str(self.root / "data/telegram.lock"), timeout=2):
            with self._paused():
                yield

    @contextmanager
    def _paused(self):
        was_active = self.active()
        if was_active:
            print("临时停止服务，避免共用 Telegram 登录会话。")
            self.command(["systemctl", "stop", SERVICE])
        try:
            yield
        finally:
            if was_active:
                self.command(["systemctl", "start", SERVICE])

    def cli(self, *args: str, session: bool = False) -> None:
        argv = [
            "runuser",
            "-u",
            "tgbutler",
            "--",
            str(self.root / ".venv/bin/tg-butler"),
            "--config",
            str(self.config),
            *args,
        ]
        if session:
            with self.paused():
                self.command(argv)
        else:
            self.command(argv)

    def credentials(self) -> None:
        self.edit_credentials("application")

    def notification_credentials(self) -> None:
        self.edit_credentials("notification")

    def ai_credentials(self) -> None:
        self.edit_credentials("ai")

    def edit_credentials(self, section: str) -> None:
        path = self.root / ".env"
        current = {key: value or "" for key, value in dotenv_values(path).items()}
        if current.get("TG_API_HASH") == "replace_with_api_hash":
            current["TG_API_HASH"] = ""
            if current.get("TG_API_ID") == "123456":
                current["TG_API_ID"] = ""
        if current.get("TG_BOT_TOKEN") == "replace_with_bot_token":
            current["TG_BOT_TOKEN"] = ""
            if current.get("TG_NOTIFY_CHAT_ID") == "123456789":
                current["TG_NOTIFY_CHAT_ID"] = ""
        print("\n首次需要手动填写；真实值回车保留，示例占位值视为未设置。密钥输入不回显。")
        fields = {
            "application": [
                ("TG_API_ID", "应用 API ID（数字）", False),
                ("TG_API_HASH", "应用 API HASH", True),
            ],
            "notification": [
                ("TG_BOT_TOKEN", "通知机器人 Token", True),
                ("TG_NOTIFY_CHAT_ID", "你的通知用户 ID（数字）", False),
            ],
            "ai": [
                ("SUMMARY_API_KEY", "AI 摘要 API Key（可选）", True),
            ],
        }
        if section == "application":
            print("用你的 Telegram 账号登录 https://my.telegram.org → API development tools。")
            print("创建应用取得 API ID / API HASH，用于账号登录、读取群消息与发送签到。")
        elif section == "notification":
            print(
                "在 @BotFather 创建机器人取得 Token，向机器人发送 /start，再填写接收通知的用户 ID。"
            )
        else:
            print("此密钥来自你选择的 AI 服务商，仅用于日报总结，与 Telegram 应用无关。")
        values = {}
        for key, label, secret in fields[section]:
            values[key] = ask(
                label, current.get(key, ""), secret=secret, clear=key == "SUMMARY_API_KEY"
            )
        save_credentials(path, values, section=section)
        self.apply()

    def select_record(self, records: list[dict], label: str) -> tuple[int, str] | None:
        for index, record in enumerate(records, 1):
            print(
                f"{index}. {record.get('name') or record.get('id')}  "
                f"{record.get('chat_id', record.get('id', ''))}"
            )
        print(f"a. 添加{label}    d. 删除    0. 返回")
        choice = input("选择要编辑的编号：").strip().lower()
        if choice == "0":
            return None
        if choice == "a":
            return len(records), "add"
        action = "edit"
        if choice == "d":
            choice = input("要删除的编号：").strip()
            action = "delete"
        index = int(choice) - 1
        if not 0 <= index < len(records):
            raise ValueError("无效编号")
        return index, action

    def groups(self) -> None:
        data = self.read()
        records = data.setdefault("groups", [])
        print("\n群组与关注用户（同一群组可设置多个关注用户 ID）")
        selection = self.select_record(records, "群组")
        if selection is None:
            return
        index, action = selection
        if action == "delete":
            if not ask_bool("确定删除此监控群组？独立签到任务仍会保留", False):
                return
            records.pop(index)
        else:
            record = copy.deepcopy(records[index]) if action == "edit" else {}
            record["id"] = int(ask("群组数字 ID（通常以 -100 开头）", record.get("id", "")))
            record["name"] = ask("群组备注名称", record.get("name", ""))
            users = ask(
                "关注用户数字 ID（逗号分隔，- 清空）",
                ",".join(map(str, record.get("watch_users", []))),
                clear=True,
            )
            record["watch_users"] = [
                int(item.strip()) for item in users.replace("，", ",").split(",") if item.strip()
            ]
            record["notify_all"] = ask_bool("提醒群内所有新消息", record.get("notify_all", False))
            record["daily_summary"] = ask_bool("参与每日摘要", record.get("daily_summary", True))
            Group.model_validate(record)
            if action == "add":
                records.append(record)
            else:
                records[index] = record
        self.save(data)
        self.apply()

    def summary(self) -> None:
        data = self.read()
        record = data.setdefault("summary", {})
        record["enabled"] = ask_bool("启用每日日报", record.get("enabled", True))
        record["at"] = ask("日报时间（HH:MM，汇总前一天）", record.get("at", "00:05"))
        if ask_bool(
            "启用 AI 话题总结（消息将发送至你指定的模型服务）", bool(record.get("api_url"))
        ):
            record["api_url"] = ask("完整 HTTPS Chat Completions 地址", record.get("api_url") or "")
            record["model"] = ask("模型名称", record.get("model") or "")
            print("AI API Key 请在主菜单“21. AI 摘要密钥”中填写。")
        else:
            record["api_url"] = None
            record["model"] = None
        record["max_messages"] = int(
            ask("AI 最多输入消息数（1-10000）", record.get("max_messages", 500))
        )
        record["max_chars"] = int(
            ask("AI 最多输入字符数（1000-200000）", record.get("max_chars", 30000))
        )
        self.save(data)
        self.apply()

    def checkins(self) -> None:
        data = self.read()
        records = data.setdefault("checkins", [])
        print("\n定时签到（同一任务名称每天只尝试一次，修改名称视为新任务）")
        selection = self.select_record(records, "签到任务")
        if selection is None:
            return
        index, action = selection
        if action == "delete":
            if not ask_bool("确定删除签到任务", False):
                return
            records.pop(index)
        else:
            record = copy.deepcopy(records[index]) if action == "edit" else {}
            record["name"] = ask("任务唯一名称", record.get("name", ""))
            record["chat_id"] = int(ask("签到群组数字 ID", record.get("chat_id", "")))
            record["at"] = ask("签到时间（HH:MM）", record.get("at", "09:00"))
            record["command"] = ask("签到命令", record.get("command", "/checkin"))
            if ask_bool("通过签到机器人的回复确认结果", bool(record.get("responder_id"))):
                record["responder_id"] = int(
                    ask("签到机器人数字 ID", record.get("responder_id") or "")
                )
                record["success_pattern"] = ask(
                    "成功回复正则（例如 签到成功|已签到）",
                    record.get("success_pattern") or "签到成功|已签到",
                )
                record["failure_pattern"] = (
                    ask(
                        "失败回复正则（可选，- 清空）",
                        record.get("failure_pattern") or "",
                        clear=True,
                    )
                    or None
                )
                record["require_reply"] = ask_bool(
                    "仅匹配回复本次命令的消息（推荐）", record.get("require_reply", True)
                )
                record["timeout_seconds"] = int(
                    ask("等待回复秒数（1-600）", record.get("timeout_seconds", 60))
                )
            else:
                record.update(responder_id=None, success_pattern=None, failure_pattern=None)
            Checkin.model_validate(record)
            if action == "add":
                records.append(record)
            else:
                records[index] = record
        self.save(data)
        self.apply()

    def basic(self) -> None:
        data = self.read()
        data["timezone"] = ask("所有任务使用的时区", data.get("timezone", "Asia/Shanghai"))
        data["retention_days"] = int(ask("消息保留天数（1-3650）", data.get("retention_days", 30)))
        notifications = data.setdefault("notifications", {})
        notifications["queue_size"] = int(
            ask("实时通知队列容量（1-10000）", notifications.get("queue_size", 500))
        )
        self.save(data)
        self.apply()

    def overview(self) -> None:
        settings = Settings.model_validate(self.read())
        print(f"\n时区：{settings.timezone}；消息保留：{settings.retention_days} 天")
        print(f"通知队列：{settings.notifications.queue_size}；数据目录：{settings.data_dir}")
        print(
            f"日报：{'启用' if settings.summary.enabled else '关闭'}，时间 {settings.summary.at}，"
            f"AI：{'启用' if settings.summary.api_url else '关闭'}"
        )
        for group in settings.groups:
            print(
                f"群组 {group.name or group.id} ({group.id})：关注 {group.watch_users}，"
                f"全群提醒 {'开' if group.notify_all else '关'}，"
                f"日报 {'开' if group.daily_summary else '关'}"
            )
        for job in settings.checkins:
            print(
                f"签到 {job.name}：{job.chat_id}，{job.at}，{job.command}，"
                f"回复确认 {'开' if job.responder_id else '关'}"
            )

    def start_service(self) -> None:
        self.cli("check-config")
        self.command(["systemctl", "reset-failed", SERVICE])
        self.command(["systemctl", "enable", "--now", SERVICE])
        print("已请求启动。请通过机器人启动通知与日志确认连接正常。")

    def update(self) -> bool:
        print("更新将拉取 GitHub main、安装依赖并更新菜单和服务文件。配置和数据保留。")
        if not ask_bool("现在更新脚本", True):
            return False
        # Stop the web manager during package replacement and credential/schema updates.
        self.command(["bash", str(self.root / "deploy/update.sh")])
        print("更新完成。请输入 bu 重新进入新版菜单。")
        return True

    def uninstall(self) -> bool:
        result = self.command(["bash", str(self.root / "deploy/uninstall.sh")], check=False)
        if result.returncode == 2:  # Explicit cancellation in the interactive script.
            return False
        result.check_returncode()
        return True

    def web_admin(self) -> None:
        self.command(
            [
                "runuser",
                "-u",
                "tgbutler",
                "--",
                str(self.root / ".venv/bin/python"),
                "-m",
                "tg_butler.admin",
                "account",
                "--root",
                str(self.root),
            ]
        )

    def web_settings(self) -> None:
        self.command(
            [
                "runuser",
                "-u",
                "tgbutler",
                "--",
                str(self.root / ".venv/bin/python"),
                "-m",
                "tg_butler.admin",
                "settings",
                "--root",
                str(self.root),
            ]
        )
        self.command(["systemctl", "restart", "tg-butler-web.service"])
        print("网页后台已重启。")
        self.web_access()

    def web_access(self) -> None:
        from tg_butler.admin import web_options

        options = web_options(self.root)
        port = int(options["port"])
        target = "[::1]" if ":" in options["host"] else "127.0.0.1"
        print(f"\n网页监听：{options['host']}:{port}")
        print(f"电脑建立 SSH 隧道：ssh -N -L {port}:{target}:{port} 用户@服务器")
        print(f"电脑浏览器访问：http://127.0.0.1:{port}")
        if options["secure_cookie"]:
            print("已启用 HTTPS Cookie，请通过已配置的 HTTPS 代理域名登录。")
        print("首次默认账号：pingzi / pingzi；已有账户保留，修改或重置请进入“管理员账户”。")

    def members(self) -> None:
        chat_id = str(int(ask("群组数字 ID")))
        self.cli("members", "--limit", "100", chat_id, session=True)

    def manual_summary(self) -> None:
        day = ask("日报日期（YYYY-MM-DD；同群同日仅尝试一次）")
        self.cli("summary", "--date", day, session=True)

    def restart_service(self) -> None:
        self.cli("check-config")
        self.command(["systemctl", "restart", SERVICE])

    def menu_sections(self) -> dict:
        return {
            "1": (
                "网页后台",
                [
                    ("查看访问地址与登录说明", self.web_access),
                    ("管理员账户（修改 / 重置）", self.web_admin),
                    ("监听地址、端口与 HTTPS Cookie", self.web_settings),
                    (
                        "安装 / 启动网页后台（开机自启）",
                        lambda: self.command(["bash", str(self.root / "deploy/setup-web.sh")]),
                    ),
                    (
                        "查看网页后台状态",
                        lambda: self.command(
                            ["systemctl", "--no-pager", "status", "tg-butler-web.service"],
                            check=False,
                        ),
                    ),
                    (
                        "查看网页后台日志",
                        lambda: self.command(
                            ["journalctl", "-u", "tg-butler-web.service", "-n", "80", "--no-pager"]
                        ),
                    ),
                ],
            ),
            "2": (
                "Telegram 配置",
                [
                    ("账号应用凭据（API ID / HASH）", self.credentials),
                    ("登录 Telegram 账号", lambda: self.cli("login", session=True)),
                    ("通知机器人（Token / 接收用户 ID）", self.notification_credentials),
                    ("测试机器人通知", lambda: self.cli("notify-test")),
                    ("查看群组 ID", lambda: self.cli("dialogs", session=True)),
                    ("查看群成员 ID", self.members),
                ],
            ),
            "3": (
                "监控任务",
                [
                    ("群组与关注用户管理", self.groups),
                    ("日报与 AI 摘要设置", self.summary),
                    ("AI 摘要密钥（可选）", self.ai_credentials),
                    ("定时签到任务管理", self.checkins),
                    ("手动发送指定日期日报", self.manual_summary),
                    ("查看任务执行记录", lambda: self.cli("status")),
                ],
            ),
            "4": (
                "监控服务与日志",
                [
                    ("启动监控任务 / 开机自启（需完成 Telegram 配置）", self.start_service),
                    ("停止监控任务", lambda: self.command(["systemctl", "stop", SERVICE])),
                    ("重启监控任务", self.restart_service),
                    (
                        "查看监控服务状态",
                        lambda: self.command(
                            ["systemctl", "--no-pager", "--full", "status", SERVICE], check=False
                        ),
                    ),
                    (
                        "查看监控服务日志",
                        lambda: self.command(
                            ["journalctl", "-u", SERVICE, "-n", "80", "--no-pager"]
                        ),
                    ),
                ],
            ),
            "5": (
                "运行参数与配置检查",
                [
                    ("时区、保留天数与通知队列", self.basic),
                    ("查看当前配置（隐藏密钥）", self.overview),
                    ("校验监控配置", lambda: self.cli("check-config")),
                ],
            ),
        }

    def loop(self) -> None:
        section = None
        while True:
            try:
                sections = self.menu_sections()
                print("\n══════════ TG-Butler 中文管理菜单 ══════════")
                web_state = "运行中" if self.active("tg-butler-web.service") else "未运行"
                print(
                    f"网页后台：{web_state} │ 监控任务：{'运行中' if self.active() else '未运行'}"
                )
                if section is None:
                    print("推荐在网页完成配置；网页后台无需 Telegram 凭据即可运行。")
                    for key, (title, _) in sections.items():
                        print(f" {key}. {title}")
                    print(" 6. 更新脚本\n 7. 卸载并清除数据\n 0. 退出")
                else:
                    title, entries = sections[section]
                    print(f"\n【{title}】")
                    for index, (label, _) in enumerate(entries, 1):
                        print(f" {index}. {label}")
                    print(" 0. 返回主菜单")
                choice = input("请输入选项：").strip()
                if choice == "0":
                    if section is None:
                        return
                    section = None
                elif section is None and choice in sections:
                    section = choice
                elif section is None and choice in {"6", "19"}:
                    if self.update():
                        return
                elif section is None and choice == "7":
                    if self.uninstall():
                        return
                elif section is None and choice in {"22", "23", "24", "25"}:
                    # Keep the previous release's documented web shortcuts working.
                    legacy = {"22": 1, "23": 2, "24": 3, "25": 4}
                    sections["1"][1][legacy[choice]][1]()
                elif section is not None and choice in {
                    str(index) for index in range(1, len(sections[section][1]) + 1)
                }:
                    sections[section][1][int(choice) - 1][1]()
                else:
                    print("无效选项。")
            except ValidationError as exc:
                print("配置未保存，填写内容未通过校验：")
                show_validation(exc)
            except (ValueError, KeyError):
                print("输入无效，配置未保存。请检查数字、日期、时区和正则格式。")
            except subprocess.CalledProcessError:
                print("操作未完成，请检查上方错误；可用菜单查看日志后重试。")
            except Timeout:
                print("其他菜单或网页正在使用 Telegram 会话，请稍后重试。")
            except OSError as exc:
                print(f"文件或系统操作失败（{type(exc).__name__}），请检查权限与磁盘空间。")
            except yaml.YAMLError:
                print("config.yaml 格式错误，请修复 YAML 后重试；原文件未覆盖。")


def main() -> None:
    parser = argparse.ArgumentParser(description="TG-Butler 中文管理菜单")
    parser.add_argument("--config", type=Path, default=Path("/opt/tg-butler/config.yaml"))
    args = parser.parse_args()
    if os.name == "nt" or os.geteuid() != 0:
        parser.exit(1, "请在 Debian 服务器使用 bu 或 sudo bu 打开管理菜单。\n")
    if not sys.stdin.isatty():
        parser.exit(1, "管理菜单需要交互终端，请通过 SSH 终端运行 bu。\n")
    os.umask(0o077)
    try:
        Menu(args.config).loop()
    except (KeyboardInterrupt, EOFError):
        print("\n已退出管理菜单。")


if __name__ == "__main__":
    main()
