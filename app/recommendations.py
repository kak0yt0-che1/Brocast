"""Builds the daily messages and sends them: morning summary + optional evening umbrella reminder."""
from __future__ import annotations

import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from app.advice import build_recommendation, build_reminder_text, build_styled
from app.config import Settings
from app.phrases import PhrasePool, categorize_weather
from app.storage import Reminder, Storage, User
from app.weather import WeatherClient, build_weather_context
from app.widget import build_widget_html, send_widget

logger = logging.getLogger(__name__)

RETRY_BACKOFF_SECONDS = 600
MAX_LATE_MINUTES = 180  # don't deliver a missed slot later than this
REMINDER_LATE_MINUTES = 30  # a custom reminder is useless if it arrives much later


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


class NotificationService:
    def __init__(
        self,
        bot: Bot,
        storage: Storage,
        weather_client: WeatherClient,
        phrase_pool: PhrasePool,
        settings: Settings,
    ) -> None:
        self._bot = bot
        self._storage = storage
        self._weather = weather_client
        self._phrases = phrase_pool
        self._settings = settings
        self._last_attempt: dict[tuple[int, str], float] = {}

    async def run_due_notifications(self) -> None:
        for user in await self._storage.get_users_with_notifications():
            for kind in ("morning", "evening"):
                if not self._is_due(user, kind):
                    continue
                try:
                    await self._deliver(user, kind)
                except TelegramForbiddenError:
                    logger.info("User %s blocked the bot; disabling notifications", user.telegram_user_id)
                    await self._storage.disable_user(user.telegram_user_id)
                    break
                except Exception:
                    logger.exception("Scheduled %s send failed for user %s", kind, user.telegram_user_id)

    async def run_due_reminders(self) -> None:
        for rem in await self._storage.all_reminders():
            if not self._reminder_due(rem):
                continue
            try:
                await self._deliver_reminder(rem)
            except TelegramForbiddenError:
                await self._storage.disable_user(rem.telegram_user_id)
                await self._storage.delete_reminders(rem.telegram_user_id, None)
            except Exception:
                logger.exception("Reminder %s failed", rem.id)

    def _reminder_due(self, rem: Reminder) -> bool:
        now = dt.datetime.now(ZoneInfo(rem.timezone))
        if not rem.runs_on(now.weekday()) or rem.last_sent_date == now.strftime("%Y-%m-%d"):
            return False
        late = now.hour * 60 + now.minute - _minutes(rem.time)
        if late < 0 or late > REMINDER_LATE_MINUTES:
            return False
        last = self._last_attempt.get((rem.telegram_user_id, f"r{rem.id}"))
        return last is None or time.monotonic() - last >= 120

    async def _deliver_reminder(self, rem: Reminder) -> None:
        key = (rem.telegram_user_id, f"r{rem.id}")
        self._last_attempt[key] = time.monotonic()
        raw = await self._weather.fetch()
        context = build_weather_context(raw, self._settings.city_name)
        today = dt.datetime.now(ZoneInfo(rem.timezone)).strftime("%Y-%m-%d")
        rainy = bool(context["rain_hours_left"])
        if rem.only_rain and not rainy:
            await self._storage.mark_reminder_sent(rem.id, today)
            self._last_attempt.pop(key, None)
            return
        phrase = self._phrases.pick("umbrella" if rainy else categorize_weather(context))
        text = build_reminder_text(context, phrase)
        user = User(rem.telegram_user_id, rem.chat_id, rem.timezone, None, "", "", None)
        await self._send(user, context, text, "evening" if rainy else "reminder")
        await self._storage.mark_reminder_sent(rem.id, today)
        self._last_attempt.pop(key, None)

    async def send_now(self, user: User) -> None:
        """Reply to /now. Doesn't touch the once-a-day bookkeeping."""
        await self._deliver(user, "now")

    def _is_due(self, user: User, kind: str) -> bool:
        when = user.notification_time if kind == "morning" else user.evening_time
        last_date = user.last_sent_date if kind == "morning" else user.last_evening_date
        if when is None:
            return False
        now = dt.datetime.now(ZoneInfo(user.timezone))
        if last_date == now.strftime("%Y-%m-%d"):
            return False
        late = now.hour * 60 + now.minute - _minutes(when)
        if late < 0 or late > MAX_LATE_MINUTES:
            return False
        last = self._last_attempt.get((user.telegram_user_id, kind))
        return last is None or time.monotonic() - last >= RETRY_BACKOFF_SECONDS

    async def _deliver(self, user: User, kind: str) -> None:
        key = (user.telegram_user_id, kind)
        self._last_attempt[key] = time.monotonic()
        raw = await self._weather.fetch()
        context = build_weather_context(raw, self._settings.city_name)
        today = dt.datetime.now(ZoneInfo(user.timezone)).strftime("%Y-%m-%d")

        if kind == "evening":
            if not context["rain_hours_left"]:  # nothing to warn about: stay quiet
                await self._storage.mark_evening_sent(user.telegram_user_id, today)
                self._last_attempt.pop(key, None)
                return
            phrase = self._phrases.pick("umbrella")
            text = build_styled(context, phrase, evening=True)
        else:
            text = self._build_message(context)
            phrase = None

        await self._send(user, context, text, kind)

        if kind == "morning":
            await self._storage.mark_sent(user.telegram_user_id, today)
        elif kind == "evening":
            await self._storage.mark_evening_sent(user.telegram_user_id, today)
        self._last_attempt.pop(key, None)
        logger.info("Delivered %s message to user %s", kind, user.telegram_user_id)

    async def _send(self, user: User, context: dict, text: str, kind: str) -> None:
        if self._settings.widgets_enabled:
            phrase = text
            doc = build_widget_html(context, phrase, evening=(kind in ("evening", "reminder")))
            if await send_widget(self._bot.token, user.chat_id, doc, text):
                return
        await self._bot.send_message(user.chat_id, text)

    def _build_message(self, context: dict) -> str:
        if not self._settings.phrase_mode_enabled:
            return _format_recommendation(build_recommendation(context), self._settings.city_name)
        phrase = self._phrases.pick(categorize_weather(context))
        return build_styled(context, phrase)


def _format_recommendation(recommendation: dict, city: str) -> str:
    lines = [f"🌤 {recommendation['headline']}", "", recommendation["summary"]]
    if recommendation["tips"]:
        lines.append("")
        lines.extend(f"• {tip}" for tip in recommendation["tips"])
    lines += ["", f"— {city}"]
    return "\n".join(lines)
