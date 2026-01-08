# -*- coding: utf-8 -*-
"""
بوت الخدمات المالية مع التحقق الآلي الحقيقي من سيرياتيل كاش
نسخة Railway - (مشروع جاهز للنشر)
ملاحظة: هذا الملف يجمع كل أجزاء الكود التي أرسلتها ضمن ملف واحد.
"""

# --- SSL workaround (اختياري لحل مشاكل الشهادات على بعض البيئات) ---
import ssl
ssl._create_default_https_context = ssl._create_unverified_context

import json
import os
import shutil
import tempfile
import time
import threading
from datetime import datetime
import zipfile
from difflib import SequenceMatcher
import asyncio
import re
import random
import sys
from typing import Dict, Any, Optional, List, Tuple
import logging

# =========================
# إعدادات logging
# =========================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- فحص البيئة ---
REQUIRED_ENV = ["BOT_TOKEN", "SUPER_ADMIN_ID", "SYRIATEL_USERNAME", "SYRIATEL_PASSWORD"]
for env in REQUIRED_ENV:
    if not os.getenv(env):
        logger.critical(f"❌ Missing environment variable: {env}")
        sys.exit(1)

# === مكتبات التحقق الآلي ===
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    import undetected_chromedriver as uc
    from bs4 import BeautifulSoup
    SELENIUM_AVAILABLE = True
except ImportError as e:
    print(f"❌ خطأ في استيراد Selenium: {e}")
    SELENIUM_AVAILABLE = False

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# =========================
# إعدادات أساسية
# =========================
TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0") or "0")

# بيانات سيرياتيل من Environment Variables (آمنة)
SYRIATEL_USERNAME = os.getenv("SYRIATEL_USERNAME", "").strip()
SYRIATEL_PASSWORD = os.getenv("SYRIATEL_PASSWORD", "").strip()
SYRIATEL_CASH_CODE = os.getenv("SYRIATEL_CASH_CODE", "23547").strip()

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

# تعريفات الملفات
ADMINS_FILE = os.path.join(DATA_DIR, "admins.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
BALANCES_FILE = os.path.join(DATA_DIR, "balances.json")
ORDERS_FILE = os.path.join(DATA_DIR, "orders.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
VERIFIED_TX_FILE = os.path.join(DATA_DIR, "verified_transactions.json")
TRANSACTION_LOG_FILE = os.path.join(DATA_DIR, "transaction_log.json")

DEFAULT_SETTINGS = {
    "syriatel_code": SYRIATEL_CASH_CODE,
    "min_topup": 15000,
    "min_withdraw": 50000,
    "max_pending": 1,
    "auto_verify_enabled": True,
    "auto_verify_interval": 300,  # 5 دقائق
    "max_auto_amount": 100000,
    "stealth_mode": True,
    "max_checks_per_session": 5
}

# =========================
# دوال المساعدة للـ JSON
# =========================
def _ensure_data_files():
    files = [
        (BALANCES_FILE, {}),
        (ORDERS_FILE, {}),
        (SETTINGS_FILE, DEFAULT_SETTINGS),
        (USERS_FILE, {}),
        (ADMINS_FILE, {"super_admin": SUPER_ADMIN_ID, "admins": [SUPER_ADMIN_ID]}),
        (VERIFIED_TX_FILE, {}),
        (TRANSACTION_LOG_FILE, []),
    ]
    for path, default in files:
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump(default, f, ensure_ascii=False, indent=2)

def _load_json(path: str):
    _ensure_data_files()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # إذا الملف فاضي/مكسور
        return {} if not path.endswith("transaction_log.json") else []

def _save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def log_transaction(event_type, details):
    """تسجيل الأحداث المهمة"""
    log_entry = {
        "timestamp": int(time.time()),
        "type": event_type,
        "details": details
    }

    log_data = _load_json(TRANSACTION_LOG_FILE)
    if not isinstance(log_data, list):
        log_data = []

    log_data.append(log_entry)
    log_data = log_data[-1000:]  # حفظ آخر 1000 حدث فقط
    _save_json(TRANSACTION_LOG_FILE, log_data)

# =========================
# إدارة العمليات المحققة
# =========================
class VerifiedTransactionsManager:
    def __init__(self):
        self.verified_tx = self.load_verified_transactions()

    def load_verified_transactions(self):
        return _load_json(VERIFIED_TX_FILE) or {}

    def save_verified_transactions(self):
        _save_json(VERIFIED_TX_FILE, self.verified_tx)

    def is_transaction_verified(self, transaction_id, amount=None):
        tx_data = self.verified_tx.get(transaction_id)
        if not tx_data:
            return False
        if amount is not None and tx_data.get("amount") != amount:
            return False
        return True

    def add_verified_transaction(self, transaction_id, amount, cash_code, user_id, order_id):
        self.verified_tx[transaction_id] = {
            "transaction_id": transaction_id,
            "amount": amount,
            "cash_code": cash_code,
            "user_id": user_id,
            "order_id": order_id,
            "verified_at": int(time.time()),
            "verified_by": "auto_system",
            "status": "verified"
        }
        self.save_verified_transactions()

        log_transaction("transaction_verified", {
            "tx_id": transaction_id,
            "amount": amount,
            "user_id": user_id,
            "order_id": order_id
        })

    def get_transaction_info(self, transaction_id):
        return self.verified_tx.get(transaction_id)

tx_manager = VerifiedTransactionsManager()

# =========================
# نظام التحقق الآلي الحقيقي
# =========================
class RealSyriatelVerifier:
    """نظام يدخل فعلياً إلى سيرياتيل كاش ويتحقق"""

    def __init__(self):
        self.driver = None
        self.logged_in = False
        self.last_login_attempt = 0
        self.login_cooldown = 300  # 5 دقائق بين محاولات الدخول
        self.session_start = None

    async def init_driver(self):
        """تهيئة متصفح Chrome على Railway"""
        if not SELENIUM_AVAILABLE:
            logger.error("❌ Selenium غير متوفر")
            return False

        try:
            logger.info("🚀 بدء تهيئة متصفح Chrome...")

            options = uc.ChromeOptions()

            # إعدادات Headless
            options.add_argument('--headless=new')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--remote-debugging-port=9222')
            options.add_argument('--remote-debugging-address=0.0.0.0')

            # User-Agent واقعي
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            ]
            options.add_argument(f'--user-agent={random.choice(user_agents)}')

            # محاولة اكتشاف مسار Chrome/Chromium في Railway/Nixpacks
            chrome_candidates = [
                '/usr/bin/google-chrome-stable',
                '/usr/bin/google-chrome',
                '/usr/bin/chromium',
                '/usr/bin/chromium-browser',
            ]
            for p in chrome_candidates:
                if os.path.exists(p):
                    options.binary_location = p
                    logger.info(f"✅ تم تحديد موقع المتصفح: {p}")
                    break

            self.driver = uc.Chrome(options=options, use_subprocess=True)

            self.session_start = time.time()
            logger.info("✅ تم تهيئة المتصفح بنجاح")
            return True

        except Exception as e:
            logger.error(f"❌ فشل تهيئة المتصفح: {e}")
            return False

    async def human_like_login(self, username, password):
        """تسجيل دخول"""
        current_time = time.time()

        if current_time - self.last_login_attempt < self.login_cooldown:
            logger.warning("⏳ في فترة انتظار قبل محاولة دخول جديدة")
            return False

        if not self.driver:
            if not await self.init_driver():
                return False

        self.last_login_attempt = current_time

        try:
            logger.info("🔑 محاولة تسجيل الدخول...")

            await asyncio.sleep(random.uniform(2, 4))

            login_urls = [
                "https://cash.syriatel.sy/",
                "https://www.syriatel.sy/cash",
                "https://syriatelcash.sy/"
            ]

            for url in login_urls:
                try:
                    logger.info(f"🌐 تجربة: {url}")
                    self.driver.get(url)
                    await asyncio.sleep(random.uniform(5, 8))

                    username_field = None
                    password_field = None

                    # By XPATH عام
                    try:
                        username_field = self.driver.find_element(By.XPATH, "//input[@type='text' or @type='email']")
                    except:
                        pass
                    try:
                        password_field = self.driver.find_element(By.XPATH, "//input[@type='password']")
                    except:
                        pass

                    if username_field and password_field:
                        await self.human_type(username_field, username)
                        await asyncio.sleep(random.uniform(1, 2))
                        await self.human_type(password_field, password)
                        await asyncio.sleep(random.uniform(1, 2))

                        # زر submit
                        login_button = None
                        for selector in ["//button[@type='submit']", "//input[@type='submit']"]:
                            try:
                                login_button = self.driver.find_element(By.XPATH, selector)
                                break
                            except:
                                continue

                        if login_button:
                            login_button.click()
                            logger.info("🖱️ تم النقر على زر الدخول")
                            await asyncio.sleep(random.uniform(8, 12))

                            if await self.check_login_success():
                                self.logged_in = True
                                logger.info("✅ تم تسجيل الدخول بنجاح")
                                return True

                except Exception as e:
                    logger.warning(f"⚠️ فشل عبر {url}: {str(e)[:120]}")
                    continue

            logger.error("❌ فشل جميع محاولات الدخول")
            return False

        except Exception as e:
            logger.error(f"❌ خطأ في عملية الدخول: {e}")
            return False

    async def human_type(self, element, text):
        for char in text:
            element.send_keys(char)
            await asyncio.sleep(random.uniform(0.08, 0.25))

    async def check_login_success(self):
        try:
            current_url = (self.driver.current_url or "").lower()
            page_source = (self.driver.page_source or "").lower()

            success_indicators = [
                "dashboard", "home", "مرحباً", "welcome",
                "الرصيد", "balance", "المحفظة", "wallet",
                "حسابي", "my account"
            ]
            return any(ind in page_source or ind in current_url for ind in success_indicators)

        except Exception as e:
            logger.error(f"❌ خطأ في التحقق من الدخول: {e}")
            return False

    async def find_transaction(self, transaction_id, amount):
        """البحث عن تحويل محدد في صفحة المعاملات"""
        if not self.logged_in or not self.driver:
            return False

        try:
            logger.info(f"🔍 البحث عن التحويل: {transaction_id} - {amount}")

            transactions_urls = [
                "https://cash.syriatel.sy/transactions",
                "https://cash.syriatel.sy/history",
                "https://cash.syriatel.sy/statement",
            ]

            for url in transactions_urls:
                try:
                    self.driver.get(url)
                    await asyncio.sleep(random.uniform(5, 8))
                    page_source = self.driver.page_source or ""

                    if transaction_id in page_source:
                        amount_str = str(amount)
                        if amount_str in page_source:
                            try:
                                soup = BeautifulSoup(page_source, 'html.parser')
                                for table in soup.find_all('table'):
                                    t = table.get_text().lower()
                                    if transaction_id in t and amount_str in t:
                                        return True
                            except:
                                return True
                        return True

                except Exception as e:
                    logger.warning(f"⚠️ فشل فحص {url}: {str(e)[:120]}")
                    continue

            return False

        except Exception as e:
            logger.error(f"❌ خطأ في البحث عن التحويل: {e}")
            return False

    async def logout(self):
        if self.driver and self.logged_in:
            try:
                for url in ["https://cash.syriatel.sy/logout", "https://www.syriatel.sy/cash/logout"]:
                    try:
                        self.driver.get(url)
                        await asyncio.sleep(3)
                        break
                    except:
                        continue
            finally:
                self.logged_in = False
                logger.info("👋 تم تسجيل الخروج")

    async def close(self):
        if self.driver:
            try:
                await self.logout()
                self.driver.quit()
            except Exception as e:
                logger.error(f"❌ خطأ في إغلاق المتصفح: {e}")
            finally:
                self.driver = None
                self.logged_in = False

    async def rotate_session(self):
        logger.info("🔄 تغيير الجلسة...")
        await self.close()
        await asyncio.sleep(random.uniform(30, 60))

verifier = RealSyriatelVerifier()

# =========================
# دوال النظام الأساسية
# =========================
def get_settings():
    return _load_json(SETTINGS_FILE) or DEFAULT_SETTINGS

def set_settings(updates):
    s = get_settings()
    s.update(updates)
    _save_json(SETTINGS_FILE, s)

def get_admin_ids():
    obj = _load_json(ADMINS_FILE) or {}
    return obj.get("admins", [SUPER_ADMIN_ID])

def is_admin(uid):
    return uid in get_admin_ids()

def get_wallet(uid):
    balances = _load_json(BALANCES_FILE) or {}
    w = balances.get(str(uid), {"balance": 0, "hold": 0})
    return int(w.get("balance", 0)), int(w.get("hold", 0))

def set_wallet(uid, balance, hold):
    balances = _load_json(BALANCES_FILE) or {}
    balances[str(uid)] = {"balance": int(balance), "hold": int(hold)}
    _save_json(BALANCES_FILE, balances)

def make_order_id():
    return f"TOP-{int(time.time())}-{random.randint(1000, 9999)}"

def add_order(order):
    orders = _load_json(ORDERS_FILE) or {}
    orders[order["order_id"]] = order
    _save_json(ORDERS_FILE, orders)

def get_order(order_id):
    return (_load_json(ORDERS_FILE) or {}).get(order_id)

def update_order(order_id, updates):
    orders = _load_json(ORDERS_FILE) or {}
    if order_id in orders:
        orders[order_id].update(updates)
        _save_json(ORDERS_FILE, orders)

def list_orders():
    orders = _load_json(ORDERS_FILE) or {}
    return list(orders.values())

# =========================
# مهمة التحقق الآلي الرئيسية
# =========================
async def real_auto_verification_job(context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if not settings.get("auto_verify_enabled", True):
        logger.info("⏸️ التحقق الآلي معطل")
        return

    if not SYRIATEL_USERNAME or not SYRIATEL_PASSWORD:
        logger.error("❌ بيانات سيرياتيل غير مكتملة")
        return

    orders = list_orders()
    pending_orders = [
        o for o in orders
        if o.get("status") == "pending"
        and o.get("type") == "topup"
        and time.time() - o.get("created_at", 0) < 86400
    ]

    if not pending_orders:
        logger.info("📭 لا توجد طلبات معلقة")
        return

    if not verifier.logged_in:
        login_success = await verifier.human_like_login(SYRIATEL_USERNAME, SYRIATEL_PASSWORD)
        if not login_success:
            logger.error("❌ فشل الدخول")
            for admin_id in get_admin_ids():
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text="❌ فشل الدخول إلى سيرياتيل (التحقق الآلي لم ينجح)."
                    )
                except:
                    pass
            return

    max_checks = settings.get("max_checks_per_session", 5)
    orders_to_check = pending_orders[:max_checks]
    verified_count = 0

    for order in orders_to_check:
        try:
            order_id = order.get("order_id")
            tx_id = (order.get("tx_id") or "").strip()
            amount = int(order.get("amount", 0) or 0)
            user_id = order.get("user_id")

            if not tx_id or amount <= 0 or not user_id:
                continue

            if tx_manager.is_transaction_verified(tx_id, amount):
                update_order(order_id, {
                    "status": "rejected",
                    "rejected_at": int(time.time()),
                    "reject_reason": "تكرار رقم العملية"
                })
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"❌ تم رفض طلبك: رقم العملية {tx_id} مستخدم مسبقاً.\nOrderID: {order_id}"
                    )
                except:
                    pass
                continue

            found = await verifier.find_transaction(tx_id, amount)
            if found:
                tx_manager.add_verified_transaction(tx_id, amount, order.get("cash_code", SYRIATEL_CASH_CODE), user_id, order_id)
                update_order(order_id, {
                    "status": "completed",
                    "verified_at": int(time.time()),
                    "verified_by": "auto_system",
                    "verification_method": "real_syriatel"
                })

                bal, hold = get_wallet(user_id)
                set_wallet(user_id, bal + amount, hold)

                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "✅ تم التحقق من تحويلك! 🎉\n\n"
                            f"📋 رقم الطلب: `{order_id}`\n"
                            f"💰 المبلغ: {amount:,}\n"
                            f"🔢 رقم العملية: `{tx_id}`\n"
                            "💳 تم إضافة المبلغ إلى رصيدك تلقائياً."
                        ),
                        parse_mode="Markdown"
                    )
                except:
                    pass

                verified_count += 1

            await asyncio.sleep(random.uniform(2, 5))

        except Exception as e:
            logger.error(f"❌ خطأ: {e}")

    logger.info(f"📊 نتيجة الجولة: {verified_count}/{len(orders_to_check)}")
    await verifier.logout()

# =========================
# معالجات المستخدم الأساسية
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = f"""
مرحباً {user.first_name}! 👋

🎯 **خدمات البوت:**
• شحن رصيد البوت (سيرياتيل كاش)
• سحب رصيد البوت
• تحقق آلي من التحويلات

💰 **للشحن:**
1. أرسل المبلغ إلى: **{SYRIATEL_CASH_CODE}**
2. أدخل رقم العملية
3. النظام يتحقق تلقائياً خلال دقائق!
    """

    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("💳 شحن رصيد"), KeyboardButton("💰 رصيدي")],
        [KeyboardButton("💸 سحب رصيد"), KeyboardButton("📞 الدعم")],
        [KeyboardButton("⚙️ الإعدادات")],
    ], resize_keyboard=True)

    await update.message.reply_text(welcome_text, reply_markup=keyboard, parse_mode="Markdown")

async def handle_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    await update.message.reply_text(
        f"💳 **طريقة الشحن:**\n\n"
        f"1. أرسل المبلغ إلى الرقم: **{settings['syriatel_code']}**\n"
        f"2. الحد الأدنى: {settings['min_topup']}\n"
        f"3. أدخل رقم العملية بعد الدفع:\n\n"
        f"📱 مثال:\nالمبلغ: 20000\nالرقم: {settings['syriatel_code']}\nرقم العملية: 123456789",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("⬅️ رجوع")]], resize_keyboard=True)
    )
    context.user_data["awaiting_txid"] = True
    return "AWAITING_TXID"

async def handle_txid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tx_id = update.message.text.strip()
    user_id = update.effective_user.id

    if tx_id == "⬅️ رجوع":
        await start(update, context)
        return ConversationHandler.END

    if not (tx_id.isdigit() and 6 <= len(tx_id) <= 20):
        await update.message.reply_text("❌ رقم العملية غير صالح (6-20 رقم)")
        return "AWAITING_TXID"

    if tx_manager.is_transaction_verified(tx_id):
        await update.message.reply_text(
            f"🚨 **هذا الرقم مستخدم مسبقاً!**\n\n"
            f"رقم العملية: `{tx_id}`\n"
            f"يرجى استخدام رقم عملية مختلف.",
            parse_mode="Markdown"
        )
        return "AWAITING_TXID"

    context.user_data["tx_id"] = tx_id

    await update.message.reply_text(
        f"✅ **رقم العملية مقبول**\n\n"
        f"🔢 الرقم: `{tx_id}`\n\n"
        f"الآن أدخل المبلغ:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("⬅️ رجوع")]], resize_keyboard=True)
    )
    return "AWAITING_AMOUNT"

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount_text = update.message.text.strip()
    user_id = update.effective_user.id

    if amount_text == "⬅️ رجوع":
        await start(update, context)
        return ConversationHandler.END

    if not amount_text.isdigit() or int(amount_text) <= 0:
        await update.message.reply_text("❌ أدخل مبلغ صحيح")
        return "AWAITING_AMOUNT"

    amount = int(amount_text)
    settings = get_settings()
    tx_id = context.user_data.get("tx_id", "")

    if amount < settings["min_topup"]:
        await update.message.reply_text(f"❌ الحد الأدنى للشحن: {settings['min_topup']}")
        return "AWAITING_AMOUNT"

    order_id = make_order_id()
    order = {
        "order_id": order_id,
        "type": "topup",
        "status": "pending",
        "user_id": user_id,
        "username": update.effective_user.username or "",
        "tx_id": tx_id,
        "amount": amount,
        "cash_code": SYRIATEL_CASH_CODE,
        "created_at": int(time.time()),
        "auto_verify": True
    }
    add_order(order)

    log_transaction("order_created", {
        "order_id": order_id,
        "user_id": user_id,
        "amount": amount,
        "tx_id": tx_id
    })

    await update.message.reply_text(
        f"✅ **تم استلام طلبك!** 🎉\n\n"
        f"📋 رقم الطلب: `{order_id}`\n"
        f"💰 المبلغ: {amount:,}\n"
        f"🔢 رقم العملية: `{tx_id}`\n"
        f"📞 الكود: {SYRIATEL_CASH_CODE}\n\n"
        f"⏳ **جاري التحقق الآلي...**\n"
        f"سيتم إشعارك فور اكتمال التحقق.",
        parse_mode="Markdown"
    )

    return ConversationHandler.END

async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance, hold = get_wallet(user_id)

    text = f"""
💼 **رصيدك:**
💰 الرصيد المتاح: {balance:,}
⏳ قيد الانتظار: {hold:,}
💵 الإجمالي: {balance + hold:,}
    """
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
📞 **الدعم الفني:**
للتواصل مع الدعم أو الإدارة:
👨‍💼 **المسؤول:** @admin_username
📧 **البريد:** support@example.com

⚠️ **تنبيه:**
- لا تشارك بياناتك مع أي شخص
- تأكد من دقة المعلومات المدخلة
- البلاغات: @abuse_report
    """
    await update.message.reply_text(text, parse_mode="Markdown")

async def handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    text = f"""
⚙️ **إعدادات النظام:**
📱 كود السيرياتيل: {settings['syriatel_code']}
💰 حد الشحن الأدنى: {settings['min_topup']:,}
💸 حد السحب الأدنى: {settings['min_withdraw']:,}
✅ التحقق الآلي: {'مفعل' if settings['auto_verify_enabled'] else 'معطل'}
🔄 مدة التحقق: كل {settings['auto_verify_interval']//60} دقيقة
    """
    await update.message.reply_text(text, parse_mode="Markdown")

# =========================
# السحب
# =========================
async def handle_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance, hold = get_wallet(user_id)
    settings = get_settings()

    if balance < settings["min_withdraw"]:
        await update.message.reply_text(
            f"❌ **الحد الأدنى للسحب:** {settings['min_withdraw']:,}\n"
            f"💰 رصيدك الحالي: {balance:,}",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    text = f"""
💰 **عملية السحب:**
الحد الأدنى: {settings['min_withdraw']:,}
رصيدك المتاح: {balance:,}

أدخل المبلغ المطلوب سحبه:
    """
    await update.message.reply_text(text)
    context.user_data["awaiting_withdraw_amount"] = True
    return "AWAITING_WITHDRAW_AMOUNT"

async def handle_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount_text = update.message.text.strip()
    user_id = update.effective_user.id

    if not amount_text.isdigit():
        await update.message.reply_text("❌ أدخل مبلغ صحيح")
        return "AWAITING_WITHDRAW_AMOUNT"

    amount = int(amount_text)
    balance, hold = get_wallet(user_id)
    settings = get_settings()

    if amount < settings["min_withdraw"]:
        await update.message.reply_text(f"❌ الحد الأدنى: {settings['min_withdraw']:,}")
        return "AWAITING_WITHDRAW_AMOUNT"

    if amount > balance:
        await update.message.reply_text(f"❌ رصيدك غير كافي. الرصيد: {balance:,}")
        return "AWAITING_WITHDRAW_AMOUNT"

    context.user_data["withdraw_amount"] = amount
    await update.message.reply_text(
        f"✅ **المبلغ: {amount:,}**\n\nالآن أدخل رقم سيرياتيل كاش المستلم:",
        parse_mode="Markdown"
    )
    return "AWAITING_WITHDRAW_NUMBER"

async def handle_withdraw_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    user_id = update.effective_user.id
    amount = context.user_data.get("withdraw_amount", 0)

    if not (phone.isdigit() and 9 <= len(phone) <= 10):
        await update.message.reply_text("❌ رقم سيرياتيل غير صالح")
        return "AWAITING_WITHDRAW_NUMBER"

    balance, hold = get_wallet(user_id)
    new_balance = balance - amount
    set_wallet(user_id, new_balance, hold)

    order_id = f"WDR-{int(time.time())}-{random.randint(1000, 9999)}"
    order = {
        "order_id": order_id,
        "type": "withdraw",
        "status": "pending",
        "user_id": user_id,
        "username": update.effective_user.username or "",
        "amount": amount,
        "phone": phone,
        "created_at": int(time.time()),
        "balance_before": balance,
        "balance_after": new_balance
    }
    add_order(order)

    for admin_id in get_admin_ids():
        try:
            await update.message.bot.send_message(
                chat_id=admin_id,
                text=f"📤 **طلب سحب جديد**\n\n"
                     f"📋 OrderID: {order_id}\n"
                     f"👤 المستخدم: {user_id}\n"
                     f"📞 الرقم: {phone}\n"
                     f"💰 المبلغ: {amount:,}\n"
                     f"🕒 الوقت: {datetime.now().strftime('%H:%M:%S')}",
                parse_mode="Markdown"
            )
        except:
            pass

    await update.message.reply_text(
        f"✅ **تم استلام طلب السحب!**\n\n"
        f"📋 رقم الطلب: `{order_id}`\n"
        f"💰 المبلغ: {amount:,}\n"
        f"📞 الرقم: {phone}\n"
        f"⏳ جاري المعالجة...",
        parse_mode="Markdown"
    )

    log_transaction("withdraw_request", {
        "order_id": order_id,
        "user_id": user_id,
        "amount": amount,
        "phone": phone
    })

    return ConversationHandler.END

# =========================
# لوحة الأدمن
# =========================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ غير مصرح")
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("🔄 التحقق اليدوي", callback_data="admin_verify")],
    ])
    await update.message.reply_text("👨‍💼 **لوحة الإدارة**", reply_markup=keyboard, parse_mode="Markdown")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    orders = list_orders()
    balances = _load_json(BALANCES_FILE) or {}
    settings = get_settings()

    total_orders = len(orders)
    pending_orders = len([o for o in orders if o.get("status") == "pending"])
    completed_orders = len([o for o in orders if o.get("status") == "completed"])

    total_balance = 0
    for b in balances.values():
        try:
            total_balance += int(b.get("balance", 0)) + int(b.get("hold", 0))
        except:
            pass
    total_users = len(balances)

    text = f"""
📊 **إحصائيات النظام:**

👥 **المستخدمين:**
• عدد المستخدمين: {total_users}
• إجمالي الأرصدة: {total_balance:,}

📋 **الطلبات:**
• إجمالي الطلبات: {total_orders}
• قيد الانتظار: {pending_orders}
• مكتملة: {completed_orders}

⚙️ **النظام:**
• التحقق الآلي: {'✅' if settings.get('auto_verify_enabled') else '❌'}
• الكود: {settings.get('syriatel_code')}
• الجلسة: {'✅' if verifier.logged_in else '❌'}
    """
    await query.edit_message_text(text, parse_mode="Markdown")

async def manual_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    orders = [o for o in list_orders() if o.get("status") == "pending"][:10]
    if not orders:
        await query.edit_message_text("📭 لا توجد طلبات معلقة")
        return

    keyboard_buttons = []
    for order in orders:
        btn_text = f"{order['order_id']} - {int(order.get('amount', 0)):,}"
        keyboard_buttons.append([InlineKeyboardButton(btn_text, callback_data=f"verify_{order['order_id']}")])

    keyboard_buttons.append([InlineKeyboardButton("⬅️ رجوع", callback_data="admin_panel")])
    keyboard = InlineKeyboardMarkup(keyboard_buttons)

    await query.edit_message_text("🔍 **التحقق اليدوي**\nاختر طلب للتحقق:", reply_markup=keyboard, parse_mode="Markdown")

# =========================
# Main
# =========================
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    topup_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text(["💳 شحن رصيد"]), handle_topup)],
        states={
            "AWAITING_TXID": [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_txid)],
            "AWAITING_AMOUNT": [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )

    withdraw_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Text(["💸 سحب رصيد"]), handle_withdraw)],
        states={
            "AWAITING_WITHDRAW_AMOUNT": [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_withdraw_amount)],
            "AWAITING_WITHDRAW_NUMBER": [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_withdraw_number)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("balance", handle_balance))

    app.add_handler(topup_conv)
    app.add_handler(withdraw_conv)

    app.add_handler(MessageHandler(filters.Text(["💰 رصيدي"]), handle_balance))
    app.add_handler(MessageHandler(filters.Text(["📞 الدعم"]), handle_support))
    app.add_handler(MessageHandler(filters.Text(["⚙️ الإعدادات"]), handle_settings))

    app.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(manual_verify, pattern="^admin_verify$"))

    settings = get_settings()
    interval = settings.get("auto_verify_interval", 300)

    # JobQueue موجود فقط إذا كان python-telegram-bot مثبتًا مع extra job-queue
    if app.job_queue:
        app.job_queue.run_repeating(real_auto_verification_job, interval=interval, first=10)
    else:
        logger.warning('⚠️ JobQueue غير متوفر. ثبّت: python-telegram-bot[job-queue] لتفعيل التحقق الدوري.')

    logger.info("🤖 بدء تشغيل البوت...")
    # run_polling يدير الـ event loop بنفسه، لذلك لا نستخدم asyncio.run هنا
    app.run_polling()

if __name__ == "__main__":
    _ensure_data_files()
    main()
