import json
from unittest.mock import AsyncMock, Mock

import pytest

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
    from dotenv import dotenv_values

    assert dotenv_values(config.parent / ".env")["TG_BOT_TOKEN"] == "private-token"
    assert (
        client.post("/api/credentials/ai", json={"clear_key": True}, headers=headers).status_code
        == 200
    )
    assert dotenv_values(config.parent / ".env")["SUMMARY_API_KEY"] == ""


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
    connection.assert_awaited_once_with(
        payload["api_url"], payload["model"], payload["api_key"]
    )


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
