import os
import stat
import subprocess
from unittest.mock import Mock

import pytest
import yaml
from dotenv import dotenv_values
from pydantic import ValidationError

from tg_butler.menu import Menu, ask, save_credentials, save_settings


@pytest.fixture
def menu(tmp_path):
    config = tmp_path / "config.yaml"
    save_settings(config, {"data_dir": "data", "groups": [], "checkins": []})
    manager = Menu(config)
    manager.active = Mock(return_value=False)
    manager.command = Mock()
    return manager


def answer(monkeypatch, values):
    replies = iter(values)
    monkeypatch.setattr("builtins.input", lambda _: next(replies))


def test_save_preserves_relative_data_path_and_private_permissions(menu):
    original = menu.config.stat()
    data = menu.read()
    data["retention_days"] = 7
    save_settings(menu.config, data)
    assert yaml.safe_load(menu.config.read_text(encoding="utf-8"))["data_dir"] == "data"
    assert menu.config.stat().st_uid == original.st_uid
    if os.name != "nt":
        assert stat.S_IMODE(menu.config.stat().st_mode) == 0o600
    assert list(menu.root.glob(".config.yaml.*")) == []


def test_invalid_settings_do_not_replace_existing_file(menu):
    before = menu.config.read_bytes()
    data = menu.read()
    data["groups"] = [{"id": 1}, {"id": 1}]
    with pytest.raises(ValidationError):
        save_settings(menu.config, data)
    assert menu.config.read_bytes() == before


def test_credentials_preserve_comments_and_round_trip_special_characters(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# my comment\nEXTRA_SETTING=keep\n", encoding="utf-8")
    values = {
        "TG_API_ID": "123",
        "TG_API_HASH": "my'hash\\value",
        "TG_BOT_TOKEN": '123:secret$()"',
        "TG_NOTIFY_CHAT_ID": "456",
        "SUMMARY_API_KEY": "",
    }
    save_credentials(path, values)
    assert dict(dotenv_values(path)) == {"EXTRA_SETTING": "keep", **values}
    assert "# my comment" in path.read_text(encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValidationError):
        save_credentials(path, values | {"TG_API_ID": "bad"})
    assert path.read_bytes() == before


def test_secret_prompt_never_displays_existing_token(monkeypatch):
    prompts = []

    def password(prompt):
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("getpass.getpass", password)
    assert ask("Token", "private-token", secret=True) == "private-token"
    assert "private-token" not in prompts[0]


def test_add_edit_delete_group_and_multiple_watch_users(menu, monkeypatch):
    answer(monkeypatch, ["a", "-100123", "中文群", "123，456", "n", "y"])
    menu.groups()
    group = menu.read()["groups"][0]
    assert group["watch_users"] == [123, 456]
    assert group["name"] == "中文群"
    answer(monkeypatch, ["1", "", "", "-", "y", "n"])
    menu.groups()
    assert menu.read()["groups"][0]["watch_users"] == []
    assert menu.read()["groups"][0]["notify_all"]
    answer(monkeypatch, ["d", "1", "y"])
    menu.groups()
    assert menu.read()["groups"] == []


def test_invalid_checkin_does_not_corrupt_file(menu, monkeypatch):
    before = menu.config.read_bytes()
    answer(monkeypatch, ["a", "签到", "-100123", "25:00", "/checkin", "n"])
    with pytest.raises(ValidationError):
        menu.checkins()
    assert menu.config.read_bytes() == before


def test_checkin_confirmation_settings_are_saved(menu, monkeypatch):
    answer(
        monkeypatch,
        ["a", "签到", "-100123", "09:15", "/go", "y", "789", "成功|已签到", "失败", "y", "120"],
    )
    menu.checkins()
    job = menu.read()["checkins"][0]
    assert job["responder_id"] == 789
    assert job["success_pattern"] == "成功|已签到"
    assert job["require_reply"]
    assert job["timeout_seconds"] == 120


def test_disable_ai_keeps_other_settings_and_local_digest(menu, monkeypatch):
    data = menu.read()
    data["summary"] = {"api_url": "https://example.test/v1/chat/completions", "model": "m"}
    save_settings(menu.config, data)
    answer(monkeypatch, ["y", "01:00", "n", "100", "2000"])
    menu.summary()
    assert menu.read()["summary"] == {
        "enabled": True,
        "at": "01:00",
        "api_url": None,
        "model": None,
        "max_messages": 100,
        "max_chars": 2000,
    }


def test_configuration_apply_restarts_only_running_service(menu):
    menu.apply()
    menu.command.assert_not_called()
    menu.active.return_value = True
    menu.apply()
    menu.command.assert_called_once_with(["systemctl", "restart", "tg-butler.service"])


def test_login_failure_still_restores_running_service(menu):
    menu.active.return_value = True

    def invoke(argv):
        if argv[0] == "runuser":
            raise subprocess.CalledProcessError(1, argv)

    menu.command.side_effect = invoke
    with pytest.raises(subprocess.CalledProcessError):
        menu.cli("login", session=True)
    commands = [call.args[0] for call in menu.command.call_args_list]
    assert commands[0] == ["systemctl", "stop", "tg-butler.service"]
    assert commands[-1] == ["systemctl", "start", "tg-butler.service"]


def test_nonrunning_service_not_started_after_listing_groups(menu):
    menu.cli("dialogs", session=True)
    assert len(menu.command.call_args_list) == 1
    assert menu.command.call_args.args[0][0] == "runuser"


def test_failed_update_does_not_claim_success_or_exit(menu, monkeypatch, capsys):
    answer(monkeypatch, ["y"])
    menu.command.side_effect = subprocess.CalledProcessError(1, ["bash"])
    with pytest.raises(subprocess.CalledProcessError):
        menu.update()
    assert "更新完成" not in capsys.readouterr().out


def test_menu_dispatch_and_exit(menu, monkeypatch, capsys):
    answer(monkeypatch, ["invalid", "5", "2", "0", "0"])
    menu.loop()
    output = capsys.readouterr().out
    assert "无效选项" in output
    assert "中文管理菜单" in output
    assert "保留：30 天" in output


def test_home_is_compact_and_distinguishes_web_from_monitor(menu, monkeypatch, capsys):
    menu.active.side_effect = lambda service=None: service == "tg-butler-web.service"
    answer(monkeypatch, ["0"])
    menu.loop()
    output = capsys.readouterr().out
    assert "网页后台：运行中 │ 监控任务：未运行" in output
    assert "6. 更新脚本" in output
    assert "7. 卸载并清除数据" in output
    assert "API ID / HASH" not in output
    assert "25." not in output
    menu.command.assert_not_called()


def test_web_start_from_submenu_needs_no_telegram_credentials(menu, monkeypatch):
    answer(monkeypatch, ["1", "4", "0", "0"])
    menu.cli = Mock()
    menu.loop()
    menu.command.assert_called_once_with(["bash", str(menu.root / "deploy/setup-web.sh")])
    menu.cli.assert_not_called()
    assert not (menu.root / ".env").exists()


def test_failed_monitor_start_stays_in_submenu_and_allows_return(menu, monkeypatch, capsys):
    menu.cli = Mock(side_effect=subprocess.CalledProcessError(1, ["check-config"]))
    answer(monkeypatch, ["4", "1", "0", "1", "1", "0", "0"])
    menu.loop()
    menu.cli.assert_called_once_with("check-config")
    menu.command.assert_not_called()
    output = capsys.readouterr().out
    assert "操作未完成" in output
    assert "http://127.0.0.1:15678" in output


def test_web_access_uses_saved_port(menu, capsys):
    (menu.root / "data").mkdir(exist_ok=True)
    (menu.root / "data/web.json").write_text('{"port": 23456}', encoding="utf-8")
    menu.web_access()
    output = capsys.readouterr().out
    assert "23456:127.0.0.1:23456" in output
    assert "http://127.0.0.1:23456" in output


@pytest.mark.parametrize("choice", ["6", "19"])
def test_update_exits_to_load_new_menu(menu, monkeypatch, choice):
    menu.update = Mock(return_value=True)
    answer(monkeypatch, [choice])
    menu.loop()
    menu.update.assert_called_once_with()


def test_uninstall_success_exits_menu_without_loading_deleted_files(menu, monkeypatch):
    menu.config.unlink()  # Uninstall must also work with a missing/broken configuration.
    menu.command.return_value = subprocess.CompletedProcess(["bash"], 0)
    answer(monkeypatch, ["7"])
    menu.loop()
    menu.command.assert_called_once_with(
        ["bash", str(menu.root / "deploy/uninstall.sh")], check=False
    )


def test_cancelled_uninstall_returns_to_menu(menu, monkeypatch, capsys):
    menu.command.return_value = subprocess.CompletedProcess(["bash"], 2)
    answer(monkeypatch, ["7", "0"])
    menu.loop()
    assert capsys.readouterr().out.count("中文管理菜单") == 2


def test_failed_uninstall_reports_error_and_keeps_menu(menu, monkeypatch, capsys):
    menu.command.return_value = subprocess.CompletedProcess(["bash"], 1)
    answer(monkeypatch, ["7", "0"])
    menu.loop()
    output = capsys.readouterr().out
    assert "操作未完成" in output
    assert output.count("中文管理菜单") == 2


def test_menu_overview_does_not_read_or_display_credentials(menu, capsys):
    (menu.root / ".env").write_text("TG_BOT_TOKEN=private-token", encoding="utf-8")
    menu.overview()
    assert "private-token" not in capsys.readouterr().out


def test_application_saves_without_bot_and_ignores_example_defaults(menu, monkeypatch):
    path = menu.root / ".env"
    path.write_text(
        "TG_API_ID=123456\nTG_API_HASH=replace_with_api_hash\n"
        "TG_BOT_TOKEN=replace_with_bot_token\n",
        encoding="utf-8",
    )
    prompts = []

    def ask_value(label, default="", **kwargs):
        prompts.append((label, default))
        return "123" if "ID" in label else "my-real-hash"

    monkeypatch.setattr("tg_butler.menu.ask", ask_value)
    menu.credentials()
    result = dotenv_values(path)
    assert result["TG_API_HASH"] == "my-real-hash"
    assert result["TG_BOT_TOKEN"] == "replace_with_bot_token"
    assert len(prompts) == 2
    assert all(default == "" for _, default in prompts)


def test_bot_setup_is_independent_and_preserves_application(menu, monkeypatch):
    path = menu.root / ".env"
    path.write_text("TG_API_ID=\nTG_API_HASH=\nSUMMARY_API_KEY=keep\n", encoding="utf-8")
    answer(monkeypatch, ["456"])
    monkeypatch.setattr("getpass.getpass", lambda _: "123:real-token")
    menu.notification_credentials()
    result = dotenv_values(path)
    assert result["TG_BOT_TOKEN"] == "123:real-token"
    assert result["TG_NOTIFY_CHAT_ID"] == "456"
    assert result["TG_API_HASH"] == ""
    assert result["SUMMARY_API_KEY"] == "keep"


def test_ai_key_can_save_and_clear_independently(menu, monkeypatch):
    path = menu.root / ".env"
    path.write_text("TG_BOT_TOKEN=preserve\n", encoding="utf-8")
    monkeypatch.setattr("getpass.getpass", lambda _: "ai-key")
    menu.ai_credentials()
    assert dotenv_values(path)["SUMMARY_API_KEY"] == "ai-key"
    monkeypatch.setattr("getpass.getpass", lambda _: "-")
    menu.ai_credentials()
    assert dotenv_values(path)["SUMMARY_API_KEY"] == ""
    assert dotenv_values(path)["TG_BOT_TOKEN"] == "preserve"


@pytest.mark.parametrize(
    "section,values",
    [
        ("application", {"TG_API_ID": "123456", "TG_API_HASH": "replace_with_api_hash"}),
        (
            "notification",
            {"TG_BOT_TOKEN": "replace_with_bot_token", "TG_NOTIFY_CHAT_ID": "123456789"},
        ),
    ],
)
def test_example_credentials_rejected_without_overwriting(menu, section, values):
    path = menu.root / ".env"
    path.write_text("# preserve\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        save_credentials(path, values, section=section)
    assert path.read_text(encoding="utf-8") == "# preserve\n"


@pytest.mark.parametrize(
    "key,value",
    [
        ("TG_API_HASH", "replace_with_api_hash"),
        ("TG_BOT_TOKEN", "replace_with_bot_token"),
    ],
)
def test_full_validation_rejects_both_placeholder_types(menu, key, value):
    values = {
        "TG_API_ID": "123",
        "TG_API_HASH": "hash",
        "TG_BOT_TOKEN": "token",
        "TG_NOTIFY_CHAT_ID": "456",
    }
    values[key] = value
    with pytest.raises(ValidationError):
        save_credentials(menu.root / ".env", values)


def test_menu_refuses_to_overwrite_configuration_changed_by_web(menu, monkeypatch):
    data = menu.read()
    changed = data | {"retention_days": 10}
    save_settings(menu.config, changed)
    with pytest.raises(ValueError, match="配置已被网页"):
        menu.save(data)
    assert menu.read()["retention_days"] == 10


def test_menu_admin_management_runs_as_service_account(menu):
    menu.web_admin()
    args = menu.command.call_args.args[0]
    assert args[:4] == ["runuser", "-u", "tgbutler", "--"]
    assert "tg_butler.admin" in args
    assert "account" in args
