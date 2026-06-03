"""Builds the daily message and sends it, once per user per day."""
from __future__ import annotations

import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.advice import build_recommendation, build_styled
from app.config import Settings
from app.phrases import PhrasePool, categorize_weather
from app.storage import Storage, User
from app.weather import WeatherClient, build_weather_context

logger = logging.getLogger(__name__)

RETRY_BACKOFF_SECONDS = 600


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
        self._last_attempt: dict[int, float] = {}

    async def run_due_notifications(self) -> None:
        for user in await self._storage.get_users_with_notifications():
            if not self._is_due(user):
                continue
            try:
                await self._deliver(user, mark_sent=True)
            except Exception:
                logger.exception("Scheduled send failed for user %s", user.telegram_user_id)

    async def send_now(self, user: User) -> None:
        """Reply to /now. Doesn't touch the once-a-day bookkeeping."""
        await self._deliver(user, mark_sent=False)

    def _is_due(self, user: User) -> bool:
        if user.notification_time is None:
            return False
        now = dt.datetime.now(ZoneInfo(user.timezone))
        if user.last_sent_date == now.strftime("%Y-%m-%d"):
            return False
        if now.strftime("%H:%M") < user.notification_time:
            return False
        last = self._last_attempt.get(user.telegram_user_id)
        return last is None or time.monotonic() - last >= RETRY_BACKOFF_SECONDS

    async def _deliver(self, user: User, mark_sent: bool) -> None:
        self._last_attempt[user.telegram_user_id] = time.monotonic()
        raw = await self._weather.fetch()
        context = build_weather_context(raw, self._settings.city_name)
        message = self._build_message(context)

        await self._bot.send_message(user.chat_id, message)

        if mark_sent:
            today = dt.datetime.now(ZoneInfo(user.timezone)).strftime("%Y-%m-%d")
            await self._storage.mark_sent(user.telegram_user_id, today)
        self._last_attempt.pop(user.telegram_user_id, None)
        logger.info("Delivered recommendation to user %s", user.telegram_user_id)

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
