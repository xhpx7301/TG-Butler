"""Shared local administrator store used by both bu and the web application."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


class AdminStore:
    def __init__(self, root: Path):
        self.path = root / "data" / "web.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS admin (
                    id INTEGER PRIMARY KEY CHECK (id=1), username TEXT NOT NULL,
                    password_hash TEXT NOT NULL, revision TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    bucket TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM admin WHERE id=1").fetchone()
        return dict(row) if row else None

    def secret(self) -> str:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO secrets VALUES ('session', ?)", (secrets.token_hex(32),)
            )
            return db.execute("SELECT value FROM secrets WHERE name='session'").fetchone()[0]

    def set(self, username: str, password: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,64}", username):
            raise ValueError("管理员用户名需为 3–64 位字母、数字或 _ . @ -")
        if len(password) < 12 or len(password) > 256 or password.isspace():
            raise ValueError("管理员密码需为 12–256 个字符，不能全为空格")
        encoded = generate_password_hash(password, method="scrypt")
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO admin VALUES (1, ?, ?, ?)",
                (username, encoded, secrets.token_hex(24)),
            )

    def verify(self, username: str, password: str) -> dict | None:
        admin = self.get()
        # A fixed dummy hash equalizes the expensive work when no administrator exists.
        encoded = admin["password_hash"] if admin else self.dummy_hash()
        valid = check_password_hash(encoded, password[:256]) and len(password) <= 256
        if (
            admin
            and secrets.compare_digest(admin["username"].encode(), username.encode())
            and valid
        ):
            return admin
        return None

    def dummy_hash(self) -> str:
        # Initialized once per store instance, no password is ever accepted for this hash.
        if not hasattr(self, "_dummy"):
            self._dummy = generate_password_hash(secrets.token_hex(32), method="scrypt")
        return self._dummy

    def allow_attempt(self, address: str) -> bool:
        """Persisted fixed windows: 5 attempts/IP and 30 globally per 15 minutes."""
        now = time.time()
        address = hashlib.sha256(address.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE expires <= ?", (now,))
            for bucket, limit in ((address, 5), ("global", 30)):
                row = db.execute("SELECT count FROM attempts WHERE bucket=?", (bucket,)).fetchone()
                if row and row[0] >= limit:
                    return False
            for bucket in (address, "global"):
                db.execute(
                    "INSERT INTO attempts VALUES (?, 1, ?) ON CONFLICT(bucket) "
                    "DO UPDATE SET count=count+1",
                    (bucket, now + 900),
                )
        return True


def web_options(root: Path) -> dict:
    path = root / "data" / "web.json"
    defaults = {"host": "127.0.0.1", "port": 15678, "secure_cookie": False}
    if path.exists():
        defaults.update(json.loads(path.read_text(encoding="utf-8")))
    # Values are used as arguments, never shell command text.
    if defaults["host"] not in {"127.0.0.1", "0.0.0.0", "::1", "::"}:
        raise ValueError("网页监听地址须为 127.0.0.1、0.0.0.0、::1 或 ::")
    if not 1 <= int(defaults["port"]) <= 65535:
        raise ValueError("网页端口须为 1–65535")
    if not isinstance(defaults["secure_cookie"], bool):
        raise ValueError("secure_cookie 必须为布尔值")
    return defaults


def main():
    import argparse
    import getpass

    from tg_butler.menu import ask, ask_bool, atomic_write

    parser = argparse.ArgumentParser(description="网页管理员与监听设置")
    parser.add_argument("action", choices=["account", "settings"])
    parser.add_argument("--root", type=Path, default=Path("/opt/tg-butler"))
    args = parser.parse_args()
    os.umask(0o077)
    try:
        store = AdminStore(args.root)
        if args.action == "account":
            current = store.get()
            print("设置网页唯一管理员；保存后全部网页登录会话失效。忘记密码可在此重置。")
            username = ask("管理员用户名", current["username"] if current else "admin")
            password = getpass.getpass("新管理员密码（12–256 字符）：")
            confirm = getpass.getpass("再次输入新密码：")
            if password != confirm:
                raise ValueError("两次密码不一致，未修改账号")
            store.set(username, password)
            print("管理员已保存，可在网页使用新凭据登录。")
        else:
            options = web_options(args.root)
            options["host"] = ask("监听地址（推荐 127.0.0.1）", options["host"])
            if options["host"] not in {"127.0.0.1", "0.0.0.0", "::1", "::"}:
                raise ValueError("无效监听地址")
            options["port"] = int(ask("网页端口", options["port"]))
            if not 1 <= options["port"] <= 65535:
                raise ValueError("端口必须为 1–65535")
            options["secure_cookie"] = ask_bool(
                "仅允许 HTTPS Cookie（HTTPS 代理选 y，SSH 隧道选 n）", options["secure_cookie"]
            )
            atomic_write(args.root / "data/web.json", json.dumps(options, indent=2))
            print("网页监听设置已保存，重启网页服务后生效。")
    except (ValueError, KeyboardInterrupt, EOFError) as exc:
        print(str(exc) or "已取消")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
