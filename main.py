from __future__ import annotations

import os
import json
import time
import tempfile
import logging
import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# =========================
# Config
# =========================

def _to_int(x: str | None, default: int) -> int:
    try:
        return int(x) if x is not None else default
    except Exception:
        return default

@dataclass(frozen=True)
class Config:
    BOT_TOKEN: str
    SUPER_ADMIN_ID: int
    REQUIRED_CHANNEL: str
    SUPPORT_USERNAME: str
    DATA_DIR: str
    MIN_TOPUP: int
    MIN_WITHDRAW: int
    LOG_LEVEL: str

    @staticmethod
    def from_env() -> "Config":
        return Config(
            BOT_TOKEN=os.getenv("BOT_TOKEN", "").strip(),
            SUPER_ADMIN_ID=_to_int(os.getenv("SUPER_ADMIN_ID"), 0),
            REQUIRED_CHANNEL=os.getenv("REQUIRED_CHANNEL", "").strip(),
            SUPPORT_USERNAME=os.getenv("SUPPORT_USERNAME", "@support").strip(),
            DATA_DIR=os.getenv("DATA_DIR", "data").strip() or "data",
            MIN_TOPUP=_to_int(os.getenv("MIN_TOPUP"), 15000),
            MIN_WITHDRAW=_to_int(os.getenv("MIN_WITHDRAW"), 50000),
            LOG_LEVEL=(os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"),
        )

    def validate(self) -> Tuple[bool, str]:
        if not self.BOT_TOKEN:
            return False, "Missing BOT_TOKEN"
        if not self.SUPER_ADMIN_ID:
            return False, "Missing/invalid SUPER_ADMIN_ID"
        if not self.REQUIRED_CHANNEL:
            return False, "Missing REQUIRED_CHANNEL"
        if not self.REQUIRED_CHANNEL.startswith("@"):
            return False, "REQUIRED_CHANNEL must start with @"
        if not self.SUPPORT_USERNAME.startswith("@"):
            return False, "SUPPORT_USERNAME must start with @"
        return True, "OK"

# =========================
# Texts
# =========================

WELCOME = "أهلاً بك 👋\nاختر من القائمة بالأسفل."
NEED_SUB = "⚠️ يجب الاشتراك بالقناة أولاً لاستخدام البوت."
SUB_OK = "✅ تم التحقق من الاشتراك. أهلاً بك!"
SUB_FAIL = "❌ لم يتم العثور على اشتراكك بعد. اشترك ثم أعد المحاولة."
ERR_GENERIC = "حدث خطأ غير متوقع. حاول مرة أخرى لاحقاً."

WALLET_TEXT = "💰 محفظتك:\n- الرصيد: {balance}\n- المعلّق (Hold): {hold}"
SUPPORT_TEXT = "🆘 للدعم تواصل هنا: {support}"

ICH_MENU = "💼 حساب ايشانسي:\nاختر خدمة:"
ICH_CREATE_ASK_USER = "أرسل اسم المستخدم لحساب ايشانسي:"
ICH_CREATE_ASK_PASS = "أرسل كلمة المرور لحساب ايشانسي:"
ICH_AMOUNT_ASK = "أرسل المبلغ المطلوب:"

TOPUP_ASK_OP = "أرسل رقم العملية (سيرياتيل كاش):"
TOPUP_ASK_AMOUNT = "أرسل مبلغ الشحن (>= {min_topup}):"

WITHDRAW_ASK_RECEIVER = "أرسل رقم المستلم (سيرياتيل كاش):"
WITHDRAW_ASK_AMOUNT = "أرسل مبلغ السحب (>= {min_withdraw}):"

ADMIN_ONLY = "هذه الميزة للأدمن فقط."
ADMIN_MENU = "لوحة الأدمن ⚙️\nاختر خياراً:"
NO_PENDING = "لا يوجد طلبات معلّقة حالياً."
PENDING_TITLE = "📌 الطلبات المعلقة:"
ASK_USER_ID = "أرسل ID المستخدم (رقم) أو قم بتحويل رسالة منه."
USER_NOT_FOUND = "لم يتم العثور على المستخدم."
ASK_ADJUST_AMOUNT = "أرسل قيمة التعديل (مثال: 1000 أو -500):"
ADJUST_DONE = "✅ تم تعديل الرصيد."
ASK_EDIT_VALUES = "أرسل القيم الجديدة حسب النوع:\n- إنشاء ايشانسي: username,password\n- باقي الأنواع: amount"
EDIT_DONE = "✅ تم تحديث بيانات الطلب."
ORDER_UPDATED = "✅ تم تحديث حالة الطلب: {status}"

# =========================
# Constants
# =========================

ORDER_TOPUP = "topup"
ORDER_WITHDRAW = "withdraw"
ORDER_ICH_CREATE = "ichancy_create"
ORDER_ICH_TOPUP = "ichancy_topup"
ORDER_ICH_WITHDRAW = "ichancy_withdraw"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

CB_CHECK_SUB = "chk_sub"
CB_ORDER_APPROVE = "ord_ok"
CB_ORDER_REJECT = "ord_no"
CB_ORDER_EDIT = "ord_edit"

# =========================
# Helpers
# =========================

def parse_int(text: str) -> Optional[int]:
    try:
        return int((text or "").strip().replace(",", ""))
    except Exception:
        return None

def safe_str(s: str | None, max_len: int = 128) -> str:
    s = (s or "").strip()
    return s[:max_len]

def now_ts() -> int:
    return int(time.time())

def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

# =========================
# JSON Storage (async locks + atomic write)
# =========================

class JSONStorage:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._locks: Dict[str, asyncio.Lock] = {}
        os.makedirs(self.data_dir, exist_ok=True)
        self._ensure_file("users.json", {})
        self._ensure_file("wallet.json", {})
        self._ensure_file("orders.json", {"next_id": 1, "orders": []})
        self._ensure_file("logs.json", [])

    def _path(self, filename: str) -> str:
        return os.path.join(self.data_dir, filename)

    def _lock(self, filename: str) -> asyncio.Lock:
        if filename not in self._locks:
            self._locks[filename] = asyncio.Lock()
        return self._locks[filename]

    def _ensure_file(self, filename: str, default: Any) -> None:
        p = self._path(filename)
        if not os.path.exists(p):
            self._write_atomic_sync(filename, default)

    async def read(self, filename: str, default: Any) -> Any:
        async with self._lock(filename):
            p = self._path(filename)
            if not os.path.exists(p):
                await self.write(filename, default)
                return default
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                try:
                    os.replace(p, p + ".corrupted")
                except Exception:
                    pass
                await self.write(filename, default)
                return default

    async def write(self, filename: str, data: Any) -> None:
        async with self._lock(filename):
            self._write_atomic_sync(filename, data)

    def _write_atomic_sync(self, filename: str, data: Any) -> None:
        p = self._path(filename)
        os.makedirs(self.data_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=filename + ".", suffix=".tmp", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

# =========================
# Keyboards
# =========================

def kb_user_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("💼 حساب ايشانسي"), KeyboardButton("💰 محفظتي")],
            [KeyboardButton("➕ شحن رصيد البوت"), KeyboardButton("➖ سحب رصيد من البوت")],
            [KeyboardButton("🆘 دعم")],
        ],
        resize_keyboard=True,
    )

def kb_ichancy() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("1) إنشاء حساب ايشانسي"), KeyboardButton("2) شحن حساب ايشانسي")],
            [KeyboardButton("3) سحب من حساب ايشانسي")],
            [KeyboardButton("⬅️ رجوع")],
        ],
        resize_keyboard=True,
    )

def kb_admin() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📌 الطلبات المعلقة"), KeyboardButton("🔍 بحث مستخدم")],
            [KeyboardButton("💳 تعديل رصيد"), KeyboardButton("⬅️ رجوع")],
        ],
        resize_keyboard=True,
    )

def kb_subscribe(channel: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ اشترك بالقناة", url=f"https://t.me/{channel.lstrip('@')}")],
            [InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data=CB_CHECK_SUB)],
        ]
    )

def kb_order_actions(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ تعديل", callback_data=f"{CB_ORDER_EDIT}:{order_id}"),
                InlineKeyboardButton("✅ قبول", callback_data=f"{CB_ORDER_APPROVE}:{order_id}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"{CB_ORDER_REJECT}:{order_id}"),
            ]
        ]
    )

# =========================
# Business (users/wallet/orders)
# =========================

async def create_or_update_user(storage: JSONStorage, tg_user) -> None:
    users = await storage.read("users.json", {})
    uid = str(tg_user.id)
    old = users.get(uid, {})
    users[uid] = {
        "id": tg_user.id,
        "username": tg_user.username,
        "first_name": tg_user.first_name,
        "last_name": tg_user.last_name,
        "created_at": old.get("created_at") or now_ts(),
        "updated_at": now_ts(),
        "ichancy": old.get("ichancy"),
    }
    await storage.write("users.json", users)

async def get_user(storage: JSONStorage, user_id: int) -> Optional[dict]:
    users = await storage.read("users.json", {})
    return users.get(str(user_id))

async def set_ichancy(storage: JSONStorage, user_id: int, username: str, password: str) -> None:
    users = await storage.read("users.json", {})
    uid = str(user_id)
    if uid not in users:
        users[uid] = {"id": user_id, "created_at": now_ts()}
    users[uid]["ichancy"] = {"username": username, "password": password, "updated_at": now_ts()}
    users[uid]["updated_at"] = now_ts()
    await storage.write("users.json", users)

async def get_wallet(storage: JSONStorage, user_id: int) -> dict:
    wallet = await storage.read("wallet.json", {})
    uid = str(user_id)
    if uid not in wallet:
        wallet[uid] = {"balance": 0, "hold": 0}
        await storage.write("wallet.json", wallet)
    w = wallet[uid]
    w["balance"] = max(0, int(w.get("balance", 0)))
    w["hold"] = max(0, int(w.get("hold", 0)))
    return w

async def add_balance(storage: JSONStorage, user_id: int, amount: int) -> dict:
    wallet = await storage.read("wallet.json", {})
    uid = str(user_id)
    w = wallet.get(uid, {"balance": 0, "hold": 0})
    w["balance"] = max(0, int(w.get("balance", 0)) + int(amount))
    w["hold"] = max(0, int(w.get("hold", 0)))
    wallet[uid] = w
    await storage.write("wallet.json", wallet)
    return w

async def reserve_withdraw(storage: JSONStorage, user_id: int, amount: int) -> Tuple[bool, dict, str]:
    if amount <= 0:
        return False, await get_wallet(storage, user_id), "amount_invalid"
    wallet = await storage.read("wallet.json", {})
    uid = str(user_id)
    w = wallet.get(uid, {"balance": 0, "hold": 0})
    bal = int(w.get("balance", 0))
    hold = int(w.get("hold", 0))
    if bal < amount:
        return False, {"balance": bal, "hold": hold}, "insufficient"
    bal -= amount
    hold += amount
    wallet[uid] = {"balance": max(0, bal), "hold": max(0, hold)}
    await storage.write("wallet.json", wallet)
    return True, wallet[uid], "ok"

async def release_hold(storage: JSONStorage, user_id: int, amount: int) -> dict:
    wallet = await storage.read("wallet.json", {})
    uid = str(user_id)
    w = wallet.get(uid, {"balance": 0, "hold": 0})
    bal = int(w.get("balance", 0))
    hold = int(w.get("hold", 0))
    hold = max(0, hold - amount)
    bal = max(0, bal + amount)
    wallet[uid] = {"balance": bal, "hold": hold}
    await storage.write("wallet.json", wallet)
    return wallet[uid]

async def finalize_withdraw(storage: JSONStorage, user_id: int, amount: int) -> dict:
    wallet = await storage.read("wallet.json", {})
    uid = str(user_id)
    w = wallet.get(uid, {"balance": 0, "hold": 0})
    bal = int(w.get("balance", 0))
    hold = int(w.get("hold", 0))
    hold = max(0, hold - amount)
    wallet[uid] = {"balance": max(0, bal), "hold": hold}
    await storage.write("wallet.json", wallet)
    return wallet[uid]

async def create_order(storage: JSONStorage, order_type: str, user_id: int, data: dict) -> dict:
    obj = await storage.read("orders.json", {"next_id": 1, "orders": []})
    oid = int(obj.get("next_id", 1))
    order = {
        "id": oid,
        "type": order_type,
        "status": STATUS_PENDING,
        "user_id": int(user_id),
        "data": data,
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    obj["orders"] = list(obj.get("orders", [])) + [order]
    obj["next_id"] = oid + 1
    await storage.write("orders.json", obj)
    return order

async def list_pending(storage: JSONStorage, limit: int = 20) -> list[dict]:
    obj = await storage.read("orders.json", {"next_id": 1, "orders": []})
    orders = list(obj.get("orders", []))
    pending = [o for o in orders if o.get("status") == STATUS_PENDING]
    pending.sort(key=lambda x: int(x.get("id", 0)))
    return pending[:limit]

async def get_order(storage: JSONStorage, order_id: int) -> Optional[dict]:
    obj = await storage.read("orders.json", {"next_id": 1, "orders": []})
    for o in obj.get("orders", []):
        if int(o.get("id", 0)) == int(order_id):
            return o
    return None

async def update_order(storage: JSONStorage, order_id: int, patch: dict) -> Optional[dict]:
    obj = await storage.read("orders.json", {"next_id": 1, "orders": []})
    orders = list(obj.get("orders", []))
    updated = None
    for i, o in enumerate(orders):
        if int(o.get("id", 0)) == int(order_id):
            o.update(patch)
            o["updated_at"] = now_ts()
            orders[i] = o
            updated = o
            break
    if updated:
        obj["orders"] = orders
        await storage.write("orders.json", obj)
    return updated

# =========================
# Subscription gate
# =========================

def is_member_status(status: str) -> bool:
    return status in ("member", "administrator", "creator")

async def is_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channel = context.application.bot_data["cfg"].REQUIRED_CHANNEL
    user = update.effective_user
    if not channel or not user:
        return True
    try:
        member = await context.bot.get_chat_member(chat_id=channel, user_id=user.id)
        return is_member_status(getattr(member, "status", ""))
    except Exception:
        return False

async def send_sub_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    channel = context.application.bot_data["cfg"].REQUIRED_CHANNEL
    if update.message:
        await update.message.reply_text(NEED_SUB, reply_markup=kb_subscribe(channel), disable_web_page_preview=True)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(NEED_SUB, reply_markup=kb_subscribe(channel), disable_web_page_preview=True)

# =========================
# Handlers
# =========================

logger = logging.getLogger("brobotbro")

# Conversation states
(
    ST_TOPUP_OP,
    ST_TOPUP_AMOUNT,
    ST_WITHDRAW_RECEIVER,
    ST_WITHDRAW_AMOUNT,
    ST_ICH_MENU,
    ST_ICH_CREATE_USER,
    ST_ICH_CREATE_PASS,
    ST_ICH_AMOUNT_TOPUP,
    ST_ICH_AMOUNT_WITHDRAW,
) = range(9)

def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    cfg: Config = context.application.bot_data["cfg"]
    return bool(update.effective_user and int(update.effective_user.id) == int(cfg.SUPER_ADMIN_ID))

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    storage: JSONStorage = context.application.bot_data["storage"]
    await create_or_update_user(storage, update.effective_user)

    if not await is_subscribed(update, context):
        await send_sub_gate(update, context)
        return

    await update.message.reply_text(WELCOME, reply_markup=kb_user_main())

async def cb_check_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    if await is_subscribed(update, context):
        await q.message.reply_text(SUB_OK, reply_markup=kb_user_main())
    else:
        await q.message.reply_text(SUB_FAIL, reply_markup=kb_subscribe(context.application.bot_data["cfg"].REQUIRED_CHANNEL))

async def user_entry_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END

    if not await is_subscribed(update, context):
        await send_sub_gate(update, context)
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    cfg: Config = context.application.bot_data["cfg"]
    storage: JSONStorage = context.application.bot_data["storage"]

    if text == "💰 محفظتي":
        w = await get_wallet(storage, update.effective_user.id)
        await update.message.reply_text(WALLET_TEXT.format(balance=w["balance"], hold=w["hold"]), reply_markup=kb_user_main())
        return ConversationHandler.END

    if text == "🆘 دعم":
        await update.message.reply_text(SUPPORT_TEXT.format(support=cfg.SUPPORT_USERNAME), reply_markup=kb_user_main())
        return ConversationHandler.END

    if text == "💼 حساب ايشانسي":
        await update.message.reply_text(ICH_MENU, reply_markup=kb_ichancy())
        return ST_ICH_MENU

    if text == "➕ شحن رصيد البوت":
        context.user_data.clear()
        await update.message.reply_text(TOPUP_ASK_OP, reply_markup=kb_user_main())
        return ST_TOPUP_OP

    if text == "➖ سحب رصيد من البوت":
        context.user_data.clear()
        await update.message.reply_text(WITHDRAW_ASK_RECEIVER, reply_markup=kb_user_main())
        return ST_WITHDRAW_RECEIVER

    await update.message.reply_text("اختر من الأزرار بالأسفل 👇", reply_markup=kb_user_main())
    return ConversationHandler.END

# --- Topup flow
async def topup_get_op(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    op = safe_str(update.message.text, 64)
    if len(op) < 3:
        await update.message.reply_text("رقم العملية غير صحيح. أعد الإرسال.")
        return ST_TOPUP_OP
    context.user_data["topup_op"] = op
    cfg: Config = context.application.bot_data["cfg"]
    await update.message.reply_text(TOPUP_ASK_AMOUNT.format(min_topup=cfg.MIN_TOPUP))
    return ST_TOPUP_AMOUNT

async def topup_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    cfg: Config = context.application.bot_data["cfg"]
    storage: JSONStorage = context.application.bot_data["storage"]

    amount = parse_int(update.message.text or "")
    if amount is None or amount < cfg.MIN_TOPUP:
        await update.message.reply_text(f"المبلغ غير صحيح. يجب أن يكون >= {cfg.MIN_TOPUP}.")
        return ST_TOPUP_AMOUNT

    op = context.user_data.get("topup_op")
    order = await create_order(storage, ORDER_TOPUP, update.effective_user.id, {"operation_no": op, "amount": amount})

    await update.message.reply_text(f"✅ تم إرسال طلب الشحن للأدمن.\nرقم الطلب: #{order['id']}", reply_markup=kb_user_main())

    try:
        await context.bot.send_message(
            chat_id=cfg.SUPER_ADMIN_ID,
            text=format_order_admin(order),
            reply_markup=kb_order_actions(order["id"]),
        )
    except Exception:
        pass

    context.user_data.clear()
    return ConversationHandler.END

# --- Withdraw flow
async def withdraw_get_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    recv = safe_str(update.message.text, 64)
    if len(recv) < 3:
        await update.message.reply_text("رقم المستلم غير صحيح. أعد الإرسال.")
        return ST_WITHDRAW_RECEIVER
    context.user_data["withdraw_receiver"] = recv
    cfg: Config = context.application.bot_data["cfg"]
    await update.message.reply_text(WITHDRAW_ASK_AMOUNT.format(min_withdraw=cfg.MIN_WITHDRAW))
    return ST_WITHDRAW_AMOUNT

async def withdraw_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    cfg: Config = context.application.bot_data["cfg"]
    storage: JSONStorage = context.application.bot_data["storage"]

    amount = parse_int(update.message.text or "")
    if amount is None or amount < cfg.MIN_WITHDRAW:
        await update.message.reply_text(f"المبلغ غير صحيح. يجب أن يكون >= {cfg.MIN_WITHDRAW}.")
        return ST_WITHDRAW_AMOUNT

    ok, _, reason = await reserve_withdraw(storage, update.effective_user.id, amount)
    if not ok:
        if reason == "insufficient":
            await update.message.reply_text("❌ رصيدك لا يكفي لإتمام السحب.", reply_markup=kb_user_main())
        else:
            await update.message.reply_text("❌ تعذر تنفيذ العملية. أعد المحاولة.", reply_markup=kb_user_main())
        context.user_data.clear()
        return ConversationHandler.END

    recv = context.user_data.get("withdraw_receiver")
    order = await create_order(storage, ORDER_WITHDRAW, update.effective_user.id, {"receiver_no": recv, "amount": amount})

    await update.message.reply_text(f"✅ تم حجز المبلغ مباشرة (Hold).\nرقم الطلب: #{order['id']}", reply_markup=kb_user_main())

    try:
        await context.bot.send_message(
            chat_id=cfg.SUPER_ADMIN_ID,
            text=format_order_admin(order),
            reply_markup=kb_order_actions(order["id"]),
        )
    except Exception:
        pass

    context.user_data.clear()
    return ConversationHandler.END

# --- Ichancy submenu
async def ich_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    if text == "⬅️ رجوع":
        await update.message.reply_text("رجعناك للقائمة الرئيسية.", reply_markup=kb_user_main())
        return ConversationHandler.END

    if text.startswith("1)"):
        context.user_data.clear()
        await update.message.reply_text(ICH_CREATE_ASK_USER, reply_markup=kb_ichancy())
        return ST_ICH_CREATE_USER

    if text.startswith("2)"):
        context.user_data.clear()
        await update.message.reply_text(ICH_AMOUNT_ASK, reply_markup=kb_ichancy())
        return ST_ICH_AMOUNT_TOPUP

    if text.startswith("3)"):
        context.user_data.clear()
        await update.message.reply_text(ICH_AMOUNT_ASK, reply_markup=kb_ichancy())
        return ST_ICH_AMOUNT_WITHDRAW

    await update.message.reply_text("اختر خياراً صحيحاً من قائمة ايشانسي.", reply_markup=kb_ichancy())
    return ST_ICH_MENU

async def ich_create_get_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    u = safe_str(update.message.text, 64)
    if len(u) < 3:
        await update.message.reply_text("اسم المستخدم غير صحيح. أعد الإرسال.")
        return ST_ICH_CREATE_USER
    context.user_data["ich_user"] = u
    await update.message.reply_text(ICH_CREATE_ASK_PASS)
    return ST_ICH_CREATE_PASS

async def ich_create_get_pass(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    p = safe_str(update.message.text, 128)
    if len(p) < 3:
        await update.message.reply_text("كلمة المرور غير صحيحة. أعد الإرسال.")
        return ST_ICH_CREATE_PASS

    cfg: Config = context.application.bot_data["cfg"]
    storage: JSONStorage = context.application.bot_data["storage"]
    order = await create_order(storage, ORDER_ICH_CREATE, update.effective_user.id, {"username": context.user_data.get("ich_user"), "password": p})

    await update.message.reply_text(f"✅ تم إرسال طلب إنشاء حساب ايشانسي للأدمن.\nرقم الطلب: #{order['id']}", reply_markup=kb_user_main())

    try:
        await context.bot.send_message(
            chat_id=cfg.SUPER_ADMIN_ID,
            text=format_order_admin(order),
            reply_markup=kb_order_actions(order["id"]),
        )
    except Exception:
        pass

    context.user_data.clear()
    return ConversationHandler.END

async def ich_topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    amount = parse_int(update.message.text or "")
    if amount is None or amount <= 0:
        await update.message.reply_text("المبلغ غير صحيح. أعد الإرسال.")
        return ST_ICH_AMOUNT_TOPUP

    cfg: Config = context.application.bot_data["cfg"]
    storage: JSONStorage = context.application.bot_data["storage"]
    order = await create_order(storage, ORDER_ICH_TOPUP, update.effective_user.id, {"amount": amount})

    await update.message.reply_text(f"✅ تم إرسال طلب شحن ايشانسي للأدمن.\nرقم الطلب: #{order['id']}", reply_markup=kb_user_main())

    try:
        await context.bot.send_message(chat_id=cfg.SUPER_ADMIN_ID, text=format_order_admin(order), reply_markup=kb_order_actions(order["id"]))
    except Exception:
        pass

    context.user_data.clear()
    return ConversationHandler.END

async def ich_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    amount = parse_int(update.message.text or "")
    if amount is None or amount <= 0:
        await update.message.reply_text("المبلغ غير صحيح. أعد الإرسال.")
        return ST_ICH_AMOUNT_WITHDRAW

    cfg: Config = context.application.bot_data["cfg"]
    storage: JSONStorage = context.application.bot_data["storage"]
    order = await create_order(storage, ORDER_ICH_WITHDRAW, update.effective_user.id, {"amount": amount})

    await update.message.reply_text(f"✅ تم إرسال طلب سحب من ايشانسي للأدمن.\nرقم الطلب: #{order['id']}", reply_markup=kb_user_main())

    try:
        await context.bot.send_message(chat_id=cfg.SUPER_ADMIN_ID, text=format_order_admin(order), reply_markup=kb_order_actions(order["id"]))
    except Exception:
        pass

    context.user_data.clear()
    return ConversationHandler.END

# =========================
# Admin
# =========================

(AD_ST_MENU, AD_ST_FIND_USER, AD_ST_ADJUST_USER, AD_ST_ADJUST_AMOUNT) = range(4)

def format_order_admin(order: dict) -> str:
    return (
        f"🧾 #{order.get('id')} | {order.get('type')} | {order.get('status')}\n"
        f"user_id: {order.get('user_id')}\n"
        f"data: {order.get('data')}\n"
        f"created: {order.get('created_at')}"
    )

def extract_user_id(update: Update) -> Optional[int]:
    if update.message and update.message.forward_from:
        return int(update.message.forward_from.id)
    if update.message:
        n = parse_int(update.message.text or "")
        if n and n > 0:
            return int(n)
    return None

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_admin(update, context):
        await update.message.reply_text(ADMIN_ONLY)
        return ConversationHandler.END
    await update.message.reply_text(ADMIN_MENU, reply_markup=kb_admin())
    return AD_ST_MENU

async def admin_menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_admin(update, context):
        await update.message.reply_text(ADMIN_ONLY)
        return ConversationHandler.END

    storage: JSONStorage = context.application.bot_data["storage"]
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text("رجعناك لقائمة المستخدم.", reply_markup=kb_user_main())
        return ConversationHandler.END

    if text == "📌 الطلبات المعلقة":
        pending = await list_pending(storage, limit=20)
        if not pending:
            await update.message.reply_text(NO_PENDING)
            return AD_ST_MENU
        await update.message.reply_text(PENDING_TITLE)
        for o in pending:
            await update.message.reply_text(format_order_admin(o), reply_markup=kb_order_actions(o["id"]))
        return AD_ST_MENU

    if text == "🔍 بحث مستخدم":
        await update.message.reply_text(ASK_USER_ID)
        return AD_ST_FIND_USER

    if text == "💳 تعديل رصيد":
        await update.message.reply_text(ASK_USER_ID)
        return AD_ST_ADJUST_USER

    await update.message.reply_text("اختر خياراً من لوحة الأدمن.", reply_markup=kb_admin())
    return AD_ST_MENU

async def admin_find_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not is_admin(update, context):
        return ConversationHandler.END
    storage: JSONStorage = context.application.bot_data["storage"]

    uid = extract_user_id(update)
    if not uid:
        await update.message.reply_text("لم أفهم. أرسل ID رقمياً أو حوّل رسالة.")
        return AD_ST_FIND_USER

    u = await get_user(storage, uid)
    if not u:
        await update.message.reply_text(USER_NOT_FOUND)
        return AD_ST_MENU

    w = await get_wallet(storage, uid)
    ich = u.get("ichancy")
    await update.message.reply_text(
        f"👤 المستخدم: {uid}\n"
        f"username: @{u.get('username')}\n"
        f"الاسم: {u.get('first_name')} {u.get('last_name')}\n"
        f"ichancy: {ich if ich else 'لا يوجد'}\n"
        f"wallet: balance={w['balance']}, hold={w['hold']}\n"
    )
    return AD_ST_MENU

async def admin_adjust_user_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not is_admin(update, context):
        return ConversationHandler.END

    uid = extract_user_id(update)
    if not uid:
        await update.message.reply_text("أرسل ID رقمياً أو حوّل رسالة.")
        return AD_ST_ADJUST_USER

    context.user_data["admin_target_user"] = int(uid)
    await update.message.reply_text(ASK_ADJUST_AMOUNT)
    return AD_ST_ADJUST_AMOUNT

async def admin_adjust_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not is_admin(update, context):
        return ConversationHandler.END

    amt = parse_int(update.message.text or "")
    if amt is None:
        await update.message.reply_text("القيمة غير صحيحة. مثال: 1000 أو -500")
        return AD_ST_ADJUST_AMOUNT

    uid = context.user_data.get("admin_target_user")
    if not uid:
        await update.message.reply_text("لم يتم اختيار مستخدم.")
        return AD_ST_MENU

    storage: JSONStorage = context.application.bot_data["storage"]
    w = await add_balance(storage, int(uid), int(amt))
    await update.message.reply_text(f"{ADJUST_DONE}\nwallet الآن: balance={w['balance']}, hold={w['hold']}")
    return AD_ST_MENU

async def admin_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()

    if not is_admin(update, context):
        await q.message.reply_text(ADMIN_ONLY)
        return

    data = q.data or ""
    if ":" not in data:
        return
    action, sid = data.split(":", 1)
    order_id = parse_int(sid)
    if not order_id:
        return

    storage: JSONStorage = context.application.bot_data["storage"]
    order = await get_order(storage, order_id)
    if not order:
        await q.message.reply_text("الطلب غير موجود.")
        return

    if action == CB_ORDER_EDIT:
        context.user_data["edit_order_id"] = int(order_id)
        await q.message.reply_text(ASK_EDIT_VALUES)
        return

    if action == CB_ORDER_APPROVE:
        await approve_order(context, q, order)
        return

    if action == CB_ORDER_REJECT:
        await reject_order(context, q, order)
        return

async def admin_edit_listener(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not is_admin(update, context):
        return
    edit_id = context.user_data.get("edit_order_id")
    if not edit_id:
        return

    storage: JSONStorage = context.application.bot_data["storage"]
    order = await get_order(storage, int(edit_id))
    if not order:
        context.user_data.pop("edit_order_id", None)
        await update.message.reply_text("الطلب غير موجود.")
        return

    text = (update.message.text or "").strip()
    otype = order.get("type")

    if otype == ORDER_ICH_CREATE:
        if "," not in text:
            await update.message.reply_text("الصيغة غير صحيحة. أرسل: username,password")
            return
        u, p = text.split(",", 1)
        u = safe_str(u, 64)
        p = safe_str(p, 128)
        if len(u) < 3 or len(p) < 3:
            await update.message.reply_text("قيم غير صحيحة. أعد الإرسال.")
            return
        patch = {"data": {"username": u, "password": p}}
    else:
        amt = parse_int(text)
        if amt is None or amt <= 0:
            await update.message.reply_text("أرسل مبلغ صحيح (رقم موجب).")
            return
        d = dict(order.get("data", {}) or {})
        d["amount"] = int(amt)
        patch = {"data": d}

    updated = await update_order(storage, int(edit_id), patch)
    context.user_data.pop("edit_order_id", None)
    await update.message.reply_text(EDIT_DONE if updated else "تعذر تحديث الطلب.")

async def approve_order(context: ContextTypes.DEFAULT_TYPE, q, order: dict) -> None:
    storage: JSONStorage = context.application.bot_data["storage"]

    if order.get("status") != STATUS_PENDING:
        await q.message.reply_text("هذا الطلب ليس معلقاً.")
        return

    otype = order.get("type")
    user_id = int(order.get("user_id"))
    data = order.get("data", {}) or {}

    try:
        if otype == ORDER_TOPUP:
            amt = int(data.get("amount", 0))
            await add_balance(storage, user_id, amt)

        elif otype == ORDER_WITHDRAW:
            amt = int(data.get("amount", 0))
            await finalize_withdraw(storage, user_id, amt)

        elif otype == ORDER_ICH_CREATE:
            username = safe_str(data.get("username"), 64)
            password = safe_str(data.get("password"), 128)
            await set_ichancy(storage, user_id, username, password)

        await update_order(storage, int(order["id"]), {"status": STATUS_APPROVED})
        await q.message.reply_text(ORDER_UPDATED.format(status=STATUS_APPROVED))

        try:
            await context.bot.send_message(chat_id=user_id, text=f"✅ تم قبول طلبك #{order['id']} ({otype}).")
        except Exception:
            pass

    except Exception:
        logger.exception("Approve failed")
        await q.message.reply_text("تعذر قبول الطلب بسبب خطأ.")

async def reject_order(context: ContextTypes.DEFAULT_TYPE, q, order: dict) -> None:
    storage: JSONStorage = context.application.bot_data["storage"]

    if order.get("status") != STATUS_PENDING:
        await q.message.reply_text("هذا الطلب ليس معلقاً.")
        return

    otype = order.get("type")
    user_id = int(order.get("user_id"))
    data = order.get("data", {}) or {}

    try:
        if otype == ORDER_WITHDRAW:
            amt = int(data.get("amount", 0))
            await release_hold(storage, user_id, amt)

        await update_order(storage, int(order["id"]), {"status": STATUS_REJECTED})
        await q.message.reply_text(ORDER_UPDATED.format(status=STATUS_REJECTED))

        try:
            await context.bot.send_message(chat_id=user_id, text=f"❌ تم رفض طلبك #{order['id']} ({otype}).")
        except Exception:
            pass

    except Exception:
        logger.exception("Reject failed")
        await q.message.reply_text("تعذر رفض الطلب بسبب خطأ.")

# =========================
# Errors
# =========================

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(ERR_GENERIC)
    except Exception:
        pass

# =========================
# Main entry
# =========================

async def main() -> None:
    load_dotenv()
    cfg = Config.from_env()
    ok, msg = cfg.validate()
    setup_logging(cfg.LOG_LEVEL)

    log = logging.getLogger("startup")
    if not ok:
        log.critical("❌ Invalid config: %s", msg)
        raise SystemExit(1)

    os.makedirs(cfg.DATA_DIR, exist_ok=True)
    storage = JSONStorage(cfg.DATA_DIR)

    app: Application = ApplicationBuilder().token(cfg.BOT_TOKEN).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["storage"] = storage

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_check_sub, pattern=f"^{CB_CHECK_SUB}$"))

    app.add_handler(CallbackQueryHandler(admin_callbacks, pattern=r"^(ord_ok|ord_no|ord_edit):\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_listener), group=1)

    user_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, user_entry_router)],
        states={
            ST_TOPUP_OP: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_get_op)],
            ST_TOPUP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_get_amount)],
            ST_WITHDRAW_RECEIVER: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_get_receiver)],
            ST_WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_get_amount)],
            ST_ICH_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, ich_menu_handler)],
            ST_ICH_CREATE_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, ich_create_get_user)],
            ST_ICH_CREATE_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ich_create_get_pass)],
            ST_ICH_AMOUNT_TOPUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, ich_topup_amount)],
            ST_ICH_AMOUNT_WITHDRAW: [MessageHandler(filters.TEXT & ~filters.COMMAND, ich_withdraw_amount)],
        },
        fallbacks=[],
        name="user_conv",
        persistent=False,
    )
    app.add_handler(user_conv, group=2)

    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("admin", cmd_admin)],
        states={
            AD_ST_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_menu_router)],
            AD_ST_FIND_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_find_user)],
            AD_ST_ADJUST_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_adjust_user_pick)],
            AD_ST_ADJUST_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_adjust_amount)],
        },
        fallbacks=[],
        name="admin_conv",
        persistent=False,
    )
    app.add_handler(admin_conv, group=0)

    app.add_error_handler(on_error)

    log.info("✅ Bot started (polling). DATA_DIR=%s", cfg.DATA_DIR)
    await app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
        close_loop=False,
    )

if __name__ == "__main__":
    asyncio.run(main())
