"""Telegram commands and inline-button callbacks.

The dispatcher injects store/settings/notifications into handlers by name.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.keyboards import main_menu, time_menu
from app.recommendations import NotificationService
from app.storage import Storage, User
from app.validation import normalize_time, normalize_timezone

logger = logging.getLogger(__name__)
router = Router()

WELCOME = (
    "Привет! Я раз в день кидаю погодную сводку по {city}.\n"
    "Жми кнопки или используй команды:\n"
    "/settime ЧЧ:ММ · /timezone Area/City · /now · /status · /stop"
)


def _status_text(user: User | None) -> str:
    if user is None:
        return "Ты ещё не настроен. Нажми /start."
    return (
        f"Город: {user.preferred_city}\n"
        f"Часовой пояс: {user.timezone}\n"
        f"Время: {user.notification_time or 'выключено'}"
    )


async def _send_recommendation(user: User, notifications: NotificationService) -> bool:
    try:
        await notifications.send_now(user)
        return True
    except Exception:
        logger.exception("On-demand send failed for user %s", user.telegram_user_id)
        return False



@router.message(CommandStart())
async def cmd_start(message: Message, store: Storage, settings: Settings) -> None:
    if message.from_user is None:
        return
    await store.ensure_user(
        telegram_user_id=message.from_user.id,
        chat_id=message.chat.id,
        default_timezone=settings.default_timezone,
        default_city=settings.city_name,
    )
    await message.answer(WELCOME.format(city=settings.city_name), reply_markup=main_menu())


@router.message(Command("settime"))
async def cmd_settime(message: Message, store: Storage, command: Command) -> None:
    when = normalize_time(command.args or "")
    if when is None:
        await message.answer("Нужен формат 24ч, например /settime 07:30")
        return
    await store.set_notification_time(message.from_user.id, when)
    await message.answer(f"Готово. Буду присылать сводку в {when}.", reply_markup=main_menu())


@router.message(Command("timezone"))
async def cmd_timezone(message: Message, store: Storage, command: Command) -> None:
    tz = normalize_timezone(command.args or "")
    if tz is None:
        await message.answer("Не знаю такой пояс. Нужно имя IANA, например /timezone Asia/Almaty")
        return
    await store.set_timezone(message.from_user.id, tz)
    await message.answer(f"Часовой пояс: {tz}.")


@router.message(Command("stop"))
async def cmd_stop(message: Message, store: Storage) -> None:
    await store.set_notification_time(message.from_user.id, None)
    await message.answer("Ежедневные уведомления выключены.", reply_markup=main_menu())


@router.message(Command("status"))
async def cmd_status(message: Message, store: Storage) -> None:
    user = await store.get_user(message.from_user.id)
    await message.answer(_status_text(user), reply_markup=main_menu())


@router.message(Command("now"))
async def cmd_now(
    message: Message, store: Storage, settings: Settings, notifications: NotificationService
) -> None:
    await store.ensure_user(
        telegram_user_id=message.from_user.id,
        chat_id=message.chat.id,
        default_timezone=settings.default_timezone,
        default_city=settings.city_name,
    )
    user = await store.get_user(message.from_user.id)
    await message.answer("Смотрю погоду…")
    if not await _send_recommendation(user, notifications):
        await message.answer("Сейчас не получилось собрать сводку. Попробуй позже.")



@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Меню:", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "now")
async def cb_now(
    callback: CallbackQuery, store: Storage, settings: Settings, notifications: NotificationService
) -> None:
    await store.ensure_user(
        telegram_user_id=callback.from_user.id,
        chat_id=callback.message.chat.id,
        default_timezone=settings.default_timezone,
        default_city=settings.city_name,
    )
    user = await store.get_user(callback.from_user.id)
    await callback.answer("Смотрю погоду…")
    if not await _send_recommendation(user, notifications):
        await callback.message.answer("Сейчас не получилось собрать сводку. Попробуй позже.")


@router.callback_query(F.data == "time_menu")
async def cb_time_menu(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Во сколько присылать сводку?", reply_markup=time_menu())
    await callback.answer()


@router.callback_query(F.data == "time_custom")
async def cb_time_custom(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "Пришли время в формате ЧЧ:ММ (например 07:45) или команду /settime 07:45."
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set:"))
async def cb_set_time(callback: CallbackQuery, store: Storage) -> None:
    when = normalize_time(callback.data.split(":", 1)[1])
    if when is None:
        await callback.answer("Неверное время", show_alert=True)
        return
    await store.set_notification_time(callback.from_user.id, when)
    await callback.message.edit_text(f"Готово. Буду присылать в {when}.", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "status")
async def cb_status(callback: CallbackQuery, store: Storage) -> None:
    user = await store.get_user(callback.from_user.id)
    await callback.message.edit_text(_status_text(user), reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "stop")
async def cb_stop(callback: CallbackQuery, store: Storage) -> None:
    await store.set_notification_time(callback.from_user.id, None)
    await callback.message.edit_text("Уведомления выключены.", reply_markup=main_menu())
    await callback.answer()



@router.message(F.text & ~F.text.startswith("/"))
async def on_plain_text(message: Message, store: Storage, settings: Settings) -> None:
    when = normalize_time(message.text or "")
    if when is None:
        await message.answer("Не понял. Нажми /start для меню или задай время: /settime 07:45")
        return
    await store.ensure_user(
        telegram_user_id=message.from_user.id,
        chat_id=message.chat.id,
        default_timezone=settings.default_timezone,
        default_city=settings.city_name,
    )
    await store.set_notification_time(message.from_user.id, when)
    await message.answer(f"Готово. Буду присылать в {when}.", reply_markup=main_menu())
