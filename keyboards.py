# keyboards.py

from telebot.types import *

import config
from config import *
from db import *


def main_menu_keyboard(user_id=None):
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("⭐ Купить звезды", callback_data='buy_stars'),
        InlineKeyboardButton("💰 Пополнить баланс", callback_data='deposit')
    )
    keyboard.row(
        InlineKeyboardButton("⭐ Внутренние звезды", callback_data='buy_internal_stars'),
        InlineKeyboardButton("👤 Профиль", callback_data='profile'),
        InlineKeyboardButton("🔗 Рефералы", callback_data='referrals_menu') # НОВАЯ КНОПКА
    )
    keyboard.row(
        InlineKeyboardButton("🧮 Калькулятор", callback_data='calculator')
    )
    # keyboard.row(
    #     InlineKeyboardButton("🧪 +50 внутренних ⭐ (тест)", callback_data='grant_internal_50')
    # )
    if user_id and str(user_id) == str(config.ADMIN_ID):
        keyboard.row(InlineKeyboardButton("⚙️ Админка", callback_data='admin_menu'))
    return keyboard


def buy_stars_options_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("Себе", callback_data='buy_stars_self'),
        InlineKeyboardButton("Другу", callback_data='buy_stars_friend')
    )
    keyboard.row(InlineKeyboardButton("↩️ Назад", callback_data='main_menu'))
    return keyboard


def buy_stars_quantity_keyboard(user_data):
    keyboard = InlineKeyboardMarkup()

    # Получаем актуальную цену из БД
    star_price = get_star_price()

    options = [
        (50, f"50 звезд - {star_price * 50:.2f} руб"),
        (100, f"100 звезд - {star_price * 100:.2f} руб"),
        (500, f"500 звезд - {star_price * 500:.2f} руб"),
        (1000, f"1000 звезд - {star_price * 1000:.2f} руб")
    ]

    for stars, text in options:
        keyboard.row(InlineKeyboardButton(text, callback_data=f'buy_{stars}'))

    keyboard.row(InlineKeyboardButton("✍️ Другое количество", callback_data='buy_custom'))
    keyboard.row(InlineKeyboardButton("↩️ Назад", callback_data='main_menu'))
    return keyboard


def deposit_keyboard(user_data):
    keyboard = InlineKeyboardMarkup()

    amounts = [50, 100, 500, 1000]
    for amount in amounts:
        keyboard.row(InlineKeyboardButton(f"{amount} руб", callback_data=f'deposit_{amount}'))

    keyboard.row(InlineKeyboardButton("✍️ Другая сумма", callback_data='deposit_custom'))

    keyboard.row(InlineKeyboardButton("↩️ Назад", callback_data='main_menu'))
    return keyboard


def back_to_main_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.row(InlineKeyboardButton("↩️ Назад", callback_data='main_menu'))

    return keyboard


def calculator_menu_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("Рубли → ⭐", callback_data='calc_rub_to_stars'),
        InlineKeyboardButton("⭐ → Рубли", callback_data='calc_stars_to_rub')
    )
    keyboard.row(
        InlineKeyboardButton("TON → Рубли", callback_data='calc_ton_to_rub'),
        InlineKeyboardButton("Рубли → TON", callback_data='calc_rub_to_ton')
    )
    keyboard.row(
        InlineKeyboardButton("TON → ⭐", callback_data='calc_ton_to_stars'),
        InlineKeyboardButton("⭐ → TON", callback_data='calc_stars_to_ton')
    )
    keyboard.row(InlineKeyboardButton("↩️ Назад", callback_data='main_menu'))
    return keyboard


def buy_internal_stars_quantity_keyboard():
    keyboard = InlineKeyboardMarkup()
    options = [
        (10, "10 ⭐ Telegram"),
        (50, "50 ⭐ Telegram"),
        (100, "100 ⭐ Telegram"),
        (500, "500 ⭐ Telegram")
    ]

    for stars, text in options:
        keyboard.row(InlineKeyboardButton(text, callback_data=f'buy_internal_{stars}'))

    keyboard.row(InlineKeyboardButton("✍️ Другое количество", callback_data='buy_internal_custom'))
    keyboard.row(InlineKeyboardButton("↩️ Назад", callback_data='main_menu'))
    return keyboard

