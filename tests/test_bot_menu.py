from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tg_butler.bot_menu import TelegramMenu
from tg_butler.menu import save_settings


@pytest.fixture
def menu(tmp_path: Path):
    config = tmp_path / "config.yaml"
    save_settings(
        config,
        {
            "timezone": "Asia/Shanghai",
            "groups": [
                {"id": -100123, "name": "测试群", "watch_users": [7]},
            ],
            "checkins": [],
        },
    )
    item = TelegramMenu("token", 456, config, on_changed=AsyncMock())
    item.send = AsyncMock()
    yield item


@pytest.mark.asyncio
async def test_menu_only_accepts_admin_chat_and_user():
    update = {"message": {"chat": {"id": 456}, "from": {"id": 456}}}
    assert TelegramMenu._is_admin(update, 456)
    update["message"]["from"]["id"] = 789
    assert not TelegramMenu._is_admin(update, 456)


@pytest.mark.asyncio
async def test_menu_changes_group_filter_and_persists(menu):
    await menu.handle_text("/group -100123 notify_all on")
    assert menu._read()["groups"][0]["notify_all"] is True
    await menu.handle_text("/group -100123 watch_users 8, 9")
    assert menu._read()["groups"][0]["watch_users"] == [8, 9]
    assert menu.on_changed.await_count == 2


@pytest.mark.asyncio
async def test_menu_changes_summary_time_with_button_flow(menu):
    await menu.handle_text("⚙️ 配置参数")
    await menu.handle_text("🕐 日报时间")
    await menu.handle_text("08:30")
    assert menu._read()["summary"]["at"] == "08:30"
    assert menu.pending is None


@pytest.mark.asyncio
async def test_menu_rejects_invalid_parameter_without_writing(menu):
    await menu.handle_text("⚙️ 配置参数")
    await menu.handle_text("🗑 保留天数")
    with pytest.raises(ValueError, match="范围"):
        await menu.handle_text("0")
    assert menu._read().get("retention_days", 30) == 30
