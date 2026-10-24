from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
from contextlib import suppress
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telethon import TelegramClient, events
from telethon.utils import get_display_name

from tg_butler.bot_menu import TelegramMenu
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


def make_client(
    settings: Settings, credentials: ApplicationCredentials, account_id: str = "default"
) -> TelegramClient:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    session = (
        settings.data_dir / "user"
        if account_id == "default"
        else settings.data_dir / "accounts" / account_id
    )
    session.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(
        str(session),
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


def message_sender_id(event, sender) -> int | None:
    """Resolve the numeric user ID used by watch lists for a new message."""
    sender_id = getattr(sender, "id", None) or getattr(event, "sender_id", None)
    return int(sender_id) if sender_id is not None else None


def live_alert(
    group,
    sender,
    sender_name: str,
    sender_id: int,
    text: str,
    message_date: datetime,
    message_id: int,
    timezone: ZoneInfo,
    chat_username: str | None = None,
    watched: bool = True,
) -> str:
    """Build a readable Telegram HTML alert with a direct link to its source message."""
    username = getattr(sender, "username", None)
    identity = f"{sender_id} | @{username}" if username else str(sender_id)
    stamp = message_date.astimezone(timezone).strftime("%Y/%m/%d %H:%M:%S")
    link = message_link(group.id, message_id, chat_username)
    link_line = (
        f'🔗 消息直达：<a href="{escape(link, quote=True)}">点击直接跳转消息</a>' if link else ""
    )
    lines = [
        "🚨 <b>【监控预警：目标用户已发言】</b>"
        if watched
        else "🔔 <b>【监控提醒：群组收到新消息】</b>",
        f"👤 <b>发言人:</b> {escape(sender_name)}（{escape(identity)}）",
        f"👥 <b>来源群组:</b> {group.id}（{escape(group.name or str(group.id))}）",
        f"🕒 <b>发言时间:</b> {stamp}",
    ]
    if link_line:
        lines.append(link_line)
    lines.append(f"📝 <b>发言内容:</b>\n{escape(text[:6000])}")
    return "\n".join(lines)


async def connect_authorized(client) -> None:
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("Telegram session missing or expired; run tg-butler login first")


async def run(
    settings: Settings, credentials: Credentials, config_path: Path | None = None
) -> None:
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
    active_account_ids = {group.account_id for group in settings.groups if group.enabled} | {
        job.account_id for job in settings.checkins if job.enabled
    }
    active_account_ids.add("default")
    clients = {
        account.id: make_client(settings, credentials, account.id)
        for account in settings.accounts
        if account.id in active_account_ids
    }
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    zone = ZoneInfo(settings.timezone)
    groups_by_account = {
        account_id: {
            group.id: group
            for group in settings.groups
            if group.enabled and group.account_id == account_id
        }
        for account_id in clients
    }
    dialog_names: dict[str, dict[int, str]] = {}
    menu = None
    menu_task = None

    async def restart_after_menu_change() -> None:
        # Let the confirmation message leave Telegram before systemd restarts us.
        await asyncio.sleep(1)
        os.kill(os.getpid(), signal.SIGTERM)

    async def on_message(event, group):
        try:
            sender = await event.get_sender()
            sender_id = message_sender_id(event, sender)
            if sender_id is None:
                log.warning(
                    "Skipping message without a resolvable sender ID in chat %s", event.chat_id
                )
                return
            sender_name = get_display_name(sender) if sender else str(sender_id)
            text = event.raw_text or ("[媒体消息]" if event.message.media else "[非文本消息]")
            added = store.add_message(
                event.chat_id,
                event.id,
                sender_id,
                sender_name,
                text,
                event.message.date,
            )
            watched = sender_id in group.watch_users
            if added and (group.notify_all or watched):
                alert = live_alert(
                    group,
                    sender,
                    sender_name,
                    sender_id,
                    text,
                    event.message.date,
                    event.id,
                    zone,
                    getattr(event.chat, "username", None),
                    watched,
                )
                # A disabled filter sends every watched-user message through
                # the monitor/daily bot.  When enabled, watched-user alerts
                # are routed to the custom bot only when a rule matches.
                if group.notify_all or (watched and not group.report_filter_enabled):
                    monitor_notifier.enqueue(alert, parse_mode="HTML")
                if (
                    watched
                    and group.report_filter_enabled
                    and not (same_notifier and group.notify_all)
                    and report_alert_matches(
                        group, text, event.message
                    )
                ):
                    filtered_alert = alert.replace(
                        "监控预警：目标用户已发言", "筛选提醒：目标用户消息命中条件"
                    )
                    if same_notifier:
                        # A legacy single-bot setup cannot copy from the
                        # source chat, so keep filtered delivery on that bot.
                        monitor_notifier.enqueue(filtered_alert, parse_mode="HTML")
                    else:
                        report_notifier.enqueue_copy(
                            filtered_alert,
                            event.chat_id,
                            event.id,
                            parse_mode="HTML",
                        )
        except Exception:
            log.exception("Message processing failed for chat %s", event.chat_id)

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
        for account_id, client in clients.items():
            await connect_authorized(client)
            dialogs = await client.get_dialogs()
            dialog_names[account_id] = {dialog.id: dialog.name for dialog in dialogs}
            available = {dialog.id for dialog in dialogs}
            groups = groups_by_account[account_id]
            jobs = [
                job
                for job in settings.checkins
                if job.enabled and job.account_id == account_id
            ]
            required = set(groups) | {job.chat_id for job in jobs}
            missing = required - available
            if missing:
                raise RuntimeError(
                    f"Configured chats are not in account {account_id} dialogs: {sorted(missing)}"
                )
            for group in groups.values():
                async def handle(event, selected_group=group):
                    await on_message(event, selected_group)

                client.add_event_handler(
                    handle, events.NewMessage(chats=[group.id], incoming=True)
                )
        workers = [asyncio.create_task(item.worker()) for item in notifiers]
        menu = TelegramMenu(
            monitor_credentials.bot_token.get_secret_value(),
            monitor_credentials.notify_chat_id,
            config_path or settings.data_dir.parent / "config.yaml",
            on_changed=restart_after_menu_change,
        )
        menu_task = asyncio.create_task(menu.run())
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
            if not job.enabled:
                continue
            account_name = next(
                account.name for account in settings.accounts if account.id == job.account_id
            )
            chat_name = dialog_names[job.account_id].get(job.chat_id, str(job.chat_id))
            scheduler.add_job(
                run_checkin,
                trigger(job.at),
                id=f"checkin:{job.name}",
                args=[
                    clients[job.account_id],
                    job,
                    store,
                    monitor_notifier,
                    settings.timezone,
                    account_name,
                    chat_name,
                ],
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
            sum(group.enabled for group in settings.groups),
            sum(job.enabled for job in settings.checkins),
            settings.timezone,
        )
        await asyncio.gather(*(client.run_until_disconnected() for client in clients.values()))
    finally:
        if menu is not None:
            await menu.close()
        if menu_task is not None:
            menu_task.cancel()
            with suppress(asyncio.CancelledError):
                await menu_task
        if scheduler.running:
            scheduler.shutdown(wait=False)
            # APScheduler schedules its shutdown on the event loop.
            await asyncio.sleep(0)
        for client in clients.values():
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
