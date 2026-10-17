from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telethon import TelegramClient, events
from telethon.utils import get_display_name

from tg_butler.checkin import run_checkin
from tg_butler.config import (
    ApplicationCredentials,
    Credentials,
    NotificationCredentials,
    Settings,
    time_parts,
)
from tg_butler.notify import Notifier
from tg_butler.store import Store
from tg_butler.summary import message_link, send_daily

log = logging.getLogger(__name__)
LINK_RE = re.compile(r"(?:https?://|www\.|t\.me/)", re.IGNORECASE)


def make_client(settings: Settings, credentials: ApplicationCredentials) -> TelegramClient:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        str(settings.data_dir / "user"),
        credentials.api_id,
        credentials.api_hash.get_secret_value(),
        flood_sleep_threshold=0,
    )


def make_notifier(settings: Settings, credentials: NotificationCredentials) -> Notifier:
    return Notifier(
        credentials.bot_token.get_secret_value(),
        credentials.notify_chat_id,
        settings.notifications.queue_size,
    )


def report_alert_matches(group, text: str, message) -> bool:
    """Return whether a watched-user message matches the group's report filters."""
    checks: list[bool] = []
    keywords = [item.casefold() for item in group.report_keywords]
    if keywords:
        haystack = (text or "").casefold()
        checks.append(
            all(keyword in haystack for keyword in keywords)
            if group.report_match_mode == "all"
            else any(keyword in haystack for keyword in keywords)
        )
    if group.report_images:
        media = getattr(message, "media", None)
        document = getattr(media, "document", None)
        mime_type = getattr(document, "mime_type", "") or ""
        checks.append(getattr(message, "photo", None) is not None or mime_type.startswith("image/"))
    if group.report_links:
        checks.append(bool(LINK_RE.search(text or "")))
    return bool(checks) and any(checks)


async def connect_authorized(client) -> None:
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Telegram session missing or expired; run tg-butler login first")


async def run(settings: Settings, credentials: Credentials) -> None:
    store = Store(settings.data_dir / "butler.sqlite3")
    monitor_credentials = credentials.notification("monitor")
    report_credentials = credentials.notification("report")
    monitor_notifier = make_notifier(settings, monitor_credentials)
    same_notifier = (
        monitor_credentials.bot_token.get_secret_value()
        == report_credentials.bot_token.get_secret_value()
        and monitor_credentials.notify_chat_id == report_credentials.notify_chat_id
    )
    report_notifier = (
        monitor_notifier if same_notifier else make_notifier(settings, report_credentials)
    )
    notifiers = [monitor_notifier] if same_notifier else [monitor_notifier, report_notifier]
    client = make_client(settings, credentials)
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
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
            watched = event.sender_id in group.watch_users
            if added and (group.notify_all or watched):
                stamp = event.message.date.astimezone(zone).strftime("%m-%d %H:%M")
                monitor_notifier.enqueue(
                    f"🔔 {group.name or group.id} · {stamp}\n"
                    f"{sender_name}（{event.sender_id}）\n{text[:6000]}\n"
                    f"{message_link(event.chat_id, event.id)}"
                )
                if (
                    watched
                    and not same_notifier
                    and report_alert_matches(group, text, event.message)
                ):
                    report_notifier.enqueue_copy(
                        f"🎯 {group.name or group.id} · {stamp}\n"
                        f"{sender_name}（{event.sender_id}）\n{text[:6000]}\n"
                        f"{message_link(event.chat_id, event.id)}",
                        event.chat_id,
                        event.id,
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
            monitor_notifier,
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
        workers = [asyncio.create_task(item.worker()) for item in notifiers]
        # Fail startup when the notification channel is not usable.
        await monitor_notifier.send("🟢 TG-Butler 关注-日报服务已启动。")
        if not same_notifier:
            await report_notifier.send("🟢 TG-Butler 自定义通知服务已启动。")
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
                args=[client, job, store, monitor_notifier, settings.timezone],
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
        for item in notifiers:
            with suppress(TimeoutError):
                await asyncio.wait_for(item.queue.join(), timeout=10)
        for item in workers if "workers" in locals() else []:
            item.cancel()
        for item in workers if "workers" in locals() else []:
            with suppress(asyncio.CancelledError):
                await item
        for item in notifiers:
            await item.close()
        store.close()
