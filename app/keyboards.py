"""Inline keyboards. All state lives inside callback_data, so no FSM is needed."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.phrases import TONE_LABELS
from app.validation import DAY_NAMES_RU

PRESET_HOURS = range(6, 23)
TIMEZONES = [
    ("Алматы / Астана", "Asia/Almaty"),
    ("Ташкент", "Asia/Tashkent"),
    ("Бишкек", "Asia/Bishkek"),
    ("Москва", "Europe/Moscow"),
    ("Киев", "Europe/Kyiv"),
    ("Минск", "Europe/Minsk"),
    ("Баку", "Asia/Baku"),
    ("Стамбул", "Europe/Istanbul"),
]


def _back(target: str = "menu") -> InlineKeyboardButton:
    return InlineKeyboardButton(text="‹ Назад", callback_data=target)


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🌤 Сейчас", callback_data="now")
    kb.button(text="⏰ Напоминания", callback_data="rem_menu")
    kb.button(text="🌅 Утро", callback_data="time_menu")
    kb.button(text="🎭 Тон", callback_data="tone_menu")
    kb.button(text="🌍 Пояс", callback_data="tz_menu")
    kb.button(text="⚙️ Статус", callback_data="status")
    kb.button(text="🔕 Выключить всё", callback_data="stop")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


def time_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for hour in PRESET_HOURS:
        kb.button(text=f"{hour:02d}:00", callback_data=f"set:{hour:02d}:00")
    kb.adjust(4)
    kb.row(
        InlineKeyboardButton(text="✍️ Своё время", callback_data="time_custom"),
        InlineKeyboardButton(text="🔕 Выкл", callback_data="set:off"),
    )
    kb.row(_back())
    return kb.as_markup()


def tone_menu(current: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for tone, label in TONE_LABELS.items():
        mark = "✅ " if tone == current else ""
        kb.button(text=f"{mark}{label}", callback_data=f"tone:{tone}")
    kb.adjust(1)
    kb.row(_back())
    return kb.as_markup()


def tz_menu(current: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for label, name in TIMEZONES:
        mark = "✅ " if name == current else ""
        kb.button(text=f"{mark}{label}", callback_data=f"tz:{name}")
    kb.adjust(2)
    kb.row(_back())
    return kb.as_markup()


def reminders_menu(items: list) -> InlineKeyboardMarkup:
    """items: Reminder objects; each gets a delete button."""
    from app.validation import describe_days

    kb = InlineKeyboardBuilder()
    for r in items:
        rain = " ☔" if r.only_rain else ""
        kb.button(
            text=f"🗑 {r.time} · {describe_days(r.days)}{rain}", callback_data=f"rdel:{r.id}"
        )
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text="➕ Добавить", callback_data="radd"))
    kb.row(_back())
    return kb.as_markup()


def rem_hours() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for hour in range(6, 24):
        kb.button(text=f"{hour:02d}", callback_data=f"rh:{hour:02d}")
    kb.adjust(6)
    kb.row(_back("rem_menu"))
    return kb.as_markup()


def rem_minutes(hour: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for minute in ("00", "10", "15", "20", "30", "40", "45", "50"):
        kb.button(text=f"{hour}:{minute}", callback_data=f"rd:{hour}{minute}:0000000:0")
    kb.adjust(4)
    kb.row(_back("radd"))
    return kb.as_markup()


def rem_days(hhmm: str, mask: str, rain: str) -> InlineKeyboardMarkup:
    """Day toggles. `mask` is 7 chars of 0/1 (Mon..Sun); all zeros means every day."""
    kb = InlineKeyboardBuilder()
    for i, name in enumerate(DAY_NAMES_RU):
        flipped = mask[:i] + ("0" if mask[i] == "1" else "1") + mask[i + 1:]
        text = f"✅ {name}" if mask[i] == "1" else name
        kb.button(text=text, callback_data=f"rd:{hhmm}:{flipped}:{rain}")
    kb.adjust(4, 3)
    kb.row(
        InlineKeyboardButton(text="Будни", callback_data=f"rd:{hhmm}:1111100:{rain}"),
        InlineKeyboardButton(text="Выходные", callback_data=f"rd:{hhmm}:0000011:{rain}"),
        InlineKeyboardButton(text="Каждый день", callback_data=f"rd:{hhmm}:0000000:{rain}"),
    )
    flip_rain = "0" if rain == "1" else "1"
    kb.row(
        InlineKeyboardButton(
            text=f"{'✅' if rain == '1' else '☐'} Только если будет дождь",
            callback_data=f"rd:{hhmm}:{mask}:{flip_rain}",
        )
    )
    kb.row(
        InlineKeyboardButton(text="💾 Сохранить", callback_data=f"rs:{hhmm}:{mask}:{rain}"),
        _back("radd"),
    )
    return kb.as_markup()
