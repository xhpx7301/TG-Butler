from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tg_butler.cli import execute


async def test_login_only_needs_application_credentials(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("groups: []\n", encoding="utf-8")
    for key in ("TG_BOT_TOKEN", "TG_NOTIFY_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TG_API_ID", "123")
    monkeypatch.setenv("TG_API_HASH", "valid-hash")
    client = AsyncMock()
    factory = Mock(return_value=client)
    monkeypatch.setattr("tg_butler.cli.make_client", factory)
    await execute(SimpleNamespace(config=path, command="login"))
    client.start.assert_awaited_once()
    client.disconnect.assert_awaited_once()
    assert factory.call_args.args[1].api_id == 123


async def test_notification_only_needs_bot_credentials(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("groups: []\n", encoding="utf-8")
    for key in ("TG_API_ID", "TG_API_HASH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TG_BOT_TOKEN", "123:real-token")
    monkeypatch.setenv("TG_NOTIFY_CHAT_ID", "456")
    notifier = AsyncMock()
    monkeypatch.setattr("tg_butler.cli.make_notifier", Mock(return_value=notifier))
    await execute(SimpleNamespace(config=path, command="notify-test"))
    notifier.send.assert_awaited_once()
    notifier.close.assert_awaited_once()
