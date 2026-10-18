from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from tg_butler.app import connect_authorized, make_client, make_notifier, run
from tg_butler.config import (
    load_application_credentials,
    load_credentials,
    load_notification_credentials,
    load_settings,
)
from tg_butler.store import Store
from tg_butler.summary import send_daily


async def execute(args) -> None:
    settings = load_settings(args.config)
    if args.command == "status":
        store = Store(settings.data_dir / "butler.sqlite3")
        try:
            print(json.dumps(store.runs(), ensure_ascii=False, indent=2))
        finally:
            store.close()
        return
    if args.command == "check-config":
        load_credentials(args.config)
        print(
            f"配置校验通过：{len(settings.groups)} 个群组，"
            f"{len(settings.checkins)} 个签到任务，时区 {settings.timezone}。"
        )
        print("尚未连接 Telegram；账号权限、通知目标和签到回复规则需实际验证。")
        return
    if args.command == "run":
        await run(settings, load_credentials(args.config), args.config)
        return
    if args.command in {"login", "dialogs", "members"}:
        credentials = load_application_credentials(args.config)
        client = make_client(settings, credentials)
        try:
            if args.command == "login":
                await client.start()
                print("登录成功。会话已保存到 data_dir；请勿将会话文件提交到 Git。")
            else:
                await connect_authorized(client)
                dialogs = await client.get_dialogs()
                if args.command == "dialogs":
                    for dialog in dialogs:
                        print(f"{dialog.id}\t{dialog.name}")
                else:
                    chat = next((d.entity for d in dialogs if d.id == args.chat_id), None)
                    if chat is None:
                        raise RuntimeError("Chat not found; use tg-butler dialogs")
                    async for user in client.iter_participants(chat, limit=args.limit):
                        name = " ".join(filter(None, [user.first_name, user.last_name]))
                        print(f"{user.id}\t@{user.username or '-'}\t{name}")
        finally:
            await client.disconnect()
        return
    if args.command == "notify-test":
        credentials = None
        notifier_credentials = load_notification_credentials(args.config, "monitor")
    else:
        credentials = load_credentials(args.config)
        notifier_credentials = credentials.notification("report")
    notifier = make_notifier(settings, notifier_credentials)
    try:
        if args.command == "notify-test":
            await notifier.send("✅ TG-Butler 通知测试成功。")
            print("通知已发送。")
        elif args.command == "summary":
            day = args.date or (
                datetime.now(ZoneInfo(settings.timezone)).date() - timedelta(days=1)
            )
            store = Store(settings.data_dir / "butler.sqlite3")
            try:
                await send_daily(
                    settings, store, notifier, day, credentials.summary_api_key.get_secret_value()
                )
                print("摘要任务已处理；发送结果请用 tg-butler status 查看。")
            finally:
                store.close()
    finally:
        await notifier.close()


def main() -> None:
    if os.name != "nt":
        os.umask(0o077)
    parser = argparse.ArgumentParser(description="TG-Butler Telegram 群组管家")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("login", "dialogs", "check-config", "run", "notify-test", "status"):
        commands.add_parser(name)
    members = commands.add_parser("members", help="列出群成员 ID（取决于群权限）")
    members.add_argument("chat_id", type=int)
    members.add_argument("--limit", type=int, default=100)
    summary = commands.add_parser("summary", help="发送指定日期摘要；每群每天只尝试一次")
    summary.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # HTTP request logs can contain bot tokens or provider credentials in URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    try:
        asyncio.run(execute(args))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # ValidationError and network tracebacks may contain secret input values.
        print(f"执行失败（{type(exc).__name__}）。请检查配置、网络与登录会话。", file=sys.stderr)
        if isinstance(exc, ValidationError):
            for error in exc.errors(include_input=False, include_url=False, include_context=False):
                location = ".".join(str(part) for part in error["loc"]) or "config"
                print(f"  {location}: {error['msg']}", file=sys.stderr)
        elif isinstance(exc, RuntimeError):
            print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
