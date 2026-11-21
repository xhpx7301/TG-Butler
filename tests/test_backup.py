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


def test_prune_backups_enforces_7_days_max_7_count_and_drops_empty_files(tmp_path):
    import os
    from datetime import datetime, timedelta

    from tg_butler.backup import BACKUP_RETENTION, BACKUP_RETENTION_DAYS, backup_dir, prune_backups

    assert BACKUP_RETENTION == 7
    assert BACKUP_RETENTION_DAYS == 7

    root = tmp_path / "install"
    folder = backup_dir(root)
    now = datetime(2026, 11, 21, 12, 0, 0).astimezone()

    def make_file(name: str, age_days: float, payload: bytes = b"valid-bytes"):
        path = folder / name
        path.write_bytes(payload)
        ts = (now - timedelta(days=age_days)).timestamp()
        os.utime(path, (ts, ts))
        return path

    # 9 recent backups within 7 days -> should keep newest 7
    for idx in range(9):
        make_file(f"tg-butler-20261121-00000{idx}.tar.gz", age_days=idx * 0.5)
    # 2 expired backups (>7 days)
    make_file("tg-butler-20261101-030000.tar.gz", age_days=20)
    make_file("tg-butler-20261110-030000.tar.gz", age_days=11)
    # 1 broken 0-byte backup (even if very recent)
    make_file("tg-butler-20261121-999999.tar.gz", age_days=0.01, payload=b"")

    removed, freed = prune_backups(root, now=now)
    remaining = list_backups(root)
    assert removed == 5
    assert freed == 4 * len(b"valid-bytes")
    assert len(remaining) == 7
    assert all(item["size"] > 0 for item in remaining)
    assert "tg-butler-20261101-030000.tar.gz" not in {item["name"] for item in remaining}


def test_prune_backups_keeps_newest_valid_even_if_older_than_7_days(tmp_path):
    import os
    from datetime import datetime, timedelta

    from tg_butler.backup import backup_dir, prune_backups

    root = tmp_path / "install"
    folder = backup_dir(root)
    now = datetime(2026, 11, 21, 12, 0, 0).astimezone()

    for name, age in (
        ("tg-butler-20261105-030000.tar.gz", 16),
        ("tg-butler-20261101-030000.tar.gz", 20),
    ):
        path = folder / name
        path.write_bytes(b"archive")
        ts = (now - timedelta(days=age)).timestamp()
        os.utime(path, (ts, ts))

    removed, _ = prune_backups(root, now=now)
    remaining = list_backups(root)
    assert removed == 1
    assert [item["name"] for item in remaining] == ["tg-butler-20261105-030000.tar.gz"]
