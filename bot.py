import base64
import os
import json
import logging
import sqlite3
import uuid
import threading
import time
import asyncio
import requests
from datetime import datetime
from dotenv import load_dotenv
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from telebot.types import LabeledPrice
from excel_export import export_database_to_excel, cleanup_old_exports
import os


try:
    from config import MAIN_MENU_IMAGE, WELCOME_MES, logger, REFERRAL_REWARD, \
    ADMIN_ID, DB_NAME
    from db import (
        init_db, get_user, create_user, update_balance, add_transaction,
        get_pending_payment, update_payment_status,
        set_session_data, get_session_data, delete_session_data,
        get_setting, set_setting, get_referral_count, get_ton_rate_updated_at,
        set_ton_rate, set_ton_rate_updated_at, get_ton_rate,
        update_internal_stars, get_internal_stars_pool, update_internal_stars_pool,
        set_internal_stars_pool, get_star_price, set_star_price
)
    from fragment_api import load_fragment_token, authenticate_fragment, send_stars
    from yookassa import create_yookassa_payment, check_payment_status
    from keyboards import (
        main_menu_keyboard, buy_stars_options_keyboard, buy_stars_quantity_keyboard,
        back_to_main_keyboard, calculator_menu_keyboard, buy_internal_stars_quantity_keyboard
    )
except ImportError as e:

    class MockLogger:
        def info(self, msg): print(f"INFO: {msg}")

        def error(self, msg): print(f"ERROR: {msg}")

        def warning(self, msg): print(f"WARNING: {msg}")


# Загрузка переменных окружения
load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')

TON_DEPOSIT_ADDRESS = os.getenv('TON_DEPOSIT_ADDRESS')  # Адрес кошелька для приема
TON_API_KEY = os.getenv('TON_API_KEY')  # Ключ от toncenter.com
TON_API_BASE_URL = os.getenv('TON_API_BASE_URL', 'https://toncenter.com')

TON_RATE_API = "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=rub"

# Инициализация бота
bot = telebot.TeleBot(BOT_TOKEN)

animation_running = False

# Добавьте эту функцию после импортов и перед обработчиками
def safe_edit_message_caption(bot, chat_id, message_id, new_caption, new_reply_markup=None, parse_mode=None):
    """Безопасно редактирует caption сообщения, проверяя изменения."""
    try:
        bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=new_caption,
            reply_markup=new_reply_markup,
            parse_mode=parse_mode
        )
        return True
    except Exception as e:
        error_text = str(e)
        if "message is not modified" in error_text:
            # Игнорируем эту ошибку - сообщение уже имеет нужный контент
            logger.debug("Сообщение не требует изменений")
            return True
        if "there is no caption" in error_text:
            try:
                bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=new_caption,
                    reply_markup=new_reply_markup,
                    parse_mode=parse_mode
                )
                return True
            except Exception as text_error:
                if "message is not modified" in str(text_error):
                    logger.debug("Сообщение не требует изменений")
                    return True
                logger.error(f"Ошибка редактирования сообщения: {text_error}")
                raise text_error
        # Пробрасываем другие ошибки
        logger.error(f"Ошибка редактирования сообщения: {e}")
        raise e


def get_referral_reward_settings():
    amount_raw = get_setting('referral_reward_amount', REFERRAL_REWARD)
    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        amount = float(REFERRAL_REWARD)
    if amount <= 0:
        amount = float(REFERRAL_REWARD)
    currency = get_setting('referral_reward_currency', 'rub')
    if currency not in ('rub', 'stars'):
        currency = 'rub'
    if currency == 'stars':
        amount = int(amount)
    return amount, currency


def format_referral_reward(amount, currency):
    if currency == 'stars':
        amount_value = float(amount)
        if amount_value.is_integer():
            return f"{int(amount_value)} ⭐"
        return f"{amount_value} ⭐"
    return f"{float(amount):.2f} руб"


def edit_message_with_fallback(chat_id, message_id, text, reply_markup=None, parse_mode=None):
    try:
        bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except Exception as e:
        if "message is not modified" in str(e):
            return
        try:
            bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        except Exception as fallback_error:
            logger.error(f"Ошибка редактирования сообщения: {fallback_error}")
# --- Анимация загрузки ---
def animate_caption(bot, call):
    global animation_running
    dots = 1
    while animation_running:
        caption = "🔄 Отправляю звезды" + "." * dots
        edit_message_with_fallback(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=caption,
            reply_markup=back_to_main_keyboard()
        )

        dots = (dots % 3) + 1
        time.sleep(1)


# --- Обработчики команд ---
@bot.message_handler(commands=['start', 'menu'])
def start_or_menu(message: Message):
    user = message.from_user
    username = user.username if user.username else None

    # --- ЛОГИКА РЕФЕРАЛЬНОЙ ССЫЛКИ ---
    referrer_id = None
    if message.text and len(message.text.split()) > 1:
        payload = message.text.split()[1]
        # Ожидаем формат: /start r<referrer_id>
        if payload.startswith('r') and payload[1:].isdigit():
            possible_referrer_id = int(payload[1:])
            # Проверяем, что реферер не сам себя пригласил
            if possible_referrer_id != user.id:
                # Проверяем, что реферер существует
                if get_user(possible_referrer_id):
                    referrer_id = possible_referrer_id
                    logger.info(f"Обнаружен реферер: {referrer_id} для нового пользователя: {user.id}")

    # Создаем пользователя и получаем статус создания
    user_created = create_user(user.id, username, referrer_id)  # ПЕРЕДАЕМ referrer_id

    # Если пользователь НОВЫЙ И был реферер, начисляем награду
    if user_created and referrer_id is not None:
        reward_amount, reward_currency = get_referral_reward_settings()
        reward_text = format_referral_reward(reward_amount, reward_currency)
        if reward_currency == 'stars':
            reward_stars = int(reward_amount)
            if reward_stars > 0:
                update_internal_stars(referrer_id, reward_stars)
                add_transaction(
                    user_id=referrer_id,
                    amount=reward_stars,
                    transaction_type='referral_reward_internal',
                    status='completed',
                    target_user=str(user.id)
                )
        else:
            update_balance(referrer_id, reward_amount)
            add_transaction(
                user_id=referrer_id,
                amount=reward_amount,
                transaction_type='referral_reward',
                status='completed',
                target_user=str(user.id)
            )
        # Уведомляем реферера
        try:
            bot.send_message(
                referrer_id,
                f"✅ Награда за реферала!\n\n"
                f"Пользователь @{username or user.id} зарегистрировался по вашей ссылке. На ваш баланс зачислено **{reward_text}**!",
                parse_mode='Markdown',
                reply_markup=back_to_main_keyboard()
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление рефереру {referrer_id}: {e}")

    # --- КОНЕЦ ЛОГИКИ РЕФЕРАЛЬНОЙ ССЫЛКИ ---

    bot.send_photo(
        message.chat.id,
        MAIN_MENU_IMAGE,
        caption=WELCOME_MES,
        reply_markup=main_menu_keyboard(user.id)
    )

@bot.message_handler(commands=['export'])
def handle_export_command(message: Message):
    """Обработчик команды /export для экспорта БД в Excel."""
    user_id = message.from_user.id

    # Проверяем, что команду вызвал админ
    if str(user_id) != ADMIN_ID:
        bot.reply_to(message, "❌ У вас нет прав для выполнения этой команды.", reply_markup=back_to_main_keyboard())
        return

    try:
        # Отправляем сообщение о начале процесса
        processing_msg = bot.reply_to(message, "🔄 Начинаю экспорт базы данных в Excel...", reply_markup=back_to_main_keyboard())

        # Выполняем экспорт
        filename = export_database_to_excel()

        if filename and os.path.exists(filename):
            # Отправляем файл
            with open(filename, 'rb') as file:
                bot.send_document(
                    chat_id=message.chat.id,
                    document=file,
                    caption=f"📊 Экспорт базы данных завершен\nФайл: {filename}",
                    reply_to_message_id=message.message_id,
                    reply_markup=back_to_main_keyboard()
                )

            # УДАЛЯЕМ файл после успешной отправки
            try:
                os.remove(filename)
                logger.info(f"✅ Файл экспорта удален: {filename}")
            except Exception as delete_error:
                logger.error(f"❌ Ошибка удаления файла {filename}: {delete_error}")

            # Удаляем сообщение о процессе
            bot.delete_message(chat_id=message.chat.id, message_id=processing_msg.message_id)

        else:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=processing_msg.message_id,
                text="❌ Не удалось создать файл экспорта.",
                reply_markup=back_to_main_keyboard()
            )

    except Exception as e:
        logger.error(f"Ошибка при выполнении команды /export: {e}")

        # Пытаемся удалить файл даже в случае ошибки отправки
        try:
            if 'filename' in locals() and filename and os.path.exists(filename):
                os.remove(filename)
                logger.info(f"✅ Файл экспорта удален после ошибки: {filename}")
        except Exception as delete_error:
            logger.error(f"❌ Ошибка удаления файла после ошибки отправки: {delete_error}")

        bot.reply_to(message, f"❌ Произошла ошибка при экспорте: {e}", reply_markup=back_to_main_keyboard())


@bot.message_handler(commands=['stats'])
def handle_stats_command(message: Message):
    """Обработчик команды /stats для быстрой статистики."""
    user_id = message.from_user.id

    # Проверяем, что команду вызвал админ
    if str(user_id) != ADMIN_ID:
        bot.reply_to(message, "❌ У вас нет прав для выполнения этой команды.", reply_markup=back_to_main_keyboard())
        return

    try:
        from db import get_setting
        import sqlite3

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        # Быстрая статистика
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM users WHERE referrer_id IS NOT NULL")
        users_with_referrer = cursor.fetchone()[0]

        cursor.execute("SELECT SUM(balance) FROM users")
        total_balance = cursor.fetchone()[0] or 0

        cursor.execute("SELECT SUM(internal_stars) FROM users")
        total_internal_stars = cursor.fetchone()[0] or 0

        cursor.execute("SELECT COUNT(*) FROM transactions WHERE type = 'stars_purchase' AND status = 'completed'")
        stars_transactions = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM payments WHERE status = 'succeeded'")
        successful_payments = cursor.fetchone()[0]

        cursor.execute("SELECT SUM(amount) FROM payments WHERE status = 'succeeded'")
        total_payments = cursor.fetchone()[0] or 0

        ton_rate = get_setting('ton_rub_rate', 'N/A')
        last_rate_update = get_setting('ton_rate_updated_at', 'N/A')
        internal_pool = get_setting('internal_stars_pool', '0')

        conn.close()

        stats_message = (
            "📊 *Статистика бота*\n\n"
            f"👥 *Пользователи:*\n"
            f"• Всего: {total_users}\n"
            f"• С реферерами: {users_with_referrer}\n"
            f"• Общий баланс: {total_balance:.2f} руб\n\n"
            f"💫 *Звезды:*\n"
            f"• Покупок звезд: {stars_transactions}\n\n"
            f"• Внутренние звезды (пул): {internal_pool}\n"
            f"• Внутренние звезды у пользователей: {total_internal_stars}\n\n"
            f"💳 *Платежи:*\n"
            f"• Успешных: {successful_payments}\n"
            f"• Общая сумма: {total_payments:.2f} руб\n\n"
            f"🪙 *Курс TON:*\n"
            f"• Текущий: {ton_rate} RUB\n"
            f"• Обновлен: {last_rate_update[:16] if last_rate_update != 'N/A' else 'N/A'}"
        )

        bot.reply_to(message, stats_message, parse_mode='Markdown', reply_markup=back_to_main_keyboard())

    except Exception as e:
        logger.error(f"Ошибка при выполнении команды /stats: {e}")
        bot.reply_to(message, f"❌ Ошибка получения статистики: {e}", reply_markup=back_to_main_keyboard())

# --- Обработчики колбэков (Меню и Профиль) ---
@bot.callback_query_handler(func=lambda call: call.data == 'buy_stars')
def buy_stars_selection_menu(call: CallbackQuery):
    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Выберите, кому вы хотите купить звёзды:",
        reply_markup=buy_stars_options_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data == 'buy_internal_stars')
def buy_internal_stars_menu(call: CallbackQuery):
    user_id = call.from_user.id
    pool = get_internal_stars_pool()
    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="⭐ Покупка внутренних звезд (1 внутренняя ⭐ = 1 Telegram ⭐)\n\n"
             "💳 Оплата списывается Telegram Stars\n"
             ,
        reply_markup=buy_internal_stars_quantity_keyboard()
    )


def deposit_keyboard(user_data):
    keyboard = InlineKeyboardMarkup()
    amounts = [50, 100, 500, 1000]
    for amount in amounts:
        keyboard.row(InlineKeyboardButton(f"{amount} руб (ЮKassa)", callback_data=f'deposit_{amount}'))

    # Добавляем TON пополнение
    keyboard.row(InlineKeyboardButton("🪙 Пополнить TON", callback_data='deposit_ton'))

    # Добавляем кнопку для ввода кастомной суммы (ЮKassa)
    keyboard.row(InlineKeyboardButton("✍️ Другая сумма (ЮKassa)", callback_data='deposit_custom'))
    keyboard.row(InlineKeyboardButton("↩️ Назад", callback_data='main_menu'))
    return keyboard


@bot.callback_query_handler(func=lambda call: call.data == 'deposit')
def deposit_menu(call: CallbackQuery):
    user_id = call.from_user.id
    user_data = get_user(user_id)
    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="💳 Выберите способ пополнения и сумму:\n\n"
             f"💰 Текущий баланс: {user_data['balance']:.2f} руб",
        reply_markup=deposit_keyboard(user_data)
    )


@bot.callback_query_handler(func=lambda call: call.data == 'profile')
def show_profile(call: CallbackQuery):
    user_id = call.from_user.id
    user_data = get_user(user_id)
    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"👤 Ваш профиль:\n\n"
             f"🆔 ID: {user_data['user_id']}\n"
             f"👤 Username: @{user_data['username'] or 'Не указан'}\n"
             f"💰 Баланс: {user_data['balance']:.2f} руб\n"
             f"⭐ Внутренние звезды: {user_data.get('internal_stars', 0)}\n",
        reply_markup=back_to_main_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data == 'referrals_menu')
def show_referrals_menu(call: CallbackQuery):
    user_id = call.from_user.id

    # Получаем никнейм бота для генерации ссылки
    bot_username = bot.get_me().username
    referral_link = f"https://t.me/{bot_username}?start=r{user_id}"

    # Получаем количество рефералов
    referral_count = get_referral_count(user_id)

    # Создаем клавиатуру для меню рефералов
    referral_keyboard = InlineKeyboardMarkup()
    referral_keyboard.row(InlineKeyboardButton("↩️ Назад", callback_data='main_menu'))

    reward_amount, reward_currency = get_referral_reward_settings()
    reward_text = format_referral_reward(reward_amount, reward_currency)
    reward_target = "на баланс" if reward_currency == 'rub' else "на баланс внутренних звезд"
    caption = (
        f"🔗 **Реферальная программа**\n\n"
        f"Приглашайте друзей и получайте вознаграждение!\n"
        f"🎁 За каждого приглашенного пользователя, который запустит бота, вы получаете **{reward_text}** {reward_target}.\n\n"
        f"👤 Количество ваших рефералов: **{referral_count}**\n\n"
        f"**Ваша реферальная ссылка:**\n"
        f"`{referral_link}`"
    )

    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=caption,
        reply_markup=referral_keyboard,
        parse_mode='Markdown'
    )

@bot.callback_query_handler(func=lambda call: call.data == 'main_menu')
def main_menu_callback(call: CallbackQuery):
    delete_session_data(call.from_user.id)  # Очищаем сессию при возврате в меню
    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=WELCOME_MES,
        reply_markup=main_menu_keyboard(call.from_user.id)
    )


@bot.callback_query_handler(func=lambda call: call.data == 'grant_internal_50')
def grant_internal_50(call: CallbackQuery):
    user_id = call.from_user.id
    if str(user_id) != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ Доступно только администратору.", show_alert=True)
        return

    update_internal_stars(user_id, 50)
    update_internal_stars_pool(50)
    add_transaction(user_id, 50, 'internal_stars_grant', status='completed', target_user='test_grant')
    bot.answer_callback_query(call.id, "✅ Начислено 50 внутренних ⭐", show_alert=True)


def calculator_result_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.row(InlineKeyboardButton("🧮 Еще раз", callback_data='calculator'))
    keyboard.row(InlineKeyboardButton("↩️ Главное меню", callback_data='main_menu'))
    return keyboard


def admin_menu_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.row(InlineKeyboardButton("🎁 Реферальная программа", callback_data='admin_referral_settings'))
    keyboard.row(InlineKeyboardButton("⭐ Цена Telegram Stars", callback_data='admin_star_price'))
    keyboard.row(InlineKeyboardButton("↩️ Главное меню", callback_data='main_menu'))
    return keyboard


def admin_referral_settings_keyboard(active_currency):
    keyboard = InlineKeyboardMarkup()
    keyboard.row(InlineKeyboardButton("✏️ Изменить размер", callback_data='admin_referral_amount'))
    if active_currency == 'rub':
        keyboard.row(InlineKeyboardButton("✅ Начислять в рублях", callback_data='admin_referral_currency_rub'))
        keyboard.row(InlineKeyboardButton("⭐ Начислять внутренними звездами", callback_data='admin_referral_currency_stars'))
    else:
        keyboard.row(InlineKeyboardButton("💰 Начислять в рублях", callback_data='admin_referral_currency_rub'))
        keyboard.row(InlineKeyboardButton("✅ Начислять внутренними звездами", callback_data='admin_referral_currency_stars'))
    keyboard.row(InlineKeyboardButton("↩️ Назад", callback_data='admin_menu'))
    keyboard.row(InlineKeyboardButton("↩️ Главное меню", callback_data='main_menu'))
    return keyboard


def admin_referral_amount_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.row(InlineKeyboardButton("↩️ Назад", callback_data='admin_referral_settings'))
    keyboard.row(InlineKeyboardButton("↩️ Главное меню", callback_data='main_menu'))
    return keyboard


def admin_star_price_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.row(InlineKeyboardButton("↩️ Назад", callback_data='admin_menu'))
    keyboard.row(InlineKeyboardButton("↩️ Главное меню", callback_data='main_menu'))
    return keyboard


@bot.message_handler(commands=['admin'])
def handle_admin_command(message: Message):
    user_id = message.from_user.id
    if str(user_id) != ADMIN_ID:
        bot.reply_to(message, "❌ У вас нет прав для выполнения этой команды.", reply_markup=back_to_main_keyboard())
        return
    bot.send_message(
        message.chat.id,
        "⚙️ Админка\n\nВыберите раздел:",
        reply_markup=admin_menu_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data == 'admin_menu')
def show_admin_menu(call: CallbackQuery):
    user_id = call.from_user.id
    if str(user_id) != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ Доступно только администратору.", show_alert=True)
        return
    text = "⚙️ Админка\n\nВыберите раздел:"
    if getattr(call.message, 'photo', None):
        bot.edit_message_caption(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            caption=text,
            reply_markup=admin_menu_keyboard()
        )
    else:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            reply_markup=admin_menu_keyboard()
        )


@bot.callback_query_handler(func=lambda call: call.data == 'admin_referral_settings')
def show_admin_referral_settings(call: CallbackQuery):
    user_id = call.from_user.id
    if str(user_id) != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ Доступно только администратору.", show_alert=True)
        return
    reward_amount, reward_currency = get_referral_reward_settings()
    reward_text = format_referral_reward(reward_amount, reward_currency)
    reward_target = "на баланс" if reward_currency == 'rub' else "на баланс внутренних звезд"
    text = (
        "🎁 Реферальная программа\n\n"
        f"Текущая награда: **{reward_text}** {reward_target}\n\n"
        "Что хотите изменить?"
    )
    keyboard = admin_referral_settings_keyboard(reward_currency)
    if getattr(call.message, 'photo', None):
        bot.edit_message_caption(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            caption=text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    else:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )


@bot.callback_query_handler(func=lambda call: call.data == 'admin_referral_amount')
def prompt_admin_referral_amount(call: CallbackQuery):
    user_id = call.from_user.id
    if str(user_id) != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ Доступно только администратору.", show_alert=True)
        return
    reward_amount, reward_currency = get_referral_reward_settings()
    reward_text = format_referral_reward(reward_amount, reward_currency)
    reward_target = "в рублях" if reward_currency == 'rub' else "во внутренних звездах"
    session_data = {
        'state': 'admin_referral_amount',
        'message_id': call.message.message_id
    }
    set_session_data(user_id, session_data)
    text = (
        "✏️ Изменение награды за реферала\n\n"
        f"Текущая награда: **{reward_text}**\n"
        f"Введите новую сумму ({reward_target}):"
    )
    if getattr(call.message, 'photo', None):
        bot.edit_message_caption(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            caption=text,
            reply_markup=admin_referral_amount_keyboard(),
            parse_mode='Markdown'
        )
    else:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=text,
            reply_markup=admin_referral_amount_keyboard(),
            parse_mode='Markdown'
        )
    bot.register_next_step_handler(call.message, process_admin_referral_amount)


def process_admin_referral_amount(message: Message):
    user_id = message.from_user.id
    amount_input = message.text.strip().replace(',', '.')

    state_data = get_session_data(user_id)
    target_message_id = state_data.get('message_id')

    if state_data.get('state') != 'admin_referral_amount' or not target_message_id:
        return

    try:
        if message.message_id != target_message_id:
            bot.delete_message(message.chat.id, message.message_id)
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    reward_amount, reward_currency = get_referral_reward_settings()
    reward_target = "в рублях" if reward_currency == 'rub' else "во внутренних звездах"

    try:
        amount = float(amount_input)
        if amount <= 0:
            raise ValueError
        if reward_currency == 'stars' and not amount.is_integer():
            raise ValueError
    except ValueError:
        text = (
            "❌ Некорректная сумма. "
            f"Введите число больше 0 ({reward_target})."
        )
        edit_message_with_fallback(
            chat_id=message.chat.id,
            message_id=target_message_id,
            text=text,
            reply_markup=admin_referral_amount_keyboard()
        )
        bot.register_next_step_handler(message, process_admin_referral_amount)
        return

    if reward_currency == 'stars':
        amount = int(amount)

    set_setting('referral_reward_amount', amount)
    delete_session_data(user_id)

    reward_text = format_referral_reward(amount, reward_currency)
    reward_target = "на баланс" if reward_currency == 'rub' else "на баланс внутренних звезд"
    text = (
        "✅ Награда обновлена!\n\n"
        f"Теперь за реферала начисляется **{reward_text}** {reward_target}."
    )
    edit_message_with_fallback(
        chat_id=message.chat.id,
        message_id=target_message_id,
        text=text,
        reply_markup=admin_referral_settings_keyboard(reward_currency),
        parse_mode='Markdown'
    )


@bot.callback_query_handler(func=lambda call: call.data in ('admin_referral_currency_rub', 'admin_referral_currency_stars'))
def update_admin_referral_currency(call: CallbackQuery):
    user_id = call.from_user.id
    if str(user_id) != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ Доступно только администратору.", show_alert=True)
        return
    new_currency = 'rub' if call.data.endswith('rub') else 'stars'
    set_setting('referral_reward_currency', new_currency)
    if new_currency == 'stars':
        amount_raw = get_setting('referral_reward_amount', REFERRAL_REWARD)
        try:
            amount_value = float(amount_raw)
        except (TypeError, ValueError):
            amount_value = float(REFERRAL_REWARD)
        if not amount_value.is_integer():
            bot.answer_callback_query(
                call.id,
                "⚠️ Для начисления звездами нужна целая сумма. Задайте размер.",
                show_alert=True
            )
    show_admin_referral_settings(call)


@bot.callback_query_handler(func=lambda call: call.data == 'admin_star_price')
def prompt_admin_star_price(call: CallbackQuery):
    user_id = call.from_user.id
    if str(user_id) != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ Доступно только администратору.", show_alert=True)
        return
    star_price = get_star_price()
    session_data = {
        'state': 'admin_star_price',
        'message_id': call.message.message_id
    }
    set_session_data(user_id, session_data)
    text = (
        "⭐ Цена Telegram Stars\n\n"
        f"Текущая цена: **{star_price:.2f} руб**\n"
        "Введите новую цену за 1 ⭐:"
    )
    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=text,
        reply_markup=admin_star_price_keyboard(),
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(call.message, process_admin_star_price)


def process_admin_star_price(message: Message):
    user_id = message.from_user.id
    amount_input = message.text.strip().replace(',', '.')

    state_data = get_session_data(user_id)
    target_message_id = state_data.get('message_id')

    if state_data.get('state') != 'admin_star_price' or not target_message_id:
        return

    try:
        if message.message_id != target_message_id:
            bot.delete_message(message.chat.id, message.message_id)
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    try:
        amount = float(amount_input)
        if amount <= 0:
            raise ValueError
    except ValueError:
        text = "❌ Некорректная цена. Введите число больше 0:"
        edit_message_with_fallback(
            chat_id=message.chat.id,
            message_id=target_message_id,
            text=text,
            reply_markup=admin_star_price_keyboard()
        )
        bot.register_next_step_handler(message, process_admin_star_price)
        return

    set_star_price(amount)
    delete_session_data(user_id)

    text = f"✅ Цена обновлена! Теперь 1 ⭐ = **{amount:.2f} руб**."
    edit_message_with_fallback(
        chat_id=message.chat.id,
        message_id=target_message_id,
        text=text,
        reply_markup=admin_menu_keyboard(),
        parse_mode='Markdown'
    )


@bot.callback_query_handler(func=lambda call: call.data == 'calculator')
def show_calculator_menu(call: CallbackQuery):
    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="🧮 Калькулятор\n\nВыберите, что нужно посчитать:",
        reply_markup=calculator_menu_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('calc_'))
def handle_calculator_choice(call: CallbackQuery):
    user_id = call.from_user.id
    calc_type = call.data

    prompt_map = {
        'calc_rub_to_stars': "Введите сумму в рублях:",
        'calc_stars_to_rub': "Введите количество звезд:",
        'calc_ton_to_rub': "Введите сумму в TON:",
        'calc_rub_to_ton': "Введите сумму в рублях:",
        'calc_ton_to_stars': "Введите сумму в TON:",
        'calc_stars_to_ton': "Введите количество звезд:"
    }
    prompt = prompt_map.get(calc_type, "Введите значение:")

    session_data = {
        'state': 'calculator_wait_amount',
        'message_id': call.message.message_id,
        'target_username': calc_type
    }
    set_session_data(user_id, session_data)

    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=f"🧮 Калькулятор\n\n{prompt}",
        reply_markup=back_to_main_keyboard()
    )
    bot.register_next_step_handler(call.message, process_calculator_amount)


def process_calculator_amount(message: Message):
    user_id = message.from_user.id
    amount_input = message.text.strip().replace(',', '.')

    state_data = get_session_data(user_id)
    target_message_id = state_data.get('message_id')
    calc_type = state_data.get('target_username')

    if state_data.get('state') != 'calculator_wait_amount' or not target_message_id or not calc_type:
        return

    try:
        if message.message_id != target_message_id:
            bot.delete_message(message.chat.id, message.message_id)
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    try:
        amount = float(amount_input)
        if amount <= 0:
            raise ValueError
    except ValueError:
        edit_message_with_fallback(
            chat_id=message.chat.id,
            message_id=target_message_id,
            text="❌ Некорректное значение. Введите число больше 0:",
            reply_markup=back_to_main_keyboard()
        )
        bot.register_next_step_handler(message, process_calculator_amount)
        return

    ton_rate = None
    if calc_type in ('calc_ton_to_rub', 'calc_rub_to_ton', 'calc_ton_to_stars', 'calc_stars_to_ton'):
        ton_rate = get_ton_rub_rate()
        if not ton_rate:
            edit_message_with_fallback(
                chat_id=message.chat.id,
                message_id=target_message_id,
                text="❌ Курс TON сейчас недоступен. Попробуйте позже.",
                reply_markup=calculator_result_keyboard()
            )
            delete_session_data(user_id)
            return

    star_price = get_star_price()
    if calc_type == 'calc_rub_to_stars':
        stars = amount / star_price
        caption = (
            "🧮 Рубли → ⭐\n\n"
            f"Сумма: {amount:.2f} руб\n"
            f"Цена звезды: {star_price:.2f} руб\n\n"
            f"Получите: ~{stars:.2f} ⭐"
        )
    elif calc_type == 'calc_stars_to_rub':
        rub = amount * star_price
        caption = (
            "🧮 ⭐ → Рубли\n\n"
            f"Количество звезд: {amount:.2f} ⭐\n"
            f"Цена звезды: {star_price:.2f} руб\n\n"
            f"Нужно заплатить: {rub:.2f} руб"
        )
    elif calc_type == 'calc_ton_to_rub':
        rub = amount * ton_rate
        caption = (
            "🧮 TON → Рубли\n\n"
            f"Сумма: {amount:.6f} TON\n"
            f"Курс: 1 TON ≈ {ton_rate:.2f} руб\n\n"
            f"Получите: {rub:.2f} руб"
        )
    elif calc_type == 'calc_rub_to_ton':
        ton = amount / ton_rate
        caption = (
            "🧮 Рубли → TON\n\n"
            f"Сумма: {amount:.2f} руб\n"
            f"Курс: 1 TON ≈ {ton_rate:.2f} руб\n\n"
            f"Нужно отправить: {ton:.6f} TON"
        )
    elif calc_type == 'calc_ton_to_stars':
        stars = (amount * ton_rate) / star_price
        caption = (
            "🧮 TON → ⭐\n\n"
            f"Сумма: {amount:.6f} TON\n"
            f"Курс: 1 TON ≈ {ton_rate:.2f} руб\n"
            f"Цена звезды: {star_price:.2f} руб\n\n"
            f"Получите: ~{stars:.2f} ⭐"
        )
    elif calc_type == 'calc_stars_to_ton':
        ton = (amount * star_price) / ton_rate
        caption = (
            "🧮 ⭐ → TON\n\n"
            f"Количество звезд: {amount:.2f} ⭐\n"
            f"Цена звезды: {star_price:.2f} руб\n"
            f"Курс: 1 TON ≈ {ton_rate:.2f} руб\n\n"
            f"Нужно отправить: {ton:.6f} TON"
        )
    else:
        caption = "❌ Неизвестный тип расчета."

    edit_message_with_fallback(
        chat_id=message.chat.id,
        message_id=target_message_id,
        text=caption,
        reply_markup=calculator_result_keyboard()
    )
    delete_session_data(user_id)


# --- Покупка звезд (логика остается прежней) ---
@bot.callback_query_handler(func=lambda call: call.data == 'buy_stars_self')
def buy_stars_self(call: CallbackQuery):
    user_id = call.from_user.id
    user_data = get_user(user_id)

    # Сохраняем собственный username и ID сообщения в БД
    session_data = {
        'target_username': user_data['username'],
        'state': 'buying_stars',
        'message_id': call.message.message_id
    }
    set_session_data(user_id, session_data)

    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="🎯 Выберите количество звезд для покупки:\n\n"
             f"💰 Ваш баланс: {user_data['balance']:.2f} руб",
        reply_markup=buy_stars_quantity_keyboard(user_data)
    )


@bot.callback_query_handler(func=lambda call: call.data == 'buy_stars_friend')
def buy_stars_friend(call: CallbackQuery):
    user_id = call.from_user.id

    # Сохраняем состояние ожидания username и ID сообщения в БД
    session_data = {
        'state': 'waiting_for_username',
        'message_id': call.message.message_id,
        'target_username': None  # Сбрасываем предыдущего получателя
    }
    set_session_data(user_id, session_data)

    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Пожалуйста, введите @username друга (без @):",
        reply_markup=back_to_main_keyboard()
    )
    bot.register_next_step_handler(call.message, process_friend_username)


def process_friend_username(message: Message):
    user_id = message.from_user.id
    username_input = message.text.strip().lstrip('@')

    # Получаем состояние из БД
    state_data = get_session_data(user_id)
    target_message_id = state_data.get('message_id')

    # Проверка состояния
    if state_data.get('state') != 'waiting_for_username' or not target_message_id:
        return  # Игнорируем, если не в режиме ожидания username

    try:
        # Удаляем сообщение пользователя, чтобы не засорять чат
        if message.message_id != target_message_id:
            bot.delete_message(message.chat.id, message.message_id)
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    if not username_input:
        edit_message_with_fallback(
            chat_id=message.chat.id,
            message_id=target_message_id,
            text="❌ Некорректный username. Попробуйте еще раз:",
            reply_markup=back_to_main_keyboard()
        )
        bot.register_next_step_handler(message, process_friend_username)
        return

    # Обновляем сессию в БД: сохраняем получателя и сбрасываем состояние ожидания
    session_data = {
        'target_username': username_input,
        'state': 'buying_stars',
        'message_id': target_message_id
    }
    set_session_data(user_id, session_data)

    user_data = get_user(user_id)

    # ИСПРАВЛЕНИЕ: Экранируем username для корректного отображения в Markdown
    escaped_username = username_input.replace('_', r'\_').replace('*', r'\*').replace('`', r'\`')

    edit_message_with_fallback(
        chat_id=message.chat.id,
        message_id=target_message_id,
        text=f"Вы будете покупать звёзды для пользователя **@{escaped_username}**. Выберите количество:",
        reply_markup=buy_stars_quantity_keyboard(user_data),
        parse_mode='Markdown'
    )


def execute_star_purchase(call, stars):
    user_id = call.from_user.id
    user_data = get_user(user_id)
    star_price = get_star_price()
    cost = stars * star_price

    # Получаем целевой username из БД
    session_data = get_session_data(user_id)
    target_username = session_data.get('target_username')

    if not target_username:
        if getattr(call, 'id', None):
            bot.answer_callback_query(
                call.id,
                "❌ Не удалось определить получателя. Пожалуйста, начните заново.",
                show_alert=True
            )
        edit_message_with_fallback(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="❌ Не удалось определить получателя. Начните заново.",
            reply_markup=back_to_main_keyboard()
        )
        return

    if user_data['balance'] < cost:
        if getattr(call, 'id', None):
            bot.answer_callback_query(call.id, f"❌ Недостаточно средств. Нужно {cost:.2f} руб.", show_alert=True)
        else:
            edit_message_with_fallback(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"❌ Недостаточно средств. Нужно {cost:.2f} руб.",
                reply_markup=back_to_main_keyboard()
            )
        return

    # Запуск анимации
    global animation_running
    animation_running = True
    animation_thread = threading.Thread(target=animate_caption, args=(bot, call))
    animation_thread.start()

    try:
        token = load_fragment_token() or authenticate_fragment()
        if not token:
            animation_running = False
            edit_message_with_fallback(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="❌ Ошибка системы. Не удалось получить токен Fragment API. Попробуйте позже.",
                reply_markup=back_to_main_keyboard()
            )
            return

        success, message = send_stars(token, target_username, stars)

        animation_running = False
        animation_thread.join()

        if success:
            update_balance(user_data['user_id'], -cost)
            add_transaction(user_data['user_id'], stars, 'stars_purchase', target_user=target_username)
            user_data_new = get_user(user_id)

            edit_message_with_fallback(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=f"✅ Успешно отправлено {stars} звезд пользователю **@{target_username}**!\n"
                     f"💰 Ваш новый баланс: {user_data_new['balance']:.2f} руб",
                reply_markup=back_to_main_keyboard(),
                parse_mode='Markdown'
            )
        else:
            if "not enough funds" in message.lower() or "баланс" in message.lower():
                error_message = "❌ У нас закончились звезды. Попробуйте позже."
            else:
                error_message = f"❌ Ошибка при отправке: {message}"

            edit_message_with_fallback(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=error_message,
                reply_markup=back_to_main_keyboard()
            )
    finally:
        # Очищаем состояние после завершения
        delete_session_data(user_id)


@bot.callback_query_handler(func=lambda call: call.data == 'buy_custom')
def prompt_custom_stars_amount(call: CallbackQuery):
    user_id = call.from_user.id
    session_data = get_session_data(user_id)
    if not session_data.get('target_username'):
        bot.answer_callback_query(call.id, "❌ Не удалось определить получателя. Начните заново.", show_alert=True)
        main_menu_callback(call)
        return

    session_data.update({
        'state': 'buy_custom_stars',
        'message_id': call.message.message_id
    })
    set_session_data(user_id, session_data)

    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Введите количество звезд (от 1 до 10000):",
        reply_markup=back_to_main_keyboard()
    )
    bot.register_next_step_handler(call.message, process_custom_stars_amount)


def process_custom_stars_amount(message: Message):
    user_id = message.from_user.id
    amount_input = message.text.strip()

    state_data = get_session_data(user_id)
    target_message_id = state_data.get('message_id')

    if state_data.get('state') != 'buy_custom_stars' or not target_message_id:
        return

    try:
        if message.message_id != target_message_id:
            bot.delete_message(message.chat.id, message.message_id)
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    try:
        stars = int(amount_input)
        if stars < 1 or stars > 10000:
            raise ValueError
    except ValueError:
        edit_message_with_fallback(
            chat_id=message.chat.id,
            message_id=target_message_id,
            text="❌ Некорректное количество. Введите число от 1 до 10000:",
            reply_markup=back_to_main_keyboard()
        )
        bot.register_next_step_handler(message, process_custom_stars_amount)
        return

    call_mock = type('MockCall', (object,), {
        'id': None,
        'from_user': message.from_user,
        'message': type('MockMessage', (object,), {
            'chat': type('MockChat', (object,), {'id': message.chat.id})(),
            'message_id': target_message_id
        })()
    })()

    execute_star_purchase(call_mock, stars)


@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_') and not call.data.startswith('buy_internal_'))
def handle_star_purchase(call: CallbackQuery):
    stars = int(call.data.split('_')[1])
    execute_star_purchase(call, stars)


@bot.pre_checkout_query_handler(func=lambda query: True)
def handle_pre_checkout_query(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@bot.message_handler(content_types=['successful_payment'])
def handle_successful_payment(message: Message):
    payment = message.successful_payment
    if payment.currency != "XTR":
        return

    payload = payment.invoice_payload or ""
    if not payload.startswith("internal_stars:"):
        return

    try:
        _, user_id_str, stars_str = payload.split(":")
        user_id = int(user_id_str)
        stars = int(stars_str)
    except Exception:
        logger.error("Некорректный payload успешного платежа Stars.")
        return

    if message.from_user.id != user_id:
        logger.error("Платеж Stars не соответствует пользователю.")
        return

    if payment.total_amount != stars:
        logger.error("Сумма Stars не совпадает с запрошенным количеством.")
        return

    update_internal_stars_pool(stars)
    update_internal_stars(user_id, stars)
    add_transaction(user_id, stars, 'internal_stars_purchase', status='completed',
                    target_user=f"stars_payment:{payment.telegram_payment_charge_id}")

    user_data_new = get_user(user_id)
    pool_new = get_internal_stars_pool()

    bot.send_message(
        message.chat.id,
        "✅ Оплата Telegram Stars успешна!\n\n"
        f"⭐ Получено: {stars}\n"
        f"⭐ Ваши внутренние звезды: {user_data_new.get('internal_stars', 0)}\n"
       ,
        reply_markup=back_to_main_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data == 'buy_internal_custom')
def prompt_custom_internal_stars(call: CallbackQuery):
    user_id = call.from_user.id
    session_data = {
        'state': 'buy_custom_internal_stars',
        'message_id': call.message.message_id
    }
    set_session_data(user_id, session_data)
    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Введите количество внутренних звезд (от 1 до 10000):",
        reply_markup=back_to_main_keyboard()
    )
    bot.register_next_step_handler(call.message, process_custom_internal_stars_amount)


def process_custom_internal_stars_amount(message: Message):
    user_id = message.from_user.id
    amount_input = message.text.strip()

    state_data = get_session_data(user_id)
    target_message_id = state_data.get('message_id')

    if state_data.get('state') != 'buy_custom_internal_stars' or not target_message_id:
        return

    try:
        if message.message_id != target_message_id:
            bot.delete_message(message.chat.id, message.message_id)
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    try:
        stars = int(amount_input)
        if stars < 1 or stars > 10000:
            raise ValueError
    except ValueError:
        edit_message_with_fallback(
            chat_id=message.chat.id,
            message_id=target_message_id,
            text="❌ Некорректное количество. Введите число от 1 до 10000:",
            reply_markup=back_to_main_keyboard()
        )
        bot.register_next_step_handler(message, process_custom_internal_stars_amount)
        return

    delete_session_data(user_id)

    payload = f"internal_stars:{user_id}:{stars}"
    prices = [LabeledPrice(label=f"{stars} Telegram Stars", amount=stars)]

    bot.send_invoice(
        message.chat.id,
        title="Покупка внутренних звезд",
        description=f"Оплата {stars} Telegram Stars за {stars} внутренних ⭐",
        invoice_payload=payload,
        provider_token="",
        currency="XTR",
        prices=prices
    )

    edit_message_with_fallback(
        chat_id=message.chat.id,
        message_id=target_message_id,
        text="✅ Счет на оплату отправлен. Проверьте сообщение с оплатой.",
        reply_markup=back_to_main_keyboard()
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_internal_') and call.data != 'buy_internal_custom')
def handle_internal_star_purchase(call: CallbackQuery):
    stars = int(call.data.split('_')[2])
    user_id = call.from_user.id

    payload = f"internal_stars:{user_id}:{stars}"
    prices = [LabeledPrice(label=f"{stars} Telegram Stars", amount=stars)]

    bot.send_invoice(
        call.message.chat.id,
        title="Покупка внутренних звезд",
        description=f"Оплата {stars} Telegram Stars за {stars} внутренних ⭐",
        invoice_payload=payload,
        provider_token="",
        currency="XTR",
        prices=prices
    )


@bot.callback_query_handler(func=lambda call: call.data == 'deposit_ton')
def handle_ton_deposit(call: CallbackQuery):
    user_id = call.from_user.id

    if not TON_DEPOSIT_ADDRESS:
        bot.answer_callback_query(call.id, "❌ Пополнение TON временно недоступно (адрес не указан).", show_alert=True)
        return

    # Получаем курс из БД (кэшированный)
    ton_rub_rate = get_ton_rub_rate()
    rate_text = f"~{ton_rub_rate:.2f} руб" if ton_rub_rate else "курс недоступен"

    # Добавляем информацию о времени обновления курса
    last_updated = get_ton_rate_updated_at()
    if last_updated:
        last_updated_dt = datetime.fromisoformat(last_updated)
        update_info = f" (обновлен {last_updated_dt.strftime('%H:%M')})"
    else:
        update_info = ""

    # URL для быстрой оплаты
    payment_url = f'ton://transfer/{TON_DEPOSIT_ADDRESS}?text={user_id}'

    caption = (
        f"🪙 Пополнение через TON:\n\n"
        f"1. Переведите любую сумму TON на этот адрес:\n"
        f"   `{TON_DEPOSIT_ADDRESS}`\n\n"
        f"2. **Обязательно** укажите в комментарии свой ID:\n"
        f"   `{user_id}`\n\n"
        f"💰 Текущий курс: 1 TON ≈ {rate_text}{update_info}\n"
        f"⚠️ Средства будут зачислены на ваш баланс в **РУБЛЯХ** после 3 подтверждений сети."
    )

    keyboard = InlineKeyboardMarkup()
    keyboard.row(InlineKeyboardButton("↗️ Оплатить в TON", url=payment_url))
    keyboard.row(InlineKeyboardButton("↩️ Назад", callback_data='deposit'))
    keyboard.row(InlineKeyboardButton("↩️ Главное меню", callback_data='main_menu'))

    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=caption,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )


# --- Пополнение ЮKassa (логика остается прежней) ---
@bot.callback_query_handler(
    func=lambda call: call.data.startswith('deposit_') and call.data != 'deposit_custom' and call.data != 'deposit_ton')
def handle_predefined_deposit(call: CallbackQuery):
    amount = int(call.data.split('_')[1])
    process_deposit(call, amount, 'yookassa')


def send_admin_deposit_notification(user, amount_rub, deposit_type, status, ton_amount=None):
    """Отправляет уведомление администратору о пополнении баланса."""
    try:
        admin_id = ADMIN_ID
        if not admin_id:
            logger.warning("ADMIN_ID не установлен. Уведомления администратора не будут отправляться.")
            return

        # Формируем текст уведомления в зависимости от типа пополнения
        if deposit_type == 'ton':
            type_text = "TON"
            amount_info = f"{ton_amount:.4f} TON ({amount_rub:.2f} руб)"
        else:
            type_text = "ЮKassa"
            amount_info = f"{amount_rub:.2f} руб"

        status_text = "создан" if status == 'created' else "завершен"

        message = (
            f"💰 *Пополнение баланса {status_text}*\n\n"
            f"👤 *Пользователь:*\n"
            f"   ID: `{user.id}`\n"
            f"   Username: @{user.username or 'не указан'}\n"
            f"   Имя: {getattr(user, 'first_name', 'не указано')}\n\n"
            f"💳 *Детали пополнения:*\n"
            f"   Способ: {type_text}\n"
            f"   Сумма: {amount_info}\n"
            f"   Статус: {status_text}"
        )

        bot.send_message(
            admin_id,
            message,
            parse_mode='Markdown',
            reply_markup=back_to_main_keyboard()
        )
        logger.info(f"Уведомление отправлено администратору {admin_id} о пополнении пользователя {user.id}")

    except Exception as e:
        logger.error(f"Ошибка отправки уведомления администратору: {e}")


@bot.callback_query_handler(func=lambda call: call.data == 'deposit_custom')
def handle_custom_deposit(call: CallbackQuery):
    user_id = call.from_user.id

    # Сохраняем состояние ожидания суммы и ID сообщения в БД
    session_data = {
        'state': 'waiting_for_deposit_amount',
        'message_id': call.message.message_id
    }
    set_session_data(user_id, session_data)

    edit_message_with_fallback(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="💰 На какую сумму хотите пополнить?",
        reply_markup=back_to_main_keyboard()
    )
    bot.register_next_step_handler(call.message, process_custom_deposit_amount)


def process_custom_deposit_amount(message: Message):
    user_id = message.from_user.id
    amount_input = message.text.strip()

    # Получаем состояние из БД
    state_data = get_session_data(user_id)
    target_message_id = state_data.get('message_id')

    # Проверка состояния
    if state_data.get('state') != 'waiting_for_deposit_amount' or not target_message_id:
        return

    try:
        # Удаляем сообщение пользователя, чтобы не засорять чат
        if message.message_id != target_message_id and target_message_id:
            bot.delete_message(message.chat.id, message.message_id)
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    try:
        amount = round(float(amount_input), 2)
        if amount <= 0:
            raise ValueError
    except ValueError:
        if target_message_id:
            edit_message_with_fallback(
                chat_id=message.chat.id,
                message_id=target_message_id,
                text="❌ Некорректная сумма. Пожалуйста, введите число больше 0:",
                reply_markup=back_to_main_keyboard()
            )
            bot.register_next_step_handler(message, process_custom_deposit_amount)
            return
        else:
            bot.send_message(
                message.chat.id,
                "❌ Некорректная сумма. Пожалуйста, начните заново.",
                reply_markup=back_to_main_keyboard()
            )
            return

    # --- ИСПРАВЛЕНИЕ БАГА: Создание объекта-заглушки (MockCall) вместо CallbackQuery ---
    call_mock = type('MockCall', (object,), {
        'id': 'mock_id',
        'from_user': message.from_user,
        'message': type('MockMessage', (object,), {
            'chat': type('MockChat', (object,), {'id': message.chat.id})(),
            'message_id': target_message_id
        })()
    })()

    # Создаем и обрабатываем платеж
    process_deposit(call_mock, amount, 'yookassa_custom')

    # Удаляем состояние после завершения
    delete_session_data(user_id)


def process_deposit(call, amount: float, deposit_type='yookassa'):
    bot_username = bot.get_me().username
    payment_url = create_yookassa_payment(amount, call.from_user.id, bot_username)

    if payment_url:
        keyboard = InlineKeyboardMarkup()
        keyboard.row(InlineKeyboardButton("✅ Я оплатил", callback_data='check_payment'))
        keyboard.row(InlineKeyboardButton("↩️ Главное меню", callback_data='main_menu'))

        safe_edit_message_caption(
            bot,
            call.message.chat.id,
            call.message.message_id,
            f"💳 Для пополнения на **{amount:.2f} руб**:\n\n"
            f"1. Перейдите по ссылке: {payment_url}\n"
            f"2. Оплатите счет\n"
            f"3. Нажмите кнопку '✅ Я оплатил'\n\n"
            "⚠️ Платеж обрабатывается автоматически в течение нескольких минут.",
            keyboard,
            parse_mode='Markdown'
        )
    else:
        # Просто уведомляем пользователя об ошибке без изменения сообщения
        try:
            if hasattr(call, 'id'):
                bot.answer_callback_query(call.id, "❌ Ошибка создания платежа! Попробуйте позже.", show_alert=True)
        except Exception as e:
            logger.error(f"Ошибка при ответе на callback: {e}")

        # НЕ возвращаемся в deposit_menu, чтобы избежать конфликта изменений



@bot.callback_query_handler(func=lambda call: call.data == 'check_payment')
def handle_check_payment(call: CallbackQuery):
    user_id = call.from_user.id

    payment = get_pending_payment(user_id)

    if not payment:
        bot.answer_callback_query(call.id, "❌ Активный платеж для проверки не найден", show_alert=True)
        return

    payment_id, amount = payment
    payment_info = check_payment_status(payment_id)

    if not payment_info:
        bot.answer_callback_query(call.id, "❌ Ошибка проверки платежа", show_alert=True)
        return

    if payment_info['status'] == 'succeeded':
        # Обновление статуса платежа
        update_payment_status(payment_id, 'succeeded')

        # Обновление баланса и добавление транзакции
        update_balance(user_id, amount)
        add_transaction(user_id, amount, 'deposit', 'completed')

        user_data = get_user(user_id)

        # Отправляем уведомление администратору об успешном пополнении
        send_admin_deposit_notification(call.from_user, amount, 'yookassa', 'completed')

        edit_message_with_fallback(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"✅ Платеж успешно завершен!\n"
                 f"💳 Сумма: **{amount:.2f} руб**\n"
                 f"💰 Новый баланс: **{user_data['balance']:.2f} руб**",
            reply_markup=back_to_main_keyboard(),
            parse_mode='Markdown'
        )

    elif payment_info['status'] == 'pending':
        bot.answer_callback_query(
            call.id,
            "⌛ Платеж еще не прошел. Попробуйте проверить позже.",
            show_alert=True
        )
    else:
        # Платеж не прошел (например, canceled, expired, etc.)
        update_payment_status(payment_id, payment_info['status'])
        bot.answer_callback_query(
            call.id,
            f"❌ Платеж не прошел. Статус: {payment_info['status']}",
            show_alert=True
        )


# --- ФУНКЦИИ ФОНОВОГО МОНИТОРИНГА TON (ОБНОВЛЕННЫЕ) ---
# bot.py - добавить эти функции

def get_ton_rub_rate():
    """Получает текущий курс TON к рублю с кэшированием в БД."""
    try:
        # Пытаемся получить курс из БД
        cached_rate = get_ton_rate()
        last_updated = get_ton_rate_updated_at()

        # Если курс в БД есть и он обновлялся менее 10 минут назад - используем его
        if cached_rate and last_updated:
            last_updated_dt = datetime.fromisoformat(last_updated)
            if (datetime.now() - last_updated_dt).total_seconds() < 600:  # 10 минут
                return float(cached_rate)

        # Иначе получаем свежий курс из API
        fresh_rate = fetch_fresh_ton_rate()
        if fresh_rate:
            # Сохраняем в БД
            set_ton_rate(fresh_rate)
            set_ton_rate_updated_at(datetime.now().isoformat())
            logger.info(f"✅ Курс TON обновлен: {fresh_rate:.2f} RUB")
            return fresh_rate
        elif cached_rate:
            # Если не удалось получить свежий курс, используем кэшированный (даже если старый)
            logger.warning("⚠️ Используется устаревший курс TON из кэша")
            return float(cached_rate)
        else:
            return None

    except Exception as e:
        logger.error(f"Ошибка получения курса TON: {e}")
        # Пытаемся вернуть кэшированное значение в случае ошибки
        cached_rate = get_ton_rate()
        return float(cached_rate) if cached_rate else None


def fetch_fresh_ton_rate():
    """Получает свежий курс TON от API."""
    try:
        response = requests.get(TON_RATE_API, timeout=5)
        response.raise_for_status()
        data = response.json()
        rate = data.get('the-open-network', {}).get('rub')
        if rate:
            return float(rate)
        return None
    except Exception as e:
        logger.error(f"Ошибка получения свежего курса TON/RUB: {e}")
        return None


async def update_ton_rate_periodically():
    """Периодическое обновление курса TON каждые 10 минут."""
    while True:
        try:
            fresh_rate = fetch_fresh_ton_rate()
            if fresh_rate:
                set_ton_rate(fresh_rate)
                set_ton_rate_updated_at(datetime.now().isoformat())
                logger.info(f"🔄 Курс TON обновлен в фоне: {fresh_rate:.2f} RUB")
                bot.send_message(
                    ADMIN_ID,
                    f"🔄 Курс TON обновлен: {fresh_rate:.2f} RUB",
                    reply_markup=back_to_main_keyboard()
                )
            else:
                logger.warning("❌ Не удалось обновить курс TON в фоновом режиме")
        except Exception as e:
            logger.error(f"Ошибка фонового обновления курса TON: {e}")

        await asyncio.sleep(600)  # 10 минут


async def check_deposits():
    if not TON_DEPOSIT_ADDRESS or not TON_API_KEY:
        logger.error("TON_DEPOSIT_ADDRESS или TON_API_KEY не заданы. Мониторинг не запущен.")
        return

    # --- Чтение last_lt из БД вместо файла ---
    last_lt_str = get_setting('last_lt', '0')
    try:
        last_lt = int(last_lt_str)
    except ValueError:
        logger.error(f"Некорректное значение last_lt в БД: '{last_lt_str}'. Используется 0.")
        last_lt = 0

    logger.info(f"Запуск мониторинга TON. Последний LT: {last_lt}")

    while True:
        await asyncio.sleep(10)
        try:
            ton_rub_rate = get_ton_rub_rate()
            if not ton_rub_rate:
                continue

            def fetch_ton_transactions(archival):
                """Запрашивает транзакции TON и логирует детали при ошибке."""
                api_url = (
                    f'{TON_API_BASE_URL}/api/v2/getTransactions?'
                    f'address={TON_DEPOSIT_ADDRESS}&limit=100&'
                    f'archival={str(archival).lower()}&api_key={TON_API_KEY}'
                )
                try:
                    response = requests.get(api_url, timeout=10)
                    if response.status_code != 200:
                        logger.error(
                            "TON API HTTP %s: %s",
                            response.status_code,
                            response.text[:500]
                        )
                        return None
                    resp_json = response.json()
                except Exception as e:
                    logger.error(f"Ошибка запроса TON API: {e}")
                    return None

                if not resp_json.get('ok'):
                    err = resp_json.get('error') or resp_json.get('message') or resp_json
                    logger.error(f"Ошибка ответа TON API: {err}")
                    return None

                return resp_json

            # Сначала пробуем archival=true, если не получилось — fallback на archival=false.
            resp = fetch_ton_transactions(archival=True) or fetch_ton_transactions(archival=False)
            if not resp:
                continue

            current_max_lt = last_lt

            # Обрабатываем транзакции в обратном порядке (от новых к старым)
            for tx in reversed(resp.get('result', [])):
                lt = int(tx['transaction_id']['lt'])

                if lt > current_max_lt:
                    current_max_lt = lt

                if lt <= last_lt:
                    continue

                in_msg = tx.get('in_msg')
                if not in_msg:
                    continue

                value_nano = int(in_msg.get('value', 0))

                if value_nano > 0:
                    uid_str = ''
                    # Пытаемся получить user_id из поля 'message' (обычно там комментарий)
                    uid_str = in_msg.get('message', '').strip()

                    if not uid_str.isdigit():
                        logger.warning(f"Пропущена транзакция: {lt}. Некорректный uid в комментарии: '{uid_str}'")
                        continue

                    uid = int(uid_str)
                    ton_amount = value_nano / 1e9

                    # Конвертация TON в RUB
                    rub_amount = round(ton_amount * ton_rub_rate, 2)

                    if rub_amount < 1.0:  # Игнорируем слишком маленькие суммы
                        continue

                    user_data = get_user(uid)
                    if not user_data:
                        logger.warning(f"Пропущена транзакция: {lt}. Пользователь {uid} не найден.")
                        continue

                    # Пополнение баланса в РУБЛЯХ
                    update_balance(uid, rub_amount)
                    # target_user используем для хранения информации о TON транзакции
                    add_transaction(uid, rub_amount, 'deposit_ton', 'completed', target_user=f'{ton_amount:.4f} TON')

                    logger.info(f"✅ Депозит TON подтвержден! User: {uid}, TON: {ton_amount}, RUB: {rub_amount}")

                    # Отправляем уведомление администратору о TON пополнении
                    try:
                        from_user_info = type('MockUser', (object,), {
                            'id': uid,
                            'username': user_data['username'],
                            'first_name': f"User{uid}"  # Заглушка, так как нет реального объекта пользователя
                        })()
                        send_admin_deposit_notification(from_user_info, rub_amount, 'ton', 'completed', ton_amount)
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления администратору: {e}")

                    try:
                        bot.send_message(
                            uid,
                            '✅ Депозит через TON подтвержден!\n'
                            f'Сумма: *+{ton_amount:.4f} TON* ({rub_amount:.2f} руб)\n'
                            f'Ваш новый баланс: {get_user(uid)["balance"]:.2f} руб',
                            parse_mode='Markdown',
                            reply_markup=back_to_main_keyboard()
                        )
                    except Exception as e:
                        logger.error(f"Error sending message to user {uid}: {e}")

            # --- Сохранение максимального LT в БД ---
            if current_max_lt > last_lt:
                last_lt = current_max_lt
                set_setting('last_lt', last_lt)  # <--- Запись в БД

        except requests.exceptions.Timeout:
            logger.error("TON API запрос таймаут.")
        except Exception as e:
            logger.error(f"Критическая ошибка в TON мониторинге: {e}")


def run_async_loop():
    """Запуск asyncio loop в отдельном потоке."""
    # Небольшая задержка перед запуском
    time.sleep(1)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(check_deposits())


def run_async_rate_updater():
    """Запуск асинхронного обновления курса в отдельном потоке."""
    time.sleep(2)  # Небольшая задержка после старта
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(update_ton_rate_periodically())


def main():
    try:
        init_db()
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")

    try:
        cleanup_old_exports(max_files=1)
    except Exception as e:
        logger.error(f"Ошибка очистки старых файлов экспорта: {e}")

    logger.info("Получение начального курса TON...")
    initial_rate = get_ton_rub_rate()
    if initial_rate:
        logger.info(f"✅ Начальный курс TON установлен: {initial_rate:.2f} RUB")
    else:
        logger.error("❌ Не удалось получить начальный курс TON")

    deposit_thread = threading.Thread(target=run_async_loop, daemon=True)
    deposit_thread.start()
    logger.info("Запущен фоновый мониторинг TON депозитов.")

    rate_thread = threading.Thread(target=run_async_rate_updater, daemon=True)
    rate_thread.start()
    logger.info("Запущен фоновый мониторинг курса TON.")

    # Проверка и обновление токена Fragment API
    logger.info("Проверка и обновление токена Fragment API...")
    try:
        token = load_fragment_token()
        if not token:
            logger.info("Токен не найден. Запуск аутентификации...")
            token = authenticate_fragment()
            if token:
                logger.info("✅ Аутентификация Fragment API прошла успешно!")
            else:
                logger.error("❌ Не удалось получить токен Fragment API. Отправка звезд будет невозможна.")
        else:
            logger.info("✅ Существующий токен Fragment API найден.")
    except Exception as e:
        logger.error(f"Ошибка работы с Fragment API: {e}")

    logger.info("Бот запущен...")
    try:
        bot.infinity_polling()
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")


if __name__ == "__main__":

    main()
