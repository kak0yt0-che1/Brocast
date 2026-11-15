"""Telegram commands and inline-button callbacks.

The dispatcher injects store/settings/notifications into handlers by name.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.keyboards import evening_menu, main_menu, time_menu
from app.recommendations import NotificationService
from app.storage import Storage, User
from app.validation import describe_days, normalize_time, normalize_timezone, parse_days

logger = logging.getLogger(__name__)
router = Router()

WELCOME = (
    "Привет! Я раз в день кидаю погодную сводку по {city}.\n"
    "Жми кнопки или используй команды:\n"
    "/settime ЧЧ:ММ · /evening ЧЧ:ММ|off · /remind ЧЧ:ММ вт,чт · /timezone Area/City · /now · /status · /stop"
)


def _status_text(user: User | None) -> str:
    if user is None:
        return "Ты ещё не настроен. Нажми /start."
    return (
        f"Город: {user.preferred_city}\n"
        f"Часовой пояс: {user.timezone}\n"
        f"Утро: {user.notification_time or 'выключено'}\n"
        f"Вечернее напоминание про зонт: {user.evening_time or 'выключено'}"
    )


async def _register(store: Storage, settings: Settings, user_id: int, chat_id: int) -> None:
    await store.ensure_user(
        telegram_user_id=user_id,
        chat_id=chat_id,
        default_timezone=settings.default_timezone,
        default_city=settings.city_name,
    )


async def _edit(callback: CallbackQuery, text: str, markup=None) -> None:
    """edit_text that survives 'message is not modified' and stale messages, and always answers."""
    try:
        if callback.message is not None and hasattr(callback.message, "edit_text"):
            await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" not in str(exc):
            logger.warning("edit_text failed: %s", exc)
    await callback.answer()


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
    await _register(store, settings, message.from_user.id, message.chat.id)
    await message.answer(WELCOME.format(city=settings.city_name), reply_markup=main_menu())


@router.message(Command("settime"))
async def cmd_settime(
    message: Message, store: Storage, settings: Settings, command: CommandObject
) -> None:
    if message.from_user is None:
        return
    when = normalize_time(command.args or "")
    if when is None:
        await message.answer("Нужен формат 24ч, например /settime 07:30")
        return
    await _register(store, settings, message.from_user.id, message.chat.id)
    await store.set_notification_time(message.from_user.id, when)
    await message.answer(f"Готово. Буду присылать сводку в {when}.", reply_markup=main_menu())


@router.message(Command("evening"))
async def cmd_evening(
    message: Message, store: Storage, settings: Settings, command: CommandObject
) -> None:
    if message.from_user is None:
        return
    arg = (command.args or "").strip().lower()
    await _register(store, settings, message.from_user.id, message.chat.id)
    if arg in ("off", "stop", "выкл"):
        await store.set_evening_time(message.from_user.id, None)
        await message.answer("Вечернее напоминание выключено.", reply_markup=main_menu())
        return
    when = normalize_time(arg)
    if when is None:
        await message.answer("Формат: /evening 17:30 или /evening off")
        return
    await store.set_evening_time(message.from_user.id, when)
    await message.answer(
        f"Готово. В {when} напомню про зонт — только если вечером ожидается дождь.",
        reply_markup=main_menu(),
    )


@router.message(Command("remind"))
async def cmd_remind(
    message: Message, store: Storage, settings: Settings, command: CommandObject
) -> None:
    """/remind 19:15 вт,чт [rain] — extra weather pings on chosen days (as many as you like)."""
    if message.from_user is None:
        return
    usage = "\n".join([
        "Формат: /remind ЧЧ:ММ дни [rain]",
        "Примеры:",
        "/remind 19:15 вт,чт",
        "/remind 19:45 вт,чт rain — только если будет дождь",
        "/remind 08:00 будни",
        "Дни: пн вт ср чт пт сб вс, будни, выходные, daily",
    ])
    tokens = (command.args or "").split()
    only_rain = bool(tokens) and tokens[-1].lower() in ("rain", "дождь")
    if only_rain:
        tokens = tokens[:-1]
    when = normalize_time(tokens[0]) if tokens else None
    days = parse_days(" ".join(tokens[1:])) if len(tokens) > 1 else ""
    if when is None or days is None:
        await message.answer(usage)
        return
    await _register(store, settings, message.from_user.id, message.chat.id)
    rid = await store.add_reminder(message.from_user.id, when, days, only_rain)
    suffix = ", только при дожде" if only_rain else ""
    await message.answer(
        f"Добавил #{rid}: {when}, {describe_days(days)}{suffix}.", reply_markup=main_menu()
    )


@router.message(Command("reminders"))
async def cmd_reminders(message: Message, store: Storage) -> None:
    if message.from_user is None:
        return
    items = await store.list_reminders(message.from_user.id)
    if not items:
        await message.answer("Доп. напоминаний нет. Добавить: /remind 19:15 вт,чт")
        return
    lines = [
        f"#{r.id} · {r.time} · {describe_days(r.days)}{' · при дожде' if r.only_rain else ''}"
        for r in items
    ]
    lines += ["", "Удалить: /unremind <номер> или /unremind all"]
    await message.answer("\n".join(lines))


@router.message(Command("unremind"))
async def cmd_unremind(message: Message, store: Storage, command: CommandObject) -> None:
    if message.from_user is None:
        return
    arg = (command.args or "").strip().lower()
    if arg == "all":
        removed = await store.delete_reminders(message.from_user.id, None)
    elif arg.isdigit():
        removed = await store.delete_reminders(message.from_user.id, int(arg))
    else:
        await message.answer("Формат: /unremind <номер> или /unremind all (список: /reminders)")
        return
    await message.answer(f"Удалено: {removed}.")


@router.message(Command("timezone"))
async def cmd_timezone(
    message: Message, store: Storage, settings: Settings, command: CommandObject
) -> None:
    if message.from_user is None:
        return
    tz = normalize_timezone(command.args or "")
    if tz is None:
        await message.answer("Не знаю такой пояс. Нужно имя IANA, например /timezone Asia/Almaty")
        return
    await _register(store, settings, message.from_user.id, message.chat.id)
    await store.set_timezone(message.from_user.id, tz)
    await message.answer(f"Часовой пояс: {tz}.")


@router.message(Command("stop"))
async def cmd_stop(message: Message, store: Storage) -> None:
    if message.from_user is None:
        return
    await store.set_notification_time(message.from_user.id, None)
    await store.set_evening_time(message.from_user.id, None)
    await message.answer("Все уведомления выключены.", reply_markup=main_menu())


@router.message(Command("status"))
async def cmd_status(message: Message, store: Storage) -> None:
    if message.from_user is None:
        return
    user = await store.get_user(message.from_user.id)
    await message.answer(_status_text(user), reply_markup=main_menu())


@router.message(Command("now"))
async def cmd_now(
    message: Message, store: Storage, settings: Settings, notifications: NotificationService
) -> None:
    if message.from_user is None:
        return
    await _register(store, settings, message.from_user.id, message.chat.id)
    user = await store.get_user(message.from_user.id)
    await message.answer("Смотрю погоду…")
    if not await _send_recommendation(user, notifications):
        await message.answer("Сейчас не получилось собрать сводку. Попробуй позже.")


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery) -> None:
    await _edit(callback, "Меню:", main_menu())


@router.callback_query(F.data == "now")
async def cb_now(
    callback: CallbackQuery, store: Storage, settings: Settings, notifications: NotificationService
) -> None:
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    await _register(store, settings, callback.from_user.id, chat_id)
    user = await store.get_user(callback.from_user.id)
    await callback.answer("Смотрю погоду…")
    if not await _send_recommendation(user, notifications):
        await callback.bot.send_message(chat_id, "Сейчас не получилось собрать сводку. Попробуй позже.")


@router.callback_query(F.data == "time_menu")
async def cb_time_menu(callback: CallbackQuery) -> None:
    await _edit(callback, "Во сколько присылать утреннюю сводку?", time_menu("set"))


@router.callback_query(F.data == "evening_menu")
async def cb_evening_menu(callback: CallbackQuery) -> None:
    await _edit(
        callback,
        "Во сколько напомнить про зонт? Придёт только если вечером ожидается дождь.",
        evening_menu(),
    )


@router.callback_query(F.data == "time_custom")
async def cb_time_custom(callback: CallbackQuery) -> None:
    await _edit(
        callback,
        "Пришли время в формате ЧЧ:ММ (например 07:45) или команду /settime 07:45.",
        main_menu(),
    )


@router.callback_query(F.data.startswith("set:"))
async def cb_set_time(callback: CallbackQuery, store: Storage, settings: Settings) -> None:
    when = normalize_time(callback.data.split(":", 1)[1])
    if when is None:
        await callback.answer("Неверное время", show_alert=True)
        return
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    await _register(store, settings, callback.from_user.id, chat_id)
    await store.set_notification_time(callback.from_user.id, when)
    await _edit(callback, f"Готово. Буду присылать в {when}.", main_menu())


@router.callback_query(F.data.startswith("eve:"))
async def cb_set_evening(callback: CallbackQuery, store: Storage, settings: Settings) -> None:
    raw = callback.data.split(":", 1)[1]
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    await _register(store, settings, callback.from_user.id, chat_id)
    if raw == "off":
        await store.set_evening_time(callback.from_user.id, None)
        await _edit(callback, "Вечернее напоминание выключено.", main_menu())
        return
    when = normalize_time(raw)
    if when is None:
        await callback.answer("Неверное время", show_alert=True)
        return
    await store.set_evening_time(callback.from_user.id, when)
    await _edit(callback, f"Готово. В {when} напомню про зонт, если будет дождь.", main_menu())


@router.callback_query(F.data == "status")
async def cb_status(callback: CallbackQuery, store: Storage) -> None:
    user = await store.get_user(callback.from_user.id)
    await _edit(callback, _status_text(user), main_menu())


@router.callback_query(F.data == "stop")
async def cb_stop(callback: CallbackQuery, store: Storage) -> None:
    await store.set_notification_time(callback.from_user.id, None)
    await store.set_evening_time(callback.from_user.id, None)
    await _edit(callback, "Все уведомления выключены.", main_menu())


@router.message(F.text & ~F.text.startswith("/"))
async def on_plain_text(message: Message, store: Storage, settings: Settings) -> None:
    if message.from_user is None:
        return
    when = normalize_time(message.text or "")
    if when is None:
        await message.answer("Не понял. Нажми /start для меню или задай время: /settime 07:45")
        return
    await _register(store, settings, message.from_user.id, message.chat.id)
    await store.set_notification_time(message.from_user.id, when)
    await message.answer(f"Готово. Буду присылать в {when}.", reply_markup=main_menu())
