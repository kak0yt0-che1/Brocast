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
    evening_time       TEXT,                 -- 'HH:MM' local umbrella reminder, or NULL
    last_evening_date  TEXT,
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
    evening_time: str | None = None
    last_evening_date: str | None = None


def _row_to_user(row: aiosqlite.Row) -> User:
    return User(
        telegram_user_id=row["telegram_user_id"],
        chat_id=row["chat_id"],
        timezone=row["timezone"],
        notification_time=row["notification_time"],
        preferred_city=row["preferred_city"],
        language=row["language"],
        last_sent_date=row["last_sent_date"],
        evening_time=row["evening_time"],
        last_evening_date=row["last_evening_date"],
    )


def _initial_last_sent(timezone: str, value: str) -> str | None:
    """last_sent value that makes a newly chosen time fire once: today if still ahead,
    otherwise today is marked done so the first send is tomorrow."""
    now = dt.datetime.now(ZoneInfo(timezone))
    return now.strftime("%Y-%m-%d") if now.strftime("%H:%M") >= value else None


class Storage:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.executescript(REMINDER_SCHEMA)
        async with self._db.execute("PRAGMA table_info(users)") as cur:
            columns = {row["name"] for row in await cur.fetchall()}
        for name in ("evening_time", "last_evening_date"):
            if name not in columns:
                await self._db.execute(f"ALTER TABLE users ADD COLUMN {name} TEXT")
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

    async def set_notification_time(self, telegram_user_id: int, value: str | None) -> bool:
        """Set (or clear with None) the morning time. Returns False if the user is unknown.

        Setting a time reschedules today so the user gets exactly one send: still
        ahead today -> fire today at that time; already passed -> first send tomorrow.
        Genuine catch-up after downtime is unaffected.
        """
        return await self._set_slot(telegram_user_id, "notification_time", "last_sent_date", value)

    async def set_evening_time(self, telegram_user_id: int, value: str | None) -> bool:
        return await self._set_slot(telegram_user_id, "evening_time", "last_evening_date", value)

    async def _set_slot(self, uid: int, time_col: str, date_col: str, value: str | None) -> bool:
        user = await self.get_user(uid)
        if user is None:
            return False
        if value is None:
            cur = await self._conn.execute(
                f"UPDATE users SET {time_col} = NULL, updated_at = ? WHERE telegram_user_id = ?",
                (self._now(), uid),
            )
        else:
            cur = await self._conn.execute(
                f"UPDATE users SET {time_col} = ?, {date_col} = ?, updated_at = ? "
                "WHERE telegram_user_id = ?",
                (value, _initial_last_sent(user.timezone, value), self._now(), uid),
            )
        await self._conn.commit()
        return cur.rowcount > 0

    async def set_timezone(self, telegram_user_id: int, timezone: str) -> bool:
        """Change timezone and re-plan today's slots so nothing is doubled or lost."""
        user = await self.get_user(telegram_user_id)
        if user is None:
            return False
        morning = _initial_last_sent(timezone, user.notification_time) if user.notification_time else None
        evening = _initial_last_sent(timezone, user.evening_time) if user.evening_time else None
        await self._conn.execute(
            "UPDATE users SET timezone = ?, last_sent_date = ?, last_evening_date = ?, "
            "updated_at = ? WHERE telegram_user_id = ?",
            (timezone, morning, evening, self._now(), telegram_user_id),
        )
        await self._conn.commit()
        return True

    async def disable_user(self, telegram_user_id: int) -> None:
        """Turn off all scheduled sends (e.g. the user blocked the bot)."""
        await self._conn.execute(
            "UPDATE users SET notification_time = NULL, evening_time = NULL, updated_at = ? "
            "WHERE telegram_user_id = ?",
            (self._now(), telegram_user_id),
        )
        await self._conn.commit()

    async def mark_evening_sent(self, telegram_user_id: int, date_str: str) -> None:
        await self._conn.execute(
            "UPDATE users SET last_evening_date = ?, updated_at = ? WHERE telegram_user_id = ?",
            (date_str, self._now(), telegram_user_id),
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
            "SELECT * FROM users WHERE notification_time IS NOT NULL OR evening_time IS NOT NULL"
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_user(row) for row in rows]

    async def add_reminder(self, uid: int, time: str, days: str, only_rain: bool) -> int | None:
        user = await self.get_user(uid)
        if user is None:
            return None
        cur = await self._conn.execute(
            "INSERT INTO reminders (telegram_user_id, time, days, only_rain, last_sent_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (uid, time, days, int(only_rain), _initial_last_sent(user.timezone, time)),
        )
        await self._conn.commit()
        return cur.lastrowid


    async def list_reminders(self, uid: int) -> list[Reminder]:
        async with self._conn.execute(
            "SELECT * FROM reminders WHERE telegram_user_id = ? ORDER BY time, id", (uid,)
        ) as cur:
            return [_row_to_reminder(r) for r in await cur.fetchall()]


    async def delete_reminders(self, uid: int, reminder_id: int | None) -> int:
        if reminder_id is None:
            cur = await self._conn.execute("DELETE FROM reminders WHERE telegram_user_id = ?", (uid,))
        else:
            cur = await self._conn.execute(
                "DELETE FROM reminders WHERE telegram_user_id = ? AND id = ?", (uid, reminder_id)
            )
        await self._conn.commit()
        return cur.rowcount


    async def all_reminders(self) -> list[Reminder]:
        async with self._conn.execute(
            "SELECT r.*, u.timezone, u.chat_id FROM reminders r "
            "JOIN users u ON u.telegram_user_id = r.telegram_user_id"
        ) as cur:
            return [_row_to_reminder(r) for r in await cur.fetchall()]


    async def mark_reminder_sent(self, reminder_id: int, date_str: str) -> None:
        await self._conn.execute(
            "UPDATE reminders SET last_sent_date = ? WHERE id = ?", (date_str, reminder_id)
        )
        await self._conn.commit()


@dataclass(frozen=True)
class Reminder:
    id: int
    telegram_user_id: int
    time: str            # 'HH:MM' local
    days: str            # comma list of weekday numbers (Mon=0), '' = every day
    only_rain: bool
    last_sent_date: str | None
    timezone: str = "UTC"
    chat_id: int = 0

    def runs_on(self, weekday: int) -> bool:
        return not self.days or str(weekday) in self.days.split(",")


REMINDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id  INTEGER NOT NULL,
    time              TEXT    NOT NULL,
    days              TEXT    NOT NULL DEFAULT '',
    only_rain         INTEGER NOT NULL DEFAULT 0,
    last_sent_date    TEXT
);
"""


def _row_to_reminder(row) -> Reminder:
    keys = row.keys()
    return Reminder(
        id=row["id"], telegram_user_id=row["telegram_user_id"], time=row["time"],
        days=row["days"], only_rain=bool(row["only_rain"]), last_sent_date=row["last_sent_date"],
        timezone=row["timezone"] if "timezone" in keys else "UTC",
        chat_id=row["chat_id"] if "chat_id" in keys else 0,
    )
