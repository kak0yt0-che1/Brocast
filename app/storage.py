"""SQLite-backed user store. Keeps only what a daily send needs."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_user_id   INTEGER PRIMARY KEY,
    chat_id            INTEGER NOT NULL,
    timezone           TEXT    NOT NULL,
    notification_time  TEXT,                 -- 'HH:MM' local, or NULL when off
    preferred_city     TEXT    NOT NULL,
    language           TEXT    NOT NULL DEFAULT 'en',
    last_sent_date     TEXT,                 -- 'YYYY-MM-DD' in the user's timezone
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);
"""


@dataclass(frozen=True)
class User:
    telegram_user_id: int
    chat_id: int
    timezone: str
    notification_time: str | None
    preferred_city: str
    language: str
    last_sent_date: str | None


def _row_to_user(row: aiosqlite.Row) -> User:
    return User(
        telegram_user_id=row["telegram_user_id"],
        chat_id=row["chat_id"],
        timezone=row["timezone"],
        notification_time=row["notification_time"],
        preferred_city=row["preferred_city"],
        language=row["language"],
        last_sent_date=row["last_sent_date"],
    )


class Storage:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("connect() must run before the store is used")
        return self._db

    @staticmethod
    def _now() -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    async def ensure_user(
        self, telegram_user_id: int, chat_id: int, default_timezone: str, default_city: str
    ) -> None:
        """Insert on first contact; on later /start just refresh chat_id."""
        now = self._now()
        await self._conn.execute(
            """
            INSERT INTO users (telegram_user_id, chat_id, timezone, preferred_city,
                               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                updated_at = excluded.updated_at
            """,
            (telegram_user_id, chat_id, default_timezone, default_city, now, now),
        )
        await self._conn.commit()

    async def set_notification_time(self, telegram_user_id: int, value: str | None) -> None:
        """Set (or clear with None) the daily notification time.

        Setting a time also reschedules today, so the user gets exactly one send
        at the chosen time:
        - time still ahead today -> clear last_sent_date so the summary fires
          today at that time (even if one was already sent earlier today);
        - time already passed today -> mark today as sent so we don't fire an
          immediate catch-up; the first send then lands tomorrow.

        Genuine catch-up after downtime is unaffected: there last_sent_date stays
        in the past, so a missed slot is still delivered when the bot returns.
        """
        user = await self.get_user(telegram_user_id) if value is not None else None
        if value is not None and user is not None:
            now = dt.datetime.now(ZoneInfo(user.timezone))
            last_sent = now.strftime("%Y-%m-%d") if now.strftime("%H:%M") >= value else None
            await self._conn.execute(
                "UPDATE users SET notification_time = ?, last_sent_date = ?, updated_at = ? "
                "WHERE telegram_user_id = ?",
                (value, last_sent, self._now(), telegram_user_id),
            )
        else:
            await self._conn.execute(
                "UPDATE users SET notification_time = ?, updated_at = ? WHERE telegram_user_id = ?",
                (value, self._now(), telegram_user_id),
            )
        await self._conn.commit()

    async def set_timezone(self, telegram_user_id: int, timezone: str) -> None:
        await self._conn.execute(
            "UPDATE users SET timezone = ?, updated_at = ? WHERE telegram_user_id = ?",
            (timezone, self._now(), telegram_user_id),
        )
        await self._conn.commit()

    async def mark_sent(self, telegram_user_id: int, date_str: str) -> None:
        await self._conn.execute(
            "UPDATE users SET last_sent_date = ?, updated_at = ? WHERE telegram_user_id = ?",
            (date_str, self._now(), telegram_user_id),
        )
        await self._conn.commit()

    async def get_user(self, telegram_user_id: int) -> User | None:
        async with self._conn.execute(
            "SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_user(row) if row else None

    async def get_users_with_notifications(self) -> list[User]:
        async with self._conn.execute(
            "SELECT * FROM users WHERE notification_time IS NOT NULL"
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_user(row) for row in rows]
