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
from app.keyboards import (
    TIMEZONES,
    main_menu,
    rem_days,
    rem_hours,
    rem_minutes,
    reminders_menu,
    time_menu,
    tone_menu,
    tz_menu,
)
from app.phrases import TONE_LABELS
from app.recommendations import NotificationService
from app.storage import Storage, User
from app.validation import (
    describe_days,
    normalize_time,
    normalize_timezone,
    parse_days,
)

logger = logging.getLogger(__name__)
router = Router()
NL = chr(10)

WELCOME = NL.join([
    "Привет! Я присылаю погоду по {city}: утреннюю сводку и напоминания, когда тебе удобно.",
    "",
    "Быстрый старт:",
    "1) 🌅 Утро — выбери время сводки",
    "2) ⏰ Напоминания — например, вт и чт в 19:15, когда идёшь с работы",
    "3) 🎭 Тон — по-братски, по-доброму или микс",
    "",
    "Всё настраивается кнопками. Команды: /help",
])

HELP = NL.join([
    "Команды:",
    "/now — погода прямо сейчас",
    "/settime 07:30 — время утренней сводки",
    "/remind 19:15 вт,чт [rain] — напоминание в выбранные дни (rain — только при дожде)",
    "/reminders — список, /unremind N — удалить, /unremind all — удалить все",
    "/tone bro|nice|mix — тон фраз",
    "/timezone Asia/Almaty — часовой пояс",
    "/status — настройки, /stop — выключить всё",
    "",
    "Дни: пн вт ср чт пт сб вс, будни, выходные, daily",
])

REMIND_USAGE = NL.join([
    "Формат: /remind ЧЧ:ММ дни [rain]",
    "/remind 19:15 вт,чт",
    "/remind 19:45 вт,чт rain — только если будет дождь",
    "/remind 08:00 будни",
])


def _tz_label(name: str) -> str:
    return next((label for label, tz in TIMEZONES if tz == name), name)


def _reminder_line(r) -> str:
    return f"{r.time} · {describe_days(r.days)}{' · только при дожде' if r.only_rain else ''}"


async def _status_text(user: User | None, store: Storage) -> str:
    if user is None:
        return "Ты ещё не настроен. Нажми /start."
    lines = [
        f"Город: {user.preferred_city}",
        f"Часовой пояс: {_tz_label(user.timezone)}",
        f"Утренняя сводка: {user.notification_time or 'выключена'}",
        f"Тон: {TONE_LABELS.get(user.tone, user.tone)}",
    ]
    reminders = await store.list_reminders(user.telegram_user_id)
    if reminders:
        lines.append("Напоминания:")
        lines.extend(f"  • {_reminder_line(r)}" for r in reminders)
    else:
        lines.append("Напоминания: нет")
    return NL.join(lines)


async def _register(store: Storage, settings: Settings, user_id: int, chat_id: int) -> None:
    await store.ensure_user(
        telegram_user_id=user_id,
        chat_id=chat_id,
        default_timezone=settings.default_timezone,
        default_city=settings.city_name,
    )


def _chat_id(callback: CallbackQuery) -> int:
    return callback.message.chat.id if callback.message else callback.from_user.id


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


def _mask_to_days(mask: str) -> str:
    chosen = [str(i) for i, c in enumerate(mask) if c == "1"]
    return "" if len(chosen) in (0, 7) else ",".join(chosen)


def _pretty_hhmm(hhmm: str) -> str:
    return f"{hhmm[:2]}:{hhmm[2:]}"


def _days_screen_text(hhmm: str, mask: str, rain: str) -> str:
    days = describe_days(_mask_to_days(mask))
    extra = NL + "Придёт только если ожидается дождь." if rain == "1" else ""
    return NL.join([
        f"Напоминание на {_pretty_hhmm(hhmm)}",
        f"Дни: {days}{extra}",
        "",
        "Выбери дни (ничего не выбрано = каждый день) и нажми «Сохранить».",
    ])


# ---------------------------------------------------------------- commands ----


@router.message(CommandStart())
async def cmd_start(message: Message, store: Storage, settings: Settings) -> None:
    if message.from_user is None:
        return
    await _register(store, settings, message.from_user.id, message.chat.id)
    await message.answer(WELCOME.format(city=settings.city_name), reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP, reply_markup=main_menu())


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


@router.message(Command("remind"))
async def cmd_remind(
    message: Message, store: Storage, settings: Settings, command: CommandObject
) -> None:
    if message.from_user is None:
        return
    tokens = (command.args or "").split()
    only_rain = bool(tokens) and tokens[-1].lower() in ("rain", "дождь")
    if only_rain:
        tokens = tokens[:-1]
    when = normalize_time(tokens[0]) if tokens else None
    days = parse_days(" ".join(tokens[1:])) if len(tokens) > 1 else ""
    if when is None or days is None:
        await message.answer(REMIND_USAGE)
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
        await message.answer("Напоминаний нет. Добавить: /remind 19:15 вт,чт или кнопка ниже.",
                             reply_markup=reminders_menu(items))
        return
    lines = [f"#{r.id} · {_reminder_line(r)}" for r in items]
    lines += ["", "Удалить: /unremind <номер> или /unremind all"]
    await message.answer(NL.join(lines), reply_markup=reminders_menu(items))


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


@router.message(Command("tone"))
async def cmd_tone(
    message: Message, store: Storage, settings: Settings, command: CommandObject
) -> None:
    if message.from_user is None:
        return
    await _register(store, settings, message.from_user.id, message.chat.id)
    arg = (command.args or "").strip().lower()
    if arg in TONE_LABELS:
        await store.set_tone(message.from_user.id, arg)
        await message.answer(f"Тон: {TONE_LABELS[arg]}.", reply_markup=main_menu())
        return
    user = await store.get_user(message.from_user.id)
    await message.answer("Выбери тон сообщений:", reply_markup=tone_menu(user.tone if user else "mix"))


@router.message(Command("timezone"))
async def cmd_timezone(
    message: Message, store: Storage, settings: Settings, command: CommandObject
) -> None:
    if message.from_user is None:
        return
    await _register(store, settings, message.from_user.id, message.chat.id)
    if not (command.args or "").strip():
        user = await store.get_user(message.from_user.id)
        await message.answer("Выбери часовой пояс:", reply_markup=tz_menu(user.timezone if user else ""))
        return
    tz = normalize_timezone(command.args or "")
    if tz is None:
        await message.answer("Не знаю такой пояс. Нужно имя IANA, например /timezone Asia/Almaty")
        return
    await store.set_timezone(message.from_user.id, tz)
    await message.answer(f"Часовой пояс: {tz}.")


@router.message(Command("stop"))
async def cmd_stop(message: Message, store: Storage) -> None:
    if message.from_user is None:
        return
    await store.set_notification_time(message.from_user.id, None)
    await store.delete_reminders(message.from_user.id, None)
    await message.answer("Все уведомления выключены.", reply_markup=main_menu())


@router.message(Command("status"))
async def cmd_status(message: Message, store: Storage) -> None:
    if message.from_user is None:
        return
    user = await store.get_user(message.from_user.id)
    await message.answer(await _status_text(user, store), reply_markup=main_menu())


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


# --------------------------------------------------------------- callbacks ----


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery) -> None:
    await _edit(callback, "Меню:", main_menu())


@router.callback_query(F.data == "now")
async def cb_now(
    callback: CallbackQuery, store: Storage, settings: Settings, notifications: NotificationService
) -> None:
    chat_id = _chat_id(callback)
    await _register(store, settings, callback.from_user.id, chat_id)
    user = await store.get_user(callback.from_user.id)
    await callback.answer("Смотрю погоду…")
    if not await _send_recommendation(user, notifications):
        await callback.bot.send_message(chat_id, "Сейчас не получилось собрать сводку. Попробуй позже.")


@router.callback_query(F.data == "status")
async def cb_status(callback: CallbackQuery, store: Storage) -> None:
    user = await store.get_user(callback.from_user.id)
    await _edit(callback, await _status_text(user, store), main_menu())


@router.callback_query(F.data == "stop")
async def cb_stop(callback: CallbackQuery, store: Storage) -> None:
    await store.set_notification_time(callback.from_user.id, None)
    await store.delete_reminders(callback.from_user.id, None)
    await _edit(callback, "Все уведомления выключены.", main_menu())


# morning time

@router.callback_query(F.data == "time_menu")
async def cb_time_menu(callback: CallbackQuery) -> None:
    await _edit(callback, "Во сколько присылать утреннюю сводку?", time_menu())


@router.callback_query(F.data == "time_custom")
async def cb_time_custom(callback: CallbackQuery) -> None:
    await _edit(
        callback,
        "Пришли время в формате ЧЧ:ММ (например 07:45) или команду /settime 07:45.",
        main_menu(),
    )


@router.callback_query(F.data.startswith("set:"))
async def cb_set_time(callback: CallbackQuery, store: Storage, settings: Settings) -> None:
    raw = callback.data.split(":", 1)[1]
    await _register(store, settings, callback.from_user.id, _chat_id(callback))
    if raw == "off":
        await store.set_notification_time(callback.from_user.id, None)
        await _edit(callback, "Утренняя сводка выключена.", main_menu())
        return
    when = normalize_time(raw)
    if when is None:
        await callback.answer("Неверное время", show_alert=True)
        return
    await store.set_notification_time(callback.from_user.id, when)
    await _edit(callback, f"Готово. Буду присылать в {when}.", main_menu())


# tone

@router.callback_query(F.data == "tone_menu")
async def cb_tone_menu(callback: CallbackQuery, store: Storage) -> None:
    user = await store.get_user(callback.from_user.id)
    await _edit(callback, "Выбери тон сообщений:", tone_menu(user.tone if user else "mix"))


@router.callback_query(F.data.startswith("tone:"))
async def cb_set_tone(callback: CallbackQuery, store: Storage, settings: Settings) -> None:
    tone = callback.data.split(":", 1)[1]
    if tone not in TONE_LABELS:
        await callback.answer("Неизвестный тон", show_alert=True)
        return
    await _register(store, settings, callback.from_user.id, _chat_id(callback))
    await store.set_tone(callback.from_user.id, tone)
    await _edit(callback, f"Тон: {TONE_LABELS[tone]}", tone_menu(tone))


# timezone

@router.callback_query(F.data == "tz_menu")
async def cb_tz_menu(callback: CallbackQuery, store: Storage) -> None:
    user = await store.get_user(callback.from_user.id)
    await _edit(
        callback,
        "Выбери часовой пояс (другой — командой /timezone Area/City):",
        tz_menu(user.timezone if user else ""),
    )


@router.callback_query(F.data.startswith("tz:"))
async def cb_set_tz(callback: CallbackQuery, store: Storage, settings: Settings) -> None:
    tz = normalize_timezone(callback.data.split(":", 1)[1])
    if tz is None:
        await callback.answer("Неизвестный пояс", show_alert=True)
        return
    await _register(store, settings, callback.from_user.id, _chat_id(callback))
    await store.set_timezone(callback.from_user.id, tz)
    await _edit(callback, f"Часовой пояс: {_tz_label(tz)}", tz_menu(tz))


# reminders

async def _show_reminders(callback: CallbackQuery, store: Storage) -> None:
    items = await store.list_reminders(callback.from_user.id)
    text = (
        "Твои напоминания (нажми, чтобы удалить):"
        if items
        else "Напоминаний пока нет. Добавь — например, на вт и чт в 19:15."
    )
    await _edit(callback, text, reminders_menu(items))


@router.callback_query(F.data == "rem_menu")
async def cb_rem_menu(callback: CallbackQuery, store: Storage) -> None:
    await _show_reminders(callback, store)


@router.callback_query(F.data.startswith("rdel:"))
async def cb_rem_delete(callback: CallbackQuery, store: Storage) -> None:
    raw = callback.data.split(":", 1)[1]
    if raw.isdigit():
        await store.delete_reminders(callback.from_user.id, int(raw))
    await _show_reminders(callback, store)


@router.callback_query(F.data == "radd")
async def cb_rem_add(callback: CallbackQuery) -> None:
    await _edit(callback, "Во сколько напомнить? Выбери час:", rem_hours())


@router.callback_query(F.data.startswith("rh:"))
async def cb_rem_hour(callback: CallbackQuery) -> None:
    hour = callback.data.split(":", 1)[1]
    if not (hour.isdigit() and len(hour) == 2 and 0 <= int(hour) <= 23):
        await callback.answer("Неверный час", show_alert=True)
        return
    await _edit(callback, f"{hour}:__ — выбери минуты:", rem_minutes(hour))


def _parse_state(data: str):
    """'rd:1915:0101000:1' -> ('1915', '0101000', '1') or None if malformed."""
    parts = data.split(":")
    if len(parts) != 4:
        return None
    _, hhmm, mask, rain = parts
    if (
        len(hhmm) != 4 or not hhmm.isdigit() or normalize_time(_pretty_hhmm(hhmm)) is None
        or len(mask) != 7 or set(mask) - {"0", "1"} or rain not in ("0", "1")
    ):
        return None
    return hhmm, mask, rain


@router.callback_query(F.data.startswith("rd:"))
async def cb_rem_days(callback: CallbackQuery) -> None:
    state = _parse_state(callback.data)
    if state is None:
        await callback.answer("Что-то пошло не так", show_alert=True)
        return
    hhmm, mask, rain = state
    await _edit(callback, _days_screen_text(hhmm, mask, rain), rem_days(hhmm, mask, rain))


@router.callback_query(F.data.startswith("rs:"))
async def cb_rem_save(callback: CallbackQuery, store: Storage, settings: Settings) -> None:
    state = _parse_state(callback.data)
    if state is None:
        await callback.answer("Что-то пошло не так", show_alert=True)
        return
    hhmm, mask, rain = state
    await _register(store, settings, callback.from_user.id, _chat_id(callback))
    await store.add_reminder(
        callback.from_user.id, _pretty_hhmm(hhmm), _mask_to_days(mask), rain == "1"
    )
    await _show_reminders(callback, store)


# ------------------------------------------------------------- plain text ----


@router.message(F.text & ~F.text.startswith("/"))
async def on_plain_text(message: Message, store: Storage, settings: Settings) -> None:
    if message.from_user is None:
        return
    when = normalize_time(message.text or "")
    if when is None:
        await message.answer("Не понял. Нажми /start для меню или пришли время: 07:45")
        return
    await _register(store, settings, message.from_user.id, message.chat.id)
    await store.set_notification_time(message.from_user.id, when)
    await message.answer(f"Готово. Буду присылать утреннюю сводку в {when}.", reply_markup=main_menu())
