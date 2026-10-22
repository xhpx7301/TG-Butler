import json
from unittest.mock import AsyncMock, Mock

import pytest
from dotenv import dotenv_values

from tg_butler.admin import AdminStore
from tg_butler.menu import save_settings
from tg_butler.web import create_app


@pytest.fixture
def web(tmp_path):
    config = tmp_path / "config.yaml"
    save_settings(config, {"groups": [], "checkins": [], "data_dir": "data"})
    (tmp_path / ".env").write_text(
        "TG_API_ID=123\nTG_API_HASH=private-hash\n"
        "TG_BOT_TOKEN=private-token\nTG_NOTIFY_CHAT_ID=456\n"
        "SUMMARY_API_KEY=private-key\n",
        encoding="utf-8",
    )
    control = Mock(return_value="inactive")
    app = create_app(config, control=control)
    app.config["TESTING"] = True
    admin = app.extensions["admin_store"]
    admin.set("admin", "correct-password-123")
    return app, config, control


def authenticate(client):
    info = client.get("/api/session").get_json()
    response = client.post(
        "/api/login",
        json={"username": "admin", "password": "correct-password-123"},
        headers={"X-CSRF-Token": info["csrf"]},
    )
    assert response.status_code == 200
    return {"X-CSRF-Token": response.get_json()["csrf"]}


def test_all_private_data_requires_admin_and_writes_require_csrf(web):
    app, _, control = web
    client = app.test_client()
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/service/start", json={}).status_code == 403
    headers = authenticate(client)
    assert client.post("/api/service/start", json={}).status_code == 403
    assert client.post("/api/service/start", json={}, headers=headers).status_code == 200
    control.assert_called_once_with("start")


def test_update_requires_authentication_and_uses_fixed_control_action(web):
    app, _, control = web
    client = app.test_client()
    assert client.post("/api/update", json={}).status_code == 403
    headers = authenticate(client)
    assert client.post("/api/update", json={}, headers=headers).status_code == 200
    control.assert_called_once_with("update")


def test_docker_mode_uses_container_controls_and_host_update_message(web, monkeypatch):
    app, _, control = web
    monkeypatch.setenv("TG_BUTLER_CONTROL_MODE", "supervisor")
    client = app.test_client()
    page = client.get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'data-docker="true"' in html
    assert 'id="update-project"' in html

    headers = authenticate(client)
    response = client.post("/api/update", json={}, headers=headers)
    assert response.status_code == 200
    assert "docker compose up -d --build" in response.json["message"]
    control.assert_not_called()


def test_telegram_identify_route_is_available(web):
    app, _, _ = web
    client = app.test_client()
    headers = authenticate(client)
    gateway = app.extensions["telegram_gateway"]
    gateway.execute = Mock(return_value={"items": [{"id": 42, "name": "Example"}]})

    response = client.post(
        "/api/telegram/identify",
        json={"kind": "user", "ids": ["42"]},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json == {"items": [{"id": 42, "name": "Example"}]}
    assert gateway.execute.call_args.args[:2] == ("identify", {"kind": "user", "ids": ["42"]})


def test_backup_api_create_download_schedule_and_import(web):
    import io

    from tg_butler.backup import create_backup

    app, config, _ = web
    client = app.test_client()
    headers = authenticate(client)
    response = client.post("/api/backups/create", json={}, headers=headers)
    assert response.status_code == 200
    name = response.json["name"]
    download = client.get(f"/api/backups/{name}")
    assert download.status_code == 200
    assert download.headers["Content-Type"] == "application/gzip"

    response = client.post(
        "/api/backups/schedule",
        json={"enabled": True, "at": "04:25"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json["schedule"] == {"enabled": True, "at": "04:25"}
    assert response.json["next"]
    assert app.extensions["backup_scheduler"].get_job("daily-backup") is not None
    response = client.get("/api/backups", headers=headers)
    assert response.json["schedule"] == {"enabled": True, "at": "04:25"}

    archive = create_backup(config.parent, config)
    config.write_text("data_dir: data\ngroups: []\ncheckins: []\n", encoding="utf-8")
    before = (config.parent / "data" / "web.sqlite3").read_bytes()
    response = client.post(
        "/api/backups/import",
        data={"file": (io.BytesIO(archive.read_bytes()), archive.name)},
        headers=headers,
        content_type="multipart/form-data",
    )
    assert response.status_code == 200, response.get_json()
    assert "groups" in config.read_text(encoding="utf-8")
    assert (config.parent / "data" / "web.sqlite3").read_bytes() == before
    assert app.extensions["backup_scheduler"].get_job("daily-backup") is not None


def test_secret_values_not_returned_to_browser(web):
    app, _, _ = web
    client = app.test_client()
    authenticate(client)
    response = client.get("/api/state")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for secret in ("private-hash", "private-token", "private-key"):
        assert secret not in body
    assert response.json["configured"]["TG_API_HASH"] is True
    assert response.json["accounts"] == [{"id": "default", "authorized": False}]


def test_stale_config_and_invalid_input_never_overwrite(web):
    app, config, _ = web
    client = app.test_client()
    headers = authenticate(client)
    state = client.get("/api/state").json
    changed = state["config"] | {"retention_days": 7}
    assert (
        client.post(
            "/api/config", json={"revision": state["revision"], "config": changed}, headers=headers
        ).status_code
        == 200
    )
    before = config.read_bytes()
    assert (
        client.post(
            "/api/config", json={"revision": state["revision"], "config": changed}, headers=headers
        ).status_code
        == 409
    )
    state = client.get("/api/state").json
    changed["retention_days"] = -1
    assert (
        client.post(
            "/api/config", json={"revision": state["revision"], "config": changed}, headers=headers
        ).status_code
        == 400
    )
    assert config.read_bytes() == before
    changed["data_dir"] = "/tmp/elsewhere"
    assert (
        client.post(
            "/api/config", json={"revision": state["revision"], "config": changed}, headers=headers
        ).status_code
        == 400
    )


def test_saving_config_restarts_only_an_active_task_service(web):
    app, _, control = web
    control.side_effect = lambda action: "active" if action == "status" else "restarted"
    client = app.test_client()
    headers = authenticate(client)
    state = client.get("/api/state").json
    changed = state["config"] | {"retention_days": 7}

    response = client.post(
        "/api/config", json={"revision": state["revision"], "config": changed}, headers=headers
    )

    assert response.status_code == 200
    assert "自动重启" in response.json["message"]
    assert [call.args[0] for call in control.call_args_list] == [
        "status",
        "status",
        "restart",
    ]


def test_saving_checkin_with_optional_response_fields(web):
    app, _, _ = web
    client = app.test_client()
    headers = authenticate(client)
    state = client.get("/api/state").json
    changed = state["config"] | {
        "checkins": [
            {
                "name": "daily",
                "chat_id": -100123,
                "at": "09:00",
                "command": "/checkin",
                "responder_id": None,
                "success_pattern": "签到成功|已签到",
                "failure_pattern": "签到失败",
            }
        ]
    }

    response = client.post(
        "/api/config", json={"revision": state["revision"], "config": changed}, headers=headers
    )

    assert response.status_code == 200, response.get_json()
    saved = client.get("/api/state").json["config"]["checkins"][0]
    assert saved["responder_id"] is None
    assert saved["success_pattern"] == "签到成功|已签到"
    assert saved["failure_pattern"] == "签到失败"


def test_admin_password_change_and_bu_reset_revoke_all_sessions(web):
    app, config, _ = web
    first, other = app.test_client(), app.test_client()
    headers = authenticate(first)
    authenticate(other)
    data = {"current_password": "wrong", "username": "newadmin", "password": "new-password-1234"}
    assert first.post("/api/admin", json=data, headers=headers).status_code == 403
    data["current_password"] = "correct-password-123"
    assert first.post("/api/admin", json=data, headers=headers).status_code == 200
    assert first.get("/api/state").status_code == 401
    assert other.get("/api/state").status_code == 401
    store = AdminStore(config.parent)
    assert store.verify("admin", "correct-password-123") is None
    assert store.verify("newadmin", "new-password-1234")
    store.set("admin", "correct-password-123")
    authenticate(other)
    store.set("admin", "menu-reset-password-123")
    assert other.get("/api/state").status_code == 401


def test_login_rate_limit_is_persisted(web):
    app, config, _ = web
    client = app.test_client()
    token = client.get("/api/session").json["csrf"]
    for _ in range(5):
        assert (
            client.post(
                "/api/login",
                json={"username": "admin", "password": "wrong"},
                headers={"X-CSRF-Token": token},
            ).status_code
            == 401
        )
    assert client.post("/api/login", json={}, headers={"X-CSRF-Token": token}).status_code == 429
    assert not AdminStore(config.parent).allow_attempt("127.0.0.1")


def test_web_credentials_preserve_secrets_when_blank(web):
    app, config, _ = web
    client = app.test_client()
    headers = authenticate(client)
    response = client.post(
        "/api/credentials/notification",
        json={"TG_BOT_TOKEN": "", "TG_NOTIFY_CHAT_ID": "789"},
        headers=headers,
    )
    assert response.status_code == 200
    assert dotenv_values(config.parent / ".env")["TG_BOT_TOKEN"] == "private-token"
    assert (
        client.post("/api/credentials/ai", json={"clear_key": True}, headers=headers).status_code
        == 200
    )
    assert dotenv_values(config.parent / ".env")["SUMMARY_API_KEY"] == ""


def test_split_notification_credentials_are_saved_and_role_tests_use_them(web, monkeypatch):
    app, config, _ = web
    client = app.test_client()
    headers = authenticate(client)
    response = client.post(
        "/api/credentials/monitor",
        json={"TG_MONITOR_BOT_TOKEN": "monitor-token", "TG_MONITOR_NOTIFY_CHAT_ID": "101"},
        headers=headers,
    )
    assert response.status_code == 200
    response = client.post(
        "/api/credentials/report",
        json={"TG_REPORT_BOT_TOKEN": "report-token", "TG_REPORT_NOTIFY_CHAT_ID": "102"},
        headers=headers,
    )
    assert response.status_code == 200
    saved = dotenv_values(config.parent / ".env")
    assert saved["TG_MONITOR_BOT_TOKEN"] == "monitor-token"
    assert saved["TG_REPORT_NOTIFY_CHAT_ID"] == "102"

    class FakeNotifier:
        async def send(self, text):
            assert "测试成功" in text

        async def close(self):
            pass

    factory = Mock(return_value=FakeNotifier())
    monkeypatch.setattr("tg_butler.app.make_notifier", factory)
    response = client.post("/api/notify-test", json={"role": "monitor"}, headers=headers)
    assert response.status_code == 200
    factory.assert_called_once()
    assert factory.call_args.args[1].notify_chat_id == 101


def test_group_test_sends_monitor_and_report_templates_without_saving_changes(web, monkeypatch):
    app, config, _ = web
    client = app.test_client()
    headers = authenticate(client)
    save_settings(
        config,
        {
            "groups": [{"id": -100123456, "name": "测试群", "watch_users": [7]}],
            "checkins": [],
            "data_dir": "data",
        },
    )
    messages = []

    class FakeNotifier:
        async def send(self, text, parse_mode=None):
            messages.append((self.chat_id, text, parse_mode))

        async def close(self):
            pass

    def factory(settings, credentials):
        notifier = FakeNotifier()
        notifier.chat_id = credentials.notify_chat_id
        return notifier

    monkeypatch.setattr("tg_butler.app.make_notifier", factory)
    response = client.post(
        "/api/group-test",
        json={"id": -100123456, "name": "临时测试名称", "watch_users": [8]},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json["message"] == "监控通知与筛选提醒模板测试已发送。"
    assert len(messages) == 2
    assert {message[0] for message in messages} == {456}
    assert all(message[2] == "HTML" for message in messages)
    assert "临时测试名称" in messages[0][1]
    assert "监控预警：目标用户已发言" in messages[0][1]
    assert "筛选提醒：目标用户消息命中条件" in messages[1][1]
    assert "测试群" in config.read_text(encoding="utf-8")


def test_ai_models_and_connection_use_current_form_values(web, monkeypatch):
    app, _, _ = web
    client = app.test_client()
    headers = authenticate(client)
    models = AsyncMock(return_value=["gpt-5.5", "deepseek-chat"])
    connection = AsyncMock(return_value="OK")
    monkeypatch.setattr("tg_butler.web.list_models", models)
    monkeypatch.setattr("tg_butler.web.check_connection", connection)
    payload = {
        "api_url": "https://skybridge-api.com/v1/chat/completions",
        "model": "gpt-5.5",
        "api_key": "temporary-key",
    }
    response = client.post("/api/ai/models", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json == {"models": ["gpt-5.5", "deepseek-chat"]}
    models.assert_awaited_once_with(payload["api_url"], payload["api_key"])
    response = client.post("/api/ai/test", json=payload, headers=headers)
    assert response.status_code == 200
    assert "OK" in response.json["message"]
    connection.assert_awaited_once_with(payload["api_url"], payload["model"], payload["api_key"])


def test_ai_models_can_use_saved_key_and_reject_missing_model(web, monkeypatch):
    app, _, _ = web
    client = app.test_client()
    headers = authenticate(client)
    models = AsyncMock(return_value=[])
    monkeypatch.setattr("tg_butler.web.list_models", models)
    response = client.post(
        "/api/ai/models",
        json={"api_url": "https://example.test/v1/chat/completions"},
        headers=headers,
    )
    assert response.status_code == 200
    models.assert_awaited_once_with("https://example.test/v1/chat/completions", "private-key")
    response = client.post(
        "/api/ai/test",
        json={"api_url": "https://example.test/v1/chat/completions"},
        headers=headers,
    )
    assert response.status_code == 400
    assert "模型名称" in response.json["error"]


def test_summary_test_sends_preview_without_recording_run(web, monkeypatch):
    app, config, _ = web
    client = app.test_client()
    headers = authenticate(client)
    save_settings(config, {"groups": [{"id": 1}], "checkins": [], "data_dir": "data"})

    class FakeNotifier:
        async def close(self):
            pass

    monkeypatch.setattr("tg_butler.app.make_notifier", lambda settings, credentials: FakeNotifier())
    send = AsyncMock(return_value=1)
    monkeypatch.setattr("tg_butler.web.send_test_daily", send)
    response = client.post("/api/summary-test", json={}, headers=headers)
    assert response.status_code == 200
    assert "测试日报已发送" in response.json["message"]
    send.assert_awaited_once()


def test_checkin_test_requires_login_and_runs_named_saved_task(web, monkeypatch):
    app, _, _ = web
    client = app.test_client()
    assert client.post("/api/checkin-test", json={"name": "job"}).status_code == 403
    headers = authenticate(client)
    execute = Mock(return_value={"message": "测试签到已确认成功"})
    monkeypatch.setattr("tg_butler.web.TelegramGateway.execute", execute)
    response = client.post("/api/checkin-test", json={"name": "job"}, headers=headers)
    assert response.status_code == 200
    assert response.json["message"] == "测试签到已确认成功"
    execute.assert_called_once()
    assert execute.call_args.args[:2] == ("checkin-test", {"name": "job"})
    assert execute.call_args.args[2]


def test_password_validation_and_hash_storage(tmp_path):
    store = AdminStore(tmp_path)
    assert store.get() is None
    assert store.verify("admin", "admin") is None
    with pytest.raises(ValueError):
        store.set("admin", "short")
    store.set("admin", "long-password-1234")
    assert "long-password-1234" not in store.path.read_bytes().decode(errors="ignore")
    assert store.get()["password_hash"].startswith("scrypt:")


def test_default_admin_login_without_telegram_configuration(tmp_path):
    # A fresh dashboard must work before any Telegram credentials/configuration exist.
    app = create_app(tmp_path / "config.yaml")
    client = app.test_client()
    info = client.get("/api/session").json
    assert info["initialized"] is True
    response = client.post(
        "/api/login",
        json={"username": "pingzi", "password": "pingzi"},
        headers={"X-CSRF-Token": info["csrf"]},
    )
    assert response.status_code == 200
    assert response.json["username"] == "pingzi"
    store = app.extensions["admin_store"]
    assert store.get()["password_hash"].startswith("scrypt:")
    assert b"pingzi" not in store.get()["password_hash"].encode()


def test_default_initialization_preserves_custom_admin_and_sessions(tmp_path):
    store = AdminStore(tmp_path)
    assert store.initialize_default() is True
    initial = store.get()
    assert store.initialize_default() is False
    assert store.get() == initial
    store.set("customadmin", "custom-password-1234")
    before = store.get()
    signing_secret = store.secret()
    create_app(tmp_path / "config.yaml")
    assert store.get() == before
    assert store.secret() == signing_secret
    assert store.verify("customadmin", "custom-password-1234")
    assert store.verify("pingzi", "pingzi") is None


def test_installer_admin_initialization_is_noninteractive_and_preserves_account(
    tmp_path, monkeypatch
):
    from tg_butler.admin import main

    monkeypatch.setattr("sys.argv", ["tg_butler.admin", "initialize", "--root", str(tmp_path)])
    main()
    store = AdminStore(tmp_path)
    assert store.verify("pingzi", "pingzi")
    store.set("customadmin", "custom-password-1234")
    before = store.get()
    main()
    assert store.get() == before


def test_security_headers_assets_and_cookie_settings(web):
    app, config, _ = web
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200
    cookie = client.get("/api/session").headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    (config.parent / "data/web.json").write_text(json.dumps({"secure_cookie": True}))
    secure = create_app(config).test_client().get("/api/session")
    assert "Secure" in secure.headers["Set-Cookie"]
