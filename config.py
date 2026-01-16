import os
import logging
from dotenv import load_dotenv

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Константы Бота ---
STAR_PRICE = 1.5
USD_RUB_RATE = float(os.getenv('USD_RUB_RATE', '90.0'))
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ROOT_IMG_DIR = os.path.join(PROJECT_ROOT, 'img')
LOCAL_IMG_DIR = os.path.join(os.path.dirname(__file__), 'img')
IMG_DIR = ROOT_IMG_DIR if os.path.isdir(ROOT_IMG_DIR) else LOCAL_IMG_DIR


def resolve_image_path(value, default_name):
    if value:
        if value.startswith('http://') or value.startswith('https://'):
            return value
        if os.path.isabs(value):
            return value
        return os.path.join(IMG_DIR, value)
    return os.path.join(IMG_DIR, default_name)


MAIN_MENU_IMAGE = resolve_image_path(os.getenv('MAIN_MENU_IMAGE'), 'main_menu.jpg')
BUY_STARS_IMAGE = resolve_image_path(os.getenv('BUY_STARS_IMAGE'), 'buy_stars.jpg')
INTERNAL_STARS_IMAGE = resolve_image_path(os.getenv('INTERNAL_STARS_IMAGE'), 'buy_stars.jpg')
PROFILE_IMAGE = resolve_image_path(os.getenv('PROFILE_IMAGE'), 'profile.jpg')
DEPOSIT_IMAGE = resolve_image_path(os.getenv('DEPOSIT_IMAGE'), 'deposit.jpg')
REFERRALS_IMAGE = resolve_image_path(os.getenv('REFERRALS_IMAGE'), 'referrals.jpg')
CALCULATOR_IMAGE = resolve_image_path(os.getenv('CALCULATOR_IMAGE'), 'calculator.jpg')
WELCOME_MES = f"Привет👋\n\nДобро пожаловать в бота для покупки Telegram Stars! 🌟\n\nВыберите действие:"
TOKEN_FILE = "auth_token.json"
MIN_STARS = 50

REFERRAL_REWARD = 5.0 # Вознаграждение за приглашенного пользователя (в рублях)

# --- Конфигурация API ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = os.getenv('ADMIN_ID')
DB_NAME = 'bot_database.db'

# ЮKassa
YOOKASSA_SHOP_ID = os.getenv('YOOKASSA_SHOP_ID')
YOOKASSA_SECRET_KEY = os.getenv('YOOKASSA_SECRET_KEY')
YOOKASSA_API_URL = "https://api.yookassa.ru/v3/payments"

# TON Wallet Configuration
TON_DEPOSIT_ADDRESS = os.getenv('TON_DEPOSIT_ADDRESS')
TON_API_KEY = os.getenv('TON_API_KEY')
TON_API_BASE_URL = os.getenv('TON_API_BASE_URL', 'https://toncenter.com')

# Fragment API
FRAGMENT_API_URL = "https://api.fragment-api.com/v1"
FRAGMENT_API_KEY = os.getenv("FRAGMENT_API_KEY")
FRAGMENT_PHONE = os.getenv("FRAGMENT_PHONE")
FRAGMENT_MNEMONICS = os.getenv("FRAGMENT_MNEMONICS")

# Проверка наличия токена бота
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не найден в переменных окружения.")

# Проверка наличия учетных данных ЮKassa
if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
    logger.warning("⚠️ Учетные данные ЮKassa не найдены.")

# Проверка наличия учетных данных Fragment
if not FRAGMENT_API_KEY or not FRAGMENT_PHONE or not FRAGMENT_MNEMONICS:
    logger.warning("⚠️ Учетные данные Fragment API не найдены. Отправка звезд будет невозможна.")
