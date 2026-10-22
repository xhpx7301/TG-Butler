from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import subprocess
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import dotenv_values
from filelock import Timeout
from flask import Flask, jsonify, render_template, request, send_file, session
from pydantic import ValidationError
from waitress import serve

from tg_butler.admin import AdminStore, web_options
from tg_butler.config import NotificationCredentials, Settings, load_settings
from tg_butler.menu import configuration_lock, save_credentials, save_settings
from tg_butler.store import Store
from tg_butler.summary import check_connection, list_models, send_test_daily
from tg_butler.web_telegram import TelegramGateway

SECRET_FIELDS = {
    "TG_API_HASH",
    "TG_BOT_TOKEN",
    "TG_MONITOR_BOT_TOKEN",
    "TG_REPORT_BOT_TOKEN",
    "SUMMARY_API_KEY",
}
SECTIONS = {
    "application": {"TG_API_ID", "TG_API_HASH"},
    "notification": {
        "TG_BOT_TOKEN",
        "TG_NOTIFY_CHAT_ID",
        "TG_MONITOR_BOT_TOKEN",
        "TG_MONITOR_NOTIFY_CHAT_ID",
        "TG_REPORT_BOT_TOKEN",
        "TG_REPORT_NOTIFY_CHAT_ID",
    },
    "monitor": {"TG_MONITOR_BOT_TOKEN", "TG_MONITOR_NOTIFY_CHAT_ID"},
    "report": {"TG_REPORT_BOT_TOKEN", "TG_REPORT_NOTIFY_CHAT_ID"},
    "ai": {"SUMMARY_API_KEY"},
}


def system_control(action: str) -> str:
    if action not in {"status", "start", "stop", "restart", "logs", "update"}:
        raise ValueError("不支持的服务操作")
    if os.environ.get("TG_BUTLER_CONTROL_MODE") == "supervisor":
        if action == "update":
            raise ValueError("Docker 部署请在宿主机执行 docker compose up -d --build 更新")
        command = ["/usr/local/bin/tg-butler-control", action]
    else:
        command = ["sudo", "-n", "/usr/local/libexec/tg-butler-control", action]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        raise ValueError("服务操作失败，请在 bu 中检查网页管理服务的安装和授权")
    return result.stdout[-20000:]


def create_app(config: Path, *, control=system_control) -> Flask:
    config = config.resolve()
    root = config.parent
    admin = AdminStore(root)
    admin.initialize_default()
    options = web_options(root)
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=admin.secret(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=options["secure_cookie"],
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=21 * 1024 * 1024,
    )
    gateway = TelegramGateway(config, control)
    app.extensions["admin_store"] = admin
    app.extensions["telegram_gateway"] = gateway
    from tg_butler.backup import backup_dir, create_backup, read_schedule

    backup_scheduler = BackgroundScheduler(timezone="UTC")
    app.extensions["backup_scheduler"] = backup_scheduler

    def configure_backup_job(schedule: dict) -> None:
        if schedule["enabled"]:
            backup_scheduler.add_job(
                create_backup,
                CronTrigger(
                    hour=int(schedule["at"][:2]),
                    minute=int(schedule["at"][3:]),
                    timezone=load_settings(config).timezone,
                ),
                args=[root, config],
                id="daily-backup",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=86400,
            )
        elif backup_scheduler.get_job("daily-backup"):
            backup_scheduler.remove_job("daily-backup")

    initial_schedule = read_schedule(root)
    if config.is_file():
        configure_backup_job(initial_schedule)

    def csrf():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(32)
        return session["csrf"]

    def authenticated():
        account = admin.get()
        return account and secrets.compare_digest(session.get("revision", ""), account["revision"])

    def protected(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not authenticated():
                return jsonify(error="请先登录管理员账号"), 401
            return function(*args, **kwargs)

        return wrapped

    @app.before_request
    def protect_writes():
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            if not secrets.compare_digest(
                session.get("csrf", "").encode(), request.headers.get("X-CSRF-Token", "").encode()
            ) or not session.get("csrf"):
                return jsonify(error="页面验证已失效，请刷新页面重试"), 403
            if not request.is_json and request.endpoint != "import_backup":
                return jsonify(error="请使用 JSON 请求"), 415

    @app.after_request
    def headers(response):
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self'; "
                "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            }
        )
        return response

    @app.errorhandler(Exception)
    def error(exc):
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return jsonify(error=exc.description), exc.code
        if isinstance(exc, ValidationError):
            locations = [
                ".".join(map(str, item["loc"])) or "配置"
                for item in exc.errors(include_input=False)
            ]
            return jsonify(error="请检查填写格式：" + "、".join(locations)), 400
        if isinstance(exc, ValueError):
            return jsonify(error=str(exc)), 400
        if isinstance(exc, Timeout):
            return jsonify(error="其他管理操作正在执行，请稍后重试"), 409
        # Never serialize network exceptions or Telegram request bodies/passwords.
        app.logger.warning("Web action failed (%s)", type(exc).__name__)
        return jsonify(error=f"操作失败（{type(exc).__name__}），请检查配置与连接后重试"), 500

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            backup_dir=backup_dir(root),
            docker_mode=os.environ.get("TG_BUTLER_CONTROL_MODE") == "supervisor",
        )

    @app.get("/api/session")
    def session_info():
        account = admin.get()
        return jsonify(
            csrf=csrf(),
            authenticated=bool(authenticated()),
            initialized=bool(account),
            username=account["username"] if authenticated() else "",
        )

    @app.post("/api/login")
    def login():
        if not admin.allow_attempt(request.remote_addr or "unknown"):
            return jsonify(error="登录尝试过多，请 15 分钟后再试"), 429
        data = request.get_json()
        account = admin.verify(str(data.get("username", "")), str(data.get("password", "")))
        if not account:
            return jsonify(
                error="用户名或密码错误；忘记密码可在 bu → 1 网页后台 → 2 管理员账户 重置"
            ), 401
        session.clear()
        session.update(revision=account["revision"])
        session.permanent = True
        return jsonify(csrf=csrf(), username=account["username"])

    @app.post("/api/logout")
    @protected
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.post("/api/admin")
    @protected
    def change_admin():
        data = request.get_json()
        account = admin.get()
        if not admin.allow_attempt("admin-change:" + (request.remote_addr or "unknown")):
            return jsonify(error="验证尝试过多，请稍后再试"), 429
        if not admin.verify(account["username"], str(data.get("current_password", ""))):
            return jsonify(error="当前管理员密码错误"), 403
        admin.set(str(data.get("username", "")), str(data.get("password", "")))
        session.clear()
        return jsonify(message="管理员已修改，全部登录会话已失效，请重新登录。")

    def read_config():
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        Settings.model_validate(data)
        return data

    def revision():
        return hashlib.sha256(config.read_bytes()).hexdigest()

    def apply_saved_changes() -> str:
        """Reload saved settings without starting a deliberately stopped service."""
        if control("status").strip() != "active":
            return "任务服务当前未运行，保存的修改将在下次启动时生效。"
        try:
            control("restart")
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return "任务服务自动重启失败，请检查概览中的服务状态与日志。"
        return "任务服务已自动重启，保存的修改已生效。"

    @app.get("/api/state")
    @protected
    def state():
        with configuration_lock(config):
            data = read_config()
            version = revision()
            values = dotenv_values(root / ".env")
        visible = {
            key: value
            for key, value in values.items()
            if key
            in {
                "TG_API_ID",
                "TG_NOTIFY_CHAT_ID",
                "TG_MONITOR_NOTIFY_CHAT_ID",
                "TG_REPORT_NOTIFY_CHAT_ID",
            }
        }
        configured = {
            key: bool(values.get(key)) and not str(values[key]).startswith("replace_with_")
            for key in SECRET_FIELDS
        }
        for role in ("MONITOR", "REPORT"):
            if not configured[f"TG_{role}_BOT_TOKEN"]:
                configured[f"TG_{role}_BOT_TOKEN"] = configured["TG_BOT_TOKEN"]
        settings = load_settings(config)
        data.setdefault("accounts", [account.model_dump() for account in settings.accounts])
        store = Store(settings.data_dir / "butler.sqlite3")
        try:
            runs = store.runs()
        finally:
            store.close()
        return jsonify(
            config=data,
            accounts=[
                {
                    "id": account.id,
                    "authorized": (
                        settings.data_dir / "user.session"
                        if account.id == "default"
                        else settings.data_dir / "accounts" / f"{account.id}.session"
                    ).is_file(),
                }
                for account in settings.accounts
            ],
            revision=version,
            credentials=visible,
            configured=configured,
            service=control("status").strip(),
            runs=runs,
        )

    @app.post("/api/config")
    @protected
    def update_config():
        body = request.get_json()
        with configuration_lock(config):
            if body.get("revision") != revision():
                return jsonify(error="配置已被其他页面或菜单修改，请刷新后重试"), 409
            old = read_config()
            new = body.get("config")
            if not isinstance(new, dict) or new.get("data_dir", "data") != old.get(
                "data_dir", "data"
            ):
                raise ValueError("网页不能修改数据目录")
            save_settings(config, new, locked=True)
        configure_backup_job(read_schedule(root))
        return jsonify(message="配置已保存。" + apply_saved_changes())

    @app.post("/api/credentials/<section>")
    @protected
    def update_credentials(section):
        if section not in SECTIONS:
            raise ValueError("无效凭据类别")
        supplied = request.get_json()
        with configuration_lock(config):
            existing = dotenv_values(root / ".env")
            values = {}
            for key in SECTIONS[section]:
                value = str(supplied.get(key, ""))
                # Empty secret means preserve; explicit flag permits clearing AI key.
                if key in SECRET_FIELDS and not value:
                    value = existing.get(key) or ""
                values[key] = value
            if section == "ai" and supplied.get("clear_key") is True:
                values["SUMMARY_API_KEY"] = ""
            save_credentials(root / ".env", values, section, locked=True)
        return jsonify(message="凭据已保存。" + apply_saved_changes() + "密钥不会回传至浏览器。")

    @app.post("/api/service/<action>")
    @protected
    def service(action):
        if action not in {"start", "stop", "restart", "logs"}:
            raise ValueError("无效服务操作")
        return jsonify(message=control(action) or "服务操作已提交，请查看服务状态与日志。")

    @app.post("/api/update")
    @protected
    def update():
        return jsonify(
            message=control("update")
            or "更新已开始，网页会短暂断开；完成后请刷新页面确认版本和服务状态。"
        )

    @app.get("/api/backups")
    @protected
    def backups():
        from tg_butler.backup import list_backups, next_backup, read_schedule

        return jsonify(
            schedule=read_schedule(root),
            next=next_backup(root, load_settings(config).timezone),
            items=list_backups(root),
        )

    @app.post("/api/backups/schedule")
    @protected
    def backup_schedule():
        from tg_butler.backup import next_backup, write_schedule

        schedule = write_schedule(root, request.get_json() or {})
        configure_backup_job(schedule)
        return jsonify(schedule=schedule, next=next_backup(root, load_settings(config).timezone))

    @app.post("/api/backups/create")
    @protected
    def create_backup_now():
        target = create_backup(root, config)
        return jsonify(message="备份已创建。", name=target.name)

    @app.get("/api/backups/<name>")
    @protected
    def download_backup(name):
        from tg_butler.backup import backup_dir

        if (
            Path(name).name != name
            or not name.startswith("tg-butler-")
            or not name.endswith(".tar.gz")
        ):
            raise ValueError("无效的备份文件名")
        target = backup_dir(root) / name
        if not target.is_file() or target.is_symlink():
            return jsonify(error="备份文件不存在"), 404
        return send_file(
            target, mimetype="application/gzip", as_attachment=True, download_name=name
        )

    @app.post("/api/backups/import")
    @protected
    def import_backup():
        import tempfile

        from tg_butler.backup import (
            backup_dir,
            create_backup,
            read_schedule,
            restore_backup,
            validate_backup,
        )

        upload = request.files.get("file")
        if upload is None or not upload.filename:
            raise ValueError("请选择 .tar.gz 备份文件")
        with tempfile.NamedTemporaryFile(
            dir=backup_dir(root), suffix=".upload", delete=False
        ) as temp:
            path = Path(temp.name)
            upload.save(temp)
        try:
            if path.stat().st_size > 20 * 1024 * 1024:
                raise ValueError("上传文件超过 20 MB 限制")
            validate_backup(path)
            create_backup(root, config)
            was_running = control("status").strip() == "active"
            if was_running:
                control("stop")
            try:
                restore_backup(root, config, path)
            finally:
                if was_running:
                    control("start")
        finally:
            path.unlink(missing_ok=True)
        configure_backup_job(read_schedule(root))
        return jsonify(message="备份已导入并恢复。恢复后的监控服务已重新启动，请刷新页面。")

    def ai_request_values(body: dict, *, require_model: bool = True) -> tuple[str, str, str]:
        api_url = str(body.get("api_url", "")).strip()
        model = str(body.get("model", "")).strip()
        if not api_url or not api_url.startswith("https://"):
            raise ValueError("请填写 HTTPS 的 OpenAI Responses 或 Chat Completions 接口地址")
        if require_model and not model:
            raise ValueError("请先选择或填写模型名称")
        values = dotenv_values(root / ".env")
        api_key = str(body.get("api_key", "")).strip() or str(values.get("SUMMARY_API_KEY") or "")
        return api_url, model, api_key

    @app.post("/api/ai/models")
    @protected
    def ai_models():
        import asyncio

        api_url, _, api_key = ai_request_values(request.get_json() or {}, require_model=False)
        return jsonify(models=asyncio.run(list_models(api_url, api_key)))

    @app.post("/api/ai/test")
    @protected
    def ai_test():
        import asyncio

        api_url, model, api_key = ai_request_values(request.get_json() or {})
        answer = asyncio.run(check_connection(api_url, model, api_key))
        return jsonify(message=f"连接正常，模型已返回：{answer}")

    @app.post("/api/telegram/<action>")
    @protected
    def telegram(action):
        if action not in {
            "send-code",
            "verify-code",
            "password",
            "dialogs",
            "members",
            "resolve",
            "identify",
            "history",
        }:
            raise ValueError("无效 Telegram 操作")
        return jsonify(gateway.execute(action, request.get_json(), session["revision"]))

    @app.post("/api/checkin-test")
    @protected
    def checkin_test():
        result = gateway.execute("checkin-test", request.get_json() or {}, session["revision"])
        return jsonify(result)

    @app.post("/api/notify-test")
    @protected
    def notify_test():
        import asyncio

        from tg_butler.app import make_notifier

        async def send():
            values = dotenv_values(root / ".env")
            role = str((request.get_json() or {}).get("role", "monitor"))
            if role not in {"monitor", "report"}:
                raise ValueError("无效通知机器人角色")
            token_key = "TG_MONITOR_BOT_TOKEN" if role == "monitor" else "TG_REPORT_BOT_TOKEN"
            chat_key = (
                "TG_MONITOR_NOTIFY_CHAT_ID" if role == "monitor" else "TG_REPORT_NOTIFY_CHAT_ID"
            )
            credentials = NotificationCredentials(
                bot_token=values.get(token_key) or values.get("TG_BOT_TOKEN", ""),
                notify_chat_id=values.get(chat_key) or values.get("TG_NOTIFY_CHAT_ID", ""),
            )
            notifier = make_notifier(load_settings(config), credentials)
            try:
                await notifier.send("✅ TG-Butler 网页通知测试成功。")
            finally:
                await notifier.close()

        asyncio.run(send())
        return jsonify(message="测试通知已发送。")

    @app.post("/api/group-test")
    @protected
    def group_test():
        import asyncio
        from types import SimpleNamespace

        from tg_butler.app import live_alert, make_notifier
        from tg_butler.config import Group

        group = Group.model_validate(request.get_json() or {})
        values = dotenv_values(root / ".env")
        monitor = NotificationCredentials(
            bot_token=values.get("TG_MONITOR_BOT_TOKEN") or values.get("TG_BOT_TOKEN", ""),
            notify_chat_id=values.get("TG_MONITOR_NOTIFY_CHAT_ID")
            or values.get("TG_NOTIFY_CHAT_ID", ""),
        )
        report = NotificationCredentials(
            bot_token=values.get("TG_REPORT_BOT_TOKEN") or values.get("TG_BOT_TOKEN", ""),
            notify_chat_id=values.get("TG_REPORT_NOTIFY_CHAT_ID")
            or values.get("TG_NOTIFY_CHAT_ID", ""),
        )
        settings = load_settings(config)
        zone = ZoneInfo(settings.timezone)
        sender = SimpleNamespace(username="tg_butler_test")
        sample = live_alert(
            group,
            sender,
            "TG-Butler 测试用户",
            123456789,
            "这是一条测试消息，用于检查监控群组通知与消息模板。",
            datetime.now(zone),
            1,
            zone,
        )
        report_sample = sample.replace("监控预警：目标用户已发言", "筛选提醒：目标用户消息命中条件")

        async def send():
            monitor_notifier = make_notifier(settings, monitor)
            same_notifier = (
                monitor.bot_token.get_secret_value() == report.bot_token.get_secret_value()
                and monitor.notify_chat_id == report.notify_chat_id
            )
            report_notifier = monitor_notifier if same_notifier else make_notifier(settings, report)
            try:
                await monitor_notifier.send(sample, parse_mode="HTML")
                await report_notifier.send(report_sample, parse_mode="HTML")
            finally:
                await monitor_notifier.close()
                if not same_notifier:
                    await report_notifier.close()

        asyncio.run(send())
        return jsonify(message="监控通知与筛选提醒模板测试已发送。")

    @app.post("/api/summary-test")
    @protected
    def summary_test():
        import asyncio

        from tg_butler.app import make_notifier

        async def send():
            values = dotenv_values(root / ".env")
            credentials = NotificationCredentials(
                bot_token=values.get("TG_REPORT_BOT_TOKEN") or values.get("TG_BOT_TOKEN", ""),
                notify_chat_id=values.get("TG_REPORT_NOTIFY_CHAT_ID")
                or values.get("TG_NOTIFY_CHAT_ID", ""),
            )
            settings = load_settings(config)
            store = Store(settings.data_dir / "butler.sqlite3")
            notifier = make_notifier(settings, credentials)
            day = datetime.now(ZoneInfo(settings.timezone)).date() - timedelta(days=1)
            try:
                count = await send_test_daily(
                    settings,
                    store,
                    notifier,
                    day,
                    str(values.get("SUMMARY_API_KEY") or ""),
                )
                return day, count
            finally:
                store.close()
                await notifier.close()

        day, count = asyncio.run(send())
        if not count:
            raise ValueError("没有启用日报的群组，请先在群组设置中开启“参与每日日报”。")
        return jsonify(message=f"测试日报已发送：{day.isoformat()}，共 {count} 份。")

    return app


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="TG-Butler 中文网页后台")
    parser.add_argument("--config", type=Path, default=Path("/opt/tg-butler/config.yaml"))
    args = parser.parse_args()
    options = web_options(args.config.resolve().parent)
    app = create_app(args.config)
    app.extensions["backup_scheduler"].start()
    serve(
        app,
        host=options["host"],
        port=int(options["port"]),
        threads=4,
        max_request_body_size=21 * 1024 * 1024,
        ident="TG-Butler",
    )


if __name__ == "__main__":
    main()
