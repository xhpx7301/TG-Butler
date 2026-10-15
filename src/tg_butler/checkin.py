from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from telethon import events

from tg_butler.config import Checkin
from tg_butler.notify import Notifier
from tg_butler.store import Store

log = logging.getLogger(__name__)


def match_response(job: Checkin, message, command_id: int) -> str | None:
    if message.sender_id != job.responder_id:
        return None
    if message.id <= command_id:
        return None
    if job.require_reply and message.reply_to_msg_id != command_id:
        return None
    text = message.raw_text or ""
    if job.failure_pattern and re.search(job.failure_pattern, text):
        return "rejected"
    if job.success_pattern and re.search(job.success_pattern, text):
        return "confirmed"
    return None


async def run_checkin(
    client,
    job: Checkin,
    store: Store,
    notifier: Notifier,
    timezone: str,
) -> None:
    day = datetime.now(ZoneInfo(timezone)).date().isoformat()
    if not store.claim("checkin", job.name, day):
        return
    loop = asyncio.get_running_loop()
    result = loop.create_future()
    sent = None
    early: list = []

    async def handler(event):
        if event.sender_id != job.responder_id:
            return
        if sent is None:
            if len(early) < 100:
                early.append(event.message)
            return
        status = match_response(job, event.message, sent.id)
        if status and not result.done():
            result.set_result(status)

    builder = events.NewMessage(chats=[job.chat_id], incoming=True)
    if job.responder_id:
        client.add_event_handler(handler, builder)
    try:
        # Claim is committed before this call. Never retry an ambiguous command send.
        sent = await client.send_message(job.chat_id, job.command, parse_mode=None)
        store.finish("checkin", job.name, day, "sent", str(sent.id))
        notifier.enqueue(f"📤 {job.name}：签到命令已发送（消息 {sent.id}）。")
        if not job.responder_id:
            return
        for message in early:
            status = match_response(job, message, sent.id)
            if status and not result.done():
                result.set_result(status)
        try:
            status = await asyncio.wait_for(result, timeout=job.timeout_seconds)
        except TimeoutError:
            status = "timeout"
        store.finish("checkin", job.name, day, status, str(sent.id))
        labels = {
            "confirmed": "✅ 收到匹配的签到成功回复。",
            "rejected": "❌ 收到匹配的签到失败回复。",
            "timeout": "⏳ 命令已发送，但等待回复超时，签到结果尚未确认。",
        }
        notifier.enqueue(f"{job.name}：{labels[status]}")
    except Exception as exc:
        # A network error can occur after Telegram accepted a command.
        # 'uncertain' deliberately avoids claiming that nothing was sent.
        status = "uncertain" if sent is None else "error"
        store.finish("checkin", job.name, day, status, type(exc).__name__)
        notifier.enqueue(
            f"⚠️ {job.name}：执行异常（{type(exc).__name__}），"
            "请检查群内记录；为避免重复签到，本日不会自动重发。"
        )
        log.error("Check-in %s failed (%s)", job.name, type(exc).__name__)
    finally:
        if job.responder_id:
            client.remove_event_handler(handler, builder)
        if not result.done():
            result.cancel()
