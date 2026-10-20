"""Short-lived Telegram web login challenges; never expose the account session."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values
from filelock import FileLock
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import User
from telethon.utils import get_display_name, get_peer_id

from tg_butler.app import connect_authorized, make_client
from tg_butler.checkin import run_checkin
from tg_butler.config import ApplicationCredentials, load_settings
from tg_butler.store import Store
from tg_butler.summary import day_bounds


class TelegramGateway:
    def __init__(self, config, control):
        self.config = config
        self.control = control
        self.pending: dict = {}

    async def request(self, action: str, data: dict, owner: str) -> dict:
        settings = load_settings(self.config)
        account_id = str(data.get("account_id", "default"))
        if account_id not in {account.id for account in settings.accounts}:
            raise ValueError("未找到所选 Telegram 账号")
        values = dotenv_values(self.config.parent / ".env")
        credentials = ApplicationCredentials(
            api_id=values.get("TG_API_ID", ""), api_hash=values.get("TG_API_HASH", "")
        )
        client = make_client(settings, credentials, account_id)
        try:
            await client.connect()
            if action == "send-code":
                if await client.is_user_authorized():
                    return {"status": "authorized", "message": "账号已登录，无需重复验证。"}
                phone = str(data.get("phone", "")).strip()
                if not phone.startswith("+") or not phone[1:].isdigit() or len(phone) > 20:
                    raise ValueError("手机号请使用国际格式，例如 +8613800000000")
                sent = await client.send_code_request(phone)
                self.pending = {
                    "phone": phone,
                    "hash": sent.phone_code_hash,
                    "expires": time.monotonic() + 300,
                    "owner": owner,
                    "account_id": account_id,
                }
                return {
                    "status": "code",
                    "message": "验证码已申请，请查看 Telegram 官方消息或短信。",
                }
            if action in {"verify-code", "password"}:
                pending = self.pending
                if (
                    pending.get("owner") != owner
                    or pending.get("account_id") != account_id
                    or pending.get("expires", 0) < time.monotonic()
                ):
                    raise ValueError("验证流程已过期，请重新获取验证码")
                try:
                    if action == "password":
                        await client.sign_in(password=str(data.get("password", "")))
                    else:
                        code = str(data.get("code", "")).replace(" ", "")
                        await client.sign_in(
                            pending["phone"], code, phone_code_hash=pending["hash"]
                        )
                except SessionPasswordNeededError:
                    return {
                        "status": "password",
                        "message": "账号启用了两步验证，请填写两步验证密码。",
                    }
                self.pending = {}
                return {"status": "authorized", "message": "Telegram 账号登录成功。"}
            await connect_authorized(client)
            if action == "checkin-test":
                name = str(data.get("name", "")).strip()
                job = next((item for item in settings.checkins if item.name == name), None)
                if job is None:
                    raise ValueError("未找到已保存的签到任务，请先保存签到设置")
                dialogs = await client.get_dialogs()
                if job.account_id != account_id:
                    raise ValueError("此签到任务分配给其他 Telegram 账号")
                chat = next((item for item in dialogs if item.id == job.chat_id), None)
                if chat is None:
                    raise ValueError("签到群组不在当前账号的群组列表中")

                class TestNotifier:
                    def enqueue(self, _text):
                        pass

                store = Store(Path(":memory:"))
                try:
                    account_name = next(
                        account.name for account in settings.accounts if account.id == account_id
                    )
                    await run_checkin(
                        client,
                        job,
                        store,
                        TestNotifier(),
                        settings.timezone,
                        account_name,
                        chat.name,
                    )
                    run = store.runs()[0]
                    labels = {
                        "confirmed": "测试签到已确认成功",
                        "rejected": "测试签到收到失败回复",
                        "timeout": "测试签到命令已发送，但等待回复超时",
                        "sent": "测试签到命令已发送（未配置回复机器人）",
                        "uncertain": "测试签到发送结果不确定，请查看群内记录",
                        "error": "测试签到执行失败，请查看群内记录",
                    }
                    return {"message": labels.get(run["status"], "测试签到已执行")}
                finally:
                    store.close()
            dialogs = await client.get_dialogs()
            if action == "identify":
                kind = str(data.get("kind", ""))
                if kind not in {"group", "user"}:
                    raise ValueError("无效的查询类型")
                raw_ids = data.get("ids", [data.get("id", "")])
                if not isinstance(raw_ids, list) or not 1 <= len(raw_ids) <= 20:
                    raise ValueError("每次最多核对 20 个 ID")
                results = []
                for raw_id in raw_ids:
                    try:
                        entity_id = int(raw_id)
                        if not entity_id:
                            raise ValueError
                        if kind == "group":
                            dialog = next(item for item in dialogs if item.id == entity_id)
                            entity = dialog.entity
                            if isinstance(entity, User):
                                raise ValueError
                        else:
                            entity = await client.get_entity(entity_id)
                            if not isinstance(entity, User):
                                raise ValueError
                        results.append(
                            {
                                "id": entity_id,
                                "name": get_display_name(entity)
                                or getattr(entity, "title", "")
                                or "",
                                "username": getattr(entity, "username", "") or "",
                            }
                        )
                    except (ValueError, StopIteration, TypeError):
                        results.append({"id": str(raw_id), "error": "当前账号无法识别此 ID"})
                    except Exception:
                        results.append(
                            {"id": str(raw_id), "error": "查询失败，请确认账号权限和 ID"}
                        )
                return {"items": results}
            if action == "dialogs":
                items = []
                for item in dialogs:
                    if item.is_group or item.is_channel:
                        items.append({"id": item.id, "name": item.name})
                return {
                    "items": items
                }
            if action == "members":
                chat_id = int(data.get("chat_id", 0))
                chat = next((item.entity for item in dialogs if item.id == chat_id), None)
                if chat is None:
                    raise ValueError("未找到该群组，请先查看群组列表")
                items = []
                async for user in client.iter_participants(chat, limit=200):
                    items.append(
                        {
                            "id": user.id,
                            "name": " ".join(filter(None, [user.first_name, user.last_name])),
                            "username": user.username or "",
                        }
                    )
                return {"items": items}
            if action == "resolve":
                reference = str(data.get("reference", "")).strip().removeprefix("@")
                kind = str(data.get("kind", "")).strip()
                if kind not in {"group", "user"}:
                    raise ValueError("无效的查询类型")
                if not reference:
                    raise ValueError("请输入群组链接或用户名")
                for prefix in (
                    "https://t.me/",
                    "http://t.me/",
                    "https://telegram.me/",
                    "http://telegram.me/",
                ):
                    if reference.lower().startswith(prefix):
                        reference = reference[len(prefix) :].split("?", 1)[0].split("/", 1)[0]
                        break
                if not reference or reference.startswith(("+", "joinchat/", "c/")):
                    raise ValueError("暂不支持私密邀请链接，请输入公开群组链接或 @用户名")
                entity = await client.get_entity(reference)
                is_user = isinstance(entity, User)
                if kind == "group" and is_user:
                    raise ValueError("该地址对应的是用户，请输入群组链接或群组用户名")
                if kind == "user" and not is_user:
                    raise ValueError("该地址对应的是群组，请输入用户 @用户名")
                return {
                    # Use Telethon's marked peer ID so channels/supergroups match
                    # dialog IDs (for example, -1001234567890).
                    "id": get_peer_id(entity),
                    "name": get_display_name(entity)
                    or getattr(entity, "title", None)
                    or getattr(entity, "name", "")
                    or "",
                    "username": getattr(entity, "username", "") or "",
                    "kind": "user" if is_user else "group",
                }
            if action == "history":
                chat_id = int(data.get("chat_id", 0))
                days = int(data.get("days", 1))
                if days < 1 or days > 30:
                    raise ValueError("导入天数需要在 1–30 之间")
                chat = next((item.entity for item in dialogs if item.id == chat_id), None)
                if chat is None:
                    raise ValueError("未找到该群组，请先查看群组列表")
                zone = ZoneInfo(settings.timezone)
                end_day = datetime.now(zone).date()
                start_day = end_day - timedelta(days=days)
                start = day_bounds(start_day, settings.timezone)[0]
                end = day_bounds(end_day, settings.timezone)[0]
                store = Store(settings.data_dir / "butler.sqlite3")
                imported = 0
                scanned = 0
                sender_names: dict[int | None, str] = {}
                try:
                    async for message in client.iter_messages(chat, limit=5000):
                        scanned += 1
                        sent_at = message.date
                        if sent_at.tzinfo is None:
                            sent_at = sent_at.replace(tzinfo=ZoneInfo("UTC"))
                        if sent_at < start:
                            break
                        if sent_at >= end or not message.id:
                            continue
                        sender_id = message.sender_id
                        sender_name = sender_names.get(sender_id)
                        if sender_name is None:
                            sender = getattr(message, "sender", None) or await message.get_sender()
                            sender_name = get_display_name(sender) if sender else str(sender_id)
                            sender_names[sender_id] = sender_name
                        text = message.raw_text or (
                            "[媒体消息]" if message.media else "[非文本消息]"
                        )
                        imported += store.add_message(
                            chat_id,
                            message.id,
                            sender_id,
                            sender_name,
                            text,
                            sent_at,
                        )
                finally:
                    store.close()
                return {
                    "message": (
                        f"已导入 {start_day.isoformat()} 至 "
                        f"{(end_day - timedelta(days=1)).isoformat()} 的消息：新增 {imported} 条，"
                        f"扫描 {scanned} 条。"
                    ),
                    "imported": imported,
                    "scanned": scanned,
                }
            raise ValueError("不支持的 Telegram 操作")
        finally:
            await client.disconnect()

    def execute(self, action: str, data: dict, owner: str) -> dict:
        # Same lock is used by bu before any access to the Telethon session.
        with FileLock(str(self.config.parent / "data" / "telegram.lock"), timeout=2):
            active = self.control("status").strip() == "active"
            if active:
                self.control("stop")
            timeout = 660 if action == "checkin-test" else 180 if action == "history" else 60
            try:
                return asyncio.run(
                    asyncio.wait_for(self.request(action, data, owner), timeout=timeout)
                )
            finally:
                if active:
                    self.control("start")
