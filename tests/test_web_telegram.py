import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zoneinfo import ZoneInfo

import pytest
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import Channel, User

from tg_butler.menu import save_settings
from tg_butler.web_telegram import TelegramGateway


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    save_settings(path, {"groups": []})
    (tmp_path / ".env").write_text("TG_API_ID=123\nTG_API_HASH=real-hash\n", encoding="utf-8")
    control = Mock(return_value="active")
    client = AsyncMock()
    client.is_user_authorized.return_value = False
    client.send_code_request.return_value = SimpleNamespace(phone_code_hash="server-only")
    monkeypatch.setattr("tg_butler.web_telegram.make_client", Mock(return_value=client))
    return TelegramGateway(path, control), client, control


async def test_code_hash_stays_on_server_and_password_is_not_persisted(gateway):
    manager, client, _ = gateway
    result = await manager.request("send-code", {"phone": "+8613800000000"}, "owner")
    assert result["status"] == "code"
    assert "server-only" not in str(result)
    client.sign_in.side_effect = SessionPasswordNeededError(request=None)
    result = await manager.request("verify-code", {"code": "12345"}, "owner")
    assert result["status"] == "password"
    client.sign_in.side_effect = None
    result = await manager.request("password", {"password": "telegram-secret"}, "owner")
    assert result["status"] == "authorized"
    assert manager.pending == {}
    assert "telegram-secret" not in (manager.config.parent / ".env").read_text()
    client.disconnect.assert_awaited()


async def test_login_challenge_expiry_and_owner_are_checked(gateway):
    manager, client, _ = gateway
    await manager.request("send-code", {"phone": "+8613800000000"}, "owner")
    with pytest.raises(ValueError):
        await manager.request("verify-code", {"code": "12345"}, "different-admin")
    manager.pending["expires"] = time.monotonic() - 1
    with pytest.raises(ValueError):
        await manager.request("password", {"password": "x"}, "owner")
    client.sign_in.assert_not_awaited()


async def test_login_challenge_is_bound_to_selected_account(gateway):
    manager, client, _ = gateway
    from tg_butler.menu import save_settings

    save_settings(
        manager.config, {"accounts": [{"id": "default"}, {"id": "work", "name": "工作"}]}
    )
    await manager.request(
        "send-code", {"phone": "+8613800000000", "account_id": "default"}, "owner"
    )
    with pytest.raises(ValueError, match="过期"):
        await manager.request("verify-code", {"code": "12345", "account_id": "work"}, "owner")
    client.sign_in.assert_not_awaited()


def test_telegram_errors_restore_original_running_state(gateway):
    manager, client, control = gateway
    client.send_code_request.side_effect = OSError("network")
    with pytest.raises(OSError):
        manager.execute("send-code", {"phone": "+8613800000000"}, "owner")
    assert [call.args[0] for call in control.call_args_list] == ["status", "stop", "start"]


def test_stopped_task_service_not_started_by_web_lookup(gateway):
    manager, client, control = gateway
    control.return_value = "inactive"
    client.is_user_authorized.return_value = True
    client.get_dialogs.return_value = [
        SimpleNamespace(id=-100123, name="群组", is_group=True, is_channel=False)
    ]
    result = manager.execute("dialogs", {}, "owner")
    assert result["items"] == [{"id": -100123, "name": "群组"}]
    control.assert_called_once_with("status")


async def test_resolve_group_link_returns_group_id(gateway):
    manager, client, _ = gateway
    client.is_user_authorized.return_value = True
    group = Channel(
        id=1512125956,
        title="Example group",
        username="example",
        photo=None,
        date=None,
        megagroup=True,
    )
    client.get_entity.return_value = group

    result = await manager.request(
        "resolve", {"kind": "group", "reference": "https://t.me/example?start=1"}, "owner"
    )

    assert result == {
        "id": -1001512125956,
        "name": "Example group",
        "username": "example",
        "kind": "group",
    }
    client.get_entity.assert_awaited_once_with("example")


async def test_resolve_user_username_returns_user_id(gateway):
    manager, client, _ = gateway
    client.is_user_authorized.return_value = True
    client.get_entity.return_value = User(
        id=42, is_self=False, first_name="Example", username="example"
    )

    result = await manager.request("resolve", {"kind": "user", "reference": "@example"}, "owner")

    assert result == {
        "id": 42,
        "name": "Example",
        "username": "example",
        "kind": "user",
    }
    client.get_entity.assert_awaited_once_with("example")


async def test_resolve_rejects_private_invite_links(gateway):
    manager, client, _ = gateway
    client.is_user_authorized.return_value = True

    with pytest.raises(ValueError, match="私密邀请链接"):
        await manager.request(
            "resolve", {"kind": "group", "reference": "https://t.me/+secret"}, "owner"
        )

    client.get_entity.assert_not_awaited()


async def test_history_import_stores_previous_complete_day_and_deduplicates(gateway):
    manager, client, _ = gateway
    client.is_user_authorized.return_value = True
    chat = SimpleNamespace(id=-100123)
    client.get_dialogs.return_value = [
        SimpleNamespace(id=-100123, name="群组", entity=chat, is_group=True, is_channel=False)
    ]
    zone = ZoneInfo("Asia/Shanghai")
    yesterday = datetime.now(zone).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(
        days=1
    )
    old = yesterday - timedelta(days=2)

    async def messages(*args, **kwargs):
        for sent_at, message_id in ((yesterday, 10), (old, 9)):
            yield SimpleNamespace(
                id=message_id,
                date=sent_at,
                sender_id=7,
                raw_text="历史消息",
                media=None,
                get_sender=AsyncMock(
                    return_value=SimpleNamespace(first_name="A", last_name="")
                ),
            )

    client.iter_messages = messages
    result = await manager.request("history", {"chat_id": -100123, "days": 1}, "owner")
    assert result["imported"] == 1
    assert result["scanned"] == 2
    from tg_butler.store import Store

    store = Store(manager.config.parent / "data" / "butler.sqlite3")
    try:
        start = yesterday.replace(hour=0)
        assert len(store.messages(-100123, start, start + timedelta(days=1))) == 1
    finally:
        store.close()
