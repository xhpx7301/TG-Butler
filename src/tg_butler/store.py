from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
                sender_id INTEGER, sender_name TEXT NOT NULL,
                text TEXT NOT NULL, sent_at REAL NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS messages_date ON messages(chat_id, sent_at);
            CREATE TABLE IF NOT EXISTS runs (
                kind TEXT NOT NULL, name TEXT NOT NULL, day TEXT NOT NULL,
                status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (kind, name, day)
            );
        """)

    def add_message(
        self,
        chat_id: int,
        message_id: int,
        sender_id: int | None,
        sender_name: str,
        text: str,
        sent_at: datetime,
    ) -> bool:
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, message_id, sender_id, sender_name, text, sent_at.timestamp()),
            )
        return cursor.rowcount == 1

    def messages(self, chat_id: int, start: datetime, end: datetime) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM messages WHERE chat_id=? AND sent_at>=? AND sent_at<? "
                "ORDER BY sent_at, message_id",
                (chat_id, start.timestamp(), end.timestamp()),
            )
        ]

    def claim(self, kind: str, name: str, day: str) -> bool:
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO runs (kind, name, day, status) VALUES (?, ?, ?, 'claimed')",
                (kind, name, day),
            )
        return cursor.rowcount == 1

    def finish(self, kind: str, name: str, day: str, status: str, detail: str = "") -> None:
        with self.db:
            self.db.execute(
                "UPDATE runs SET status=?, detail=? WHERE kind=? AND name=? AND day=?",
                (status, detail, kind, name, day),
            )

    def runs(self) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute("SELECT * FROM runs ORDER BY day DESC, kind, name LIMIT 100")
        ]

    def prune(self, before: datetime) -> None:
        with self.db:
            self.db.execute("DELETE FROM messages WHERE sent_at < ?", (before.timestamp(),))
            self.db.execute("DELETE FROM runs WHERE day < ?", (before.date().isoformat(),))

    def close(self) -> None:
        self.db.close()
