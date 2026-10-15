"""Short-lived Telegram web login challenges; never expose the account session."""

from __future__ import annotations

import asyncio
import time

from dotenv import dotenv_values
from filelock import FileLock
from telethon.errors import SessionPasswordNeededError

from tg_butler.app import connect_authorized, make_client
from tg_butler.config import ApplicationCredentials, load_settings


class TelegramGateway:
    def __init__(self, config, control):
        self.config = config
        self.control = control
        self.pending: dict = {}

    async def request(self, action: str, data: dict, owner: str) -> dict:
        settings = load_settings(self.config)
        values = dotenv_values(self.config.parent / ".env")
        credentials = ApplicationCredentials(
            api_id=values.get("TG_API_ID", ""), api_hash=values.get("TG_API_HASH", "")
        )
        client = make_client(settings, credentials)
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
                }
                return {
                    "status": "code",
                    "message": "验证码已申请，请查看 Telegram 官方消息或短信。",
                }
            if action in {"verify-code", "password"}:
                pending = self.pending
                if pending.get("owner") != owner or pending.get("expires", 0) < time.monotonic():
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
            dialogs = await client.get_dialogs()
            if action == "dialogs":
                return {
                    "items": [
                        {"id": item.id, "name": item.name}
                        for item in dialogs
                        if item.is_group or item.is_channel
                    ]
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
            raise ValueError("不支持的 Telegram 操作")
        finally:
            await client.disconnect()

    def execute(self, action: str, data: dict, owner: str) -> dict:
        # Same lock is used by bu before any access to the Telethon session.
        with FileLock(str(self.config.parent / "data" / "telegram.lock"), timeout=2):
            active = self.control("status").strip() == "active"
            if active:
                self.control("stop")
            try:
                return asyncio.run(asyncio.wait_for(self.request(action, data, owner), timeout=60))
            finally:
                if active:
                    self.control("start")
