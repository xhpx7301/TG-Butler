from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import subprocess
from datetime import timedelta
from functools import wraps
from pathlib import Path

import yaml
from dotenv import dotenv_values
from filelock import Timeout
from flask import Flask, jsonify, render_template, request, session
from pydantic import ValidationError
from waitress import serve

from tg_butler.admin import AdminStore, web_options
from tg_butler.config import Settings, load_settings
from tg_butler.menu import configuration_lock, save_credentials, save_settings
from tg_butler.store import Store
from tg_butler.web_telegram import TelegramGateway

SECRET_FIELDS = {"TG_API_HASH", "TG_BOT_TOKEN", "SUMMARY_API_KEY"}
SECTIONS = {
    "application": {"TG_API_ID", "TG_API_HASH"},
    "notification": {"TG_BOT_TOKEN", "TG_NOTIFY_CHAT_ID"},
    "ai": {"SUMMARY_API_KEY"},
}


def system_control(action: str) -> str:
    if action not in {"status", "start", "stop", "restart", "logs"}:
        raise ValueError("不支持的服务操作")
    command = ["sudo", "-n", "/usr/local/libexec/tg-butler-control", action]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        raise ValueError("服务操作失败，请在 bu 中检查网页管理服务的安装和授权")
    return result.stdout[-20000:]


def create_app(config: Path, *, control=system_control) -> Flask:
    config = config.resolve()
    root = config.parent
    admin = AdminStore(root)
    options = web_options(root)
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=admin.secret(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=options["secure_cookie"],
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=256 * 1024,
    )
    gateway = TelegramGateway(config, control)
    app.extensions["admin_store"] = admin

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
            if not request.is_json:
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
        return render_template("index.html")

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
            return jsonify(error="用户名或密码错误；首次使用请先在 bu 的管理员设置中创建账号"), 401
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

    @app.get("/api/state")
    @protected
    def state():
        with configuration_lock(config):
            data = read_config()
            version = revision()
            values = dotenv_values(root / ".env")
        visible = {
            key: value for key, value in values.items() if key in {"TG_API_ID", "TG_NOTIFY_CHAT_ID"}
        }
        configured = {
            key: bool(values.get(key)) and not str(values[key]).startswith("replace_with_")
            for key in SECRET_FIELDS
        }
        settings = load_settings(config)
        store = Store(settings.data_dir / "butler.sqlite3")
        try:
            runs = store.runs()
        finally:
            store.close()
        return jsonify(
            config=data,
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
        return jsonify(message="配置已保存，点击“重启任务服务”使修改生效。")

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
        return jsonify(message="凭据已保存，重启任务服务后生效。密钥不会回传至浏览器。")

    @app.post("/api/service/<action>")
    @protected
    def service(action):
        if action not in {"start", "stop", "restart", "logs"}:
            raise ValueError("无效服务操作")
        return jsonify(message=control(action) or "服务操作已提交，请查看状态与启动通知。")

    @app.post("/api/telegram/<action>")
    @protected
    def telegram(action):
        if action not in {"send-code", "verify-code", "password", "dialogs", "members"}:
            raise ValueError("无效 Telegram 操作")
        return jsonify(gateway.execute(action, request.get_json(), session["revision"]))

    @app.post("/api/notify-test")
    @protected
    def notify_test():
        import asyncio

        from tg_butler.app import make_notifier
        from tg_butler.config import NotificationCredentials

        async def send():
            values = dotenv_values(root / ".env")
            credentials = NotificationCredentials(
                bot_token=values.get("TG_BOT_TOKEN", ""),
                notify_chat_id=values.get("TG_NOTIFY_CHAT_ID", ""),
            )
            notifier = make_notifier(load_settings(config), credentials)
            try:
                await notifier.send("✅ TG-Butler 网页通知测试成功。")
            finally:
                await notifier.close()

        asyncio.run(send())
        return jsonify(message="测试通知已发送。")

    return app


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="TG-Butler 中文网页后台")
    parser.add_argument("--config", type=Path, default=Path("/opt/tg-butler/config.yaml"))
    args = parser.parse_args()
    options = web_options(args.config.resolve().parent)
    serve(
        create_app(args.config),
        host=options["host"],
        port=int(options["port"]),
        threads=4,
        max_request_body_size=256 * 1024,
        ident="TG-Butler",
    )


if __name__ == "__main__":
    main()
