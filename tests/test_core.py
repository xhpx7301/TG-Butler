import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import ValidationError

from tg_butler.app import live_alert, make_client, report_alert_matches
from tg_butler.checkin import match_response, run_checkin
from tg_butler.config import Checkin, Credentials, Group, Settings, load_settings
from tg_butler.notify import Notifier, chunks
from tg_butler.store import Store
from tg_butler.summary import (
    build_digest,
    check_connection,
    clean_ai_text,
    completion_urls,
    day_bounds,
    send_daily,
    send_test_daily,
)


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "test.sqlite3")
    yield db
    db.close()


def test_daily_window_uses_local_calendar_and_excludes_next_day(store):
    start, end = day_bounds(date(2026, 10, 15), "Asia/Shanghai")
    assert start.astimezone(UTC) == datetime(2026, 10, 14, 16, tzinfo=UTC)
    assert end.astimezone(UTC) == datetime(2026, 10, 15, 16, tzinfo=UTC)
    store.add_message(-1001, 1, 7, "A", "include", start)
    store.add_message(-1001, 2, 7, "A", "exclude", end)
    assert [row["text"] for row in store.messages(-1001, start, end)] == ["include"]


def test_dst_day_is_not_assumed_to_be_24_hours():
    start, end = day_bounds(date(2026, 3, 8), "America/New_York")
    assert end.timestamp() - start.timestamp() == 23 * 3600


def test_messages_and_claims_survive_restart_without_duplicates(tmp_path):
    path = tmp_path / "db.sqlite3"
    db = Store(path)
    assert db.add_message(1, 2, 3, "user", "text", datetime.now(UTC))
    assert not db.add_message(1, 2, 3, "user", "text", datetime.now(UTC))
    assert db.claim("checkin", "job", "2026-10-15")
    db.close()
    db = Store(path)
    assert not db.claim("checkin", "job", "2026-10-15")
    assert db.claim("checkin", "job", "2026-10-16")
    db.close()


@pytest.mark.parametrize("value", ["9:00", "24:00", "09:60", "nonsense"])
def test_invalid_schedule_rejected(value):
    with pytest.raises(ValidationError):
        Checkin(name="job", chat_id=1, at=value, command="/go")


def test_config_rejects_unknown_keys_and_ambiguous_rules():
    with pytest.raises(ValidationError):
        Settings.model_validate({"groups": [{"id": 1, "watch_user": [2]}]})
    with pytest.raises(ValidationError):
        Checkin(name="job", chat_id=1, at="09:00", command="/go", success_pattern="ok")


def test_legacy_tasks_default_to_default_account_and_unknown_account_is_rejected():
    settings = Settings.model_validate(
        {
            "groups": [{"id": 1}],
            "checkins": [
                {"name": "job", "chat_id": 1, "at": "09:00", "command": "/go"}
            ],
        }
    )
    assert settings.groups[0].account_id == "default"
    assert settings.checkins[0].account_id == "default"
    with pytest.raises(ValidationError):
        Settings.model_validate({"groups": [{"id": 1, "account_id": "missing"}]})


def test_accounts_use_separate_telethon_session_paths(tmp_path):
    from tg_butler.config import ApplicationCredentials

    settings = Settings(data_dir=tmp_path)
    default = make_client(settings, ApplicationCredentials(api_id=1, api_hash="hash"))
    extra = make_client(settings, ApplicationCredentials(api_id=1, api_hash="hash"), "work")
    assert Path(default.session.filename).name == "user.session"
    assert Path(extra.session.filename).parts[-2:] == ("accounts", "work.session")


def test_report_filters_match_keywords_links_and_images():
    image = SimpleNamespace(media=SimpleNamespace(document=SimpleNamespace(mime_type="image/jpeg")))
    group = Group(
        id=1,
        watch_users=[2],
        report_keywords=["重要", "项目"],
        report_match_mode="all",
        report_images=True,
        report_links=True,
    )
    assert report_alert_matches(group, "这是重要项目 https://example.test", SimpleNamespace())
    assert report_alert_matches(group, "普通文字", image)
    assert not report_alert_matches(group, "只有重要", SimpleNamespace())


def test_live_alert_is_structured_escaped_and_has_direct_link():
    group = Group(id=-100123456, name="目标群组监控")
    alert = live_alert(
        group,
        SimpleNamespace(username="alice"),
        "Alice <管理员>",
        8454584693,
        "算了 & <好了>",
        datetime(2026, 10, 20, 7, 58, tzinfo=UTC),
        42,
        ZoneInfo("Asia/Shanghai"),
    )
    assert "【监控预警：目标用户已发言】" in alert
    assert "8454584693 | @alice" in alert
    assert "-100123456（目标群组监控）" in alert
    assert "2026/10/20 15:58:00" in alert
    assert 'href="https://t.me/c/123456/42"' in alert
    assert "Alice &lt;管理员&gt;" in alert
    assert "算了 &amp; &lt;好了&gt;" in alert


def test_notification_credentials_support_split_roles_and_legacy_fallback():
    credentials = Credentials(
        api_id=1,
        api_hash="hash",
        monitor_bot_token="monitor-token",
        monitor_notify_chat_id=10,
        report_bot_token="report-token",
        report_notify_chat_id=11,
    )
    assert credentials.notification("monitor").notify_chat_id == 10
    assert credentials.notification("report").bot_token.get_secret_value() == "report-token"
    legacy = Credentials(
        api_id=1, api_hash="hash", bot_token="legacy-token", notify_chat_id=12
    )
    assert legacy.notification("monitor").notify_chat_id == 12
    assert legacy.notification("report").bot_token.get_secret_value() == "legacy-token"
    with pytest.raises(ValidationError):
        Checkin(
            name="job", chat_id=1, at="09:00", command="/go", responder_id=3, success_pattern="["
        )


def test_example_config_is_valid_and_paths_are_config_relative():
    settings = load_settings(Path(__file__).parents[1] / "config.example.yaml")
    assert settings.data_dir == Path(__file__).parents[1] / "data"
    assert settings.checkins == []


def checkin_job(**overrides):
    return Checkin(
        **(
            {
                "name": "job",
                "chat_id": -1001,
                "at": "09:00",
                "command": "/go",
                "responder_id": 3,
                "success_pattern": "成功",
                "failure_pattern": "失败",
            }
            | overrides
        )
    )


def message(sender=3, reply=10, text="成功", message_id=11):
    return SimpleNamespace(sender_id=sender, reply_to_msg_id=reply, raw_text=text, id=message_id)


@pytest.mark.parametrize("response", [message(sender=4), message(reply=9), message(message_id=9)])
def test_confirmation_does_not_accept_other_users_or_commands(response):
    assert match_response(checkin_job(), response, 10) is None


def test_confirmation_and_failure_rules():
    assert match_response(checkin_job(), message(), 10) == "confirmed"
    assert match_response(checkin_job(), message(text="失败"), 10) == "rejected"
    assert match_response(checkin_job(require_reply=False), message(reply=None), 10) == "confirmed"


class FakeClient:
    def __init__(self, fail=False):
        self.handler = None
        self.calls = 0
        self.fail = fail

    def add_event_handler(self, handler, builder):
        self.handler = handler

    def remove_event_handler(self, handler, builder):
        self.handler = None

    async def send_message(self, chat_id, command, **kwargs):
        self.calls += 1
        if self.fail:
            raise OSError("ambiguous network send")
        # Simulate a bot reply racing ahead of send_message's return.
        await self.handler(SimpleNamespace(sender_id=3, message=message()))
        return SimpleNamespace(id=10)


class FakeNotifier:
    def __init__(self):
        self.alerts = []
        self.send = AsyncMock()

    def enqueue(self, text):
        self.alerts.append(text)


async def test_early_confirmation_race_and_daily_dedup(store):
    client, notifier = FakeClient(), FakeNotifier()
    await run_checkin(client, checkin_job(), store, notifier, "Asia/Shanghai")
    await run_checkin(client, checkin_job(), store, notifier, "Asia/Shanghai")
    assert client.calls == 1
    assert store.runs()[0]["status"] == "confirmed"
    assert client.handler is None
    assert len(notifier.alerts) == 2


async def test_ambiguous_send_is_not_automatically_retried(store):
    client, notifier = FakeClient(fail=True), FakeNotifier()
    await run_checkin(client, checkin_job(), store, notifier, "Asia/Shanghai")
    await run_checkin(client, checkin_job(), store, notifier, "Asia/Shanghai")
    assert client.calls == 1
    assert store.runs()[0]["status"] == "uncertain"


async def test_sent_without_confirmation_rules_does_not_claim_success(store):
    client, notifier = AsyncMock(), FakeNotifier()
    client.send_message.return_value = SimpleNamespace(id=10)
    job = Checkin(name="send-only", chat_id=1, at="09:00", command="/go")
    await run_checkin(client, job, store, notifier, "Asia/Shanghai")
    assert store.runs()[0]["status"] == "sent"
    assert len(notifier.alerts) == 1
    client.send_message.assert_awaited_once_with(1, "/go", parse_mode=None)


async def test_no_reply_reports_timeout_not_success(store):
    client, notifier = FakeClient(), FakeNotifier()

    async def send_without_reply(*args, **kwargs):
        return SimpleNamespace(id=10)

    client.send_message = send_without_reply
    await run_checkin(client, checkin_job(timeout_seconds=1), store, notifier, "Asia/Shanghai")
    assert store.runs()[0]["status"] == "timeout"
    assert "尚未确认" in notifier.alerts[-1]


async def test_summary_failure_is_recorded_and_other_groups_continue(store):
    settings = Settings(groups=[Group(id=1), Group(id=2)])
    notifier = FakeNotifier()
    notifier.send.side_effect = [RuntimeError("offline"), None]
    await send_daily(settings, store, notifier, date(2026, 10, 15))
    await send_daily(settings, store, notifier, date(2026, 10, 15))
    assert notifier.send.await_count == 2
    assert {row["name"]: row["status"] for row in store.runs()} == {"1": "failed", "2": "sent"}


async def test_test_daily_sends_without_claiming_a_run(store):
    settings = Settings(groups=[Group(id=1), Group(id=2, daily_summary=False)])
    notifier = FakeNotifier()
    assert await send_test_daily(settings, store, notifier, date(2026, 10, 15)) == 1
    assert notifier.send.await_count == 1
    assert store.runs() == []


async def test_ai_failure_returns_honest_local_fallback(monkeypatch):
    settings = Settings(summary={"api_url": "https://example.test/chat/completions", "model": "x"})
    post = AsyncMock(side_effect=httpx.ConnectError("offline"))
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    digest = await build_digest(
        settings,
        Group(id=1),
        date(2026, 10, 15),
        [
            {"sender_name": "A", "sender_id": 2, "message_id": 3, "text": "hello"},
        ],
        "secret",
    )
    assert "AI 摘要生成失败" in digest
    assert "hello" in digest


def test_completion_urls_support_root_and_explicit_endpoints():
    assert completion_urls("https://skybridge-api.com") == [
        ("https://skybridge-api.com/v1/responses", "responses"),
        ("https://skybridge-api.com/v1/chat/completions", "chat"),
    ]
    assert completion_urls("https://example.test/v1/chat/completions") == [
        ("https://example.test/v1/chat/completions", "chat")
    ]


async def test_connection_accepts_responses_format(monkeypatch):
    response = httpx.Response(200, json={"output_text": "OK"})
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    answer = await check_connection("https://skybridge-api.com", "gpt-5.5", "secret")
    assert answer == "OK"
    assert post.await_args.args[0] == "https://skybridge-api.com/v1/responses"
    assert post.await_args.kwargs["json"]["max_output_tokens"] == 16


def test_clean_ai_text_removes_markdown_that_plain_telegram_messages_show():
    assert clean_ai_text("**主要话题**\n# 结论\n```text\n内容\n```") == "主要话题\n结论\n内容"


def test_unicode_notification_chunks_fit_telegram_limit():
    text = "中文😀" * 3000
    parts = chunks(text)
    assert "".join(parts) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= 4096 for part in parts)


async def test_notification_retry_and_token_not_in_error():
    notifier = Notifier("SENSITIVE_TOKEN", 123)
    responses = iter([httpx.Response(500), httpx.Response(200, json={"ok": True})])
    await notifier.http.aclose()
    notifier.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: next(responses)))
    await notifier.send("hello")
    await notifier.http.aclose()
    notifier.http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(403, json={"ok": False}))
    )
    with pytest.raises(RuntimeError) as caught:
        await notifier.send("hello")
    assert "SENSITIVE_TOKEN" not in str(caught.value)
    await notifier.close()


async def test_custom_notification_copies_original_message():
    notifier = Notifier("fake", 123)
    requests = []

    async def handler(request):
        requests.append((request.url.path, request.read()))
        return httpx.Response(200, json={"ok": True})

    await notifier.http.aclose()
    notifier.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    notifier.enqueue_copy("matched alert", -100123, 456, parse_mode="HTML")
    worker = asyncio.create_task(notifier.worker())
    await notifier.queue.join()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert [path.rsplit("/", 1)[-1] for path, _ in requests] == ["sendMessage", "copyMessage"]
    assert b'"parse_mode":"HTML"' in requests[0][1]
    assert b'"from_chat_id":-100123' in requests[1][1]
    assert b'"message_id":456' in requests[1][1]
    await notifier.close()


async def test_full_alert_queue_does_not_block_collection():
    notifier = Notifier("fake", 1, queue_size=1)
    notifier.enqueue("one")
    notifier.enqueue("two")
    assert notifier.queue.qsize() == 1
    assert notifier.dropped == 1
    await notifier.close()
