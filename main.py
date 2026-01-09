# main.py
import os
import json
import asyncio
import logging
import difflib
import zipfile
import tempfile
import shutil
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
from pathlib import Path
import aiofiles
import aiofiles.os

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Message,
    User,
    Chat,
    InputFile
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from telegram.constants import ParseMode

# ==================== ENV VARIABLES ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", 0))
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@broichancy")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@support")
DATA_DIR = os.getenv("DATA_DIR", "data")
MIN_TOPUP = int(os.getenv("MIN_TOPUP", 15000))
MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", 500))
SYRIATEL_CODES = [code.strip() for code in os.getenv("SYRIATEL_CODES", "45191900,33333333,33333344").split(",")]
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ==================== LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=getattr(logging, LOG_LEVEL)
)
logger = logging.getLogger(__name__)

# ==================== PATHS ====================
Path(DATA_DIR).mkdir(exist_ok=True)
USERS_FILE = Path(DATA_DIR) / "users.json"
ACCOUNTS_FILE = Path(DATA_DIR) / "accounts.json"
PENDING_FILE = Path(DATA_DIR) / "pending.json"
ADMINS_FILE = Path(DATA_DIR) / "admins.json"
MAINTENANCE_FILE = Path(DATA_DIR) / "maintenance.json"
BACKUP_DIR = Path(DATA_DIR) / "backups"

# ==================== LOCK MANAGEMENT ====================
file_locks = {
    USERS_FILE: asyncio.Lock(),
    ACCOUNTS_FILE: asyncio.Lock(),
    PENDING_FILE: asyncio.Lock(),
    ADMINS_FILE: asyncio.Lock(),
    MAINTENANCE_FILE: asyncio.Lock()
}

# ==================== DATA STRUCTURES ====================
class UserData:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.balance = 0.0
        self.hold = 0.0
        self.eshansy_account = None
        self.eshansy_balance = 0
        self.subscribed = False
        self.is_admin = False
        self.is_super_admin = False
        self.created_at = datetime.now().isoformat()
        
    def to_dict(self):
        return {
            "user_id": self.user_id,
            "balance": self.balance,
            "hold": self.hold,
            "eshansy_account": self.eshansy_account,
            "eshansy_balance": self.eshansy_balance,
            "subscribed": self.subscribed,
            "is_admin": self.is_admin,
            "is_super_admin": self.is_super_admin,
            "created_at": self.created_at
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        user = cls(data["user_id"])
        user.balance = data.get("balance", 0.0)
        user.hold = data.get("hold", 0.0)
        user.eshansy_account = data.get("eshansy_account")
        user.eshansy_balance = data.get("eshansy_balance", 0)
        user.subscribed = data.get("subscribed", False)
        user.is_admin = data.get("is_admin", False)
        user.is_super_admin = data.get("is_super_admin", False)
        user.created_at = data.get("created_at", datetime.now().isoformat())
        return user

class EshansyAccount:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.assigned_to = None
        self.assigned_at = None
        self.created_at = datetime.now().isoformat()
        
    def to_dict(self):
        return {
            "username": self.username,
            "password": self.password,
            "assigned_to": self.assigned_to,
            "assigned_at": self.assigned_at,
            "created_at": self.created_at
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        acc = cls(data["username"], data["password"])
        acc.assigned_to = data.get("assigned_to")
        acc.assigned_at = data.get("assigned_at")
        acc.created_at = data.get("created_at", datetime.now().isoformat())
        return acc

class PendingRequest:
    def __init__(self, request_id: str, user_id: int, req_type: str, data: dict):
        self.request_id = request_id
        self.user_id = user_id
        self.type = req_type  # "topup", "withdraw", "eshansy_topup", "eshansy_withdraw"
        self.data = data
        self.status = "pending"
        self.created_at = datetime.now().isoformat()
        self.handled_by = None
        self.handled_at = None
        
    def to_dict(self):
        return {
            "request_id": self.request_id,
            "user_id": self.user_id,
            "type": self.type,
            "data": self.data,
            "status": self.status,
            "created_at": self.created_at,
            "handled_by": self.handled_by,
            "handled_at": self.handled_at
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        req = cls(
            data["request_id"],
            data["user_id"],
            data["type"],
            data["data"]
        )
        req.status = data.get("status", "pending")
        req.created_at = data.get("created_at", datetime.now().isoformat())
        req.handled_by = data.get("handled_by")
        req.handled_at = data.get("handled_at")
        return req

# ==================== STORAGE FUNCTIONS ====================
async def atomic_write(file_path: Path, data: dict):
    """Atomic write with asyncio lock"""
    lock = file_locks.get(file_path)
    if not lock:
        lock = asyncio.Lock()
        file_locks[file_path] = lock
    
    async with lock:
        # Write to temp file first
        temp_file = file_path.with_suffix('.tmp')
        async with aiofiles.open(temp_file, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, indent=2, ensure_ascii=False))
        
        # Replace original file
        await aiofiles.os.replace(temp_file, file_path)

async def load_data(file_path: Path, default: Any = None):
    """Load JSON data with lock"""
    if default is None:
        default = {}
    
    if not await aiofiles.os.path.exists(file_path):
        return default.copy() if isinstance(default, dict) else default
    
    lock = file_locks.get(file_path)
    if lock:
        async with lock:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content) if content else default.copy()
    else:
        async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
            content = await f.read()
            return json.loads(content) if content else default.copy()

async def save_data(file_path: Path, data: Any):
    """Save data atomically"""
    await atomic_write(file_path, data)

# ==================== DATA MANAGERS ====================
class DataManager:
    @staticmethod
    async def get_user(user_id: int) -> Optional[UserData]:
        users = await load_data(USERS_FILE, {})
        user_data = users.get(str(user_id))
        return UserData.from_dict(user_data) if user_data else None
    
    @staticmethod
    async def save_user(user: UserData):
        users = await load_data(USERS_FILE, {})
        users[str(user.user_id)] = user.to_dict()
        await save_data(USERS_FILE, users)
    
    @staticmethod
    async def get_all_users() -> Dict[int, UserData]:
        users = await load_data(USERS_FILE, {})
        return {int(uid): UserData.from_dict(data) for uid, data in users.items()}
    
    @staticmethod
    async def get_accounts() -> Dict[str, EshansyAccount]:
        accounts = await load_data(ACCOUNTS_FILE, {})
        return {username: EshansyAccount.from_dict(data) for username, data in accounts.items()}
    
    @staticmethod
    async def save_accounts(accounts: Dict[str, EshansyAccount]):
        data = {username: acc.to_dict() for username, acc in accounts.items()}
        await save_data(ACCOUNTS_FILE, data)
    
    @staticmethod
    async def get_pending_requests() -> Dict[str, PendingRequest]:
        pending = await load_data(PENDING_FILE, {})
        return {req_id: PendingRequest.from_dict(data) for req_id, data in pending.items()}
    
    @staticmethod
    async def save_pending_requests(requests: Dict[str, PendingRequest]):
        data = {req_id: req.to_dict() for req_id, req in requests.items()}
        await save_data(PENDING_FILE, data)
    
    @staticmethod
    async def get_admins() -> List[int]:
        admins = await load_data(ADMINS_FILE, [])
        return admins
    
    @staticmethod
    async def save_admins(admins: List[int]):
        await save_data(ADMINS_FILE, admins)
    
    @staticmethod
    async def is_maintenance() -> bool:
        maintenance = await load_data(MAINTENANCE_FILE, {"active": False})
        return maintenance.get("active", False)
    
    @staticmethod
    async def set_maintenance(active: bool):
        await save_data(MAINTENANCE_FILE, {"active": active})

# ==================== KEYBOARDS ====================
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        ["💼 حساب ايشانسي", "💰 محفظتي"],
        ["➕ شحن رصيد البوت", "➖ سحب رصيد من البوت"],
        ["🧾 إلغاء آخر طلب سحب", "🆘 دعم"]
    ], resize_keyboard=True, one_time_keyboard=False)

def get_eshansy_keyboard():
    return ReplyKeyboardMarkup([
        ["📝 إنشاء / استلام حساب", "💰 شحن حساب ايشانسي"],
        ["💸 سحب من حساب ايشانسي", "🗑️ حذف حساب ايشانسي"],
        ["🔙 رجوع"]
    ], resize_keyboard=True)

def get_topup_methods_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💳 شام كاش", callback_data="topup_sham"),
            InlineKeyboardButton("📲 سيرياتيل كاش", callback_data="topup_syriatel")
        ]
    ])

def get_withdraw_methods_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💳 شام كاش", callback_data="withdraw_sham"),
            InlineKeyboardButton("📲 سيرياتيل كاش", callback_data="withdraw_syriatel")
        ]
    ])

def get_syriatel_codes_keyboard():
    buttons = []
    for code in SYRIATEL_CODES:
        buttons.append([InlineKeyboardButton(f"📞 {code}", callback_data=f"code_{code}")])
    buttons.append([InlineKeyboardButton("🔙 رجوع", callback_data="back")])
    return InlineKeyboardMarkup(buttons)

def get_subscription_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ اشترك بالقناة", url=f"https://t.me/{REQUIRED_CHANNEL[1:]}")
        ],
        [
            InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="check_subscription")
        ]
    ])

def get_admin_keyboard(is_super: bool = False):
    buttons = [
        ["📊 الإحصائيات", "👥 المستخدمين"],
        ["📨 الطلبات المعلقة", "⚙️ إعدادات"],
        ["📢 رسالة جماعية"]
    ]
    if is_super:
        buttons.append(["💾 Backup/Restore", "🔧 الصيانة"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_pending_actions_keyboard(request_id: str):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ قبول", callback_data=f"approve_{request_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_{request_id}")
        ],
        [
            InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_{request_id}")
        ]
    ])

# ==================== CONVERSATION STATES ====================
class States:
    MAIN_MENU = 0
    ESHANSY_MENU = 1
    ESHANSY_CREATE = 2
    ESHANSY_TOPUP = 3
    ESHANSY_WITHDRAW = 4
    TOPUP_METHOD = 10
    TOPUP_SYRIA_CODE = 11
    TOPUP_SYRIA_REF = 12
    TOPUP_SYRIA_AMOUNT = 13
    TOPUP_CONFIRM = 14
    WITHDRAW_METHOD = 20
    WITHDRAW_SYRIA_NUMBER = 21
    WITHDRAW_SYRIA_AMOUNT = 22
    WITHDRAW_CONFIRM = 23
    ADMIN_BROADCAST = 30
    ADMIN_BROADCAST_CONFIRM = 31
    ADMIN_ADD_ACCOUNT = 40
    ADMIN_DELETE_ACCOUNT = 41
    ADMIN_MODIFY_BALANCE = 42
    ADMIN_SEARCH_USER = 43

# ==================== UTILITY FUNCTIONS ====================
def generate_request_id():
    return datetime.now().strftime("%Y%m%d%H%M%S%f")

async def check_subscription(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        user = await DataManager.get_user(user_id)
        if user and user.subscribed:
            return True
            
        chat_member = await context.bot.get_chat_member(
            chat_id=REQUIRED_CHANNEL,
            user_id=user_id
        )
        is_subscribed = chat_member.status in ["member", "administrator", "creator"]
        
        if is_subscribed:
            user = user or UserData(user_id)
            user.subscribed = True
            await DataManager.save_user(user)
        
        return is_subscribed
    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        return False

async def require_subscription(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if await DataManager.is_maintenance():
            if not await is_admin(user_id):
                await update.message.reply_text(
                    "⚙️ البوت في وضع الصيانة. الرجاء المحاولة لاحقًا."
                )
                return
                
        if not await check_subscription(context, user_id):
            await update.message.reply_text(
                f"👋 مرحباً {update.effective_user.first_name}!\n\n"
                f"📍 يجب الاشتراك في القناة أولاً:\n{REQUIRED_CHANNEL}\n\n"
                "بعد الاشتراك اضغط على زر التحقق",
                reply_markup=get_subscription_keyboard()
            )
            return
        
        return await func(update, context)
    return wrapper

async def is_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return True
    
    user = await DataManager.get_user(user_id)
    if user and (user.is_admin or user.is_super_admin):
        return True
    
    admins = await DataManager.get_admins()
    return user_id in admins

async def is_super_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return True
    
    user = await DataManager.get_user(user_id)
    return user and user.is_super_admin

async def send_to_admins(context: ContextTypes.DEFAULT_TYPE, message: str, parse_mode: str = ParseMode.HTML):
    """Send message to all admins"""
    users = await DataManager.get_all_users()
    for user in users.values():
        if user.is_admin or user.is_super_admin:
            try:
                await context.bot.send_message(
                    chat_id=user.user_id,
                    text=message,
                    parse_mode=parse_mode
                )
            except Exception as e:
                logger.error(f"Failed to send to admin {user.user_id}: {e}")

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Initialize user if not exists
    user = await DataManager.get_user(user_id)
    if not user:
        user = UserData(user_id)
        if user_id == SUPER_ADMIN_ID:
            user.is_super_admin = True
            user.is_admin = True
        await DataManager.save_user(user)
    
    # Check subscription
    if await check_subscription(context, user_id):
        await update.message.reply_text(
            f"👋 أهلاً وسهلاً {update.effective_user.first_name}!\n"
            "🚀 تم التحقق من اشتراكك بنجاح.\n\n"
            "⚡ اختر من القائمة:",
            reply_markup=get_main_keyboard()
        )
        return States.MAIN_MENU
    else:
        await update.message.reply_text(
            f"👋 مرحباً {update.effective_user.first_name}!\n\n"
            f"📍 يجب الاشتراك في القناة أولاً:\n{REQUIRED_CHANNEL}\n\n"
            "بعد الاشتراك اضغط على زر التحقق",
            reply_markup=get_subscription_keyboard()
        )
        return ConversationHandler.END

async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if await check_subscription(context, user_id):
        await query.edit_message_text(
            f"✅ تم التحقق من اشتراكك بنجاح!\n\n"
            f"👋 أهلاً وسهلاً {query.from_user.first_name}!\n"
            "⚡ اختر من القائمة:"
        )
        await context.bot.send_message(
            chat_id=user_id,
            text="⚡ اختر من القائمة:",
            reply_markup=get_main_keyboard()
        )
        return States.MAIN_MENU
    else:
        await query.edit_message_text(
            "❌ لم يتم التحقق من اشتراكك بعد.\n"
            f"📍 يجب الاشتراك في: {REQUIRED_CHANNEL}\n\n"
            "بعد الاشتراك اضغط على زر التحقق مرة أخرى",
            reply_markup=get_subscription_keyboard()
        )
        return ConversationHandler.END

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await DataManager.is_maintenance() and not await is_admin(update.effective_user.id):
        await update.message.reply_text("⚙️ البوت في وضع الصيانة. الرجاء المحاولة لاحقًا.")
        return
    
    text = update.message.text
    
    if text.startswith("💼 حساب ايشانسي"):
        return await eshansy_menu(update, context)
    elif text.startswith("💰 محفظتي"):
        return await my_wallet(update, context)
    elif text.startswith("➕ شحن رصيد البوت"):
        return await topup_menu(update, context)
    elif text.startswith("➖ سحب رصيد من البوت"):
        return await withdraw_menu(update, context)
    elif text.startswith("🧾 إلغاء آخر طلب سحب"):
        return await cancel_last_withdraw(update, context)
    elif text.startswith("🆘 دعم"):
        return await support(update, context)
    elif text == "/admin":
        return await admin_panel(update, context)
    else:
        await update.message.reply_text(
            "⚡ اختر من القائمة:",
            reply_markup=get_main_keyboard()
        )
        return States.MAIN_MENU

# ==================== WALLET FUNCTIONS ====================
@require_subscription
async def my_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await DataManager.get_user(user_id)
    
    if not user:
        user = UserData(user_id)
        await DataManager.save_user(user)
    
    message = (
        f"💰 <b>محفظتك</b>\n\n"
        f"💵 الرصيد المتاح: <code>{user.balance:,.0f}</code> ليرة\n"
        f"🔒 المبلغ المحجوز: <code>{user.hold:,.0f}</code> ليرة\n"
        f"⚖️ الرصيد الإجمالي: <code>{user.balance + user.hold:,.0f}</code> ليرة\n\n"
    )
    
    if user.eshansy_account:
        message += (
            f"💼 <b>حساب ايشانسي</b>\n"
            f"👤 الحساب: <code>{user.eshansy_account}</code>\n"
            f"💰 الرصيد: <code>{user.eshansy_balance}</code> نقطة\n\n"
        )
    
    await update.message.reply_text(
        message,
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )
    return States.MAIN_MENU

# ==================== ESHANSY FUNCTIONS ====================
@require_subscription
async def eshansy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💼 <b>قائمة حساب ايشانسي</b>\n\n"
        "اختر الخدمة المطلوبة:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_eshansy_keyboard()
    )
    return States.ESHANSY_MENU

@require_subscription
async def eshansy_create_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await DataManager.get_user(user_id)
    
    if user.eshansy_account:
        accounts = await DataManager.get_accounts()
        account = accounts.get(user.eshansy_account)
        
        message = (
            f"📋 <b>حسابك الحالي</b>\n\n"
            f"👤 اسم المستخدم: <code>{account.username}</code>\n"
            f"🔑 كلمة المرور: <code>{account.password}</code>\n\n"
            f"💰 رصيدك في ايشانسي: <code>{user.eshansy_balance}</code> نقطة\n\n"
            "يمكنك نسخ المعلومات بالأعلى."
        )
        await update.message.reply_text(
            message,
            parse_mode=ParseMode.HTML,
            reply_markup=get_eshansy_keyboard()
        )
        return States.ESHANSY_MENU
    
    await update.message.reply_text(
        "📝 <b>إنشاء حساب ايشانسي جديد</b>\n\n"
        "أدخل اسم مستخدم تقريبي (باللغة الإنجليزية):\n"
        "مثال: user123",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
    )
    return States.ESHANSY_CREATE

async def eshansy_create_account_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    suggested_username = update.message.text.strip().lower()
    
    accounts = await DataManager.get_accounts()
    available_accounts = {username: acc for username, acc in accounts.items() if not acc.assigned_to}
    
    if not available_accounts:
        await update.message.reply_text(
            "❌ لا توجد حسابات متاحة حالياً.\n"
            "الرجاء التواصل مع الدعم أو المحاولة لاحقاً.",
            reply_markup=get_eshansy_keyboard()
        )
        return States.ESHANSY_MENU
    
    # Find best match
    best_match = None
    best_ratio = 0
    
    for username in available_accounts.keys():
        ratio = difflib.SequenceMatcher(None, suggested_username, username).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = username
    
    if best_match:
        context.user_data["suggested_account"] = best_match
        await update.message.reply_text(
            f"✨ <b>أقترح لك هذا الحساب:</b>\n\n"
            f"👤 <code>{best_match}</code>\n\n"
            "هل تريد تأكيد الاستلام؟",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ تأكيد", callback_data="confirm_eshansy"),
                    InlineKeyboardButton("❌ رفض", callback_data="reject_eshansy")
                ]
            ])
        )
        return States.ESHANSY_CREATE
    else:
        await update.message.reply_text(
            "❌ لم أتمكن من العثور على حساب مناسب.\n"
            "الرجاء المحاولة باسم مختلف.",
            reply_markup=get_eshansy_keyboard()
        )
        return States.ESHANSY_MENU

async def eshansy_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_eshansy":
        username = context.user_data.get("suggested_account")
        if not username:
            await query.edit_message_text("❌ حدث خطأ. الرجاء المحاولة مرة أخرى.")
            return States.ESHANSY_MENU
        
        accounts = await DataManager.get_accounts()
        account = accounts.get(username)
        
        if not account or account.assigned_to:
            await query.edit_message_text("❌ الحساب لم يعد متاحاً.")
            return States.ESHANSY_MENU
        
        # Assign account
        user_id = query.from_user.id
        account.assigned_to = user_id
        account.assigned_at = datetime.now().isoformat()
        
        user = await DataManager.get_user(user_id)
        user.eshansy_account = username
        user.eshansy_balance = 0
        
        await DataManager.save_accounts(accounts)
        await DataManager.save_user(user)
        
        message = (
            f"✅ <b>تم استلام الحساب بنجاح!</b>\n\n"
            f"👤 اسم المستخدم: <code>{account.username}</code>\n"
            f"🔑 كلمة المرور: <code>{account.password}</code>\n\n"
            "🔒 <i>احفظ هذه المعلومات في مكان آمن</i>"
        )
        await query.edit_message_text(
            message,
            parse_mode=ParseMode.HTML
        )
        
        # Send to main menu
        await context.bot.send_message(
            chat_id=user_id,
            text="💼 اختر من قائمة ايشانسي:",
            reply_markup=get_eshansy_keyboard()
        )
        return States.ESHANSY_MENU
    else:
        await query.edit_message_text("❌ تم إلغاء العملية.")
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="💼 اختر من قائمة ايشانسي:",
            reply_markup=get_eshansy_keyboard()
        )
        return States.ESHANSY_MENU

@require_subscription
async def eshansy_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await DataManager.get_user(user_id)
    
    if not user.eshansy_account:
        await update.message.reply_text(
            "❌ ليس لديك حساب ايشانسي.\n"
            "الرجاء إنشاء حساب أولاً.",
            reply_markup=get_eshansy_keyboard()
        )
        return States.ESHANSY_MENU
    
    await update.message.reply_text(
        "💰 <b>شحن حساب ايشانسي</b>\n\n"
        "أدخل المبلغ بالليرة السورية:\n"
        "ملاحظة: كل 1 ليرة = 100 نقطة ايشانسي\n\n"
        "مثال: لإضافة 1000 نقطة، أدخل 10",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
    )
    return States.ESHANSY_TOPUP

async def eshansy_topup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
        
        user_id = update.effective_user.id
        user = await DataManager.get_user(user_id)
        
        required_balance = amount  # 1 ليرة = 100 نقطة
        
        if user.balance < required_balance:
            await update.message.reply_text(
                f"❌ رصيدك غير كافي.\n"
                f"💵 رصيدك: {user.balance:,.0f} ليرة\n"
                f"💰 المطلوب: {required_balance:,.0f} ليرة",
                reply_markup=get_eshansy_keyboard()
            )
            return States.ESHANSY_MENU
        
        context.user_data["eshansy_topup"] = {
            "amount_sy": amount,
            "eshansy_points": int(amount * 100)
        }
        
        await update.message.reply_text(
            f"📋 <b>تفاصيل الشحن</b>\n\n"
            f"💵 المبلغ: <code>{amount:,.0f}</code> ليرة\n"
            f"🎯 النقاط: <code>{int(amount * 100):,}</code> نقطة\n\n"
            f"💳 سيتم خصم: <code>{amount:,.0f}</code> ليرة من رصيدك\n\n"
            "هل تريد المتابعة؟",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ تأكيد", callback_data="confirm_eshansy_topup"),
                    InlineKeyboardButton("❌ إلغاء", callback_data="cancel_eshansy_topup")
                ]
            ])
        )
        return States.ESHANSY_TOPUP
    except ValueError:
        await update.message.reply_text(
            "❌ المبلغ غير صحيح. الرجاء إدخال رقم صحيح.\n"
            "مثال: 10",
            reply_markup=get_eshansy_keyboard()
        )
        return States.ESHANSY_MENU

async def eshansy_topup_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_eshansy_topup":
        data = context.user_data.get("eshansy_topup")
        if not data:
            await query.edit_message_text("❌ انتهت صلاحية البيانات.")
            return States.ESHANSY_MENU
        
        user_id = query.from_user.id
        user = await DataManager.get_user(user_id)
        
        if user.balance < data["amount_sy"]:
            await query.edit_message_text("❌ رصيدك غير كافي.")
            return States.ESHANSY_MENU
        
        # Deduct from user balance
        user.balance -= data["amount_sy"]
        user.eshansy_balance += data["eshansy_points"]
        
        # Create pending request for admin notification
        request_id = generate_request_id()
        pending_request = PendingRequest(
            request_id=request_id,
            user_id=user_id,
            req_type="eshansy_topup",
            data={
                "amount_sy": data["amount_sy"],
                "eshansy_points": data["eshansy_points"],
                "username": user.eshansy_account
            }
        )
        
        pending = await DataManager.get_pending_requests()
        pending[request_id] = pending_request
        await DataManager.save_pending_requests(pending)
        
        await DataManager.save_user(user)
        
        # Notify admins
        admin_message = (
            f"🔄 <b>طلب شحن ايشانسي جديد</b>\n\n"
            f"🆔 رقم الطلب: <code>{request_id}</code>\n"
            f"👤 المستخدم: {user_id}\n"
            f"👤 حساب ايشانسي: {user.eshansy_account}\n"
            f"💰 المبلغ: {data['amount_sy']:,.0f} ليرة\n"
            f"🎯 النقاط: {data['eshansy_points']:,} نقطة"
        )
        await send_to_admins(context, admin_message)
        
        await query.edit_message_text(
            f"✅ <b>تم شحن حسابك بنجاح!</b>\n\n"
            f"🎯 تم إضافة: <code>{data['eshansy_points']:,}</code> نقطة\n"
            f"💵 تم خصم: <code>{data['amount_sy']:,.0f}</code> ليرة\n\n"
            f"💰 رصيدك الحالي في ايشانسي: <code>{user.eshansy_balance:,}</code> نقطة",
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text("❌ تم إلغاء العملية.")
    
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="💼 اختر من قائمة ايشانسي:",
        reply_markup=get_eshansy_keyboard()
    )
    return States.ESHANSY_MENU

@require_subscription
async def eshansy_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await DataManager.get_user(user_id)
    
    if not user.eshansy_account:
        await update.message.reply_text(
            "❌ ليس لديك حساب ايشانسي.",
            reply_markup=get_eshansy_keyboard()
        )
        return States.ESHANSY_MENU
    
    await update.message.reply_text(
        "💸 <b>سحب من حساب ايشانسي</b>\n\n"
        "أدخل عدد النقاط المطلوب سحبها:\n"
        "ملاحظة: كل 100 نقطة = 1 ليرة\n\n"
        "مثال: لسحب 1000 ليرة، أدخل 100000",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
    )
    return States.ESHANSY_WITHDRAW

async def eshansy_withdraw_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        points = int(update.message.text.strip())
        if points <= 0:
            raise ValueError
        
        user_id = update.effective_user.id
        user = await DataManager.get_user(user_id)
        
        if user.eshansy_balance < points:
            await update.message.reply_text(
                f"❌ رصيدك في ايشانسي غير كافي.\n"
                f"🎯 رصيدك: {user.eshansy_balance:,} نقطة\n"
                f"💰 المطلوب: {points:,} نقطة",
                reply_markup=get_eshansy_keyboard()
            )
            return States.ESHANSY_MENU
        
        amount_sy = points / 100
        
        context.user_data["eshansy_withdraw"] = {
            "points": points,
            "amount_sy": amount_sy
        }
        
        await update.message.reply_text(
            f"📋 <b>تفاصيل السحب</b>\n\n"
            f"🎯 النقاط: <code>{points:,}</code> نقطة\n"
            f"💵 المبلغ: <code>{amount_sy:,.0f}</code> ليرة\n\n"
            f"💰 سيتم إضافة: <code>{amount_sy:,.0f}</code> ليرة إلى رصيدك\n\n"
            "هل تريد المتابعة؟",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ تأكيد", callback_data="confirm_eshansy_withdraw"),
                    InlineKeyboardButton("❌ إلغاء", callback_data="cancel_eshansy_withdraw")
                ]
            ])
        )
        return States.ESHANSY_WITHDRAW
    except ValueError:
        await update.message.reply_text(
            "❌ الرقم غير صحيح. الرجاء إدخال عدد صحيح.\n"
            "مثال: 100000",
            reply_markup=get_eshansy_keyboard()
        )
        return States.ESHANSY_MENU

async def eshansy_withdraw_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_eshansy_withdraw":
        data = context.user_data.get("eshansy_withdraw")
        if not data:
            await query.edit_message_text("❌ انتهت صلاحية البيانات.")
            return States.ESHANSY_MENU
        
        user_id = query.from_user.id
        user = await DataManager.get_user(user_id)
        
        if user.eshansy_balance < data["points"]:
            await query.edit_message_text("❌ رصيدك غير كافي.")
            return States.ESHANSY_MENU
        
        # Create pending request
        request_id = generate_request_id()
        pending_request = PendingRequest(
            request_id=request_id,
            user_id=user_id,
            req_type="eshansy_withdraw",
            data={
                "points": data["points"],
                "amount_sy": data["amount_sy"],
                "username": user.eshansy_account
            }
        )
        
        pending = await DataManager.get_pending_requests()
        pending[request_id] = pending_request
        await DataManager.save_pending_requests(pending)
        
        # Notify admins
        admin_message = (
            f"🔄 <b>طلب سحب ايشانسي جديد</b>\n\n"
            f"🆔 رقم الطلب: <code>{request_id}</code>\n"
            f"👤 المستخدم: {user_id}\n"
            f"👤 حساب ايشانسي: {user.eshansy_account}\n"
            f"🎯 النقاط: {data['points']:,} نقطة\n"
            f"💰 المبلغ: {data['amount_sy']:,.0f} ليرة"
        )
        await send_to_admins(context, admin_message)
        
        await query.edit_message_text(
            f"✅ <b>تم تقديم طلب السحب!</b>\n\n"
            f"🎯 طلب سحب: <code>{data['points']:,}</code> نقطة\n"
            f"💵 سيصلك: <code>{data['amount_sy']:,.0f}</code> ليرة\n\n"
            "📨 تم إرسال الطلب للإدارة. سيتم المعالجة قريباً.",
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text("❌ تم إلغاء العملية.")
    
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="💼 اختر من قائمة ايشانسي:",
        reply_markup=get_eshansy_keyboard()
    )
    return States.ESHANSY_MENU

@require_subscription
async def eshansy_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await DataManager.get_user(user_id)
    
    if not user.eshansy_account:
        await update.message.reply_text(
            "❌ ليس لديك حساب ايشانسي لحذفه.",
            reply_markup=get_eshansy_keyboard()
        )
        return States.ESHANSY_MENU
    
    if user.eshansy_balance > 0:
        await update.message.reply_text(
            f"⚠️ <b>تحذير!</b>\n\n"
            f"💰 لديك رصيد في حساب ايشانسي: <code>{user.eshansy_balance:,}</code> نقطة\n\n"
            "يجب سحب رصيدك أولاً قبل حذف الحساب.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_eshansy_keyboard()
        )
        return States.ESHANSY_MENU
    
    await update.message.reply_text(
        "🗑️ <b>حذف حساب ايشانسي</b>\n\n"
        "⚠️ <i>سيتم فصل حساب ايشانسي عن حسابك في البوت فقط.\n"
        "يمكنك استلام حساب جديد لاحقاً.</i>\n\n"
        "هل أنت متأكد من حذف الحساب؟",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ نعم، احذف", callback_data="confirm_delete_eshansy"),
                InlineKeyboardButton("❌ إلغاء", callback_data="cancel_delete_eshansy")
            ]
        ])
    )
    return States.ESHANSY_MENU

async def eshansy_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_delete_eshansy":
        user_id = query.from_user.id
        user = await DataManager.get_user(user_id)
        
        if user.eshansy_account:
            # Free the account
            accounts = await DataManager.get_accounts()
            account = accounts.get(user.eshansy_account)
            if account:
                account.assigned_to = None
                account.assigned_at = None
                await DataManager.save_accounts(accounts)
            
            old_account = user.eshansy_account
            user.eshansy_account = None
            user.eshansy_balance = 0
            await DataManager.save_user(user)
            
            await query.edit_message_text(
                f"✅ <b>تم حذف الحساب بنجاح!</b>\n\n"
                f"👤 الحساب المحذوف: <code>{old_account}</code>\n\n"
                "يمكنك استلام حساب جديد عندما تحتاجه.",
                parse_mode=ParseMode.HTML
            )
        else:
            await query.edit_message_text("❌ ليس لديك حساب لحذفه.")
    else:
        await query.edit_message_text("❌ تم إلغاء العملية.")
    
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="💼 اختر من قائمة ايشانسي:",
        reply_markup=get_eshansy_keyboard()
    )
    return States.ESHANSY_MENU

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ القائمة الرئيسية:",
        reply_markup=get_main_keyboard()
    )
    return States.MAIN_MENU

# ==================== TOPUP FUNCTIONS ====================
@require_subscription
async def topup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "➕ <b>شحن رصيد البوت</b>\n\n"
        "اختر طريقة الشحن:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_topup_methods_keyboard()
    )
    return States.TOPUP_METHOD

async def topup_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "topup_sham":
        await query.edit_message_text(
            "💳 <b>شام كاش</b>\n\n"
            "📍 للشحن عبر شام كاش:\n"
            "تواصل مع الدعم مباشرة:\n"
            f"{SUPPORT_USERNAME}",
            parse_mode=ParseMode.HTML
        )
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="⚡ اختر من القائمة:",
            reply_markup=get_main_keyboard()
        )
        return States.MAIN_MENU
    elif query.data == "topup_syriatel":
        await query.edit_message_text(
            "📲 <b>سيرياتيل كاش</b>\n\n"
            "اختر الكود الذي ستحول له:",
            reply_markup=get_syriatel_codes_keyboard()
        )
        return States.TOPUP_SYRIA_CODE
    elif query.data.startswith("code_"):
        code = query.data[5:]
        context.user_data["topup_code"] = code
        
        await query.edit_message_text(
            f"📞 <b>الكود المختار: {code}</b>\n\n"
            "الآن أدخل رقم عملية التحويل (رقم التحويل):\n"
            "مثال: 123456789",
            parse_mode=ParseMode.HTML
        )
        return States.TOPUP_SYRIA_REF
    elif query.data == "back":
        await query.edit_message_text(
            "➕ <b>شحن رصيد البوت</b>\n\n"
            "اختر طريقة الشحن:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_topup_methods_keyboard()
        )
        return States.TOPUP_METHOD

async def topup_ref_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ref_number = update.message.text.strip()
    
    if not ref_number.isdigit():
        await update.message.reply_text(
            "❌ رقم التحويل غير صحيح. يجب أن يكون أرقام فقط.\n"
            "الرجاء إعادة المحاولة:"
        )
        return States.TOPUP_SYRIA_REF
    
    context.user_data["topup_ref"] = ref_number
    
    await update.message.reply_text(
        f"✅ رقم التحويل: <code>{ref_number}</code>\n\n"
        f"أدخل المبلغ بالليرة السورية:\n"
        f"الحد الأدنى: {MIN_TOPUP:,} ليرة",
        parse_mode=ParseMode.HTML
    )
    return States.TOPUP_SYRIA_AMOUNT

async def topup_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        
        if amount < MIN_TOPUP:
            await update.message.reply_text(
                f"❌ المبلغ أقل من الحد الأدنى.\n"
                f"الحد الأدنى: {MIN_TOPUP:,} ليرة\n\n"
                "الرجاء إدخال مبلغ أكبر:"
            )
            return States.TOPUP_SYRIA_AMOUNT
        
        context.user_data["topup_amount"] = amount
        
        await update.message.reply_text(
            f"📋 <b>تفاصيل الشحن</b>\n\n"
            f"📞 الكود: <code>{context.user_data.get('topup_code')}</code>\n"
            f"🆔 رقم التحويل: <code>{context.user_data.get('topup_ref')}</code>\n"
            f"💰 المبلغ: <code>{amount:,.0f}</code> ليرة\n\n"
            "هل البيانات صحيحة؟",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ نعم، تأكيد", callback_data="confirm_topup"),
                    InlineKeyboardButton("❌ لا، إعادة", callback_data="cancel_topup")
                ]
            ])
        )
        return States.TOPUP_CONFIRM
    except ValueError:
        await update.message.reply_text(
            "❌ المبلغ غير صحيح. الرجاء إدخال رقم.\n"
            "مثال: 15000"
        )
        return States.TOPUP_SYRIA_AMOUNT

async def topup_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_topup":
        user_id = query.from_user.id
        
        # Create pending request
        request_id = generate_request_id()
        pending_request = PendingRequest(
            request_id=request_id,
            user_id=user_id,
            req_type="topup",
            data={
                "method": "syriatel",
                "code": context.user_data.get("topup_code"),
                "ref": context.user_data.get("topup_ref"),
                "amount": context.user_data.get("topup_amount")
            }
        )
        
        pending = await DataManager.get_pending_requests()
        pending[request_id] = pending_request
        await DataManager.save_pending_requests(pending)
        
        # Notify admins
        admin_message = (
            f"🔄 <b>طلب شحن جديد</b>\n\n"
            f"🆔 رقم الطلب: <code>{request_id}</code>\n"
            f"👤 المستخدم: {user_id}\n"
            f"📞 الكود: {context.user_data.get('topup_code')}\n"
            f"🆔 رقم التحويل: {context.user_data.get('topup_ref')}\n"
            f"💰 المبلغ: {context.user_data.get('topup_amount'):,.0f} ليرة\n"
            f"📱 الطريقة: سيرياتيل كاش"
        )
        await send_to_admins(context, admin_message)
        
        await query.edit_message_text(
            f"✅ <b>تم تقديم طلب الشحن بنجاح!</b>\n\n"
            f"🆔 رقم طلبك: <code>{request_id}</code>\n"
            f"💰 المبلغ: <code>{context.user_data.get('topup_amount'):,.0f}</code> ليرة\n\n"
            "📨 تم إرسال الطلب للإدارة. سيتم التحقق وإضافة الرصيد قريباً.",
            parse_mode=ParseMode.HTML
        )
        
        # Clear user data
        context.user_data.clear()
    else:
        await query.edit_message_text("❌ تم إلغاء الطلب.")
    
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="⚡ اختر من القائمة:",
        reply_markup=get_main_keyboard()
    )
    return States.MAIN_MENU

# ==================== WITHDRAW FUNCTIONS ====================
@require_subscription
async def withdraw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "➖ <b>سحب رصيد من البوت</b>\n\n"
        "اختر طريقة السحب:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_withdraw_methods_keyboard()
    )
    return States.WITHDRAW_METHOD

async def withdraw_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "withdraw_sham":
        await query.edit_message_text(
            "💳 <b>شام كاش</b>\n\n"
            "📍 للسحب عبر شام كاش:\n"
            "تواصل مع الدعم مباشرة:\n"
            f"{SUPPORT_USERNAME}",
            parse_mode=ParseMode.HTML
        )
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="⚡ اختر من القائمة:",
            reply_markup=get_main_keyboard()
        )
        return States.MAIN_MENU
    elif query.data == "withdraw_syriatel":
        await query.edit_message_text(
            "📲 <b>سيرياتيل كاش</b>\n\n"
            "أدخل رقم سيرياتيل المستلم:"
        )
        return States.WITHDRAW_SYRIA_NUMBER

async def withdraw_number_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone_number = update.message.text.strip()
    
    if not phone_number.isdigit() or len(phone_number) < 10:
        await update.message.reply_text(
            "❌ رقم الهاتف غير صحيح.\n"
            "الرجاء إدخال رقم سيرياتيل صحيح:\n"
            "مثال: 0991234567"
        )
        return States.WITHDRAW_SYRIA_NUMBER
    
    context.user_data["withdraw_phone"] = phone_number
    
    await update.message.reply_text(
        f"📞 رقم المستلم: <code>{phone_number}</code>\n\n"
        f"أدخل المبلغ بالليرة السورية:\n"
        f"الحد الأدنى: {MIN_WITHDRAW:,} ليرة",
        parse_mode=ParseMode.HTML
    )
    return States.WITHDRAW_SYRIA_AMOUNT

async def withdraw_amount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        user_id = update.effective_user.id
        user = await DataManager.get_user(user_id)
        
        available_balance = user.balance - user.hold
        
        if amount < MIN_WITHDRAW:
            await update.message.reply_text(
                f"❌ المبلغ أقل من الحد الأدنى.\n"
                f"الحد الأدنى: {MIN_WITHDRAW:,} ليرة\n\n"
                "الرجاء إدخال مبلغ أكبر:"
            )
            return States.WITHDRAW_SYRIA_AMOUNT
        
        if amount > available_balance:
            await update.message.reply_text(
                f"❌ رصيدك غير كافي.\n"
                f"💵 الرصيد المتاح: {available_balance:,.0f} ليرة\n"
                f"💰 المطلوب: {amount:,.0f} ليرة\n\n"
                "الرجاء إدخال مبلغ أقل:"
            )
            return States.WITHDRAW_SYRIA_AMOUNT
        
        context.user_data["withdraw_amount"] = amount
        
        await update.message.reply_text(
            f"📋 <b>تفاصيل السحب</b>\n\n"
            f"📞 رقم المستلم: <code>{context.user_data.get('withdraw_phone')}</code>\n"
            f"💰 المبلغ: <code>{amount:,.0f}</code> ليرة\n\n"
            f"💳 سيتم خصم: <code>{amount:,.0f}</code> ليرة من رصيدك\n\n"
            "هل تريد المتابعة؟",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ نعم، تأكيد", callback_data="confirm_withdraw"),
                    InlineKeyboardButton("❌ لا، إلغاء", callback_data="cancel_withdraw")
                ]
            ])
        )
        return States.WITHDRAW_CONFIRM
    except ValueError:
        await update.message.reply_text(
            "❌ المبلغ غير صحيح. الرجاء إدخال رقم.\n"
            "مثال: 500"
        )
        return States.WITHDRAW_SYRIA_AMOUNT

async def withdraw_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirm_withdraw":
        user_id = query.from_user.id
        user = await DataManager.get_user(user_id)
        amount = context.user_data.get("withdraw_amount")
        
        # Check balance again
        available_balance = user.balance - user.hold
        if amount > available_balance:
            await query.edit_message_text("❌ رصيدك غير كافي.")
            await context.bot.send_message(
                chat_id=user_id,
                text="⚡ اختر من القائمة:",
                reply_markup=get_main_keyboard()
            )
            return States.MAIN_MENU
        
        # Hold the amount
        user.balance -= amount
        user.hold += amount
        await DataManager.save_user(user)
        
        # Create pending request
        request_id = generate_request_id()
        pending_request = PendingRequest(
            request_id=request_id,
            user_id=user_id,
            req_type="withdraw",
            data={
                "method": "syriatel",
                "phone": context.user_data.get("withdraw_phone"),
                "amount": amount,
                "hold_amount": amount
            }
        )
        
        pending = await DataManager.get_pending_requests()
        pending[request_id] = pending_request
        await DataManager.save_pending_requests(pending)
        
        # Notify admins
        admin_message = (
            f"🔄 <b>طلب سحب جديد</b>\n\n"
            f"🆔 رقم الطلب: <code>{request_id}</code>\n"
            f"👤 المستخدم: {user_id}\n"
            f"📞 رقم المستلم: {context.user_data.get('withdraw_phone')}\n"
            f"💰 المبلغ: {amount:,.0f} ليرة\n"
            f"📱 الطريقة: سيرياتيل كاش\n"
            f"🔒 <i>تم حجز المبلغ من رصيد المستخدم</i>"
        )
        await send_to_admins(context, admin_message)
        
        await query.edit_message_text(
            f"✅ <b>تم تقديم طلب السحب بنجاح!</b>\n\n"
            f"🆔 رقم طلبك: <code>{request_id}</code>\n"
            f"💰 المبلغ: <code>{amount:,.0f}</code> ليرة\n"
            f"📞 إلى رقم: <code>{context.user_data.get('withdraw_phone')}</code>\n\n"
            f"🔒 <i>تم حجز المبلغ من رصيدك حتى معالجة الطلب</i>\n\n"
            "📨 تم إرسال الطلب للإدارة. سيتم التحويل قريباً.",
            parse_mode=ParseMode.HTML
        )
        
        # Clear user data
        context.user_data.clear()
    else:
        await query.edit_message_text("❌ تم إلغاء الطلب.")
    
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="⚡ اختر من القائمة:",
        reply_markup=get_main_keyboard()
    )
    return States.MAIN_MENU

@require_subscription
async def cancel_last_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    pending = await DataManager.get_pending_requests()
    user_pending = []
    
    for req_id, req in pending.items():
        if req.user_id == user_id and req.type == "withdraw" and req.status == "pending":
            user_pending.append((req.created_at, req_id, req))
    
    if not user_pending:
        await update.message.reply_text(
            "❌ لا توجد طلبات سحب معلقة.",
            reply_markup=get_main_keyboard()
        )
        return States.MAIN_MENU
    
    # Get latest withdraw request
    user_pending.sort(reverse=True)
    latest_req = user_pending[0][2]
    
    # Check if it's within reasonable time (e.g., last 24 hours)
    req_time = datetime.fromisoformat(latest_req.created_at)
    time_diff = datetime.now() - req_time
    
    if time_diff.total_seconds() > 24 * 3600:  # 24 hours
        await update.message.reply_text(
            "❌ لا يمكن إلغاء الطلب بعد مرور 24 ساعة.",
            reply_markup=get_main_keyboard()
        )
        return States.MAIN_MENU
    
    await update.message.reply_text(
        f"🧾 <b>إلغاء آخر طلب سحب</b>\n\n"
        f"🆔 رقم الطلب: <code>{latest_req.request_id}</code>\n"
        f"💰 المبلغ: <code>{latest_req.data.get('amount', 0):,.0f}</code> ليرة\n"
        f"📞 إلى رقم: <code>{latest_req.data.get('phone', '')}</code>\n\n"
        "هل تريد إلغاء هذا الطلب؟",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ نعم، ألغي", callback_data=f"cancel_req_{latest_req.request_id}"),
                InlineKeyboardButton("❌ لا، احتفظ", callback_data="keep_request")
            ]
        ])
    )
    return States.MAIN_MENU

async def cancel_withdraw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("cancel_req_"):
        request_id = query.data[11:]
        
        pending = await DataManager.get_pending_requests()
        request = pending.get(request_id)
        
        if not request or request.status != "pending" or request.type != "withdraw":
            await query.edit_message_text("❌ الطلب غير موجود أو تم معالجته بالفعل.")
            return
        
        # Return held amount to user
        user = await DataManager.get_user(request.user_id)
        user.balance += request.data.get("amount", 0)
        user.hold -= request.data.get("amount", 0)
        
        # Mark as cancelled
        request.status = "cancelled"
        request.handled_by = query.from_user.id
        request.handled_at = datetime.now().isoformat()
        
        await DataManager.save_user(user)
        await DataManager.save_pending_requests(pending)
        
        # Notify admins
        admin_message = (
            f"❌ <b>تم إلغاء طلب سحب</b>\n\n"
            f"🆔 رقم الطلب: <code>{request_id}</code>\n"
            f"👤 المستخدم: {request.user_id}\n"
            f"👤 الملغي بواسطة: {query.from_user.id}\n"
            f"💰 المبلغ: {request.data.get('amount', 0):,.0f} ليرة\n\n"
            f"💵 <i>تم إرجاع المبلغ المحجوز إلى رصيد المستخدم</i>"
        )
        await send_to_admins(context, admin_message)
        
        await query.edit_message_text(
            f"✅ <b>تم إلغاء طلب السحب بنجاح!</b>\n\n"
            f"💰 تم إرجاع: <code>{request.data.get('amount', 0):,.0f}</code> ليرة إلى رصيدك\n"
            f"💵 رصيدك الحالي: <code>{user.balance:,.0f}</code> ليرة",
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text("❌ تم الإبقاء على الطلب.")
    
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="⚡ اختر من القائمة:",
        reply_markup=get_main_keyboard()
    )
    return States.MAIN_MENU

@require_subscription
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🆘 <b>الدعم الفني</b>\n\n"
        f"للتواصل مع الدعم:\n"
        f"👤 {SUPPORT_USERNAME}\n\n"
        f"📞 تواصل معنا لحل أي مشكلة أو استفسار.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )
    return States.MAIN_MENU

# ==================== ADMIN FUNCTIONS ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not await is_admin(user_id):
        await update.message.reply_text("❌ هذا القسم للأدمن فقط.")
        return States.MAIN_MENU
    
    is_super = await is_super_admin(user_id)
    
    # Get statistics
    users = await DataManager.get_all_users()
    total_users = len(users)
    active_users = len([u for u in users.values() if u.balance > 0 or u.eshansy_account])
    
    accounts = await DataManager.get_accounts()
    total_accounts = len(accounts)
    available_accounts = len([a for a in accounts.values() if not a.assigned_to])
    
    pending = await DataManager.get_pending_requests()
    pending_count = len([r for r in pending.values() if r.status == "pending"])
    
    total_balance = sum(u.balance for u in users.values())
    total_hold = sum(u.hold for u in users.values())
    
    message = (
        f"⚙️ <b>لوحة الأدمن</b>\n\n"
        f"📊 <b>الإحصائيات:</b>\n"
        f"👥 إجمالي المستخدمين: {total_users}\n"
        f"👤 المستخدمين النشطين: {active_users}\n"
        f"💼 حسابات ايشانسي: {total_accounts}\n"
        f"🆓 حسابات متاحة: {available_accounts}\n"
        f"📨 الطلبات المعلقة: {pending_count}\n"
        f"💰 إجمالي الأرصدة: {total_balance:,.0f} ليرة\n"
        f"🔒 إجمالي المحجوز: {total_hold:,.0f} ليرة\n\n"
        f"🛠️ اختر من القائمة:"
    )
    
    await update.message.reply_text(
        message,
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_keyboard(is_super)
    )
    
    context.user_data["admin_mode"] = True
    return States.MAIN_MENU

async def admin_handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("admin_mode"):
        return await handle_message(update, context)
    
    text = update.message.text
    
    if text.startswith("📊 الإحصائيات"):
        return await admin_panel(update, context)
    elif text.startswith("👥 المستخدمين"):
        return await admin_search_user(update, context)
    elif text.startswith("📨 الطلبات المعلقة"):
        return await admin_pending_requests(update, context)
    elif text.startswith("⚙️ إعدادات"):
        return await admin_settings(update, context)
    elif text.startswith("📢 رسالة جماعية"):
        return await admin_broadcast_start(update, context)
    elif text.startswith("💾 Backup/Restore"):
        return await admin_backup_restore(update, context)
    elif text.startswith("🔧 الصيانة"):
        return await admin_maintenance(update, context)
    else:
        await update.message.reply_text(
            "⚙️ اختر من قائمة الأدمن:",
            reply_markup=get_admin_keyboard(await is_super_admin(update.effective_user.id))
        )
        return States.MAIN_MENU

async def admin_pending_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = await DataManager.get_pending_requests()
    pending_list = [r for r in pending.values() if r.status == "pending"]
    
    if not pending_list:
        await update.message.reply_text(
            "✅ لا توجد طلبات معلقة حالياً.",
            reply_markup=get_admin_keyboard(await is_super_admin(update.effective_user.id))
        )
        return States.MAIN_MENU
    
    # Group by type
    requests_by_type = {}
    for req in pending_list:
        if req.type not in requests_by_type:
            requests_by_type[req.type] = []
        requests_by_type[req.type].append(req)
    
    message = "📨 <b>الطلبات المعلقة</b>\n\n"
    
    for req_type, reqs in requests_by_type.items():
        type_name = {
            "topup": "شحن رصيد",
            "withdraw": "سحب رصيد",
            "eshansy_topup": "شحن ايشانسي",
            "eshansy_withdraw": "سحب ايشانسي"
        }.get(req_type, req_type)
        
        message += f"📌 <b>{type_name}:</b> {len(reqs)} طلب\n"
    
    message += "\nاختر نوع الطلبات لعرضها:"
    
    keyboard = []
    for req_type in requests_by_type.keys():
        type_name = {
            "topup": "💳 شحن رصيد",
            "withdraw": "💸 سحب رصيد",
            "eshansy_topup": "💰 شحن ايشانسي",
            "eshansy_withdraw": "💼 سحب ايشانسي"
        }.get(req_type, req_type)
        
        keyboard.append([InlineKeyboardButton(type_name, callback_data=f"admin_show_{req_type}")])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")])
    
    await update.message.reply_text(
        message,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return States.MAIN_MENU

async def admin_show_requests_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "admin_back":
        await query.edit_message_text(
            "⚙️ اختر من قائمة الأدمن:",
            reply_markup=get_admin_keyboard(await is_super_admin(query.from_user.id))
        )
        return
    
    req_type = query.data[11:]  # Remove "admin_show_"
    
    pending = await DataManager.get_pending_requests()
    requests = [r for r in pending.values() if r.status == "pending" and r.type == req_type]
    
    if not requests:
        await query.edit_message_text(
            f"✅ لا توجد طلبات من نوع {req_type}.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع", callback_data="admin_back_requests")]
            ])
        )
        return
    
    # Show first request
    req = requests[0]
    context.user_data["current_request_index"] = 0
    context.user_data["current_requests"] = [r.request_id for r in requests]
    
    await show_request_detail(query, context, req)

async def show_request_detail(query, context, req):
    user = await DataManager.get_user(req.user_id)
    
    type_name = {
        "topup": "💳 طلب شحن رصيد",
        "withdraw": "💸 طلب سحب رصيد",
        "eshansy_topup": "💰 طلب شحن ايشانسي",
        "eshansy_withdraw": "💼 طلب سحب ايشانسي"
    }.get(req.type, req.type)
    
    message = f"{type_name}\n\n"
    message += f"🆔 رقم الطلب: <code>{req.request_id}</code>\n"
    message += f"👤 المستخدم: <code>{req.user_id}</code>\n"
    message += f"📅 التاريخ: {req.created_at}\n\n"
    
    if req.type == "topup":
        message += (
            f"📱 الطريقة: سيرياتيل كاش\n"
            f"📞 الكود: <code>{req.data.get('code')}</code>\n"
            f"🆔 رقم التحويل: <code>{req.data.get('ref')}</code>\n"
            f"💰 المبلغ: <code>{req.data.get('amount', 0):,.0f}</code> ليرة\n"
        )
    elif req.type == "withdraw":
        message += (
            f"📱 الطريقة: سيرياتيل كاش\n"
            f"📞 رقم المستلم: <code>{req.data.get('phone')}</code>\n"
            f"💰 المبلغ: <code>{req.data.get('amount', 0):,.0f}</code> ليرة\n"
            f"🔒 المبلغ المحجوز: <code>{req.data.get('hold_amount', 0):,.0f}</code> ليرة\n"
        )
    elif req.type == "eshansy_topup":
        message += (
            f"👤 حساب ايشانسي: <code>{req.data.get('username')}</code>\n"
            f"💰 المبلغ: <code>{req.data.get('amount_sy', 0):,.0f}</code> ليرة\n"
            f"🎯 النقاط: <code>{req.data.get('eshansy_points', 0):,}</code> نقطة\n"
        )
    elif req.type == "eshansy_withdraw":
        message += (
            f"👤 حساب ايشانسي: <code>{req.data.get('username')}</code>\n"
            f"🎯 النقاط: <code>{req.data.get('points', 0):,}</code> نقطة\n"
            f"💰 المبلغ: <code>{req.data.get('amount_sy', 0):,.0f}</code> ليرة\n"
        )
    
    message += f"\n💵 رصيد المستخدم: <code>{user.balance:,.0f}</code> ليرة"
    if user.eshansy_account:
        message += f"\n💼 رصيد ايشانسي: <code>{user.eshansy_balance:,}</code> نقطة"
    
    keyboard = get_pending_actions_keyboard(req.request_id)
    
    # Add navigation if multiple requests
    current_index = context.user_data.get("current_request_index", 0)
    requests_list = context.user_data.get("current_requests", [])
    
    if len(requests_list) > 1:
        nav_buttons = []
        if current_index > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"admin_nav_{current_index-1}"))
        if current_index < len(requests_list) - 1:
            nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"admin_nav_{current_index+1}"))
        
        if nav_buttons:
            keyboard.inline_keyboard.append(nav_buttons)
    
    await query.edit_message_text(
        message,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

async def admin_request_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("admin_nav_"):
        index = int(query.data[10:])
        context.user_data["current_request_index"] = index
        
        request_id = context.user_data["current_requests"][index]
        pending = await DataManager.get_pending_requests()
        req = pending.get(request_id)
        
        if req:
            await show_request_detail(query, context, req)
        return
    
    if query.data.startswith("approve_"):
        request_id = query.data[8:]
        await handle_approve_request(query, context, request_id)
    elif query.data.startswith("reject_"):
        request_id = query.data[7:]
        await handle_reject_request(query, context, request_id)
    elif query.data.startswith("edit_"):
        request_id = query.data[5:]
        await handle_edit_request(query, context, request_id)

async def handle_approve_request(query, context, request_id):
    pending = await DataManager.get_pending_requests()
    request = pending.get(request_id)
    
    if not request or request.status != "pending":
        await query.answer("❌ الطلب غير موجود أو تم معالجته بالفعل.", show_alert=True)
        return
    
    user = await DataManager.get_user(request.user_id)
    
    if request.type == "topup":
        # Add balance to user
        user.balance += request.data.get("amount", 0)
        await DataManager.save_user(user)
        
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=request.user_id,
                text=f"✅ <b>تمت الموافقة على طلب الشحن!</b>\n\n"
                     f"🆔 رقم الطلب: <code>{request_id}</code>\n"
                     f"💰 المبلغ: <code>{request.data.get('amount', 0):,.0f}</code> ليرة\n"
                     f"💵 تم إضافة المبلغ إلى رصيدك.\n"
                     f"💰 رصيدك الحالي: <code>{user.balance:,.0f}</code> ليرة",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to notify user: {e}")
    
    elif request.type == "withdraw":
        # Keep hold amount as is (already held during request)
        # Just mark as approved
        pass
    
    elif request.type == "eshansy_topup":
        # Already processed during request creation
        pass
    
    elif request.type == "eshansy_withdraw":
        # Deduct from eshansy balance and add to user balance
        if user.eshansy_balance >= request.data.get("points", 0):
            user.eshansy_balance -= request.data.get("points", 0)
            user.balance += request.data.get("amount_sy", 0)
            await DataManager.save_user(user)
            
            try:
                await context.bot.send_message(
                    chat_id=request.user_id,
                    text=f"✅ <b>تمت الموافقة على سحب ايشانسي!</b>\n\n"
                         f"🆔 رقم الطلب: <code>{request_id}</code>\n"
                         f"🎯 النقاط المسحوبة: <code>{request.data.get('points', 0):,}</code>\n"
                         f"💰 المبلغ المضاف: <code>{request.data.get('amount_sy', 0):,.0f}</code> ليرة\n"
                         f"💰 رصيدك الحالي: <code>{user.balance:,.0f}</code> ليرة",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Failed to notify user: {e}")
    
    # Update request status
    request.status = "approved"
    request.handled_by = query.from_user.id
    request.handled_at = datetime.now().isoformat()
    
    await DataManager.save_pending_requests(pending)
    
    # Remove from current view
    requests_list = context.user_data.get("current_requests", [])
    if request_id in requests_list:
        requests_list.remove(request_id)
        context.user_data["current_requests"] = requests_list
    
    # Show next request or go back
    if requests_list:
        next_index = min(context.user_data.get("current_request_index", 0), len(requests_list)-1)
        context.user_data["current_request_index"] = next_index
        next_request_id = requests_list[next_index]
        next_req = pending.get(next_request_id)
        
        if next_req:
            await show_request_detail(query, context, next_req)
        else:
            await query.edit_message_text(
                "✅ تمت الموافقة على الطلب.\n\n"
                "⚙️ اختر من قائمة الأدمن:",
                reply_markup=get_admin_keyboard(await is_super_admin(query.from_user.id))
            )
    else:
        await query.edit_message_text(
            "✅ تمت الموافقة على الطلب.\n\n"
            "⚙️ اختر من قائمة الأدمن:",
            reply_markup=get_admin_keyboard(await is_super_admin(query.from_user.id))
        )

async def handle_reject_request(query, context, request_id):
    pending = await DataManager.get_pending_requests()
    request = pending.get(request_id)
    
    if not request or request.status != "pending":
        await query.answer("❌ الطلب غير موجود أو تم معالجته بالفعل.", show_alert=True)
        return
    
    user = await DataManager.get_user(request.user_id)
    
    if request.type == "withdraw":
        # Return held amount to available balance
        user.balance += request.data.get("amount", 0)
        user.hold -= request.data.get("amount", 0)
        await DataManager.save_user(user)
        
        try:
            await context.bot.send_message(
                chat_id=request.user_id,
                text=f"❌ <b>تم رفض طلب السحب</b>\n\n"
                     f"🆔 رقم الطلب: <code>{request_id}</code>\n"
                     f"💰 المبلغ: <code>{request.data.get('amount', 0):,.0f}</code> ليرة\n"
                     f"💵 تم إرجاع المبلغ المحجوز إلى رصيدك.\n"
                     f"💰 رصيدك الحالي: <code>{user.balance:,.0f}</code> ليرة\n\n"
                     f"📍 للاستفسار: {SUPPORT_USERNAME}",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to notify user: {e}")
    
    elif request.type == "eshansy_withdraw":
        # Just reject, no balance changes needed
        try:
            await context.bot.send_message(
                chat_id=request.user_id,
                text=f"❌ <b>تم رفض طلب سحب ايشانسي</b>\n\n"
                     f"🆔 رقم الطلب: <code>{request_id}</code>\n"
                     f"🎯 النقاط: <code>{request.data.get('points', 0):,}</code>\n\n"
                     f"📍 للاستفسار: {SUPPORT_USERNAME}",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to notify user: {e}")
    
    # Update request status
    request.status = "rejected"
    request.handled_by = query.from_user.id
    request.handled_at = datetime.now().isoformat()
    
    await DataManager.save_pending_requests(pending)
    
    # Remove from current view
    requests_list = context.user_data.get("current_requests", [])
    if request_id in requests_list:
        requests_list.remove(request_id)
        context.user_data["current_requests"] = requests_list
    
    # Show next request or go back
    if requests_list:
        next_index = min(context.user_data.get("current_request_index", 0), len(requests_list)-1)
        context.user_data["current_request_index"] = next_index
        next_request_id = requests_list[next_index]
        next_req = pending.get(next_request_id)
        
        if next_req:
            await show_request_detail(query, context, next_req)
        else:
            await query.edit_message_text(
                "❌ تم رفض الطلب.\n\n"
                "⚙️ اختر من قائمة الأدمن:",
                reply_markup=get_admin_keyboard(await is_super_admin(query.from_user.id))
            )
    else:
        await query.edit_message_text(
            "❌ تم رفض الطلب.\n\n"
            "⚙️ اختر من قائمة الأدمن:",
            reply_markup=get_admin_keyboard(await is_super_admin(query.from_user.id))
        )

async def handle_edit_request(query, context, request_id):
    await query.answer("⚠️ هذه الميزة قيد التطوير", show_alert=True)

async def admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_super = await is_super_admin(user_id)
    
    message = "⚙️ <b>إعدادات الأدمن</b>\n\n"
    
    if is_super:
        message += "👑 <b>أنت أدمن رئيسي</b>\n\n"
        message += "🔧 <b>الخيارات المتاحة:</b>\n"
        message += "1️⃣ تعديل رصيد مستخدم\n"
        message += "2️⃣ إضافة حساب ايشانسي\n"
        message += "3️⃣ حذف حساب ايشانسي\n"
        message += "4️⃣ تعيين أدمن مساعد\n"
        message += "5️⃣ إزالة أدمن مساعد\n"
    else:
        message += "👨‍💼 <b>أنت أدمن مساعد</b>\n\n"
        message += "🔧 <b>الخيارات المتاحة:</b>\n"
        message += "1️⃣ عرض طلبات المستخدمين\n"
        message += "2️⃣ قبول/رفض الطلبات\n"
    
    keyboard = []
    if is_super:
        keyboard.append([InlineKeyboardButton("➕ إضافة حساب ايشانسي", callback_data="admin_add_account")])
        keyboard.append([InlineKeyboardButton("➖ حذف حساب ايشانسي", callback_data="admin_delete_account")])
        keyboard.append([InlineKeyboardButton("💰 تعديل رصيد مستخدم", callback_data="admin_modify_balance")])
        keyboard.append([InlineKeyboardButton("👤 تعيين أدمن مساعد", callback_data="admin_add_assistant")])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")])
    
    await update.message.reply_text(
        message,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return States.MAIN_MENU

async def admin_backup_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_super_admin(user_id):
        await update.message.reply_text("❌ هذه الميزة للأدمن الرئيسي فقط.")
        return States.MAIN_MENU
    
    # Create backup directory if not exists
    BACKUP_DIR.mkdir(exist_ok=True)
    
    # List existing backups
    backups = list(BACKUP_DIR.glob("*.zip"))
    
    message = "💾 <b>Backup / Restore</b>\n\n"
    
    if backups:
        message += "📁 <b>النسخ الاحتياطية المتاحة:</b>\n"
        for backup in backups[-5:]:  # Show last 5 backups
            size = backup.stat().st_size / 1024  # Size in KB
            message += f"• {backup.name} ({size:.1f} KB)\n"
    else:
        message += "❌ لا توجد نسخ احتياطية.\n"
    
    message += "\n🔧 اختر الإجراء:"
    
    keyboard = [
        [InlineKeyboardButton("📥 إنشاء Backup", callback_data="admin_backup")],
        [InlineKeyboardButton("📤 Restore من ملف", callback_data="admin_restore")]
    ]
    
    if backups:
        keyboard.append([InlineKeyboardButton("🗑️ حذف جميع النسخ", callback_data="admin_delete_backups")])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")])
    
    await update.message.reply_text(
        message,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return States.MAIN_MENU

async def admin_backup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "admin_backup":
        try:
            # Create backup
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = BACKUP_DIR / f"backup_{timestamp}.zip"
            
            with zipfile.ZipFile(backup_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # Add all JSON files
                for file_path in [USERS_FILE, ACCOUNTS_FILE, PENDING_FILE, ADMINS_FILE, MAINTENANCE_FILE]:
                    if file_path.exists():
                        zipf.write(file_path, file_path.name)
            
            # Send file
            with open(backup_file, 'rb') as f:
                await context.bot.send_document(
                    chat_id=query.from_user.id,
                    document=InputFile(f, filename=backup_file.name),
                    caption=f"✅ تم إنشاء Backup بنجاح\n📁 {backup_file.name}"
                )
            
            await query.edit_message_text(
                "✅ تم إرسال ملف Backup إليك.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 رجوع", callback_data="admin_backup_restore")]
                ])
            )
        except Exception as e:
            logger.error(f"Backup error: {e}")
            await query.edit_message_text(
                f"❌ حدث خطأ أثناء إنشاء Backup:\n{str(e)}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 رجوع", callback_data="admin_backup_restore")]
                ])
            )
    elif query.data == "admin_restore":
        await query.edit_message_text(
            "📤 <b>استعادة من Backup</b>\n\n"
            "⚠️ <b>تحذير:</b> سيتم تفعيل وضع الصيانة تلقائياً.\n"
            "يرجى إرسال ملف ZIP الذي يحتوي على ملفات JSON.\n\n"
            "❌ أرسل 'إلغاء' للإلغاء.",
            parse_mode=ParseMode.HTML
        )
        context.user_data["awaiting_restore"] = True
    elif query.data == "admin_delete_backups":
        # Delete all backups
        for backup in BACKUP_DIR.glob("*.zip"):
            backup.unlink()
        
        await query.edit_message_text(
            "✅ تم حذف جميع النسخ الاحتياطية.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع", callback_data="admin_backup_restore")]
            ])
        )
    elif query.data == "admin_backup_restore":
        await query.edit_message_text(
            "💾 اختر الإجراء:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 إنشاء Backup", callback_data="admin_backup")],
                [InlineKeyboardButton("📤 Restore من ملف", callback_data="admin_restore")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")]
            ])
        )

async def admin_restore_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_restore"):
        return
    
    if update.message.text and update.message.text.strip().lower() == "إلغاء":
        context.user_data.pop("awaiting_restore", None)
        await update.message.reply_text(
            "❌ تم إلغاء عملية الاستعادة.",
            reply_markup=get_admin_keyboard(await is_super_admin(update.effective_user.id))
        )
        return
    
    if not update.message.document:
        await update.message.reply_text("❌ يرجى إرسال ملف ZIP.")
        return
    
    if not update.message.document.file_name.endswith('.zip'):
        await update.message.reply_text("❌ الملف يجب أن يكون بصيغة ZIP.")
        return
    
    try:
        # Enable maintenance mode
        await DataManager.set_maintenance(True)
        
        # Download file
        file = await context.bot.get_file(update.message.document.file_id)
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        await file.download_to_drive(temp_file.name)
        
        # Extract and restore
        with zipfile.ZipFile(temp_file.name, 'r') as zipf:
            # Extract to temp directory
            temp_dir = tempfile.mkdtemp()
            zipf.extractall(temp_dir)
            
            # Restore files
            for file_name in ["users.json", "accounts.json", "pending.json", "admins.json", "maintenance.json"]:
                src = Path(temp_dir) / file_name
                dst = Path(DATA_DIR) / file_name
                if src.exists():
                    shutil.copy(src, dst)
        
        # Cleanup
        os.unlink(temp_file.name)
        shutil.rmtree(temp_dir)
        
        await update.message.reply_text(
            "✅ <b>تمت استعادة البيانات بنجاح!</b>\n\n"
            "🔧 <b>ملاحظة:</b> وضع الصيانة مفعل.\n"
            "يجب إغلاق وضع الصيانة يدوياً من لوحة الأدمن.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_admin_keyboard(await is_super_admin(update.effective_user.id))
        )
        
        context.user_data.pop("awaiting_restore", None)
        
    except Exception as e:
        logger.error(f"Restore error: {e}")
        await update.message.reply_text(
            f"❌ حدث خطأ أثناء الاستعادة:\n{str(e)}",
            reply_markup=get_admin_keyboard(await is_super_admin(update.effective_user.id))
        )
        await DataManager.set_maintenance(False)

async def admin_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_super_admin(user_id):
        await update.message.reply_text("❌ هذه الميزة للأدمن الرئيسي فقط.")
        return States.MAIN_MENU
    
    is_maintenance = await DataManager.is_maintenance()
    
    status = "🟢 <b>مفعّل</b>" if is_maintenance else "🔴 <b>معطّل</b>"
    
    await update.message.reply_text(
        f"🔧 <b>وضع الصيانة</b>\n\n"
        f"الحالة الحالية: {status}\n\n"
        "في وضع الصيانة:\n"
        "• لا يستطيع المستخدمون استخدام البوت\n"
        "• الأدمن فقط يمكنهم الوصول\n\n"
        "اختر الإجراء:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ تفعيل الصيانة", callback_data="maintenance_on"),
                InlineKeyboardButton("❌ تعطيل الصيانة", callback_data="maintenance_off")
            ],
            [InlineKeyboardButton("🔙 رجوع", callback_data="admin_back")]
        ])
    )
    return States.MAIN_MENU

async def maintenance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "maintenance_on":
        await DataManager.set_maintenance(True)
        status = "✅ <b>تم تفعيل وضع الصيانة</b>"
    else:
        await DataManager.set_maintenance(False)
        status = "❌ <b>تم تعطيل وضع الصيانة</b>"
    
    await query.edit_message_text(
        f"{status}\n\n"
        f"⚙️ اختر من قائمة الأدمن:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_keyboard(await is_super_admin(query.from_user.id))
    )

async def admin_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👥 <b>بحث عن مستخدم</b>\n\n"
        "أدخل أي من المعلومات التالية:\n"
        "• رقم المستخدم (User ID)\n"
        "• اسم مستخدم ايشانسي\n"
        "• جزء من اسم مستخدم ايشانسي\n\n"
        "أو أرسل 'الكل' لعرض جميع المستخدمين.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
    )
    
    context.user_data["admin_search"] = True
    return States.ADMIN_SEARCH_USER

async def admin_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_term = update.message.text.strip()
    
    users = await DataManager.get_all_users()
    
    if search_term.lower() == "الكل":
        # Show all users with pagination
        user_list = list(users.values())
        user_list.sort(key=lambda x: x.user_id)
        
        if not user_list:
            await update.message.reply_text("❌ لا يوجد مستخدمون.")
            return States.MAIN_MENU
        
        context.user_data["search_results"] = user_list
        context.user_data["search_index"] = 0
        
        await show_user_detail(update, context, user_list[0])
        return States.ADMIN_SEARCH_USER
    
    # Search by user ID
    if search_term.isdigit():
        user_id = int(search_term)
        user = users.get(user_id)
        if user:
            await show_user_detail(update, context, user)
            return States.ADMIN_SEARCH_USER
    
    # Search by eshansy username
    results = []
    for user in users.values():
        if user.eshansy_account and search_term.lower() in user.eshansy_account.lower():
            results.append(user)
    
    if not results:
        await update.message.reply_text(
            "❌ لم يتم العثور على مستخدمين.",
            reply_markup=get_admin_keyboard(await is_super_admin(update.effective_user.id))
        )
        return States.MAIN_MENU
    
    if len(results) == 1:
        await show_user_detail(update, context, results[0])
    else:
        context.user_data["search_results"] = results
        context.user_data["search_index"] = 0
        
        await show_user_detail(update, context, results[0])
    
    return States.ADMIN_SEARCH_USER

async def show_user_detail(update, context, user):
    message = (
        f"👤 <b>معلومات المستخدم</b>\n\n"
        f"🆔 الرقم: <code>{user.user_id}</code>\n"
        f"📅 تاريخ الإنشاء: {user.created_at}\n"
        f"✅ مشترك في القناة: {'نعم' if user.subscribed else 'لا'}\n"
        f"👑 أدمن: {'نعم' if user.is_admin else 'لا'}\n"
        f"👑 أدمن رئيسي: {'نعم' if user.is_super_admin else 'لا'}\n\n"
        f"💰 <b>المحفظة</b>\n"
        f"💵 الرصيد: <code>{user.balance:,.0f}</code> ليرة\n"
        f"🔒 المحجوز: <code>{user.hold:,.0f}</code> ليرة\n"
        f"⚖️ الإجمالي: <code>{user.balance + user.hold:,.0f}</code> ليرة\n"
    )
    
    if user.eshansy_account:
        message += (
            f"\n💼 <b>حساب ايشانسي</b>\n"
            f"👤 الحساب: <code>{user.eshansy_account}</code>\n"
            f"💰 الرصيد: <code>{user.eshansy_balance:,}</code> نقطة\n"
        )
    
    keyboard = []
    
    # Navigation buttons if there are multiple results
    results = context.user_data.get("search_results", [])
    current_index = context.user_data.get("search_index", 0)
    
    if len(results) > 1:
        nav_buttons = []
        if current_index > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"user_nav_{current_index-1}"))
        nav_buttons.append(InlineKeyboardButton(f"{current_index+1}/{len(results)}", callback_data="noop"))
        if current_index < len(results) - 1:
            nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"user_nav_{current_index+1}"))
        
        keyboard.append(nav_buttons)
    
    # Action buttons for super admin
    if await is_super_admin(update.effective_user.id):
        keyboard.append([
            InlineKeyboardButton("💰 تعديل الرصيد", callback_data=f"user_edit_{user.user_id}"),
            InlineKeyboardButton("👑 صلاحيات", callback_data=f"user_perms_{user.user_id}")
        ])
    
    keyboard.append([
        InlineKeyboardButton("📨 رسالة للمستخدم", callback_data=f"user_msg_{user.user_id}"),
        InlineKeyboardButton("🔙 رجوع", callback_data="admin_back_search")
    ])
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            message,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            message,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def admin_user_nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "noop":
        return
    
    if query.data.startswith("user_nav_"):
        index = int(query.data[9:])
        context.user_data["search_index"] = index
        
        results = context.user_data.get("search_results", [])
        if 0 <= index < len(results):
            await show_user_detail(update, context, results[index])
    
    elif query.data == "admin_back_search":
        await query.edit_message_text(
            "⚙️ اختر من قائمة الأدمن:",
            reply_markup=get_admin_keyboard(await is_super_admin(query.from_user.id))
        )

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📢 <b>رسالة جماعية</b>\n\n"
        "أرسل الرسالة التي تريد إرسالها لجميع المستخدمين.\n"
        "يمكن أن تكون:\n"
        "• نص\n"
        "• صورة مع تعليق\n"
        "• فيديو مع تعليق\n\n"
        "❌ أرسل 'إلغاء' للإلغاء.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
    )
    
    context.user_data["broadcast_mode"] = True
    return States.ADMIN_BROADCAST

async def admin_broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("broadcast_mode"):
        return
    
    if update.message.text and update.message.text.strip().lower() == "إلغاء":
        context.user_data.pop("broadcast_mode", None)
        await update.message.reply_text(
            "❌ تم إلغاء الرسالة الجماعية.",
            reply_markup=get_admin_keyboard(await is_super_admin(update.effective_user.id))
        )
        return
    
    context.user_data["broadcast_message"] = update.message
    
    # Ask for confirmation
    await update.message.reply_text(
        "✅ <b>تم استلام الرسالة</b>\n\n"
        "هل تريد إرسالها لجميع المستخدمين؟\n\n"
        f"👥 عدد المستخدمين: {len(await DataManager.get_all_users())}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ نعم، أرسل", callback_data="confirm_broadcast"),
                InlineKeyboardButton("❌ لا، ألغي", callback_data="cancel_broadcast")
            ]
        ])
    )
    
    return States.ADMIN_BROADCAST_CONFIRM

async def broadcast_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_broadcast":
        await query.edit_message_text("❌ تم إلغاء الرسالة الجماعية.")
        context.user_data.pop("broadcast_mode", None)
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="⚙️ اختر من قائمة الأدمن:",
            reply_markup=get_admin_keyboard(await is_super_admin(query.from_user.id))
        )
        return
    
    # Start broadcasting
    await query.edit_message_text("🔄 <b>جاري إرسال الرسالة...</b>", parse_mode=ParseMode.HTML)
    
    users = await DataManager.get_all_users()
    success = 0
    failed = 0
    
    broadcast_msg = context.user_data.get("broadcast_message")
    
    for user_id, user in users.items():
        try:
            if broadcast_msg.text:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=broadcast_msg.text,
                    parse_mode=ParseMode.HTML if broadcast_msg.parse_mode else None
                )
            elif broadcast_msg.photo:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=broadcast_msg.photo[-1].file_id,
                    caption=broadcast_msg.caption,
                    parse_mode=ParseMode.HTML if broadcast_msg.caption_entities else None
                )
            elif broadcast_msg.video:
                await context.bot.send_video(
                    chat_id=user_id,
                    video=broadcast_msg.video.file_id,
                    caption=broadcast_msg.caption,
                    parse_mode=ParseMode.HTML if broadcast_msg.caption_entities else None
                )
            
            success += 1
            await asyncio.sleep(0.05)  # Rate limiting
            
        except Exception as e:
            logger.error(f"Failed to send broadcast to {user_id}: {e}")
            failed += 1
    
    await query.edit_message_text(
        f"✅ <b>تم إرسال الرسالة بنجاح</b>\n\n"
        f"📊 <b>النتائج:</b>\n"
        f"✅ الناجح: {success}\n"
        f"❌ الفاشل: {failed}\n"
        f"👥 الإجمالي: {success + failed}",
        parse_mode=ParseMode.HTML
    )
    
    context.user_data.pop("broadcast_mode", None)
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="⚙️ اختر من قائمة الأدمن:",
        reply_markup=get_admin_keyboard(await is_super_admin(query.from_user.id))
    )

async def admin_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "⚙️ اختر من قائمة الأدمن:",
        reply_markup=get_admin_keyboard(await is_super_admin(query.from_user.id))
    )
    return States.MAIN_MENU

async def admin_back_requests_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await admin_pending_requests(update, context)

# ==================== ERROR HANDLER ====================
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception occurred:", exc_info=context.error)
    
    if update and update.effective_user:
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="❌ حدث خطأ غير متوقع. الرجاء المحاولة مرة أخرى."
            )
        except:
            pass

# ==================== MAIN FUNCTION ====================
def main():
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handler for main flow
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            States.MAIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_handle_message),
                CallbackQueryHandler(check_subscription_callback, pattern="^check_subscription$"),
                CallbackQueryHandler(eshansy_confirm_callback, pattern="^confirm_eshansy$|^reject_eshansy$"),
                CallbackQueryHandler(eshansy_topup_confirm_callback, pattern="^confirm_eshansy_topup$|^cancel_eshansy_topup$"),
                CallbackQueryHandler(eshansy_withdraw_confirm_callback, pattern="^confirm_eshansy_withdraw$|^cancel_eshansy_withdraw$"),
                CallbackQueryHandler(eshansy_delete_callback, pattern="^confirm_delete_eshansy$|^cancel_delete_eshansy$"),
                CallbackQueryHandler(topup_method_callback, pattern="^topup_|^code_|^back$"),
                CallbackQueryHandler(topup_confirm_callback, pattern="^confirm_topup$|^cancel_topup$"),
                CallbackQueryHandler(withdraw_method_callback, pattern="^withdraw_"),
                CallbackQueryHandler(withdraw_confirm_callback, pattern="^confirm_withdraw$|^cancel_withdraw$"),
                CallbackQueryHandler(cancel_withdraw_callback, pattern="^cancel_req_|^keep_request$"),
                CallbackQueryHandler(admin_show_requests_callback, pattern="^admin_show_|^admin_back$"),
                CallbackQueryHandler(admin_request_action_callback, pattern="^approve_|^reject_|^edit_|^admin_nav_"),
                CallbackQueryHandler(admin_backup_callback, pattern="^admin_backup$|^admin_restore$|^admin_delete_backups$|^admin_backup_restore$"),
                CallbackQueryHandler(maintenance_callback, pattern="^maintenance_"),
                CallbackQueryHandler(broadcast_confirm_callback, pattern="^confirm_broadcast$|^cancel_broadcast$"),
                CallbackQueryHandler(admin_user_nav_callback, pattern="^user_nav_|^admin_back_search$"),
                CallbackQueryHandler(admin_back_callback, pattern="^admin_back$"),
                CallbackQueryHandler(admin_back_requests_callback, pattern="^admin_back_requests$")
            ],
            States.ESHANSY_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, eshansy_menu_handler),
                CallbackQueryHandler(eshansy_confirm_callback, pattern="^confirm_eshansy$|^reject_eshansy$"),
                CallbackQueryHandler(eshansy_topup_confirm_callback, pattern="^confirm_eshansy_topup$|^cancel_eshansy_topup$"),
                CallbackQueryHandler(eshansy_withdraw_confirm_callback, pattern="^confirm_eshansy_withdraw$|^cancel_eshansy_withdraw$"),
                CallbackQueryHandler(eshansy_delete_callback, pattern="^confirm_delete_eshansy$|^cancel_delete_eshansy$")
            ],
            States.ESHANSY_CREATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, eshansy_create_account_handler),
                CallbackQueryHandler(eshansy_confirm_callback, pattern="^confirm_eshansy$|^reject_eshansy$")
            ],
            States.ESHANSY_TOPUP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, eshansy_topup_handler),
                CallbackQueryHandler(eshansy_topup_confirm_callback, pattern="^confirm_eshansy_topup$|^cancel_eshansy_topup$")
            ],
            States.ESHANSY_WITHDRAW: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, eshansy_withdraw_handler),
                CallbackQueryHandler(eshansy_withdraw_confirm_callback, pattern="^confirm_eshansy_withdraw$|^cancel_eshansy_withdraw$")
            ],
            States.TOPUP_METHOD: [
                CallbackQueryHandler(topup_method_callback)
            ],
            States.TOPUP_SYRIA_REF: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, topup_ref_handler)
            ],
            States.TOPUP_SYRIA_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, topup_amount_handler)
            ],
            States.TOPUP_CONFIRM: [
                CallbackQueryHandler(topup_confirm_callback)
            ],
            States.WITHDRAW_METHOD: [
                CallbackQueryHandler(withdraw_method_callback)
            ],
            States.WITHDRAW_SYRIA_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_number_handler)
            ],
            States.WITHDRAW_SYRIA_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount_handler)
            ],
            States.WITHDRAW_CONFIRM: [
                CallbackQueryHandler(withdraw_confirm_callback)
            ],
            States.ADMIN_BROADCAST: [
                MessageHandler(filters.ALL & ~filters.COMMAND, admin_broadcast_handler)
            ],
            States.ADMIN_BROADCAST_CONFIRM: [
                CallbackQueryHandler(broadcast_confirm_callback)
            ],
            States.ADMIN_SEARCH_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_search_handler),
                CallbackQueryHandler(admin_user_nav_callback)
            ]
        },
        fallbacks=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & filters.Regex("^🔙 رجوع$"), back_to_main)
        ],
        allow_reentry=True
    )
    
    # Add handlers
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("admin", admin_panel))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    # Start the bot
    print("🤖 البوت يعمل...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

async def eshansy_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text.startswith("📝 إنشاء / استلام حساب"):
        return await eshansy_create_account(update, context)
    elif text.startswith("💰 شحن حساب ايشانسي"):
        return await eshansy_topup(update, context)
    elif text.startswith("💸 سحب من حساب ايشانسي"):
        return await eshansy_withdraw(update, context)
    elif text.startswith("🗑️ حذف حساب ايشانسي"):
        return await eshansy_delete(update, context)
    elif text.startswith("🔙 رجوع"):
        return await back_to_main(update, context)
    else:
        await update.message.reply_text(
            "💼 اختر من قائمة ايشانسي:",
            reply_markup=get_eshansy_keyboard()
        )
        return States.ESHANSY_MENU

if __name__ == "__main__":
    main()
