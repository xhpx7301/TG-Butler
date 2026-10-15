from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telethon import TelegramClient, events
from telethon.utils import get_display_name

from tg_butler.checkin import run_checkin
from tg_butler.config import Credentials, Settings, time_parts
from tg_butler.notify import Notifier
from tg_butler.store import Store
from tg_butler.summary import message_link, send_daily

log = logging.getLogger(__name__)


def make_client(settings: Settings, credentials: Credentials) -> TelegramClient:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        str(settings.data_dir / "user"),
        credentials.api_id,
        credentials.api_hash.get_secret_value(),
        flood_sleep_threshold=0,
    )


def make_notifier(settings: Settings, credentials: Credentials) -> Notifier:
    return Notifier(
        credentials.bot_token.get_secret_value(),
        credentials.notify_chat_id,
        settings.notifications.queue_size,
    )


async def connect_authorized(client) -> None:
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Telegram session missing or expired; run tg-butler login first")


async def run(settings: Settings, credentials: Credentials) -> None:
    store = Store(settings.data_dir / "butler.sqlite3")
    notifier = make_notifier(settings, credentials)
    client = make_client(settings, credentials)
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    worker = None
    zone = ZoneInfo(settings.timezone)
    groups = {group.id: group for group in settings.groups}

    async def on_message(event):
        group = groups.get(event.chat_id)
        if group is None:
            return
        try:
            sender = await event.get_sender()
            sender_name = get_display_name(sender) if sender else str(event.sender_id)
            text = event.raw_text or ("[媒体消息]" if event.message.media else "[非文本消息]")
            added = store.add_message(
                event.chat_id,
                event.id,
                event.sender_id,
                sender_name,
                text,
                event.message.date,
            )
            if added and (group.notify_all or event.sender_id in group.watch_users):
                stamp = event.message.date.astimezone(zone).strftime("%m-%d %H:%M")
                notifier.enqueue(
                    f"🔔 {group.name or group.id} · {stamp}\n"
                    f"{sender_name}（{event.sender_id}）\n{text[:6000]}\n"
                    f"{message_link(event.chat_id, event.id)}"
                )
        except Exception as exc:
            log.error(
                "Message processing failed for chat %s (%s)", event.chat_id, type(exc).__name__
            )

    async def daily():
        now = datetime.now(zone)
        await send_daily(
            settings,
            store,
            notifier,
            now.date() - timedelta(days=1),
            credentials.summary_api_key.get_secret_value(),
        )

    async def prune():
        store.prune(datetime.now(zone) - timedelta(days=settings.retention_days))

    def trigger(at: str) -> CronTrigger:
        hour, minute = time_parts(at)
        return CronTrigger(hour=hour, minute=minute, timezone=settings.timezone)

    try:
        await connect_authorized(client)
        # Populate Telethon's entity cache so numeric private group IDs resolve.
        dialogs = await client.get_dialogs()
        available = {dialog.id for dialog in dialogs}
        required = set(groups) | {job.chat_id for job in settings.checkins}
        missing = required - available
        if missing:
            raise RuntimeError(f"Configured chats are not in account dialogs: {sorted(missing)}")
        if groups:
            client.add_event_handler(
                on_message, events.NewMessage(chats=list(groups), incoming=True)
            )
        worker = asyncio.create_task(notifier.worker())
        # Fail startup when the notification channel is not usable.
        await notifier.send("🟢 TG-Butler 已启动。")
        if settings.summary.enabled:
            scheduler.add_job(
                daily,
                trigger(settings.summary.at),
                id="daily-summary",
                coalesce=True,
                max_instances=1,
                misfire_grace_time=3600,
            )
        for job in settings.checkins:
            scheduler.add_job(
                run_checkin,
                trigger(job.at),
                id=f"checkin:{job.name}",
                args=[client, job, store, notifier, settings.timezone],
                coalesce=True,
                max_instances=1,
                misfire_grace_time=60,
            )
        scheduler.add_job(
            prune, trigger("03:30"), id="retention", coalesce=True, misfire_grace_time=3600
        )
        await prune()
        scheduler.start()
        log.info(
            "TG-Butler started: %d groups, %d check-ins, timezone=%s",
            len(groups),
            len(settings.checkins),
            settings.timezone,
        )
        await client.run_until_disconnected()
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            # APScheduler schedules its shutdown on the event loop.
            await asyncio.sleep(0)
        await client.disconnect()
        if worker:
            with suppress(TimeoutError):
                await asyncio.wait_for(notifier.queue.join(), timeout=10)
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await notifier.close()
        store.close()
