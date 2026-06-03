"""Entrypoint: load config, wire everything up, run the bot and the daily tick."""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import ValidationError

from app.config import get_settings
from app.handlers import router
from app.logging_config import setup_logging
from app.phrases import PhrasePool
from app.recommendations import NotificationService
from app.storage import Storage
from app.weather import WeatherClient

logger = logging.getLogger(__name__)


async def run() -> None:
    try:
        settings = get_settings()
    except ValidationError as exc:
        print("Configuration error. Check your environment / .env file:\n", file=sys.stderr)
        print(exc, file=sys.stderr)
        raise SystemExit(1)

    setup_logging(settings.log_level)
    logger.info("Starting weather bot for %s", settings.city_name)

    storage = Storage(settings.database_path)
    await storage.connect()

    bot = Bot(token=settings.telegram_bot_token)
    notifications = NotificationService(
        bot=bot,
        storage=storage,
        weather_client=WeatherClient(settings.latitude, settings.longitude),
        phrase_pool=PhrasePool.load(),
        settings=settings,
    )

    dispatcher = Dispatcher(store=storage, settings=settings, notifications=notifications)
    dispatcher.include_router(router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        notifications.run_due_notifications,
        "interval",
        minutes=1,
        max_instances=1,
        coalesce=True,
        id="daily_notifications",
    )
    scheduler.start()

    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()
        await storage.close()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
