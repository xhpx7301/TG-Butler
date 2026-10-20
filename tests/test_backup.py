import sqlite3
import tarfile
from contextlib import closing

import pytest

from tg_butler.backup import create_backup, list_backups, restore_backup, validate_backup
from tg_butler.menu import save_settings


def setup_install(root):
    config = root / "config.yaml"
    save_settings(
        config,
        {"data_dir": "data", "groups": [{"id": -100123, "name": "原群"}], "checkins": []},
    )
    (root / ".env").write_text("TG_BOT_TOKEN=secret-token\n", encoding="utf-8")
    with closing(sqlite3.connect(root / "data" / "butler.sqlite3")) as db:
        db.execute("CREATE TABLE messages (text TEXT)")
        db.execute("INSERT INTO messages VALUES ('保存的消息')")
        db.commit()
    with closing(sqlite3.connect(root / "data" / "user.session")) as db:
        db.execute("CREATE TABLE sessions (value TEXT)")
        db.execute("INSERT INTO sessions VALUES ('telegram-session')")
        db.commit()
    return config


def test_create_and_restore_backup_preserves_runtime_but_not_admin_settings(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    config = setup_install(root)
    (root / "data" / "backup-settings.json").write_text(
        '{"enabled":true,"at":"04:15"}', encoding="utf-8"
    )
    archive = create_backup(root, config)
    validate_backup(archive)
    with tarfile.open(archive, "r:gz") as bundle:
        names = set(bundle.getnames())
    assert names >= {
        "manifest.json",
        "config.yaml",
        ".env",
        "runtime/butler.sqlite3",
        "runtime/user.session",
        "runtime/backup-settings.json",
    }
    assert "data/web.sqlite3" not in names

    config.write_text("timezone: UTC\ndata_dir: data\ngroups: []\ncheckins: []\n", encoding="utf-8")
    (root / ".env").write_text("TG_BOT_TOKEN=changed\n", encoding="utf-8")
    (root / "data" / "butler.sqlite3").unlink()
    (root / "data" / "user.session").unlink()
    restore_backup(root, config, archive)

    assert "原群" in config.read_text(encoding="utf-8")
    assert "secret-token" in (root / ".env").read_text(encoding="utf-8")
    with closing(sqlite3.connect(root / "data" / "butler.sqlite3")) as db:
        assert db.execute("SELECT text FROM messages").fetchone()[0] == "保存的消息"
    with closing(sqlite3.connect(root / "data" / "user.session")) as db:
        assert db.execute("SELECT value FROM sessions").fetchone()[0] == "telegram-session"
    assert '"at":"04:15"' in (root / "data" / "backup-settings.json").read_text()
    assert len(list_backups(root)) == 1
    assert (root / "data" / ".restore-staging").exists() is False


def test_restore_keeps_current_data_directory_and_rejects_path_traversal(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    config = setup_install(root)
    archive = create_backup(root, config)
    config.write_text("data_dir: elsewhere\ngroups: []\ncheckins: []\n", encoding="utf-8")
    restore_backup(root, config, archive)
    assert "data_dir: elsewhere" in config.read_text(encoding="utf-8")
    assert "原群" in config.read_text(encoding="utf-8")

    bad = root / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as bundle:
        source = root / ".env"
        bundle.add(source, arcname="../.env")
    with pytest.raises(ValueError, match="路径"):
        validate_backup(bad)


def test_backup_restores_named_account_sessions(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    config = setup_install(root)
    save_settings(config, {"data_dir": "data", "accounts": [{"id": "default"}, {"id": "work"}]})
    account_session = root / "data" / "accounts" / "work.session"
    account_session.parent.mkdir()
    with closing(sqlite3.connect(account_session)) as db:
        db.execute("CREATE TABLE sessions (value TEXT)")
        db.execute("INSERT INTO sessions VALUES ('work-session')")
        db.commit()
    archive = create_backup(root, config)
    account_session.unlink()
    restore_backup(root, config, archive)
    with closing(sqlite3.connect(account_session)) as db:
        assert db.execute("SELECT value FROM sessions").fetchone()[0] == "work-session"
