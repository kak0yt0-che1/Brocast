"""Inline keyboards for the bot menu."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

PRESET_HOURS = range(6, 23) 


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🌤 Сейчас", callback_data="now")
    kb.button(text="🕗 Время", callback_data="time_menu")
    kb.button(text="☂️ Вечер", callback_data="evening_menu")
    kb.button(text="⚙️ Статус", callback_data="status")
    kb.button(text="🔕 Выключить", callback_data="stop")
    kb.adjust(2, 3)
    return kb.as_markup()


def time_menu(prefix: str = "set", hours=PRESET_HOURS) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for hour in hours:
        kb.button(text=f"{hour:02d}:00", callback_data=f"{prefix}:{hour:02d}:00")
    kb.adjust(4)
    kb.row(
        InlineKeyboardButton(text="✍️ Своё время", callback_data="time_custom"),
        InlineKeyboardButton(text="‹ Назад", callback_data="menu"),
    )
    return kb.as_markup()


def evening_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for hour in range(14, 22):
        kb.button(text=f"{hour:02d}:00", callback_data=f"eve:{hour:02d}:00")
    kb.adjust(4)
    kb.row(
        InlineKeyboardButton(text="🔕 Выкл", callback_data="eve:off"),
        InlineKeyboardButton(text="‹ Назад", callback_data="menu"),
    )
    return kb.as_markup()
