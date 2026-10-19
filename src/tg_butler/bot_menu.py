"""Telegram chat menu for inspecting and changing safe TG-Butler settings."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import yaml

from tg_butler.config import Settings, load_settings, time_parts
from tg_butler.menu import save_settings

log = logging.getLogger(__name__)

MAIN_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "📊 运行状态", "callback_data": "menu:status"},
            {"text": "⚙️ 配置参数", "callback_data": "menu:settings"},
        ],
        [
            {"text": "👥 监控群组", "callback_data": "menu:groups"},
            {"text": "⏰ 签到任务", "callback_data": "menu:checkins"},
        ],
        [
            {"text": "🧾 日报设置", "callback_data": "menu:summary"},
            {"text": "❓ 帮助", "callback_data": "menu:help"},
        ],
    ],
}
BACK_KEYBOARD = {
    "inline_keyboard": [[{"text": "⬅️ 返回主菜单", "callback_data": "menu:main"}]]
}
SETTINGS_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "🕐 日报时间", "callback_data": "setting:summary_at"},
            {"text": "🌏 时区", "callback_data": "setting:timezone"},
        ],
        [
            {"text": "🗑 保留天数", "callback_data": "setting:retention_days"},
            {"text": "📦 通知队列", "callback_data": "setting:queue_size"},
        ],
        [{"text": "⬅️ 返回主菜单", "callback_data": "menu:main"}],
    ]
}


class TelegramMenu:
    """Long-poll one bot and expose a small allow-listed configuration menu."""

    def __init__(
        self,
        token: str,
        admin_chat_id: int,
        config: Path,
        *,
        on_changed: Callable[[], Awaitable[None]] | None = None,
    ):
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.admin_chat_id = int(admin_chat_id)
        self.config = config.resolve()
        self.http = httpx.AsyncClient(timeout=35)
        self.offset = 0
        self.mode = "main"
        self.pending: str | None = None
        self.on_changed = on_changed
        self._stopped = False

    async def _call(self, method: str, payload: dict) -> dict:
        try:
            response = await self.http.post(f"{self.base_url}/{method}", json=payload)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Telegram 菜单网络请求失败") from exc
        if response.status_code != 200 or data.get("ok") is not True:
            raise RuntimeError(f"Telegram 菜单请求失败（HTTP {response.status_code}）")
        return data.get("result")

    async def _answer_callback(self, callback_id: str) -> None:
        await self._call("answerCallbackQuery", {"callback_query_id": callback_id})

    async def send(self, text: str, *, keyboard: dict | None = None) -> None:
        payload = {"chat_id": self.admin_chat_id, "text": text}
        if keyboard is not None:
            payload["reply_markup"] = keyboard
        await self._call("sendMessage", payload)

    def _read(self) -> dict:
        data = yaml.safe_load(self.config.read_text(encoding="utf-8")) or {}
        Settings.model_validate(data)
        return data

    async def _save(self, data: dict) -> None:
        save_settings(self.config, data)
        await self.send("✅ 已保存配置，任务服务正在重启以应用修改。", keyboard=MAIN_KEYBOARD)
        if self.on_changed:
            await self.on_changed()

    @staticmethod
    def _is_admin(update: dict, admin_chat_id: int) -> bool:
        callback = update.get("callback_query") or {}
        message = callback.get("message") or update.get("message") or {}
        chat = message.get("chat") or {}
        user = callback.get("from") or message.get("from") or {}
        return int(chat.get("id", 0)) == admin_chat_id and int(user.get("id", 0)) == admin_chat_id

    async def _status(self) -> None:
        settings = load_settings(self.config)
        await self.send(
            "📊 TG-Butler 运行状态\n"
            "任务服务：运行中\n"
            f"监控群组：{len(settings.groups)} 个\n"
            f"签到任务：{len(settings.checkins)} 个\n"
            f"日报：{'开启' if settings.summary.enabled else '关闭'}，每天 {settings.summary.at}\n"
            f"时区：{settings.timezone}\n"
            f"消息留存：{settings.retention_days} 天",
            keyboard=MAIN_KEYBOARD,
        )

    async def _settings(self) -> None:
        settings = load_settings(self.config)
        await self.send(
            "⚙️ 配置参数\n"
            f"日报时间：{settings.summary.at}\n"
            f"时区：{settings.timezone}\n"
            f"消息留存：{settings.retention_days} 天\n"
            f"通知队列：{settings.notifications.queue_size}\n\n"
            "点击下方项目后输入新值。",
            keyboard=SETTINGS_KEYBOARD,
        )

    async def _groups(self) -> None:
        settings = load_settings(self.config)
        if not settings.groups:
            text = "👥 当前没有监控群组。\n\n请在网页或 bu 菜单中添加群组。"
        else:
            lines = ["👥 监控群组"]
            for group in settings.groups:
                lines.append(
                    f"{group.id}  {group.name or '未命名'}\n"
                    f"  全群提醒：{'开' if group.notify_all else '关'} · "
                    f"日报：{'开' if group.daily_summary else '关'}\n"
                    f"  关注用户：{len(group.watch_users)} 个"
                )
            text = "\n".join(lines)
        text += (
            "\n\n修改群组请发送：\n"
            "/group 群组ID notify_all on\n"
            "/group 群组ID daily_summary off\n"
            "/group 群组ID watch_users 123,456\n"
            "/group 群组ID report_keywords 关键词1,关键词2"
        )
        await self.send(text, keyboard=BACK_KEYBOARD)

    async def _checkins(self) -> None:
        settings = load_settings(self.config)
        if not settings.checkins:
            text = "⏰ 当前没有签到任务。\n\n请在网页或 bu 菜单中添加签到任务。"
        else:
            lines = ["⏰ 签到任务"]
            for job in settings.checkins:
                lines.append(
                    f"{job.name}\n  群组：{job.chat_id} · 时间：{job.at}\n"
                    f"  命令：{job.command} · 回复确认：{'开' if job.responder_id else '关'}"
                )
            text = "\n".join(lines)
        text += "\n\n修改时间：\n/checkin 任务名称 at 09:00"
        await self.send(text, keyboard=BACK_KEYBOARD)

    async def _summary(self) -> None:
        settings = load_settings(self.config)
        await self.send(
            "🧾 日报设置\n"
            f"状态：{'开启' if settings.summary.enabled else '关闭'}\n"
            f"发送时间：{settings.summary.at}\n"
            f"AI 模型：{settings.summary.model or '未配置'}\n"
            f"最多消息：{settings.summary.max_messages}\n"
            f"最多字符：{settings.summary.max_chars}\n\n"
            "修改发送时间：/summary_at 08:30\n"
            "开关日报：/summary on 或 /summary off",
            keyboard=BACK_KEYBOARD,
        )

    async def _help(self) -> None:
        await self.send(
            "❓ TG-Butler 菜单帮助\n\n"
            "菜单只接受管理员接收用户的私聊消息。\n"
            "/menu  显示主菜单\n"
            "/status  查看运行状态\n"
            "/group 群组ID 字段 值  修改群组\n"
            "/checkin 任务名称 字段 值  修改签到参数\n"
            "/summary_at HH:MM  修改日报时间\n"
            "/summary on|off  开关日报\n\n"
            "配置修改会保存到服务器，并自动重启任务服务。",
            keyboard=MAIN_KEYBOARD,
        )

    async def _set_parameter(self, value: str) -> None:
        if not self.pending:
            return
        data = self._read()
        key = self.pending
        value = value.strip()
        if key == "summary_at":
            time_parts(value)
            data.setdefault("summary", {})["at"] = value
        elif key == "timezone":
            from zoneinfo import ZoneInfo

            ZoneInfo(value)
            data["timezone"] = value
        elif key in {"retention_days", "queue_size"}:
            number = int(value)
            limits = {"retention_days": (1, 3650), "queue_size": (1, 10000)}
            low, high = limits[key]
            if not low <= number <= high:
                raise ValueError(f"数值范围为 {low}–{high}")
            if key == "retention_days":
                data[key] = number
            else:
                data.setdefault("notifications", {})["queue_size"] = number
        else:
            raise ValueError("不支持的配置项")
        self.pending = None
        await self._save(data)

    async def _change_group(self, command: str) -> None:
        match = re.fullmatch(
            r"/group\s+(-?\d+)\s+"
            r"(name|notify_all|daily_summary|watch_users|report_keywords|report_images|report_links)\s+(.+)",
            command,
        )
        if not match:
            raise ValueError("格式：/group 群组ID notify_all on")
        chat_id, field, value = int(match.group(1)), match.group(2), match.group(3).strip()
        data = self._read()
        group = next(
            (item for item in data.get("groups", []) if int(item.get("id")) == chat_id), None
        )
        if group is None:
            raise ValueError("未找到该群组")
        if field in {"notify_all", "daily_summary", "report_images", "report_links"}:
            if value.lower() not in {"on", "off", "1", "0", "true", "false"}:
                raise ValueError("开关值请使用 on 或 off")
            group[field] = value.lower() in {"on", "1", "true"}
        elif field == "watch_users":
            group["watch_users"] = [
                int(item.strip())
                for item in value.replace("，", ",").split(",")
                if item.strip()
            ]
        elif field == "report_keywords":
            group["report_keywords"] = [
                item.strip()
                for item in value.replace("，", ",").split(",")
                if item.strip()
            ]
        else:
            group["name"] = value
        await self._save(data)

    async def _change_checkin(self, command: str) -> None:
        match = re.fullmatch(
            r"/checkin\s+(.+?)\s+(at|command|timeout_seconds|require_reply)\s+(.+)",
            command,
        )
        if not match:
            raise ValueError("格式：/checkin 任务名称 at 09:00")
        name, field, value = match.groups()
        data = self._read()
        job = next((item for item in data.get("checkins", []) if item.get("name") == name), None)
        if job is None:
            raise ValueError("未找到该签到任务")
        if field == "at":
            time_parts(value)
            job["at"] = value
        elif field == "command":
            if not 1 <= len(value) <= 4096:
                raise ValueError("签到命令长度需要在 1–4096 之间")
            job["command"] = value
        elif field == "timeout_seconds":
            timeout = int(value)
            if not 1 <= timeout <= 600:
                raise ValueError("超时秒数需要在 1–600 之间")
            job["timeout_seconds"] = timeout
        else:
            if value.lower() not in {"on", "off", "1", "0", "true", "false"}:
                raise ValueError("开关值请使用 on 或 off")
            job["require_reply"] = value.lower() in {"on", "1", "true"}
        await self._save(data)

    async def handle_text(self, text: str) -> None:
        text = text.strip()
        if self.pending:
            if text in {"⬅️ 返回主菜单", "/cancel"}:
                self.pending = None
                await self.send("已取消。", keyboard=MAIN_KEYBOARD)
            else:
                await self._set_parameter(text)
            return
        if text in {"/start", "/menu", "⬅️ 返回主菜单"}:
            self.mode = "main"
            await self.send(
                "👋 TG-Butler 主菜单\n请选择要查看或修改的项目。", keyboard=MAIN_KEYBOARD
            )
        elif text in {"📊 运行状态", "/status"}:
            await self._status()
        elif text in {"⚙️ 配置参数"}:
            await self._settings()
        elif text == "👥 监控群组":
            await self._groups()
        elif text == "⏰ 签到任务":
            await self._checkins()
        elif text == "🧾 日报设置":
            await self._summary()
        elif text == "❓ 帮助":
            await self._help()
        elif text == "/help":
            await self._help()
        elif text in {"🕐 日报时间", "/summary_at"}:
            self.pending = "summary_at"
            await self.send(
                "请输入日报时间，例如 08:30。发送 /cancel 取消。", keyboard=BACK_KEYBOARD
            )
        elif text == "🌏 时区":
            self.pending = "timezone"
            await self.send("请输入 IANA 时区，例如 Asia/Shanghai。", keyboard=BACK_KEYBOARD)
        elif text == "🗑 保留天数":
            self.pending = "retention_days"
            await self.send("请输入消息保留天数（1–3650）。", keyboard=BACK_KEYBOARD)
        elif text == "📦 通知队列":
            self.pending = "queue_size"
            await self.send("请输入通知队列容量（1–10000）。", keyboard=BACK_KEYBOARD)
        elif text.startswith("/group "):
            await self._change_group(text)
        elif text.startswith("/checkin "):
            await self._change_checkin(text)
        elif text.startswith("/summary "):
            await self._change_summary_toggle(text)
        elif text.startswith("/summary_at "):
            await self._change_summary_command(text)
        else:
            await self.send("无法识别该选项，请发送 /menu 返回主菜单。", keyboard=MAIN_KEYBOARD)

    async def handle_callback(self, callback: dict) -> None:
        callback_id = str(callback.get("id", ""))
        data = str(callback.get("data", ""))
        if callback_id:
            await self._answer_callback(callback_id)
        if data == "menu:main":
            await self.handle_text("/menu")
        elif data == "menu:status":
            await self._status()
        elif data == "menu:settings":
            await self._settings()
        elif data == "menu:groups":
            await self._groups()
        elif data == "menu:checkins":
            await self._checkins()
        elif data == "menu:summary":
            await self._summary()
        elif data == "menu:help":
            await self._help()
        elif data.startswith("setting:"):
            key = data.removeprefix("setting:")
            if key not in {"summary_at", "timezone", "retention_days", "queue_size"}:
                raise ValueError("不支持的配置项")
            self.pending = key
            prompts = {
                "summary_at": "请输入日报时间，例如 08:30。",
                "timezone": "请输入 IANA 时区，例如 Asia/Shanghai。",
                "retention_days": "请输入消息保留天数（1–3650）。",
                "queue_size": "请输入通知队列容量（1–10000）。",
            }
            await self.send(prompts[key], keyboard=BACK_KEYBOARD)
        else:
            raise ValueError("菜单选项已失效，请重新发送 /menu")

    async def _change_summary_command(self, command: str) -> None:
        value = command.removeprefix("/summary_at ").strip()
        self.pending = "summary_at"
        await self._set_parameter(value)

    async def _change_summary_toggle(self, command: str) -> None:
        value = command.removeprefix("/summary ").strip().lower()
        if value not in {"on", "off"}:
            raise ValueError("格式：/summary on 或 /summary off")
        data = self._read()
        data.setdefault("summary", {})["enabled"] = value == "on"
        await self._save(data)

    async def run(self) -> None:
        try:
            await self._call("deleteWebhook", {})
            await self._call(
                "setMyCommands",
                {
                    "commands": [
                        {"command": "menu", "description": "显示 TG-Butler 主菜单"},
                        {"command": "status", "description": "查看运行状态"},
                        {"command": "help", "description": "查看菜单帮助"},
                    ]
                },
            )
            while not self._stopped:
                updates = await self._call(
                    "getUpdates",
                    {
                        "offset": self.offset,
                        "timeout": 25,
                        "allowed_updates": ["message", "callback_query"],
                    },
                )
                for update in updates or []:
                    self.offset = int(update["update_id"]) + 1
                    if not self._is_admin(update, self.admin_chat_id):
                        continue
                    try:
                        if update.get("callback_query"):
                            await self.handle_callback(update["callback_query"])
                        else:
                            message = update.get("message") or {}
                            text = message.get("text")
                            if text:
                                await self.handle_text(text)
                    except (ValueError, TypeError) as exc:
                        await self.send(f"⚠️ {exc}", keyboard=MAIN_KEYBOARD)
                    except Exception as exc:
                        log.error("Telegram menu action failed (%s)", type(exc).__name__)
                        await self.send("⚠️ 操作失败，请检查配置后重试。", keyboard=MAIN_KEYBOARD)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("Telegram menu stopped (%s)", type(exc).__name__)

    async def close(self) -> None:
        self._stopped = True
        await self.http.aclose()


__all__ = ["TelegramMenu"]
