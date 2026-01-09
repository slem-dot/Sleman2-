from __future__ import annotations

import os
import io
import re
import json
import time
import zipfile
import tempfile
import logging
import asyncio
import difflib
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
from telegram.constants import ChatMemberStatus
from telegram.error import RetryAfter, TimedOut, NetworkError, Forbidden

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

def _parse_codes(s: str) -> list[str]:
    parts = [p.strip() for p in (s or "").split(",")]
    return [p for p in parts if p]

@dataclass(frozen=True)
class Config:
    BOT_TOKEN: str
    SUPER_ADMIN_ID: int
    REQUIRED_CHANNEL: str
    SUPPORT_USERNAME: str
    DATA_DIR: str
    MIN_TOPUP: int
    MIN_WITHDRAW: int
    SYRIATEL_CODES: list[str]
    LOG_LEVEL: str

    @staticmethod
    def from_env() -> "Config":
        return Config(
            BOT_TOKEN=os.getenv("BOT_TOKEN", "").strip(),
            SUPER_ADMIN_ID=_to_int(os.getenv("SUPER_ADMIN_ID"), 0),
            REQUIRED_CHANNEL=os.getenv("REQUIRED_CHANNEL", "").strip(),
            SUPPORT_USERNAME=os.getenv("SUPPORT_USERNAME", "@support").strip(),
            DATA_DIR=(os.getenv("DATA_DIR", "data").strip() or "data"),
            MIN_TOPUP=_to_int(os.getenv("MIN_TOPUP"), 15000),
            MIN_WITHDRAW=_to_int(os.getenv("MIN_WITHDRAW"), 500),  # حسب طلبك: 500
            SYRIATEL_CODES=_parse_codes(os.getenv("SYRIATEL_CODES", "45191900,33333333,33333344")),
            LOG_LEVEL=(os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"),
        )

    def validate(self) -> Tuple[bool, str]:
        if not self.BOT_TOKEN:
            return False, "Missing BOT_TOKEN"
        if not self.SUPER_ADMIN_ID:
            return False, "Missing/invalid SUPER_ADMIN_ID"
        if not self.REQUIRED_CHANNEL or not self.REQUIRED_CHANNEL.startswith("@"):
            return False, "REQUIRED_CHANNEL must start with @"
        if not self.SUPPORT_USERNAME or not self.SUPPORT_USERNAME.startswith("@"):
            return False, "SUPPORT_USERNAME must start with @"
        return True, "OK"

def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

logger = logging.getLogger("brobot")

# =========================
# Texts (smart + emojis)
# =========================

TXT = {
    "welcome": "أهلاً بك 👋\nاختر الخدمة من الأزرار بالأسفل 👇",
    "need_sub": "⚠️ لازم تشترك بالقناة أولاً حتى تقدر تستخدم البوت.",
    "sub_ok": "✅ تم التحقق من الاشتراك! أهلاً وسهلاً 👋",
    "sub_fail": "❌ ما زال الاشتراك غير ظاهر.\nاشترك بالقناة ثم اضغط (تحقق) 🔄",

    "maintenance": "🔧 البوت الآن تحت الصيانة.\n⏳ جرّب لاحقاً من فضلك.",
    "support": "🆘 للدعم والمساعدة:\n{support}",

    "topup_methods_title": "➕ شحن رصيد البوت\nاختَر طريقة الشحن 👇",
    "withdraw_methods_title": "➖ سحب رصيد من البوت\nاختَر طريقة السحب 👇",

    "sham_support": "💳 حالياً {action} عبر **شام كاش** يتم فقط عبر الدعم.\n🆘 تواصل مع الدعم: {support}",

    "sy_choose_code": "📲 تمام! حوّل يدويًا إلى أحد الأكواد التالية ثم اختر الكود من القائمة 👇",
    "sy_ask_op": "🧾 أرسل رقم عملية التحويل (رقم العملية) 👇",
    "sy_ask_amount_topup": "💰 أرسل مبلغ الشحن (لازم يكون ≥ {min}) 👇",
    "sy_ask_receiver": "📩 أرسل رقم المستلم الذي سيتم إرسال المبلغ له 👇",
    "sy_ask_amount_withdraw": "💰 أرسل مبلغ السحب (لازم يكون ≥ {min}) 👇",

    "confirm_topup": "✅ تأكيد العملية\n\n📲 الطريقة: سيرياتيل كاش\n🔢 الكود: {code}\n🧾 رقم العملية: {op}\n💰 المبلغ: {amount}\n\nاضغط ✅ تأكيد لإرسال الطلب للأدمن.",
    "confirm_withdraw": "✅ تأكيد العملية\n\n📲 الطريقة: سيرياتيل كاش\n📩 المستلم: {receiver}\n💰 المبلغ: {amount}\n\n⚠️ عند التأكيد سيتم حجز المبلغ مباشرة (Hold).",

    "sent_admin": "✅ تم إرسال طلبك للأدمن بنجاح.\nرقم الطلب: #{id} 🧾",
    "reserved": "✅ تم حجز المبلغ مباشرة (Hold) لحين موافقة الأدمن.\nرقم الطلب: #{id} 🧾",

    "wallet": "💼 محفظتك:\n\n💰 الرصيد: {balance}\n⏳ المعلّق (Hold): {hold}",

    "invalid": "⚠️ لم أفهم طلبك.\nاستخدم الأزرار بالأسفل 👇",
    "back_main": "✅ رجعناك للقائمة الرئيسية 👇",
    "cancelled": "❌ تم إلغاء العملية.",
    "try_again": "⚠️ حدث شيء غير متوقع.\nجرّب مرة ثانية بعد قليل 🙏",

    "no_pending_withdraw": "ℹ️ ما عندك أي طلب سحب معلّق حالياً.",
    "withdraw_cancelled": "✅ تم إلغاء آخر طلب سحب معلّق وفكّ الحجز بنجاح 👌",

    "ich_menu": "💼 حساب ايشانسي\nاختَر الخدمة 👇",
    "ich_no_account": "⚠️ ما عندك حساب ايشانسي مربوط.\nابدأ بـ (إنشاء/استلام حساب) أولاً ✅",
    "ich_deleted": "🗑️ تم حذف حساب ايشانسي من البوت بنجاح.",
    "ich_delete_confirm": "🗑️ تأكيد حذف حساب ايشانسي؟\nهذا سيزيل الربط فقط من البوت.",
    "ich_username_ask": "✍️ اكتب اسم المستخدم الذي تريده (تقريبياً) 👇",
    "ich_suggest": "🔎 لقيت لك اقتراح مناسب من المخزون:\n\n👤 Username: `{u}`\n\nإذا مناسب اضغط ✅ تأكيد لاستلام الحساب.",
    "ich_no_suggest": "❌ ما لقيت اسم قريب بالمخزون حالياً.\nجرّب اسم ثاني أو تواصل مع الدعم 🆘",
    "ich_delivered": "✅ تم تسليم حساب ايشانسي بنجاح 🎉\n\n👤 Username: {u}\n🔑 Password: {p}\n\n📌 للنسخ السريع 👇",
    "ich_copy_block": "```text\n{u}\n{p}\n```",
    "ich_copy_line": "```text\n{u}:{p}\n```",

    "ich_topup_ask": "💳 اكتب مبلغ شحن ايشانسي (لازم يكون مضاعف لـ 100) 👇",
    "ich_withdraw_ask": "💸 اكتب مبلغ سحب ايشانسي (لازم يكون مضاعف لـ 100) 👇",

    "ich_topup_confirm": "✅ تأكيد شحن ايشانسي\n\n👤 الحساب: `{u}`\n💳 مبلغ ايشانسي: {ia}\n💰 التكلفة من رصيد البوت: {cost}\n\nاضغط ✅ تأكيد لإرسال الطلب للأدمن.",
    "ich_withdraw_confirm": "✅ تأكيد سحب من ايشانسي\n\n👤 الحساب: `{u}`\n💸 مبلغ ايشانسي: {ia}\n💰 سيضاف لرصيد البوت: {gain}\n\nاضغط ✅ تأكيد لإرسال الطلب للأدمن.",

    "admin_only": "⛔ هذه الميزة للأدمن فقط.",
    "admin_menu": "⚙️ لوحة الأدمن\nاختر خياراً 👇",
    "admin_no_pending": "ℹ️ لا يوجد طلبات معلّقة حالياً.",
    "admin_pending_title": "📌 الطلبات المعلقة:",
    "admin_ask_user": "🔍 أرسل ID المستخدم (رقم) أو حوّل رسالة منه 👇",
    "admin_user_not_found": "❌ لم يتم العثور على المستخدم.",
    "admin_adjust_amount": "💳 أرسل قيمة التعديل (مثال: 1000 أو -500) 👇",
    "admin_adjust_done": "✅ تم تعديل الرصيد بنجاح.",

    "admin_edit_hint": "✏️ أرسل التعديل حسب النوع:\n"
                      "• شحن سيرياتيل: code,op,amount\n"
                      "• سحب سيرياتيل: receiver,amount\n"
                      "• شحن ايشانسي: ichancy_amount\n"
                      "• سحب ايشانسي: ichancy_amount\n",

    "admin_order_updated": "✅ تم تحديث الطلب: {status}",

    "admin_broadcast_prompt": "📣 أرسل الآن (نص / صورة / فيديو) ليتم إرساله لكل مستخدمي البوت 👇",
    "admin_broadcast_done": "📣 تم الإرسال للجميع ✅\n\n✅ نجاح: {ok}\n❌ فشل: {bad}",

    "admin_manage_assist": "👤 إدارة الأدمن المساعد\nاختر 👇",
    "admin_add_assist_prompt": "➕ أرسل ID المساعد (رقم) أو حوّل رسالة منه 👇",
    "admin_remove_assist_prompt": "➖ أرسل ID المساعد المراد حذفه 👇",
    "admin_assist_added": "✅ تم إضافة أدمن مساعد.",
    "admin_assist_removed": "✅ تم حذف الأدمن المساعد.",
    "admin_assist_list": "👥 الأدمن المساعدين:\n{list}",

    "inv_menu": "📦 مخزون ايشانسي\nاختر 👇",
    "inv_add_prompt": "➕ أرسل الحساب بهذه الصيغة:\nusername,password",
    "inv_added": "✅ تم إضافة الحساب للمخزون.",
    "inv_list": "📦 المخزون:\nمتاح: {a}\nمحجوز/مسلّم: {b}",
    "inv_delete_prompt": "🗑️ أرسل username المراد حذفه من المخزون 👇",
    "inv_deleted": "✅ تم حذف الحساب من المخزون.",
    "inv_not_found": "❌ لم يتم العثور على هذا الحساب بالمخزون.",

    "backup_ready": "✅ تم إنشاء Backup بنجاح 📦\nاحتفظ به لاستخدامه في Restore.",
    "restore_start": "🔧 تم تفعيل وضع الصيانة تلقائياً.\n📥 الآن أرسل ملف الـ ZIP الخاص بالنسخة الاحتياطية (Backup) 👇",
    "restore_ok": "✅ تمت الاستعادة بنجاح 🎉\n🔧 وضع الصيانة ما زال مفعّل.\nعندما تتأكد من كل شيء، أوقف الصيانة من لوحة الأدمن.",
    "restore_bad": "❌ ملف غير صالح.\nتأكد أنه ZIP ناتج من زر Backup.",

    "maintenance_on": "🔧 تم تفعيل وضع الصيانة ✅",
    "maintenance_off": "✅ تم إيقاف وضع الصيانة 👌",

    "user_approved_24h": "✅ تمت الموافقة على طلبك #{id} 🎉\n⏳ أقصى مدة للتسليم: 24 ساعة.",
    "user_rejected": "❌ تم رفض طلبك #{id}.",
    "user_approved": "✅ تمت الموافقة على طلبك #{id} 🎉",

    "insufficient": "❌ رصيدك لا يكفي لإتمام العملية.",
    "must_multiple_100": "⚠️ لازم يكون المبلغ مضاعف لـ 100 (مثل 100، 200، 300...)",
}

# =========================
# Constants / callbacks
# =========================

OT_BOT_TOPUP = "bot_topup"
OT_BOT_WITHDRAW = "bot_withdraw"
OT_ICH_TOPUP = "ichancy_topup"
OT_ICH_WITHDRAW = "ichancy_withdraw"
OT_ICH_CREATE = "ichancy_create"

ST_PENDING = "pending"
ST_APPROVED = "approved"
ST_REJECTED = "rejected"
ST_CANCELLED = "cancelled"

CB_CHECK_SUB = "chk_sub"

CB_TOPUP_OK = "t_ok"
CB_TOPUP_NO = "t_no"
CB_WD_OK = "w_ok"
CB_WD_NO = "w_no"

CB_ICH_CREATE_OK = "ic_ok"
CB_ICH_CREATE_NO = "ic_no"

CB_ICH_DEL_OK = "id_ok"
CB_ICH_DEL_NO = "id_no"

CB_ICH_TOPUP_OK = "it_ok"
CB_ICH_TOPUP_NO = "it_no"

CB_ICH_WD_OK = "iw_ok"
CB_ICH_WD_NO = "iw_no"

CB_ORDER_APPROVE = "ord_ok"
CB_ORDER_REJECT = "ord_no"
CB_ORDER_EDIT = "ord_edit"

# =========================
# Helpers
# =========================

def now_ts() -> int:
    return int(time.time())

def safe_str(s: str | None, max_len: int = 256) -> str:
    return (s or "").strip()[:max_len]

def parse_int(text: str) -> Optional[int]:
    try:
        t = (text or "").strip().replace(",", "")
        return int(t)
    except Exception:
        return None

def normalize_username(u: str) -> str:
    u = (u or "").strip().lower()
    u = re.sub(r"[\s\-_\.]+", "", u)
    return u

def codeblock(text: str) -> str:
    return f"```text\n{text}\n```"

# =========================
# JSON Storage
# =========================

class JSONStorage:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self._locks: Dict[str, asyncio.Lock] = {}
        os.makedirs(self.data_dir, exist_ok=True)

        self._ensure("users.json", {})
        self._ensure("wallet.json", {})
        self._ensure("orders.json", {"next_id": 1, "orders": []})
        self._ensure("logs.json", [])
        self._ensure("settings.json", {"maintenance": False})
        self._ensure("admins.json", {"assist_admin_ids": []})
        self._ensure("ichancy_inventory.json", {"next_id": 1, "items": []})

    def _path(self, fn: str) -> str:
        return os.path.join(self.data_dir, fn)

    def _lock(self, fn: str) -> asyncio.Lock:
        if fn not in self._locks:
            self._locks[fn] = asyncio.Lock()
        return self._locks[fn]

    def _ensure(self, fn: str, default: Any) -> None:
        p = self._path(fn)
        if not os.path.exists(p):
            self._write_atomic_sync(fn, default)

    async def read(self, fn: str, default: Any) -> Any:
        async with self._lock(fn):
            p = self._path(fn)
            if not os.path.exists(p):
                await self.write(fn, default)
                return default
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                # backup corrupted and reset
                try:
                    os.replace(p, p + ".corrupted")
                except Exception:
                    pass
                await self.write(fn, default)
                return default

    async def write(self, fn: str, data: Any) -> None:
        async with self._lock(fn):
            self._write_atomic_sync(fn, data)

    def _write_atomic_sync(self, fn: str, data: Any) -> None:
        p = self._path(fn)
        os.makedirs(self.data_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=fn + ".", suffix=".tmp", dir=self.data_dir)
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
# Roles
# =========================

async def get_assist_admins(storage: JSONStorage) -> set[int]:
    data = await storage.read("admins.json", {"assist_admin_ids": []})
    ids = set(int(x) for x in data.get("assist_admin_ids", []) if isinstance(x, int) or str(x).isdigit())
    return ids

def is_super_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    cfg: Config = context.application.bot_data["cfg"]
    u = update.effective_user
    return bool(u and int(u.id) == int(cfg.SUPER_ADMIN_ID))

async def is_admin_any(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if is_super_admin(update, context):
        return True
    u = update.effective_user
    if not u:
        return False
    storage: JSONStorage = context.application.bot_data["storage"]
    return int(u.id) in await get_assist_admins(storage)

async def is_maintenance_on(context: ContextTypes.DEFAULT_TYPE) -> bool:
    storage: JSONStorage = context.application.bot_data["storage"]
    s = await storage.read("settings.json", {"maintenance": False})
    return bool(s.get("maintenance", False))

# =========================
# Subscription gate
# =========================

def _is_member_status(status: str) -> bool:
    return status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
        "member",
        "administrator",
        "creator",
    )

async def is_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    cfg: Config = context.application.bot_data["cfg"]
    user = update.effective_user
    if not user:
        return False
    try:
        cm = await context.bot.get_chat_member(chat_id=cfg.REQUIRED_CHANNEL, user_id=user.id)
        return _is_member_status(getattr(cm, "status", ""))
    except Exception:
        return False

def kb_subscribe(channel: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ اشترك بالقناة", url=f"https://t.me/{channel.lstrip('@')}")],
            [InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data=CB_CHECK_SUB)],
        ]
    )

async def send_sub_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    msg = TXT["need_sub"]
    if update.message:
        await update.message.reply_text(msg, reply_markup=kb_subscribe(cfg.REQUIRED_CHANNEL), disable_web_page_preview=True)
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(msg, reply_markup=kb_subscribe(cfg.REQUIRED_CHANNEL), disable_web_page_preview=True)

# =========================
# Guards
# =========================

async def user_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    # maintenance blocks normal users, allows admins
    if await is_maintenance_on(context):
        if await is_admin_any(update, context):
            return True
        if update.effective_message:
            await update.effective_message.reply_text(TXT["maintenance"])
        return False

    # subscription gate for normal users (admins bypass)
    if await is_admin_any(update, context):
        return True

    if not await is_subscribed(update, context):
        await send_sub_gate(update, context)
        return False

    return True

# =========================
# Users / wallet / orders
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

async def set_user_ichancy(storage: JSONStorage, user_id: int, username: str, password: str) -> None:
    users = await storage.read("users.json", {})
    uid = str(user_id)
    if uid not in users:
        users[uid] = {"id": user_id, "created_at": now_ts()}
    users[uid]["ichancy"] = {"username": username, "password": password, "updated_at": now_ts()}
    users[uid]["updated_at"] = now_ts()
    await storage.write("users.json", users)

async def delete_user_ichancy(storage: JSONStorage, user_id: int) -> bool:
    users = await storage.read("users.json", {})
    uid = str(user_id)
    if uid in users and users[uid].get("ichancy"):
        users[uid]["ichancy"] = None
        users[uid]["updated_at"] = now_ts()
        await storage.write("users.json", users)
        return True
    return False

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

async def deduct_balance(storage: JSONStorage, user_id: int, amount: int) -> Tuple[bool, dict]:
    if amount <= 0:
        return False, await get_wallet(storage, user_id)
    wallet = await storage.read("wallet.json", {})
    uid = str(user_id)
    w = wallet.get(uid, {"balance": 0, "hold": 0})
    bal = int(w.get("balance", 0))
    if bal < amount:
        return False, {"balance": bal, "hold": int(w.get("hold", 0))}
    bal -= amount
    w["balance"] = max(0, bal)
    wallet[uid] = w
    await storage.write("wallet.json", wallet)
    return True, w

async def reserve_withdraw(storage: JSONStorage, user_id: int, amount: int) -> Tuple[bool, dict]:
    if amount <= 0:
        return False, await get_wallet(storage, user_id)
    wallet = await storage.read("wallet.json", {})
    uid = str(user_id)
    w = wallet.get(uid, {"balance": 0, "hold": 0})
    bal = int(w.get("balance", 0))
    hold = int(w.get("hold", 0))
    if bal < amount:
        return False, {"balance": bal, "hold": hold}
    bal -= amount
    hold += amount
    wallet[uid] = {"balance": max(0, bal), "hold": max(0, hold)}
    await storage.write("wallet.json", wallet)
    return True, wallet[uid]

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
    hold = int(w.get("hold", 0))
    hold = max(0, hold - amount)
    w["hold"] = hold
    wallet[uid] = w
    await storage.write("wallet.json", wallet)
    return w

async def create_order(storage: JSONStorage, order_type: str, user_id: int, data: dict, status: str = ST_PENDING) -> dict:
    obj = await storage.read("orders.json", {"next_id": 1, "orders": []})
    oid = int(obj.get("next_id", 1))
    order = {
        "id": oid,
        "type": order_type,
        "status": status,
        "user_id": int(user_id),
        "data": data,
        "created_at": now_ts(),
        "updated_at": now_ts(),
    }
    obj["orders"] = list(obj.get("orders", [])) + [order]
    obj["next_id"] = oid + 1
    await storage.write("orders.json", obj)
    return order

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

async def list_pending(storage: JSONStorage, limit: int = 25) -> list[dict]:
    obj = await storage.read("orders.json", {"next_id": 1, "orders": []})
    orders = list(obj.get("orders", []))
    pending = [o for o in orders if o.get("status") == ST_PENDING]
    pending.sort(key=lambda x: int(x.get("id", 0)))
    return pending[:limit]

async def last_pending_withdraw_order(storage: JSONStorage, user_id: int) -> Optional[dict]:
    obj = await storage.read("orders.json", {"next_id": 1, "orders": []})
    orders = [o for o in obj.get("orders", []) if int(o.get("user_id", 0)) == int(user_id)]
    orders = [o for o in orders if o.get("type") == OT_BOT_WITHDRAW and o.get("status") == ST_PENDING]
    if not orders:
        return None
    orders.sort(key=lambda x: int(x.get("id", 0)), reverse=True)
    return orders[0]

# =========================
# Ichancy inventory
# =========================

async def inv_stats(storage: JSONStorage) -> tuple[int, int]:
    inv = await storage.read("ichancy_inventory.json", {"next_id": 1, "items": []})
    items = inv.get("items", [])
    a = sum(1 for it in items if it.get("status") == "available")
    b = sum(1 for it in items if it.get("status") != "available")
    return a, b

async def inv_add(storage: JSONStorage, username: str, password: str) -> dict:
    inv = await storage.read("ichancy_inventory.json", {"next_id": 1, "items": []})
    iid = int(inv.get("next_id", 1))
    item = {
        "id": iid,
        "username": username,
        "password": password,
        "status": "available",
        "assigned_to": None,
        "assigned_at": None,
        "created_at": now_ts(),
    }
    inv["items"] = list(inv.get("items", [])) + [item]
    inv["next_id"] = iid + 1
    await storage.write("ichancy_inventory.json", inv)
    return item

async def inv_delete_by_username(storage: JSONStorage, username: str) -> bool:
    inv = await storage.read("ichancy_inventory.json", {"next_id": 1, "items": []})
    items = list(inv.get("items", []))
    new_items = [it for it in items if it.get("username") != username]
    if len(new_items) == len(items):
        return False
    inv["items"] = new_items
    await storage.write("ichancy_inventory.json", inv)
    return True

async def inv_find_best_match(storage: JSONStorage, desired: str) -> Optional[dict]:
    inv = await storage.read("ichancy_inventory.json", {"next_id": 1, "items": []})
    items = [it for it in inv.get("items", []) if it.get("status") == "available"]
    if not items:
        return None

    desired_n = normalize_username(desired)
    # exact match
    for it in items:
        if normalize_username(it.get("username", "")) == desired_n:
            return it

    choices = [(it, normalize_username(it.get("username", ""))) for it in items]
    usernames_norm = [u for (_, u) in choices]
    best = difflib.get_close_matches(desired_n, usernames_norm, n=1, cutoff=0.55)
    if not best:
        return None
    best_norm = best[0]
    for it, un in choices:
        if un == best_norm:
            return it
    return None

async def inv_assign(storage: JSONStorage, item_id: int, user_id: int) -> Optional[dict]:
    inv = await storage.read("ichancy_inventory.json", {"next_id": 1, "items": []})
    items = list(inv.get("items", []))
    updated = None
    for i, it in enumerate(items):
        if int(it.get("id", 0)) == int(item_id):
            if it.get("status") != "available":
                return None
            it["status"] = "assigned"
            it["assigned_to"] = int(user_id)
            it["assigned_at"] = now_ts()
            items[i] = it
            updated = it
            break
    if updated:
        inv["items"] = items
        await storage.write("ichancy_inventory.json", inv)
    return updated

# =========================
# Keyboards
# =========================

def kb_user_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("💼 حساب ايشانسي"), KeyboardButton("💰 محفظتي")],
            [KeyboardButton("➕ شحن رصيد البوت"), KeyboardButton("➖ سحب رصيد من البوت")],
            [KeyboardButton("🧾 إلغاء آخر طلب سحب")],
            [KeyboardButton("🆘 دعم")],
        ],
        resize_keyboard=True,
    )

def kb_methods() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("💳 شام كاش"), KeyboardButton("📲 سيرياتيل كاش")],
            [KeyboardButton("⬅️ رجوع")],
        ],
        resize_keyboard=True,
    )

def kb_codes(codes: list[str]) -> ReplyKeyboardMarkup:
    rows = []
    row = []
    for c in codes:
        row.append(KeyboardButton(str(c)))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton("⬅️ رجوع")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def kb_back_only() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton("⬅️ رجوع")]], resize_keyboard=True)

def kb_confirm_inline(ok_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ تأكيد", callback_data=ok_cb),
                                 InlineKeyboardButton("❌ إلغاء", callback_data=no_cb)]])

def kb_ichancy() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("✅ إنشاء/استلام حساب ايشانسي")],
            [KeyboardButton("➕ شحن حساب ايشانسي"), KeyboardButton("➖ سحب من حساب ايشانسي")],
            [KeyboardButton("🗑️ حذف حساب ايشانسي")],
            [KeyboardButton("⬅️ رجوع")],
        ],
        resize_keyboard=True,
    )

def kb_admin() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📌 الطلبات المعلقة"), KeyboardButton("🔍 بحث مستخدم")],
            [KeyboardButton("💳 تعديل رصيد (Super)"), KeyboardButton("📦 مخزون ايشانسي (Super)")],
            [KeyboardButton("📣 رسالة جماعية (Super)"), KeyboardButton("👤 أدمن مساعد (Super)")],
            [KeyboardButton("💾 Backup (Super)"), KeyboardButton("♻️ Restore (Super)")],
            [KeyboardButton("🔧 صيانة ON/OFF (Super)")],
            [KeyboardButton("⬅️ رجوع")],
        ],
        resize_keyboard=True,
    )

def kb_admin_assist() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ إضافة مساعد"), KeyboardButton("➖ حذف مساعد")],
            [KeyboardButton("📋 عرض المساعدين")],
            [KeyboardButton("⬅️ رجوع")],
        ],
        resize_keyboard=True,
    )

def kb_inventory() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ إضافة حساب"), KeyboardButton("📋 إحصائيات")],
            [KeyboardButton("🗑️ حذف حساب")],
            [KeyboardButton("⬅️ رجوع")],
        ],
        resize_keyboard=True,
    )

def kb_order_actions(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✏️ تعديل", callback_data=f"{CB_ORDER_EDIT}:{order_id}"),
            InlineKeyboardButton("✅ قبول", callback_data=f"{CB_ORDER_APPROVE}:{order_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"{CB_ORDER_REJECT}:{order_id}"),
        ]]
    )

# =========================
# Formatting / notifications
# =========================

def fmt_order(order: dict) -> str:
    t = order.get("type")
    s = order.get("status")
    uid = order.get("user_id")
    data = order.get("data", {}) or {}

    base = f"🧾 الطلب #{order.get('id')} | {t} | {s}\n👤 user_id: {uid}\n"

    if t == OT_BOT_TOPUP:
        return base + f"📲 سيرياتيل شحن\n🔢 code: `{data.get('code')}`\n🧾 op: `{data.get('operation_no')}`\n💰 amount: {data.get('amount')}"
    if t == OT_BOT_WITHDRAW:
        return base + f"📲 سيرياتيل سحب\n📩 receiver: `{data.get('receiver_no')}`\n💰 amount: {data.get('amount')}"
    if t == OT_ICH_TOPUP:
        return base + f"💼 ايشانسي شحن\n👤 ichancy: `{data.get('ichancy_username')}`\n💳 ichancy_amount: {data.get('ichancy_amount')}\n💰 cost(bot): {data.get('bot_cost')}"
    if t == OT_ICH_WITHDRAW:
        return base + f"💼 ايشانسي سحب\n👤 ichancy: `{data.get('ichancy_username')}`\n💸 ichancy_amount: {data.get('ichancy_amount')}\n💰 gain(bot): {data.get('bot_gain')}"
    if t == OT_ICH_CREATE:
        return base + f"✅ تسليم حساب ايشانسي (من المخزون)\n👤 ichancy: `{data.get('ichancy_username')}`\n📦 inv_id: {data.get('inv_id')}"
    return base + f"data: {data}"

async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    storage: JSONStorage = context.application.bot_data["storage"]
    admins = set([cfg.SUPER_ADMIN_ID]) | await get_assist_admins(storage)
    for aid in admins:
        try:
            await context.bot.send_message(chat_id=aid, text=text, reply_markup=reply_markup)
        except Exception:
            continue

async def safe_sleep_for_flood(e: Exception) -> None:
    if isinstance(e, RetryAfter):
        await asyncio.sleep(int(getattr(e, "retry_after", 2)) + 1)
    else:
        await asyncio.sleep(1)

# =========================
# States
# =========================

(
    ST_TOPUP_METHOD,
    ST_TOPUP_CODE,
    ST_TOPUP_OP,
    ST_TOPUP_AMOUNT,
    ST_TOPUP_CONFIRM,

    ST_WD_METHOD,
    ST_WD_RECEIVER,
    ST_WD_AMOUNT,
    ST_WD_CONFIRM,

    ST_ICH_MENU,
    ST_ICH_CREATE_DESIRED,
    ST_ICH_CREATE_CONFIRM,
    ST_ICH_TOPUP_AMOUNT,
    ST_ICH_TOPUP_CONFIRM,
    ST_ICH_WD_AMOUNT,
    ST_ICH_WD_CONFIRM,
    ST_ICH_DEL_CONFIRM,

    AD_MENU,
    AD_FIND_USER,
    AD_ADJUST_USER,
    AD_ADJUST_AMOUNT,

    AD_INV_MENU,
    AD_INV_ADD,
    AD_INV_DELETE,

    AD_AS_MENU,
    AD_AS_ADD,
    AD_AS_REMOVE,

    AD_BROADCAST,
    AD_RESTORE_WAIT,
) = range(29)

# =========================
# /start + subscription button
# =========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    storage: JSONStorage = context.application.bot_data["storage"]
    await create_or_update_user(storage, update.effective_user)

    # maintenance blocks normal users
    if await is_maintenance_on(context) and not await is_admin_any(update, context):
        await update.message.reply_text(TXT["maintenance"])
        return

    if not await is_subscribed(update, context) and not await is_admin_any(update, context):
        await send_sub_gate(update, context)
        return

    await update.message.reply_text(TXT["welcome"], reply_markup=kb_user_main())

async def cb_check_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()

    if await is_maintenance_on(context) and not await is_admin_any(update, context):
        await q.message.reply_text(TXT["maintenance"])
        return

    if await is_subscribed(update, context):
        await q.message.reply_text(TXT["sub_ok"], reply_markup=kb_user_main())
    else:
        cfg: Config = context.application.bot_data["cfg"]
        await q.message.reply_text(TXT["sub_fail"], reply_markup=kb_subscribe(cfg.REQUIRED_CHANNEL))

# =========================
# User entry router
# =========================

async def user_entry_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END

    if not await user_allowed(update, context):
        return ConversationHandler.END

    cfg: Config = context.application.bot_data["cfg"]
    storage: JSONStorage = context.application.bot_data["storage"]
    text = (update.message.text or "").strip()

    if text == "💰 محفظتي":
        w = await get_wallet(storage, update.effective_user.id)
        await update.message.reply_text(TXT["wallet"].format(balance=w["balance"], hold=w["hold"]), reply_markup=kb_user_main())
        return ConversationHandler.END

    if text == "🆘 دعم":
        await update.message.reply_text(TXT["support"].format(support=cfg.SUPPORT_USERNAME), reply_markup=kb_user_main())
        return ConversationHandler.END

    if text == "➕ شحن رصيد البوت":
        context.user_data.clear()
        await update.message.reply_text(TXT["topup_methods_title"], reply_markup=kb_methods())
        return ST_TOPUP_METHOD

    if text == "➖ سحب رصيد من البوت":
        context.user_data.clear()
        await update.message.reply_text(TXT["withdraw_methods_title"], reply_markup=kb_methods())
        return ST_WD_METHOD

    if text == "🧾 إلغاء آخر طلب سحب":
        last = await last_pending_withdraw_order(storage, update.effective_user.id)
        if not last:
            await update.message.reply_text(TXT["no_pending_withdraw"], reply_markup=kb_user_main())
            return ConversationHandler.END
        amt = int((last.get("data") or {}).get("amount", 0))
        await update_order(storage, int(last["id"]), {"status": ST_CANCELLED})
        await release_hold(storage, update.effective_user.id, amt)
        await update.message.reply_text(TXT["withdraw_cancelled"], reply_markup=kb_user_main())
        await notify_admins(context, f"🧾 تم إلغاء طلب سحب من المستخدم {update.effective_user.id}\nطلب #{last['id']} (كان Pending).")
        return ConversationHandler.END

    if text == "💼 حساب ايشانسي":
        await update.message.reply_text(TXT["ich_menu"], reply_markup=kb_ichancy())
        return ST_ICH_MENU

    await update.message.reply_text(TXT["invalid"], reply_markup=kb_user_main())
    return ConversationHandler.END

# =========================
# Topup flow
# =========================

async def topup_choose_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not await user_allowed(update, context):
        return ConversationHandler.END

    cfg: Config = context.application.bot_data["cfg"]
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["back_main"], reply_markup=kb_user_main())
        return ConversationHandler.END

    if text == "💳 شام كاش":
        await update.message.reply_text(TXT["sham_support"].format(action="الشحن", support=cfg.SUPPORT_USERNAME),
                                        reply_markup=kb_user_main(), disable_web_page_preview=True)
        return ConversationHandler.END

    if text == "📲 سيرياتيل كاش":
        codes = cfg.SYRIATEL_CODES or ["45191900", "33333333", "33333344"]
        await update.message.reply_text(TXT["sy_choose_code"], reply_markup=kb_codes(codes))
        return ST_TOPUP_CODE

    await update.message.reply_text("⚠️ اختَر طريقة من الأزرار 👇", reply_markup=kb_methods())
    return ST_TOPUP_METHOD

async def topup_choose_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not await user_allowed(update, context):
        return ConversationHandler.END

    cfg: Config = context.application.bot_data["cfg"]
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["topup_methods_title"], reply_markup=kb_methods())
        return ST_TOPUP_METHOD

    codes = set(cfg.SYRIATEL_CODES or [])
    if text not in codes:
        await update.message.reply_text("⚠️ اختَر كود من القائمة فقط 👇", reply_markup=kb_codes(list(codes)))
        return ST_TOPUP_CODE

    context.user_data["topup_code"] = text
    await update.message.reply_text(TXT["sy_ask_op"], reply_markup=kb_back_only())
    return ST_TOPUP_OP

async def topup_get_op(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not await user_allowed(update, context):
        return ConversationHandler.END

    cfg: Config = context.application.bot_data["cfg"]
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["sy_choose_code"], reply_markup=kb_codes(cfg.SYRIATEL_CODES))
        return ST_TOPUP_CODE

    op = safe_str(text, 64)
    if len(op) < 3:
        await update.message.reply_text("⚠️ رقم العملية غير صحيح. أعد الإرسال 👇", reply_markup=kb_back_only())
        return ST_TOPUP_OP

    context.user_data["topup_op"] = op
    await update.message.reply_text(TXT["sy_ask_amount_topup"].format(min=cfg.MIN_TOPUP), reply_markup=kb_back_only())
    return ST_TOPUP_AMOUNT

async def topup_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    if not await user_allowed(update, context):
        return ConversationHandler.END

    cfg: Config = context.application.bot_data["cfg"]
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["sy_ask_op"], reply_markup=kb_back_only())
        return ST_TOPUP_OP

    amount = parse_int(text)
    if amount is None or amount < cfg.MIN_TOPUP:
        await update.message.reply_text(f"⚠️ مبلغ غير صحيح.\nلازم يكون ≥ {cfg.MIN_TOPUP} 👇", reply_markup=kb_back_only())
        return ST_TOPUP_AMOUNT

    context.user_data["topup_amount"] = int(amount)

    summary = TXT["confirm_topup"].format(
        code=context.user_data["topup_code"],
        op=context.user_data["topup_op"],
        amount=context.user_data["topup_amount"],
    )
    await update.message.reply_text(summary, reply_markup=kb_confirm_inline(CB_TOPUP_OK, CB_TOPUP_NO))
    return ST_TOPUP_CONFIRM

async def cb_topup_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()

    if not await user_allowed(update, context):
        return ConversationHandler.END

    if q.data == CB_TOPUP_NO:
        context.user_data.clear()
        await q.message.reply_text(TXT["cancelled"], reply_markup=kb_user_main())
        return ConversationHandler.END

    storage: JSONStorage = context.application.bot_data["storage"]
    user_id = q.from_user.id

    code = context.user_data.get("topup_code")
    op = context.user_data.get("topup_op")
    amt = context.user_data.get("topup_amount")
    if not (code and op and amt):
        context.user_data.clear()
        await q.message.reply_text("⚠️ بيانات ناقصة. أعد العملية 🙏", reply_markup=kb_user_main())
        return ConversationHandler.END

    order = await create_order(storage, OT_BOT_TOPUP, user_id,
                               {"method": "syriatel_cash", "code": code, "operation_no": op, "amount": int(amt)},
                               status=ST_PENDING)

    context.user_data.clear()
    await q.message.reply_text(TXT["sent_admin"].format(id=order["id"]), reply_markup=kb_user_main())
    await notify_admins(context, fmt_order(order), reply_markup=kb_order_actions(order["id"]))
    return ConversationHandler.END

# =========================
# Withdraw flow
# =========================

async def wd_choose_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not await user_allowed(update, context):
        return ConversationHandler.END

    cfg: Config = context.application.bot_data["cfg"]
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["back_main"], reply_markup=kb_user_main())
        return ConversationHandler.END

    if text == "💳 شام كاش":
        await update.message.reply_text(TXT["sham_support"].format(action="السحب", support=cfg.SUPPORT_USERNAME),
                                        reply_markup=kb_user_main(), disable_web_page_preview=True)
        return ConversationHandler.END

    if text == "📲 سيرياتيل كاش":
        await update.message.reply_text(TXT["sy_ask_receiver"], reply_markup=kb_back_only())
        return ST_WD_RECEIVER

    await update.message.reply_text("⚠️ اختَر طريقة من الأزرار 👇", reply_markup=kb_methods())
    return ST_WD_METHOD

async def wd_get_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not await user_allowed(update, context):
        return ConversationHandler.END

    cfg: Config = context.application.bot_data["cfg"]
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["withdraw_methods_title"], reply_markup=kb_methods())
        return ST_WD_METHOD

    receiver = safe_str(text, 64)
    if len(receiver) < 3:
        await update.message.reply_text("⚠️ رقم المستلم غير صحيح. أعد الإرسال 👇", reply_markup=kb_back_only())
        return ST_WD_RECEIVER

    context.user_data["wd_receiver"] = receiver
    await update.message.reply_text(TXT["sy_ask_amount_withdraw"].format(min=cfg.MIN_WITHDRAW), reply_markup=kb_back_only())
    return ST_WD_AMOUNT

async def wd_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    if not await user_allowed(update, context):
        return ConversationHandler.END

    cfg: Config = context.application.bot_data["cfg"]
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["sy_ask_receiver"], reply_markup=kb_back_only())
        return ST_WD_RECEIVER

    amount = parse_int(text)
    if amount is None or amount < cfg.MIN_WITHDRAW:
        await update.message.reply_text(f"⚠️ مبلغ غير صحيح.\nلازم يكون ≥ {cfg.MIN_WITHDRAW} 👇", reply_markup=kb_back_only())
        return ST_WD_AMOUNT

    context.user_data["wd_amount"] = int(amount)

    summary = TXT["confirm_withdraw"].format(receiver=context.user_data["wd_receiver"], amount=context.user_data["wd_amount"])
    await update.message.reply_text(summary, reply_markup=kb_confirm_inline(CB_WD_OK, CB_WD_NO))
    return ST_WD_CONFIRM

async def cb_withdraw_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()

    if not await user_allowed(update, context):
        return ConversationHandler.END

    if q.data == CB_WD_NO:
        context.user_data.clear()
        await q.message.reply_text(TXT["cancelled"], reply_markup=kb_user_main())
        return ConversationHandler.END

    storage: JSONStorage = context.application.bot_data["storage"]
    user_id = q.from_user.id

    receiver = context.user_data.get("wd_receiver")
    amt = context.user_data.get("wd_amount")
    if not (receiver and amt):
        context.user_data.clear()
        await q.message.reply_text("⚠️ بيانات ناقصة. أعد المحاولة 🙏", reply_markup=kb_user_main())
        return ConversationHandler.END

    ok, _w = await reserve_withdraw(storage, user_id, int(amt))
    if not ok:
        context.user_data.clear()
        await q.message.reply_text(TXT["insufficient"], reply_markup=kb_user_main())
        return ConversationHandler.END

    order = await create_order(storage, OT_BOT_WITHDRAW, user_id,
                               {"method": "syriatel_cash", "receiver_no": receiver, "amount": int(amt)},
                               status=ST_PENDING)

    context.user_data.clear()
    await q.message.reply_text(TXT["reserved"].format(id=order["id"]), reply_markup=kb_user_main())
    await notify_admins(context, fmt_order(order), reply_markup=kb_order_actions(order["id"]))
    return ConversationHandler.END

# =========================
# Ichancy menu + flows
# =========================

async def ich_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    if not await user_allowed(update, context):
        return ConversationHandler.END

    storage: JSONStorage = context.application.bot_data["storage"]
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["back_main"], reply_markup=kb_user_main())
        return ConversationHandler.END

    if text == "✅ إنشاء/استلام حساب ايشانسي":
        u = await get_user(storage, update.effective_user.id)
        if u and u.get("ichancy"):
            await update.message.reply_text("ℹ️ عندك حساب ايشانسي مربوط بالفعل.\nإذا بدك تبدله احذف الحساب أولاً 🗑️",
                                            reply_markup=kb_ichancy())
            return ST_ICH_MENU
        await update.message.reply_text(TXT["ich_username_ask"], reply_markup=kb_back_only())
        return ST_ICH_CREATE_DESIRED

    if text == "➕ شحن حساب ايشانسي":
        u = await get_user(storage, update.effective_user.id)
        if not u or not u.get("ichancy"):
            await update.message.reply_text(TXT["ich_no_account"], reply_markup=kb_ichancy())
            return ST_ICH_MENU
        await update.message.reply_text(TXT["ich_topup_ask"], reply_markup=kb_back_only())
        return ST_ICH_TOPUP_AMOUNT

    if text == "➖ سحب من حساب ايشانسي":
        u = await get_user(storage, update.effective_user.id)
        if not u or not u.get("ichancy"):
            await update.message.reply_text(TXT["ich_no_account"], reply_markup=kb_ichancy())
            return ST_ICH_MENU
        await update.message.reply_text(TXT["ich_withdraw_ask"], reply_markup=kb_back_only())
        return ST_ICH_WD_AMOUNT

    if text == "🗑️ حذف حساب ايشانسي":
        u = await get_user(storage, update.effective_user.id)
        if not u or not u.get("ichancy"):
            await update.message.reply_text("ℹ️ ما عندك حساب ايشانسي محفوظ أصلاً.", reply_markup=kb_ichancy())
            return ST_ICH_MENU
        ich_u = (u.get("ichancy") or {}).get("username")
        await update.message.reply_text(
            TXT["ich_delete_confirm"] + f"\n\n👤 الحساب: `{ich_u}`",
            reply_markup=kb_confirm_inline(CB_ICH_DEL_OK, CB_ICH_DEL_NO),
        )
        return ST_ICH_DEL_CONFIRM

    await update.message.reply_text("⚠️ اختَر خيار من قائمة ايشانسي 👇", reply_markup=kb_ichancy())
    return ST_ICH_MENU

async def ich_create_get_desired(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    if not await user_allowed(update, context):
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["ich_menu"], reply_markup=kb_ichancy())
        return ST_ICH_MENU

    desired = safe_str(text, 64)
    if len(desired) < 3:
        await update.message.reply_text("⚠️ اسم غير مناسب. جرّب اسم أطول شوي 👇", reply_markup=kb_back_only())
        return ST_ICH_CREATE_DESIRED

    storage: JSONStorage = context.application.bot_data["storage"]
    best = await inv_find_best_match(storage, desired)
    if not best:
        await update.message.reply_text(TXT["ich_no_suggest"], reply_markup=kb_ichancy())
        return ST_ICH_MENU

    context.user_data["inv_suggest_id"] = int(best["id"])
    await update.message.reply_text(
        TXT["ich_suggest"].format(u=best["username"]),
        reply_markup=kb_confirm_inline(CB_ICH_CREATE_OK, CB_ICH_CREATE_NO),
    )
    return ST_ICH_CREATE_CONFIRM

async def cb_ich_create_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()

    if not await user_allowed(update, context):
        return ConversationHandler.END

    if q.data == CB_ICH_CREATE_NO:
        context.user_data.clear()
        await q.message.reply_text("✅ تمام، إذا بدك جرّب اسم مختلف.", reply_markup=kb_ichancy())
        return ST_ICH_MENU

    storage: JSONStorage = context.application.bot_data["storage"]
    user_id = q.from_user.id
    inv_id = context.user_data.get("inv_suggest_id")
    if not inv_id:
        context.user_data.clear()
        await q.message.reply_text("⚠️ ما عاد في اقتراح محفوظ. أعد المحاولة.", reply_markup=kb_ichancy())
        return ST_ICH_MENU

    assigned = await inv_assign(storage, int(inv_id), user_id)
    if not assigned:
        context.user_data.clear()
        await q.message.reply_text("⚠️ للأسف الحساب لم يعد متاح.\nجرّب مرة ثانية باسم آخر 🙏", reply_markup=kb_ichancy())
        return ST_ICH_MENU

    u = assigned["username"]
    p = assigned["password"]
    await set_user_ichancy(storage, user_id, u, p)

    order = await create_order(storage, OT_ICH_CREATE, user_id,
                               {"inv_id": int(inv_id), "ichancy_username": u},
                               status=ST_APPROVED)

    await q.message.reply_text(TXT["ich_delivered"].format(u=u, p=p), reply_markup=kb_ichancy())
    await q.message.reply_text(TXT["ich_copy_block"].format(u=u, p=p))
    await q.message.reply_text(TXT["ich_copy_line"].format(u=u, p=p))

    await notify_admins(context, "✅ تم تسليم حساب ايشانسي من المخزون:\n" + fmt_order(order))
    context.user_data.clear()
    return ST_ICH_MENU

async def cb_ich_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()

    if not await user_allowed(update, context):
        return ConversationHandler.END

    if q.data == CB_ICH_DEL_NO:
        await q.message.reply_text("✅ تمام، ما حذفنا شي.", reply_markup=kb_ichancy())
        return ST_ICH_MENU

    storage: JSONStorage = context.application.bot_data["storage"]
    ok = await delete_user_ichancy(storage, q.from_user.id)
    await q.message.reply_text(TXT["ich_deleted"] if ok else "ℹ️ ما كان في حساب محفوظ.", reply_markup=kb_ichancy())
    return ST_ICH_MENU

async def ich_topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    if not await user_allowed(update, context):
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["ich_menu"], reply_markup=kb_ichancy())
        return ST_ICH_MENU

    ia = parse_int(text)
    if ia is None or ia <= 0:
        await update.message.reply_text("⚠️ أرسل رقم صحيح 👇", reply_markup=kb_back_only())
        return ST_ICH_TOPUP_AMOUNT
    if ia % 100 != 0:
        await update.message.reply_text(TXT["must_multiple_100"], reply_markup=kb_back_only())
        return ST_ICH_TOPUP_AMOUNT

    storage: JSONStorage = context.application.bot_data["storage"]
    u = await get_user(storage, update.effective_user.id)
    ich_u = ((u or {}).get("ichancy") or {}).get("username")
    if not ich_u:
        await update.message.reply_text(TXT["ich_no_account"], reply_markup=kb_ichancy())
        return ST_ICH_MENU

    cost = ia // 100
    w = await get_wallet(storage, update.effective_user.id)
    if w["balance"] < cost:
        await update.message.reply_text(TXT["insufficient"], reply_markup=kb_ichancy())
        return ST_ICH_MENU

    context.user_data["ich_u"] = ich_u
    context.user_data["ich_ia"] = int(ia)
    context.user_data["ich_cost"] = int(cost)

    msg = TXT["ich_topup_confirm"].format(u=ich_u, ia=ia, cost=cost)
    await update.message.reply_text(msg, reply_markup=kb_confirm_inline(CB_ICH_TOPUP_OK, CB_ICH_TOPUP_NO))
    return ST_ICH_TOPUP_CONFIRM

async def cb_ich_topup_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()

    if not await user_allowed(update, context):
        return ConversationHandler.END

    if q.data == CB_ICH_TOPUP_NO:
        context.user_data.clear()
        await q.message.reply_text(TXT["cancelled"], reply_markup=kb_ichancy())
        return ST_ICH_MENU

    storage: JSONStorage = context.application.bot_data["storage"]
    user_id = q.from_user.id
    ich_u = context.user_data.get("ich_u")
    ia = context.user_data.get("ich_ia")
    cost = context.user_data.get("ich_cost")
    if not (ich_u and ia and cost is not None):
        context.user_data.clear()
        await q.message.reply_text("⚠️ بيانات ناقصة. أعد العملية.", reply_markup=kb_ichancy())
        return ST_ICH_MENU

    order = await create_order(storage, OT_ICH_TOPUP, user_id,
                               {"ichancy_username": ich_u, "ichancy_amount": int(ia), "bot_cost": int(cost)},
                               status=ST_PENDING)

    context.user_data.clear()
    await q.message.reply_text(TXT["sent_admin"].format(id=order["id"]), reply_markup=kb_ichancy())
    admin_text = fmt_order(order) + "\n\n📌 نسخ اسم الحساب:\n" + codeblock(str(ich_u))
    await notify_admins(context, admin_text, reply_markup=kb_order_actions(order["id"]))
    return ST_ICH_MENU

async def ich_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    if not await user_allowed(update, context):
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["ich_menu"], reply_markup=kb_ichancy())
        return ST_ICH_MENU

    ia = parse_int(text)
    if ia is None or ia <= 0:
        await update.message.reply_text("⚠️ أرسل رقم صحيح 👇", reply_markup=kb_back_only())
        return ST_ICH_WD_AMOUNT
    if ia % 100 != 0:
        await update.message.reply_text(TXT["must_multiple_100"], reply_markup=kb_back_only())
        return ST_ICH_WD_AMOUNT

    storage: JSONStorage = context.application.bot_data["storage"]
    u = await get_user(storage, update.effective_user.id)
    ich_u = ((u or {}).get("ichancy") or {}).get("username")
    if not ich_u:
        await update.message.reply_text(TXT["ich_no_account"], reply_markup=kb_ichancy())
        return ST_ICH_MENU

    gain = ia // 100
    context.user_data["ich_u"] = ich_u
    context.user_data["ich_ia"] = int(ia)
    context.user_data["ich_gain"] = int(gain)

    msg = TXT["ich_withdraw_confirm"].format(u=ich_u, ia=ia, gain=gain)
    await update.message.reply_text(msg, reply_markup=kb_confirm_inline(CB_ICH_WD_OK, CB_ICH_WD_NO))
    return ST_ICH_WD_CONFIRM

async def cb_ich_withdraw_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()

    if not await user_allowed(update, context):
        return ConversationHandler.END

    if q.data == CB_ICH_WD_NO:
        context.user_data.clear()
        await q.message.reply_text(TXT["cancelled"], reply_markup=kb_ichancy())
        return ST_ICH_MENU

    storage: JSONStorage = context.application.bot_data["storage"]
    user_id = q.from_user.id
    ich_u = context.user_data.get("ich_u")
    ia = context.user_data.get("ich_ia")
    gain = context.user_data.get("ich_gain")
    if not (ich_u and ia and gain is not None):
        context.user_data.clear()
        await q.message.reply_text("⚠️ بيانات ناقصة. أعد العملية.", reply_markup=kb_ichancy())
        return ST_ICH_MENU

    order = await create_order(storage, OT_ICH_WITHDRAW, user_id,
                               {"ichancy_username": ich_u, "ichancy_amount": int(ia), "bot_gain": int(gain)},
                               status=ST_PENDING)

    context.user_data.clear()
    await q.message.reply_text(TXT["sent_admin"].format(id=order["id"]), reply_markup=kb_ichancy())
    admin_text = fmt_order(order) + "\n\n📌 نسخ اسم الحساب:\n" + codeblock(str(ich_u))
    await notify_admins(context, admin_text, reply_markup=kb_order_actions(order["id"]))
    return ST_ICH_MENU

# =========================
# Admin commands + features
# =========================

def extract_user_id_from_message(update: Update) -> Optional[int]:
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
    if not await is_admin_any(update, context):
        await update.message.reply_text(TXT["admin_only"])
        return ConversationHandler.END
    await update.message.reply_text(TXT["admin_menu"], reply_markup=kb_admin())
    return AD_MENU

async def admin_menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not await is_admin_any(update, context):
        await update.message.reply_text(TXT["admin_only"])
        return ConversationHandler.END

    storage: JSONStorage = context.application.bot_data["storage"]
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["back_main"], reply_markup=kb_user_main())
        return ConversationHandler.END

    if text == "📌 الطلبات المعلقة":
        pending = await list_pending(storage, limit=25)
        if not pending:
            await update.message.reply_text(TXT["admin_no_pending"], reply_markup=kb_admin())
            return AD_MENU
        await update.message.reply_text(TXT["admin_pending_title"])
        for o in pending:
            await update.message.reply_text(fmt_order(o), reply_markup=kb_order_actions(o["id"]))
        return AD_MENU

    if text == "🔍 بحث مستخدم":
        await update.message.reply_text(TXT["admin_ask_user"])
        return AD_FIND_USER

    if text == "💳 تعديل رصيد (Super)":
        if not is_super_admin(update, context):
            await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
            return AD_MENU
        await update.message.reply_text(TXT["admin_ask_user"])
        return AD_ADJUST_USER

    if text == "📦 مخزون ايشانسي (Super)":
        if not is_super_admin(update, context):
            await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
            return AD_MENU
        await update.message.reply_text(TXT["inv_menu"], reply_markup=kb_inventory())
        return AD_INV_MENU

    if text == "👤 أدمن مساعد (Super)":
        if not is_super_admin(update, context):
            await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
            return AD_MENU
        await update.message.reply_text(TXT["admin_manage_assist"], reply_markup=kb_admin_assist())
        return AD_AS_MENU

    if text == "📣 رسالة جماعية (Super)":
        if not is_super_admin(update, context):
            await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
            return AD_MENU
        await update.message.reply_text(TXT["admin_broadcast_prompt"])
        return AD_BROADCAST

    if text == "💾 Backup (Super)":
        if not is_super_admin(update, context):
            await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
            return AD_MENU
        await admin_backup(update, context)
        return AD_MENU

    if text == "♻️ Restore (Super)":
        if not is_super_admin(update, context):
            await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
            return AD_MENU
        await set_maintenance(context, True)  # auto maintenance
        await update.message.reply_text(TXT["restore_start"])
        return AD_RESTORE_WAIT

    if text == "🔧 صيانة ON/OFF (Super)":
        if not is_super_admin(update, context):
            await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
            return AD_MENU
        on = await is_maintenance_on(context)
        await set_maintenance(context, not on)
        await update.message.reply_text(TXT["maintenance_off"] if on else TXT["maintenance_on"])
        return AD_MENU

    await update.message.reply_text("⚠️ اختر خيار من لوحة الأدمن 👇", reply_markup=kb_admin())
    return AD_MENU

async def admin_find_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not await is_admin_any(update, context):
        await update.message.reply_text(TXT["admin_only"])
        return ConversationHandler.END

    storage: JSONStorage = context.application.bot_data["storage"]
    uid = extract_user_id_from_message(update)
    if not uid:
        await update.message.reply_text("⚠️ أرسل ID رقمي أو حوّل رسالة منه 👇")
        return AD_FIND_USER

    u = await get_user(storage, uid)
    if not u:
        await update.message.reply_text(TXT["admin_user_not_found"])
        return AD_MENU

    w = await get_wallet(storage, uid)
    ich_u = ((u.get("ichancy") or {}) if isinstance(u, dict) else {}).get("username")

    await update.message.reply_text(
        f"👤 المستخدم: {uid}\n"
        f"@{u.get('username')}\n"
        f"📛 الاسم: {u.get('first_name')} {u.get('last_name')}\n"
        f"💼 ايشانسي: {ich_u if ich_u else 'لا يوجد'}\n"
        f"💰 balance={w['balance']} | hold={w['hold']}"
    )
    return AD_MENU

async def admin_adjust_user_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_super_admin(update, context):
        await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
        return AD_MENU

    uid = extract_user_id_from_message(update)
    if not uid:
        await update.message.reply_text("⚠️ أرسل ID رقمي أو حوّل رسالة منه 👇")
        return AD_ADJUST_USER

    context.user_data["admin_target_user"] = int(uid)
    await update.message.reply_text(TXT["admin_adjust_amount"])
    return AD_ADJUST_AMOUNT

async def admin_adjust_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_super_admin(update, context):
        await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
        return AD_MENU

    amt = parse_int(update.message.text or "")
    if amt is None:
        await update.message.reply_text("⚠️ قيمة غير صحيحة.\nمثال: 1000 أو -500")
        return AD_ADJUST_AMOUNT

    uid = context.user_data.get("admin_target_user")
    if not uid:
        await update.message.reply_text("⚠️ لم يتم اختيار مستخدم.")
        return AD_MENU

    storage: JSONStorage = context.application.bot_data["storage"]
    w = await add_balance(storage, int(uid), int(amt))
    await update.message.reply_text(f"{TXT['admin_adjust_done']}\nwallet: balance={w['balance']} hold={w['hold']}")
    return AD_MENU

# ----- order callbacks -----

async def admin_order_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()

    if not await is_admin_any(update, context):
        await q.message.reply_text(TXT["admin_only"])
        return

    data = q.data or ""
    if ":" not in data:
        return
    action, sid = data.split(":", 1)
    oid = parse_int(sid)
    if not oid:
        return

    storage: JSONStorage = context.application.bot_data["storage"]
    order = await get_order(storage, int(oid))
    if not order:
        await q.message.reply_text("❌ الطلب غير موجود.")
        return

    if action == CB_ORDER_EDIT:
        context.user_data["edit_order_id"] = int(oid)
        await q.message.reply_text(TXT["admin_edit_hint"])
        return

    if action == CB_ORDER_APPROVE:
        await admin_approve_order(context, order, q)
        return

    if action == CB_ORDER_REJECT:
        await admin_reject_order(context, order, q)
        return

async def admin_edit_listener(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    if not await is_admin_any(update, context):
        return

    edit_id = context.user_data.get("edit_order_id")
    if not edit_id:
        return

    storage: JSONStorage = context.application.bot_data["storage"]
    order = await get_order(storage, int(edit_id))
    if not order:
        context.user_data.pop("edit_order_id", None)
        await update.message.reply_text("❌ الطلب غير موجود.")
        return

    text = safe_str(update.message.text, 256)
    otype = order.get("type")
    data = dict(order.get("data", {}) or {})

    try:
        if otype == OT_BOT_TOPUP:
            if "," not in text:
                await update.message.reply_text("⚠️ الصيغة: code,op,amount")
                return
            a, b, c = [p.strip() for p in text.split(",", 2)]
            amt = parse_int(c)
            if not a or not b or amt is None or amt <= 0:
                await update.message.reply_text("⚠️ قيم غير صحيحة.")
                return
            data["code"] = a
            data["operation_no"] = b
            data["amount"] = int(amt)

        elif otype == OT_BOT_WITHDRAW:
            if "," not in text:
                await update.message.reply_text("⚠️ الصيغة: receiver,amount")
                return
            a, b = [p.strip() for p in text.split(",", 1)]
            amt = parse_int(b)
            if not a or amt is None or amt <= 0:
                await update.message.reply_text("⚠️ قيم غير صحيحة.")
                return
            data["receiver_no"] = a
            data["amount"] = int(amt)

        elif otype in (OT_ICH_TOPUP, OT_ICH_WITHDRAW):
            ia = parse_int(text)
            if ia is None or ia <= 0 or ia % 100 != 0:
                await update.message.reply_text(TXT["must_multiple_100"])
                return
            data["ichancy_amount"] = int(ia)
            if otype == OT_ICH_TOPUP:
                data["bot_cost"] = int(ia // 100)
            else:
                data["bot_gain"] = int(ia // 100)
        else:
            await update.message.reply_text("⚠️ هذا النوع لا يدعم التعديل هنا.")
            context.user_data.pop("edit_order_id", None)
            return

        await update_order(storage, int(edit_id), {"data": data})
        context.user_data.pop("edit_order_id", None)
        await update.message.reply_text("✅ تم تعديل الطلب بنجاح.")
    except Exception:
        context.user_data.pop("edit_order_id", None)
        await update.message.reply_text("⚠️ تعذر التعديل. جرّب مرة ثانية.")

async def admin_approve_order(context: ContextTypes.DEFAULT_TYPE, order: dict, q) -> None:
    storage: JSONStorage = context.application.bot_data["storage"]
    oid = int(order["id"])
    if order.get("status") != ST_PENDING:
        await q.message.reply_text("ℹ️ هذا الطلب ليس Pending.")
        return

    otype = order.get("type")
    user_id = int(order.get("user_id"))
    data = order.get("data", {}) or {}

    try:
        if otype == OT_BOT_TOPUP:
            amt = int(data.get("amount", 0))
            await add_balance(storage, user_id, amt)
            await update_order(storage, oid, {"status": ST_APPROVED})
            await q.message.reply_text(TXT["admin_order_updated"].format(status=ST_APPROVED))
            try:
                await context.bot.send_message(chat_id=user_id, text=TXT["user_approved"].format(id=oid))
            except Exception:
                pass

        elif otype == OT_BOT_WITHDRAW:
            amt = int(data.get("amount", 0))
            await finalize_withdraw(storage, user_id, amt)
            await update_order(storage, oid, {"status": ST_APPROVED})
            await q.message.reply_text(TXT["admin_order_updated"].format(status=ST_APPROVED))
            try:
                await context.bot.send_message(chat_id=user_id, text=TXT["user_approved_24h"].format(id=oid))
            except Exception:
                pass

        elif otype == OT_ICH_TOPUP:
            cost = int(data.get("bot_cost", 0))
            ok, _ = await deduct_balance(storage, user_id, cost)
            if not ok:
                await update_order(storage, oid, {"status": ST_REJECTED})
                await q.message.reply_text("⚠️ تم رفض الطلب تلقائياً لأن رصيد المستخدم لا يكفي وقت الموافقة.")
                try:
                    await context.bot.send_message(chat_id=user_id, text=TXT["user_rejected"].format(id=oid))
                except Exception:
                    pass
                return
            await update_order(storage, oid, {"status": ST_APPROVED})
            await q.message.reply_text(TXT["admin_order_updated"].format(status=ST_APPROVED))
            try:
                await context.bot.send_message(chat_id=user_id, text=TXT["user_approved"].format(id=oid))
            except Exception:
                pass

        elif otype == OT_ICH_WITHDRAW:
            gain = int(data.get("bot_gain", 0))
            await add_balance(storage, user_id, gain)
            await update_order(storage, oid, {"status": ST_APPROVED})
            await q.message.reply_text(TXT["admin_order_updated"].format(status=ST_APPROVED))
            try:
                await context.bot.send_message(chat_id=user_id, text=TXT["user_approved"].format(id=oid))
            except Exception:
                pass

        else:
            await q.message.reply_text("⚠️ نوع طلب غير معروف.")
            return

    except Exception:
        logger.exception("Approve failed")
        await q.message.reply_text("❌ تعذر قبول الطلب بسبب خطأ.")

async def admin_reject_order(context: ContextTypes.DEFAULT_TYPE, order: dict, q) -> None:
    storage: JSONStorage = context.application.bot_data["storage"]
    oid = int(order["id"])
    if order.get("status") != ST_PENDING:
        await q.message.reply_text("ℹ️ هذا الطلب ليس Pending.")
        return

    otype = order.get("type")
    user_id = int(order.get("user_id"))
    data = order.get("data", {}) or {}

    try:
        if otype == OT_BOT_WITHDRAW:
            amt = int(data.get("amount", 0))
            await release_hold(storage, user_id, amt)

        await update_order(storage, oid, {"status": ST_REJECTED})
        await q.message.reply_text(TXT["admin_order_updated"].format(status=ST_REJECTED))
        try:
            await context.bot.send_message(chat_id=user_id, text=TXT["user_rejected"].format(id=oid))
        except Exception:
            pass

    except Exception:
        logger.exception("Reject failed")
        await q.message.reply_text("❌ تعذر رفض الطلب بسبب خطأ.")

# ----- inventory (super) -----

async def admin_inventory_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_super_admin(update, context):
        await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
        return AD_MENU

    storage: JSONStorage = context.application.bot_data["storage"]
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["admin_menu"], reply_markup=kb_admin())
        return AD_MENU

    if text == "➕ إضافة حساب":
        await update.message.reply_text(TXT["inv_add_prompt"], reply_markup=kb_back_only())
        return AD_INV_ADD

    if text == "📋 إحصائيات":
        a, b = await inv_stats(storage)
        await update.message.reply_text(TXT["inv_list"].format(a=a, b=b), reply_markup=kb_inventory())
        return AD_INV_MENU

    if text == "🗑️ حذف حساب":
        await update.message.reply_text(TXT["inv_delete_prompt"], reply_markup=kb_back_only())
        return AD_INV_DELETE

    await update.message.reply_text("⚠️ اختر خيار من مخزون ايشانسي 👇", reply_markup=kb_inventory())
    return AD_INV_MENU

async def admin_inventory_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_super_admin(update, context):
        return AD_MENU

    text = (update.message.text or "").strip()
    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["inv_menu"], reply_markup=kb_inventory())
        return AD_INV_MENU

    if "," not in text:
        await update.message.reply_text("⚠️ الصيغة: username,password", reply_markup=kb_back_only())
        return AD_INV_ADD

    u, p = [safe_str(x, 128) for x in text.split(",", 1)]
    if len(u) < 3 or len(p) < 3:
        await update.message.reply_text("⚠️ قيم غير صحيحة.", reply_markup=kb_back_only())
        return AD_INV_ADD

    storage: JSONStorage = context.application.bot_data["storage"]
    await inv_add(storage, u, p)
    await update.message.reply_text(TXT["inv_added"], reply_markup=kb_inventory())
    return AD_INV_MENU

async def admin_inventory_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_super_admin(update, context):
        return AD_MENU

    text = (update.message.text or "").strip()
    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["inv_menu"], reply_markup=kb_inventory())
        return AD_INV_MENU

    storage: JSONStorage = context.application.bot_data["storage"]
    ok = await inv_delete_by_username(storage, text)
    await update.message.reply_text(TXT["inv_deleted"] if ok else TXT["inv_not_found"], reply_markup=kb_inventory())
    return AD_INV_MENU

# ----- assistants (super) -----

async def admin_assist_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_super_admin(update, context):
        await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
        return AD_MENU

    storage: JSONStorage = context.application.bot_data["storage"]
    text = (update.message.text or "").strip()

    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["admin_menu"], reply_markup=kb_admin())
        return AD_MENU

    if text == "➕ إضافة مساعد":
        await update.message.reply_text(TXT["admin_add_assist_prompt"], reply_markup=kb_back_only())
        return AD_AS_ADD

    if text == "➖ حذف مساعد":
        await update.message.reply_text(TXT["admin_remove_assist_prompt"], reply_markup=kb_back_only())
        return AD_AS_REMOVE

    if text == "📋 عرض المساعدين":
        ids = sorted(list(await get_assist_admins(storage)))
        lines = "\n".join([f"• `{i}`" for i in ids]) if ids else "— لا يوجد —"
        await update.message.reply_text(TXT["admin_assist_list"].format(list=lines), reply_markup=kb_admin_assist())
        return AD_AS_MENU

    await update.message.reply_text("⚠️ اختر خيار 👇", reply_markup=kb_admin_assist())
    return AD_AS_MENU

async def admin_assist_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_super_admin(update, context):
        return AD_MENU

    text = (update.message.text or "").strip()
    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["admin_manage_assist"], reply_markup=kb_admin_assist())
        return AD_AS_MENU

    uid = extract_user_id_from_message(update)
    if not uid:
        await update.message.reply_text("⚠️ أرسل ID رقمي أو حوّل رسالة منه 👇", reply_markup=kb_back_only())
        return AD_AS_ADD

    storage: JSONStorage = context.application.bot_data["storage"]
    data = await storage.read("admins.json", {"assist_admin_ids": []})
    ids = set(int(x) for x in data.get("assist_admin_ids", []) if isinstance(x, int) or str(x).isdigit())
    ids.add(int(uid))
    data["assist_admin_ids"] = sorted(list(ids))
    await storage.write("admins.json", data)

    await update.message.reply_text(TXT["admin_assist_added"], reply_markup=kb_admin_assist())
    return AD_AS_MENU

async def admin_assist_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_super_admin(update, context):
        return AD_MENU

    text = (update.message.text or "").strip()
    if text == "⬅️ رجوع":
        await update.message.reply_text(TXT["admin_manage_assist"], reply_markup=kb_admin_assist())
        return AD_AS_MENU

    uid = parse_int(text)
    if not uid:
        await update.message.reply_text("⚠️ أرسل ID رقمي صحيح 👇", reply_markup=kb_back_only())
        return AD_AS_REMOVE

    storage: JSONStorage = context.application.bot_data["storage"]
    data = await storage.read("admins.json", {"assist_admin_ids": []})
    ids = [int(x) for x in data.get("assist_admin_ids", []) if isinstance(x, int) or str(x).isdigit()]
    ids = [i for i in ids if i != int(uid)]
    data["assist_admin_ids"] = ids
    await storage.write("admins.json", data)

    await update.message.reply_text(TXT["admin_assist_removed"], reply_markup=kb_admin_assist())
    return AD_AS_MENU

# ----- broadcast (super) -----

async def admin_broadcast_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_super_admin(update, context):
        await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
        return AD_MENU

    storage: JSONStorage = context.application.bot_data["storage"]
    users = await storage.read("users.json", {})
    user_ids = [int(v.get("id")) for v in users.values() if isinstance(v, dict) and v.get("id")]

    ok = 0
    bad = 0

    msg = update.message
    text = msg.text if msg.text else None
    photo = msg.photo[-1] if msg.photo else None
    video = msg.video if msg.video else None
    caption = msg.caption

    for uid in user_ids:
        try:
            if text and not photo and not video:
                await context.bot.send_message(chat_id=uid, text=text)
            elif photo:
                await context.bot.send_photo(chat_id=uid, photo=photo.file_id, caption=caption)
            elif video:
                await context.bot.send_video(chat_id=uid, video=video.file_id, caption=caption)
            else:
                bad += 1
                continue
            ok += 1
            await asyncio.sleep(0.06)
        except RetryAfter as e:
            await safe_sleep_for_flood(e)
        except (Forbidden, TimedOut, NetworkError):
            bad += 1
        except Exception:
            bad += 1

    await update.message.reply_text(TXT["admin_broadcast_done"].format(ok=ok, bad=bad), reply_markup=kb_admin())
    return AD_MENU

# ----- backup/restore/maintenance -----

async def set_maintenance(context: ContextTypes.DEFAULT_TYPE, on: bool) -> None:
    storage: JSONStorage = context.application.bot_data["storage"]
    s = await storage.read("settings.json", {"maintenance": False})
    s["maintenance"] = bool(on)
    await storage.write("settings.json", s)

async def admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not is_super_admin(update, context):
        return
    cfg: Config = context.application.bot_data["cfg"]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in [
            "users.json",
            "wallet.json",
            "orders.json",
            "logs.json",
            "settings.json",
            "admins.json",
            "ichancy_inventory.json",
        ]:
            p = os.path.join(cfg.DATA_DIR, fn)
            if os.path.exists(p):
                z.write(p, arcname=fn)
    buf.seek(0)
    await update.message.reply_document(document=buf, filename="backup.zip", caption=TXT["backup_ready"])

async def admin_restore_wait(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_super_admin(update, context):
        await update.message.reply_text("⛔ هذه الميزة للسوبر أدمن فقط.")
        return AD_MENU

    msg = update.message
    if not msg.document:
        await msg.reply_text("⚠️ أرسل ملف ZIP كـ Document لو سمحت 👇")
        return AD_RESTORE_WAIT

    doc = msg.document
    if not (doc.file_name or "").lower().endswith(".zip"):
        await msg.reply_text(TXT["restore_bad"])
        return AD_RESTORE_WAIT

    cfg: Config = context.application.bot_data["cfg"]
    storage: JSONStorage = context.application.bot_data["storage"]

    try:
        f = await context.bot.get_file(doc.file_id)
        tmp_dir = tempfile.mkdtemp(prefix="restore_")
        zip_path = os.path.join(tmp_dir, "backup.zip")
        await f.download_to_drive(zip_path)

        required = {
            "users.json",
            "wallet.json",
            "orders.json",
            "logs.json",
            "settings.json",
            "admins.json",
            "ichancy_inventory.json",
        }
        with zipfile.ZipFile(zip_path, "r") as z:
            names = set(z.namelist())
            if not required.issubset(names):
                await msg.reply_text(TXT["restore_bad"])
                return AD_RESTORE_WAIT
            extract_dir = os.path.join(tmp_dir, "ex")
            os.makedirs(extract_dir, exist_ok=True)
            z.extractall(extract_dir)

        for fn in required:
            p = os.path.join(extract_dir, fn)
            with open(p, "r", encoding="utf-8") as r:
                data = json.load(r)
            await storage.write(fn, data)

        await set_maintenance(context, True)  # keep ON
        await msg.reply_text(TXT["restore_ok"], reply_markup=kb_admin())
        return AD_MENU

    except Exception:
        logger.exception("Restore failed")
        await msg.reply_text(TXT["restore_bad"])
        return AD_RESTORE_WAIT

# =========================
# Global error handler
# =========================

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(TXT["try_again"])
    except Exception:
        pass

# =========================
# Build main application
# =========================

def main() -> None:
    load_dotenv()
    cfg = Config.from_env()
    ok, msg = cfg.validate()
    setup_logging(cfg.LOG_LEVEL)
    if not ok:
        logger.critical("❌ Invalid config: %s", msg)
        raise SystemExit(1)

    os.makedirs(cfg.DATA_DIR, exist_ok=True)
    storage = JSONStorage(cfg.DATA_DIR)

    app: Application = ApplicationBuilder().token(cfg.BOT_TOKEN).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["storage"] = storage

    # /start & subscription
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_check_sub, pattern=f"^{CB_CHECK_SUB}$"))

    # Confirm callbacks (unique patterns)
    app.add_handler(CallbackQueryHandler(cb_topup_confirm, pattern=f"^({CB_TOPUP_OK}|{CB_TOPUP_NO})$"))
    app.add_handler(CallbackQueryHandler(cb_withdraw_confirm, pattern=f"^({CB_WD_OK}|{CB_WD_NO})$"))
    app.add_handler(CallbackQueryHandler(cb_ich_create_confirm, pattern=f"^({CB_ICH_CREATE_OK}|{CB_ICH_CREATE_NO})$"))
    app.add_handler(CallbackQueryHandler(cb_ich_delete_confirm, pattern=f"^({CB_ICH_DEL_OK}|{CB_ICH_DEL_NO})$"))
    app.add_handler(CallbackQueryHandler(cb_ich_topup_confirm, pattern=f"^({CB_ICH_TOPUP_OK}|{CB_ICH_TOPUP_NO})$"))
    app.add_handler(CallbackQueryHandler(cb_ich_withdraw_confirm, pattern=f"^({CB_ICH_WD_OK}|{CB_ICH_WD_NO})$"))

    # Admin order inline callbacks
    app.add_handler(CallbackQueryHandler(admin_order_callbacks, pattern=r"^(ord_ok|ord_no|ord_edit):\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_listener), group=1)

    # User conversation
    user_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & ~filters.COMMAND, user_entry_router)],
        states={
            ST_TOPUP_METHOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_choose_method)],
            ST_TOPUP_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_choose_code)],
            ST_TOPUP_OP: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_get_op)],
            ST_TOPUP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_get_amount)],
            ST_TOPUP_CONFIRM: [],

            ST_WD_METHOD: [MessageHandler(filters.TEXT & ~filters.COMMAND, wd_choose_method)],
            ST_WD_RECEIVER: [MessageHandler(filters.TEXT & ~filters.COMMAND, wd_get_receiver)],
            ST_WD_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, wd_get_amount)],
            ST_WD_CONFIRM: [],

            ST_ICH_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, ich_menu_handler)],
            ST_ICH_CREATE_DESIRED: [MessageHandler(filters.TEXT & ~filters.COMMAND, ich_create_get_desired)],
            ST_ICH_CREATE_CONFIRM: [],
            ST_ICH_TOPUP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ich_topup_amount)],
            ST_ICH_TOPUP_CONFIRM: [],
            ST_ICH_WD_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ich_withdraw_amount)],
            ST_ICH_WD_CONFIRM: [],
            ST_ICH_DEL_CONFIRM: [],
        },
        fallbacks=[],
        name="user_conv",
        persistent=False,
        allow_reentry=True,
    )
    app.add_handler(user_conv, group=10)

    # Admin conversation
    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("admin", cmd_admin)],
        states={
            AD_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_menu_router)],
            AD_FIND_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_find_user)],
            AD_ADJUST_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_adjust_user_pick)],
            AD_ADJUST_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_adjust_amount)],

            AD_INV_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_inventory_router)],
            AD_INV_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_inventory_add)],
            AD_INV_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_inventory_delete)],

            AD_AS_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_assist_router)],
            AD_AS_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_assist_add)],
            AD_AS_REMOVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_assist_remove)],

            AD_BROADCAST: [MessageHandler(filters.ALL & ~filters.COMMAND, admin_broadcast_receive)],
            AD_RESTORE_WAIT: [MessageHandler(filters.ALL & ~filters.COMMAND, admin_restore_wait)],
        },
        fallbacks=[],
        name="admin_conv",
        persistent=False,
        allow_reentry=True,
    )
    app.add_handler(admin_conv, group=0)

    app.add_error_handler(on_error)

    logger.info("✅ Bot started (polling). DATA_DIR=%s", cfg.DATA_DIR)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
