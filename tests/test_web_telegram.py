import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telethon.errors import SessionPasswordNeededError

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
