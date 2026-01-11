# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Telegram Bot — Single-file, Polling only
python-telegram-bot v20+ (async)

✅ Local JSON storage only (no external DB)
✅ asyncio locks per file + atomic write (tmp + os.replace)
✅ Data survives restarts
"""

import os, re, io, json, time, zipfile, shutil, asyncio, logging, tempfile, difflib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InputFile,
)
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest
from telegram.ext import (
    Application, ContextTypes,
    CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters
)

def env_str(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None else str(v).strip()

def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(str(v).strip())
    except Exception:
        return default

BOT_TOKEN = env_str("BOT_TOKEN", "")
SUPER_ADMIN_ID = env_int("SUPER_ADMIN_ID", 0)
REQUIRED_CHANNEL = env_str("REQUIRED_CHANNEL", "")
SUPPORT_USERNAME = env_str("SUPPORT_USERNAME", "@support")
DATA_DIR = env_str("DATA_DIR", "data")
MIN_TOPUP = env_int("MIN_TOPUP", 15000)
MIN_WITHDRAW = env_int("MIN_WITHDRAW", 500)
REF_RATE = float(env_str("REF_RATE", "0.04") or "0.04")
REF_MIN_ACTIVE = env_int("REF_MIN_ACTIVE", 3)
REF_PERIOD_DAYS = env_int("REF_PERIOD_DAYS", 10)
SYRIATEL_CODES = [c.strip() for c in env_str("SYRIATEL_CODES", "").split(",") if c.strip()]
LOG_LEVEL = env_str("LOG_LEVEL", "INFO").upper()

if not BOT_TOKEN:
    raise SystemExit("Missing BOT_TOKEN")
if SUPER_ADMIN_ID <= 0:
    raise SystemExit("Missing/invalid SUPER_ADMIN_ID")
if not REQUIRED_CHANNEL.startswith("@"):
    raise SystemExit("Missing/invalid REQUIRED_CHANNEL (must start with @)")
if not SUPPORT_USERNAME.startswith("@"):
    SUPPORT_USERNAME = "@" + SUPPORT_USERNAME.lstrip("@")
if not SYRIATEL_CODES:
    SYRIATEL_CODES = ["45191900"]

os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("singlebot")

class JsonStore:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self._locks: Dict[str, asyncio.Lock] = {}

    def path(self, filename: str) -> str:
        return os.path.join(self.base_dir, filename)

    def lock(self, filename: str) -> asyncio.Lock:
        if filename not in self._locks:
            self._locks[filename] = asyncio.Lock()
        return self._locks[filename]

    async def read(self, filename: str, default: Any) -> Any:
        p = self.path(filename)
        async with self.lock(filename):
            if not os.path.exists(p):
                return default
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                log.exception("Failed to read %s", p)
                return default

    async def write(self, filename: str, data: Any) -> None:
        p = self.path(filename)
        d = os.path.dirname(p) or "."
        async with self.lock(filename):
            fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=d)
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

    async def ensure(self, filename: str, default: Any) -> None:
        if os.path.exists(self.path(filename)):
            return
        await self.write(filename, default)

STORE = JsonStore(DATA_DIR)

F_USERS = "users.json"
F_WALLETS = "wallets.json"
F_ORDERS = "orders.json"
F_ICHANCY = "ichancy_accounts.json"
F_ADMINS = "admins.json"
F_SETTINGS = "settings.json"
F_REFS = "referrals.json"

DEFAULT_USERS = {"users": {}}
DEFAULT_WALLETS = {"wallets": {}}
DEFAULT_ORDERS = {"orders": []}
DEFAULT_ICHANCY = {"stock": [], "assigned": {}}
DEFAULT_ADMINS = {"assistants": []}
DEFAULT_SETTINGS = {"maintenance": False}
DEFAULT_REFS = {"period_start": "", "inviters": {}}

def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def iso_to_dt(iso: str) -> Optional[datetime]:
    try:
        if not iso:
            return None
        s = iso.strip()
        if s.endswith("Z"):
            s = s[:-1]
        return datetime.fromisoformat(s)
    except Exception:
        return None

async def ref_get_data() -> Dict[str, Any]:
    data = await STORE.read(F_REFS, DEFAULT_REFS)
    if not data.get("period_start"):
        data["period_start"] = now_iso()
        await STORE.write(F_REFS, data)
    ps = iso_to_dt(data.get("period_start", "")) or datetime.utcnow()
    if (datetime.utcnow() - ps).days >= max(1, int(REF_PERIOD_DAYS)):
        data["period_start"] = now_iso()
        await STORE.write(F_REFS, data)
    return data

def ref_period_remaining_days(period_start_iso: str) -> int:
    ps = iso_to_dt(period_start_iso) or datetime.utcnow()
    elapsed = (datetime.utcnow() - ps).days
    return max(0, int(REF_PERIOD_DAYS) - elapsed)

def count_active_refs(inv: Dict[str, Any], period_start_iso: str) -> int:
    ps = iso_to_dt(period_start_iso) or datetime.utcnow()
    c = 0
    for _, info in (inv.get("refs", {}) or {}).items():
        la = iso_to_dt((info or {}).get("last_active_at", ""))
        if la and la >= ps:
            c += 1
    return c

async def bind_referral(new_user_id: int, inviter_id: int) -> None:
    if inviter_id <= 0 or new_user_id == inviter_id:
        return
    users = await STORE.read(F_USERS, DEFAULT_USERS)
    if str(inviter_id) not in users.get("users", {}):
        return
    nu = users["users"].get(str(new_user_id))
    if not nu or nu.get("inviter_id"):
        return
    nu["inviter_id"] = inviter_id
    nu["invited_at"] = now_iso()
    await STORE.write(F_USERS, users)

    refs = await ref_get_data()
    inv = refs["inviters"].get(str(inviter_id)) or {"refs": {}, "pending": 0, "paid": 0}
    inv["refs"].setdefault(str(new_user_id), {"joined_at": now_iso(), "last_active_at": ""})
    refs["inviters"][str(inviter_id)] = inv
    await STORE.write(F_REFS, refs)

async def mark_ref_active(referred_user_id: int) -> None:
    users = await STORE.read(F_USERS, DEFAULT_USERS)
    ru = users.get("users", {}).get(str(referred_user_id)) or {}
    inviter_id = ru.get("inviter_id")
    if not inviter_id:
        return
    refs = await ref_get_data()
    inv = refs.get("inviters", {}).get(str(inviter_id))
    if not inv:
        return
    ref_entry = (inv.get("refs", {}) or {}).get(str(referred_user_id))
    if not ref_entry:
        return
    ref_entry["last_active_at"] = now_iso()
    inv["refs"][str(referred_user_id)] = ref_entry
    refs["inviters"][str(inviter_id)] = inv
    await STORE.write(F_REFS, refs)

async def add_ref_commission_if_eligible(referred_user_id: int, amount: int) -> None:
    if amount <= 0:
        return
    users = await STORE.read(F_USERS, DEFAULT_USERS)
    ru = users.get("users", {}).get(str(referred_user_id)) or {}
    inviter_id = ru.get("inviter_id")
    if not inviter_id:
        return

    await mark_ref_active(referred_user_id)

    refs = await ref_get_data()
    inv = refs.get("inviters", {}).get(str(inviter_id)) or {"refs": {}, "pending": 0, "paid": 0}
    active = count_active_refs(inv, refs.get("period_start", ""))
    if active < int(REF_MIN_ACTIVE):
        return

    commission = int(amount * float(REF_RATE))
    if commission <= 0:
        return

    inv["pending"] = int(inv.get("pending", 0)) + commission
    refs["inviters"][str(inviter_id)] = inv
    await STORE.write(F_REFS, refs)

async def referral_message(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    refs = await ref_get_data()
    inv = refs.get("inviters", {}).get(str(user_id)) or {"refs": {}, "pending": 0, "paid": 0}
    total = len((inv.get("refs") or {}))
    active = count_active_refs(inv, refs.get("period_start", ""))
    remain = ref_period_remaining_days(refs.get("period_start", ""))

    bot_username = getattr(context.bot, "username", "") or ""
    link = f"https://t.me/{bot_username}?start={user_id}" if bot_username else "(BOT_USERNAME غير متاح)"

    lines = [
        "🤝 <b>كن وكيلاً معنا بأبسط طريقة</b>",
        "إحصل على نسبة ثابتة لكل عمليات الشحن والسحب القادمة عن طريق رابط إحالتك ضمن البوت ✅",
        "",
        "1- انسخ رابط إحالتك من هنا.",
        "2- عند تسجيل شخص عبر رابطك سنحسب نسبة ثابتة لعمليات الشحن والسحب الخاصة به.",
        f"3- يتم حساب الارباح عند وجود <b>{REF_MIN_ACTIVE}</b> إحالات نشطة او أكثر 🔥",
        "",
        f"🔗 <b>رابط الاحالة الخاص بك:</b>\n<code>{link}</code>",
        "",
        f"👥 <b>إجمالي الإحالات:</b> {total}",
        f"✅ <b>الإحالات النشطة:</b> {active}",
        "",
        f"⏳ <b>مدة حساب الارباح:</b> {REF_PERIOD_DAYS} يوم/أيام",
        f"🗓 <b>الأيام المتبقية على توزيع الارباح:</b> {remain} يوم/أيام",
        "",
        f"💰 <b>نسبة العمولة:</b> {int(float(REF_RATE)*100)}%",
        f"📌 <b>أرباحك المعلّقة:</b> {int(inv.get('pending',0))} ل.س",
        "ℹ️ يتم توزيع الأرباح يدويًا من الأدمن.",
    ]
    return "\n".join(lines)

def safe_int(txt: str) -> Optional[int]:
    try:
        return int(str(txt).strip())
    except Exception:
        return None

def norm(txt: str) -> str:
    return (txt or "").strip()

def startswith_map(txt: str, mapping: Dict[str, str]) -> Optional[str]:
    t = norm(txt)
    for k, v in mapping.items():
        if t.startswith(k):
            return v
    return None

def gen_id(prefix: str) -> str:
    return f"{prefix}-{int(time.time())}-{int.from_bytes(os.urandom(2),'big')}"

def mk_main_menu() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("💼 حساب ايشانسي"), KeyboardButton("💰 محفظتي")],
        [KeyboardButton("➕ شحن رصيد البوت"), KeyboardButton("➖ سحب رصيد من البوت")],
        [KeyboardButton("🧾 إلغاء آخر طلب سحب"), KeyboardButton("🆘 دعم")],
        [KeyboardButton("🤝 الوكالة / الإحالات")],
    ]
    rows.append([KeyboardButton("🏠 القائمة الرئيسية")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def mk_ich_menu() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("1️⃣ إنشاء / استلام حساب ايشانسي"), KeyboardButton("2️⃣ شحن حساب ايشانسي")],
        [KeyboardButton("3️⃣ سحب من حساب ايشانسي"), KeyboardButton("4️⃣ حذف حساب ايشانسي")],
        [KeyboardButton("↩️ رجوع"), KeyboardButton("🏠 القائمة الرئيسية")],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def mk_admin_menu(super_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("📌 الطلبات المعلقة"), KeyboardButton("🔎 بحث مستخدم")],
    ]
    if super_admin:
        rows.append([KeyboardButton("💰 تعديل رصيد"), KeyboardButton("📦 مخزون ايشانسي")])
        rows.append([KeyboardButton("🤝 الإحالات")])
        rows.append([KeyboardButton("👥 تعيين أدمن مساعد"), KeyboardButton("📢 رسالة جماعية")])
        rows.append([KeyboardButton("💾 Backup"), KeyboardButton("♻️ Restore")])
        rows.append([KeyboardButton("🛠 صيانة"), KeyboardButton("↩️ رجوع")])
    else:
        rows.append([KeyboardButton("↩️ رجوع")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

async def bootstrap() -> None:
    await STORE.ensure(F_USERS, DEFAULT_USERS)
    await STORE.ensure(F_WALLETS, DEFAULT_WALLETS)
    await STORE.ensure(F_ORDERS, DEFAULT_ORDERS)
    await STORE.ensure(F_ICHANCY, DEFAULT_ICHANCY)
    await STORE.ensure(F_ADMINS, DEFAULT_ADMINS)
    await STORE.ensure(F_SETTINGS, DEFAULT_SETTINGS)
    await STORE.ensure(F_REFS, DEFAULT_REFS)

async def ensure_user(update: Update) -> None:
    u = update.effective_user
    if not u:
        return
    data = await STORE.read(F_USERS, DEFAULT_USERS)
    uid = str(u.id)
    if uid not in data["users"]:
        data["users"][uid] = {"created_at": now_iso(), "username": u.username or "", "first_name": u.first_name or ""}
        await STORE.write(F_USERS, data)

async def get_wallet(user_id: int) -> Tuple[int, int]:
    data = await STORE.read(F_WALLETS, DEFAULT_WALLETS)
    w = data["wallets"].get(str(user_id))
    if not w:
        w = {"balance": 0, "hold": 0}
        data["wallets"][str(user_id)] = w
        await STORE.write(F_WALLETS, data)
    return int(w.get("balance", 0)), int(w.get("hold", 0))

async def set_wallet(user_id: int, balance: int, hold: int) -> None:
    balance = max(0, int(balance))
    hold = max(0, int(hold))
    data = await STORE.read(F_WALLETS, DEFAULT_WALLETS)
    data["wallets"][str(user_id)] = {"balance": balance, "hold": hold}
    await STORE.write(F_WALLETS, data)

async def add_wallet(user_id: int, db: int = 0, dh: int = 0) -> Tuple[int, int]:
    b, h = await get_wallet(user_id)
    nb, nh = b + int(db), h + int(dh)
    if nb < 0 or nh < 0:
        raise ValueError("Negative wallet not allowed")
    await set_wallet(user_id, nb, nh)
    return nb, nh

async def all_orders() -> List[Dict[str, Any]]:
    data = await STORE.read(F_ORDERS, DEFAULT_ORDERS)
    return data.get("orders", []) or []

async def save_orders(orders: List[Dict[str, Any]]) -> None:
    await STORE.write(F_ORDERS, {"orders": orders})

async def admins_list() -> List[int]:
    data = await STORE.read(F_ADMINS, DEFAULT_ADMINS)
    assistants = data.get("assistants", []) or []
    out = [SUPER_ADMIN_ID]
    for x in assistants:
        try:
            out.append(int(x))
        except Exception:
            pass
    return sorted(list(set(out)))

async def is_admin(uid: int) -> bool:
    return uid in (await admins_list())

def is_super(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID

async def maintenance_enabled() -> bool:
    s = await STORE.read(F_SETTINGS, DEFAULT_SETTINGS)
    return bool(s.get("maintenance", False))

async def set_maintenance(val: bool) -> None:
    s = await STORE.read(F_SETTINGS, DEFAULT_SETTINGS)
    s["maintenance"] = bool(val)
    await STORE.write(F_SETTINGS, s)

async def gate_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not await maintenance_enabled():
        return True
    u = update.effective_user
    if u and await is_admin(u.id):
        return True
    try:
        if update.callback_query:
            await update.callback_query.answer("🛠 البوت تحت الصيانة حالياً.", show_alert=True)
        elif update.message:
            await update.message.reply_text("🛠 البوت تحت الصيانة حالياً.\nارجع جرّب بعد شوي 🙏")
    except Exception:
        pass
    return False

async def require_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    u = update.effective_user
    if not u:
        return False
    try:
        m = await context.bot.get_chat_member(REQUIRED_CHANNEL, u.id)
        st = getattr(m, "status", "")
        if st in ("member", "administrator", "creator"):
            return True
    except Exception:
        if await is_admin(u.id):
            return True
    join_url = f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ اشترك بالقناة", url=join_url)],
        [InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="sys:checksub")],
    ])
    msg = "🔒 لازم تشترك بالقناة أولاً.\nبعد الاشتراك اضغط: 🔄 تحقق من الاشتراك ✅"
    if update.message:
        await update.message.reply_text(msg, reply_markup=kb)
    else:
        q = update.callback_query
        if q:
            await q.answer()
            await q.message.reply_text(msg, reply_markup=kb)
    return False

async def notify_admins(app: Application, text: str, reply_markup=None) -> None:
    for aid in await admins_list():
        try:
            await app.bot.send_message(aid, text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        except Exception:
            pass

def order_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ قبول", callback_data=f"adm:approve:{order_id}"),
         InlineKeyboardButton("❌ رفض", callback_data=f"adm:reject:{order_id}")],
        [InlineKeyboardButton("✏️ تعديل قبل القبول", callback_data=f"adm:edit:{order_id}")],
    ])

def order_text(o: Dict[str, Any]) -> str:
    t = o.get("type", "")
    st = o.get("status", "")
    uid = o.get("user_id", "")
    d = o.get("data", {}) or {}
    lines = [
        f"🧾 <b>طلب</b> #{o.get('id')}",
        f"📌 النوع: <b>{t}</b>",
        f"⏳ الحالة: <b>{st}</b>",
        f"👤 المستخدم: <code>{uid}</code>",
        f"🕒 <code>{o.get('created_at','')}</code>",
    ]
    if t == "topup":
        lines += [f"🔢 الكود: <code>{d.get('code','')}</code>",
                  f"🧾 رقم العملية: <code>{d.get('txn','')}</code>",
                  f"💰 المبلغ: <b>{d.get('amount',0)}</b>"]
    if t == "withdraw":
        lines += [f"📞 رقم المستلم: <code>{d.get('receiver','')}</code>",
                  f"💰 المبلغ: <b>{d.get('amount',0)}</b>"]
    return "\n".join(lines)

(
    S_MAIN,
    S_TOPUP_METHOD, S_TOPUP_CODE, S_TOPUP_TXN, S_TOPUP_AMOUNT, S_TOPUP_CONFIRM,
    S_WD_METHOD, S_WD_RECEIVER, S_WD_AMOUNT, S_WD_CONFIRM,
    S_ICH_MENU, S_ICH_CLAIM_QUERY, S_ICH_CLAIM_CONFIRM, S_ICH_TOPUP, S_ICH_WD, S_ICH_DEL,
    S_ADMIN_MENU, S_ADMIN_SEARCH, S_ADMIN_SETBAL_UID, S_ADMIN_SETBAL_AMT,
    S_ADMIN_ASSIST, S_ADMIN_BROADCAST, S_ADMIN_RESTORE, S_ADMIN_ICH_STOCK,
    S_ADMIN_ICH_ADD_U, S_ADMIN_ICH_ADD_P, S_ADMIN_ICH_DEL_Q,
    S_ADMIN_REF_MENU,
    S_ADMIN_ICH_BULK,
) = range(29)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    await ensure_user(update)
    try:
        args = getattr(context, "args", []) or []
        if args and update.effective_user:
            a0 = str(args[0]).strip()
            if a0.startswith("ref_"):
                a0 = a0[4:]
            inv_id = safe_int(a0)
            if inv_id and inv_id != update.effective_user.id:
                await bind_referral(update.effective_user.id, inv_id)
    except Exception:
        pass
    if not await require_sub(update, context):
        return ConversationHandler.END
    await update.message.reply_text("أهلًا فيك 👋\nاختر من القائمة 👇", reply_markup=mk_main_menu())
    return S_MAIN

async def cb_checksub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()
    u = update.effective_user
    if not u:
        return ConversationHandler.END
    try:
        m = await context.bot.get_chat_member(REQUIRED_CHANNEL, u.id)
        st = getattr(m, "status", "")
        if st in ("member", "administrator", "creator"):
            await q.message.reply_text("✅ تم التحقق! أهلاً فيك 😄", reply_markup=mk_main_menu())
            return S_MAIN
    except Exception:
        if await is_admin(u.id):
            await q.message.reply_text("✅ تم السماح (صلاحيات أدمن).", reply_markup=mk_main_menu())
            return S_MAIN
    join_url = f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ اشترك بالقناة", url=join_url)],
        [InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="sys:checksub")],
    ])
    await q.message.reply_text("لسه مو مشترك 😅\nاشترك وبعدين جرّب تحقق مرة ثانية.", reply_markup=kb)
    return ConversationHandler.END

async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    await ensure_user(update)
    if not await require_sub(update, context):
        return ConversationHandler.END
    b, h = await get_wallet(update.effective_user.id)
    await update.message.reply_text(
        f"💰 <b>محفظتك</b>\n\n✅ الرصيد: <b>{b}</b>\n⏳ المحجوز: <b>{h}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=mk_main_menu(),
    )
    return S_MAIN

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    await ensure_user(update)
    if not await require_sub(update, context):
        return ConversationHandler.END
    await update.message.reply_text(f"🆘 للدعم: {SUPPORT_USERNAME}\nبنخدمك بكل حب 🤝", reply_markup=mk_main_menu())
    return S_MAIN

async def topup_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    await ensure_user(update)
    if not await require_sub(update, context):
        return ConversationHandler.END
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 شام كاش", callback_data="topup:sham")],
        [InlineKeyboardButton("📲 سيرياتيل كاش", callback_data="topup:sy")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="topup:back")],
    ])
    await update.message.reply_text("➕ <b>شحن رصيد البوت</b>\nاختر الطريقة 👇", parse_mode=ParseMode.HTML, reply_markup=kb)
    return S_TOPUP_METHOD

async def topup_method_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    q = update.callback_query
    await q.answer()
    d = q.data or ""
    if d.endswith(":back"):
        await q.message.reply_text("رجعناك للقائمة 👇", reply_markup=mk_main_menu())
        return S_MAIN
    if d.endswith(":sham"):
        await q.message.reply_text(f"💳 شحن شام كاش: تواصل مع الدعم {SUPPORT_USERNAME} 🤝", reply_markup=mk_main_menu())
        return S_MAIN
    rows = [[InlineKeyboardButton(f"🔢 {c}", callback_data=f"topupcode:{c}")] for c in SYRIATEL_CODES]
    rows.append([InlineKeyboardButton("↩️ رجوع", callback_data="topup:back")])
    await q.message.reply_text("📲 اختر كود سيرياتيل كاش 👇", reply_markup=InlineKeyboardMarkup(rows))
    return S_TOPUP_CODE

async def topup_code_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    q = update.callback_query
    await q.answer()
    m = re.match(r"^topupcode:(.+)$", q.data or "")
    if not m:
        await q.message.reply_text("⚠️ صار خطأ بسيط. جرّب من جديد.", reply_markup=mk_main_menu())
        return S_MAIN
    code = m.group(1).strip()
    context.user_data["topup"] = {"method": "syriatel_cash", "code": code}
    await q.message.reply_text("🧾 تمام ✅\nابعت رقم عملية التحويل:", reply_markup=ReplyKeyboardRemove())
    return S_TOPUP_TXN

async def topup_txn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    txn = norm(update.message.text)
    if len(txn) < 4:
        await update.message.reply_text("🧾 رقم العملية مو واضح. ابعته مرة ثانية 🙏")
        return S_TOPUP_TXN
    context.user_data.setdefault("topup", {})["txn"] = txn
    await update.message.reply_text(f"💰 ابعت المبلغ (≥ {MIN_TOPUP}):")
    return S_TOPUP_AMOUNT

async def topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    amt = safe_int(update.message.text)
    if amt is None:
        await update.message.reply_text("💰 اكتب المبلغ أرقام فقط 🙏")
        return S_TOPUP_AMOUNT
    if amt < MIN_TOPUP:
        await update.message.reply_text(f"⚠️ الحد الأدنى للشحن: <b>{MIN_TOPUP}</b>\nجرّب مبلغ أكبر ✅", parse_mode=ParseMode.HTML)
        return S_TOPUP_AMOUNT
    context.user_data.setdefault("topup", {})["amount"] = int(amt)
    t = context.user_data["topup"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="topup:confirm")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="topup:cancel")],
    ])
    await update.message.reply_text(
        "✅ <b>تأكيد طلب الشحن</b>\n\n"
        f"🔢 الكود: <code>{t.get('code')}</code>\n"
        f"🧾 العملية: <code>{t.get('txn')}</code>\n"
        f"💰 المبلغ: <b>{amt}</b>\n\n"
        "اضغط تأكيد لإرسال الطلب للأدمن 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    return S_TOPUP_CONFIRM

async def topup_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    q = update.callback_query
    await q.answer()
    if (q.data or "").endswith(":cancel"):
        context.user_data.pop("topup", None)
        await q.message.reply_text("تم الإلغاء ✅", reply_markup=mk_main_menu())
        return S_MAIN
    t = context.user_data.get("topup") or {}
    if not all(k in t for k in ("code", "txn", "amount")):
        await q.message.reply_text("⚠️ ناقص معلومات. ابدأ من جديد.", reply_markup=mk_main_menu())
        return S_MAIN
    o = {
        "id": gen_id("TOPUP"),
        "type": "topup",
        "status": "pending",
        "created_at": now_iso(),
        "user_id": q.from_user.id,
        "data": {"method": "syriatel_cash", "code": t["code"], "txn": t["txn"], "amount": int(t["amount"])},
        "history": [{"at": now_iso(), "by": q.from_user.id, "action": "created"}],
    }
    orders = await all_orders()
    orders.insert(0, o)
    await save_orders(orders)
    await q.message.reply_text("📨 تم إرسال طلبك ✅\nرح يوصلك الرد بأقرب وقت 🤝", reply_markup=mk_main_menu())
    await notify_admins(context.application, order_text(o), reply_markup=order_kb(o["id"]))
    context.user_data.pop("topup", None)
    return S_MAIN

async def withdraw_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    await ensure_user(update)
    if not await require_sub(update, context):
        return ConversationHandler.END
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 شام كاش", callback_data="wd:sham")],
        [InlineKeyboardButton("📲 سيرياتيل كاش", callback_data="wd:sy")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="wd:back")],
    ])
    await update.message.reply_text("➖ <b>سحب رصيد من البوت</b>\nاختر الطريقة 👇", parse_mode=ParseMode.HTML, reply_markup=kb)
    return S_WD_METHOD

async def wd_method_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    q = update.callback_query
    await q.answer()
    d = q.data or ""
    if d.endswith(":back"):
        await q.message.reply_text("رجعناك للقائمة 👇", reply_markup=mk_main_menu())
        return S_MAIN
    if d.endswith(":sham"):
        await q.message.reply_text(f"💳 سحب شام كاش: تواصل مع الدعم {SUPPORT_USERNAME} 🤝", reply_markup=mk_main_menu())
        return S_MAIN
    context.user_data["wd"] = {"method": "syriatel_cash"}
    await q.message.reply_text("📞 ابعت رقم المستلم:", reply_markup=ReplyKeyboardRemove())
    return S_WD_RECEIVER

async def wd_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    r = norm(update.message.text)
    if len(r) < 6:
        await update.message.reply_text("📞 الرقم مو واضح. ابعته مرة ثانية 🙏")
        return S_WD_RECEIVER
    context.user_data.setdefault("wd", {})["receiver"] = r
    await update.message.reply_text(f"💰 ابعت المبلغ (≥ {MIN_WITHDRAW}):")
    return S_WD_AMOUNT

async def wd_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    amt = safe_int(update.message.text)
    if amt is None:
        await update.message.reply_text("💰 اكتب المبلغ أرقام فقط 🙏")
        return S_WD_AMOUNT
    if amt < MIN_WITHDRAW:
        await update.message.reply_text(f"⚠️ الحد الأدنى للسحب: <b>{MIN_WITHDRAW}</b>", parse_mode=ParseMode.HTML)
        return S_WD_AMOUNT
    b, _ = await get_wallet(update.effective_user.id)
    if amt > b:
        await update.message.reply_text(f"❌ رصيدك الحالي <b>{b}</b> وما بكفي.\nجرّب مبلغ أقل ✅", parse_mode=ParseMode.HTML)
        return S_WD_AMOUNT
    context.user_data.setdefault("wd", {})["amount"] = int(amt)
    wd = context.user_data["wd"]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تأكيد", callback_data="wd:confirm")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="wd:cancel")],
    ])
    await update.message.reply_text(
        "✅ <b>تأكيد السحب</b>\n\n"
        f"📞 المستلم: <code>{wd.get('receiver')}</code>\n"
        f"💰 المبلغ: <b>{amt}</b>\n\n"
        "عند التأكيد سيتم حجز المبلغ فورًا ⏳",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    return S_WD_CONFIRM

async def wd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    q = update.callback_query
    await q.answer()
    if (q.data or "").endswith(":cancel"):
        context.user_data.pop("wd", None)
        await q.message.reply_text("تم الإلغاء ✅", reply_markup=mk_main_menu())
        return S_MAIN
    wd = context.user_data.get("wd") or {}
    if not all(k in wd for k in ("receiver", "amount")):
        await q.message.reply_text("⚠️ ناقص معلومات. ابدأ من جديد.", reply_markup=mk_main_menu())
        return S_MAIN
    amt = int(wd["amount"])
    try:
        await add_wallet(q.from_user.id, db=-amt, dh=+amt)
    except Exception:
        b, _ = await get_wallet(q.from_user.id)
        await q.message.reply_text(f"❌ ما قدرنا نحجز المبلغ. رصيدك: <b>{b}</b>", parse_mode=ParseMode.HTML, reply_markup=mk_main_menu())
        return S_MAIN
    o = {
        "id": gen_id("WD"),
        "type": "withdraw",
        "status": "pending",
        "created_at": now_iso(),
        "user_id": q.from_user.id,
        "data": {"method": "syriatel_cash", "receiver": wd["receiver"], "amount": amt},
        "history": [{"at": now_iso(), "by": q.from_user.id, "action": "created_reserved"}],
    }
    orders = await all_orders()
    orders.insert(0, o)
    await save_orders(orders)
    await q.message.reply_text("📨 تم إرسال طلب السحب ✅\nالمبلغ صار محجوز لحد الرد ⏳", reply_markup=mk_main_menu())
    await notify_admins(context.application, order_text(o), reply_markup=order_kb(o["id"]))
    context.user_data.pop("wd", None)
    return S_MAIN

async def cancel_last_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    await ensure_user(update)
    if not await require_sub(update, context):
        return ConversationHandler.END
    uid = update.effective_user.id
    orders = await all_orders()
    pending = [o for o in orders if o.get("type") == "withdraw" and o.get("user_id") == uid and o.get("status") == "pending"]
    if not pending:
        await update.message.reply_text("✅ ما عندك طلب سحب معلّق.", reply_markup=mk_main_menu())
        return S_MAIN
    o = pending[0]
    amt = int((o.get("data") or {}).get("amount", 0))
    try:
        await add_wallet(uid, db=+amt, dh=-amt)
    except Exception:
        await update.message.reply_text("⚠️ صار خطأ بفك الحجز. تم إبلاغ الأدمن.", reply_markup=mk_main_menu())
        await notify_admins(context.application, f"⚠️ مشكلة بفك الحجز لطلب #{o.get('id')} للمستخدم {uid}.")
        return S_MAIN
    o["status"] = "canceled"
    o.setdefault("history", []).append({"at": now_iso(), "by": uid, "action": "user_canceled"})
    await save_orders(orders)
    await update.message.reply_text("✅ تم إلغاء آخر طلب سحب وفك الحجز 🔓", reply_markup=mk_main_menu())
    return S_MAIN

async def ich_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["last_menu"] = "ich"
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    await ensure_user(update)
    if not await require_sub(update, context):
        return ConversationHandler.END
    await update.message.reply_text("💼 <b>حساب ايشانسي</b>\nاختر خيار 👇", parse_mode=ParseMode.HTML, reply_markup=mk_ich_menu())
    return S_ICH_MENU

async def ich_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    t = norm(update.message.text)
    if t.startswith("↩"):
        await update.message.reply_text("رجعناك للقائمة الرئيسية 👇", reply_markup=mk_main_menu())
        return S_MAIN
    if t.startswith("1"):
        ich = await STORE.read(F_ICHANCY, DEFAULT_ICHANCY)
        assigned = (ich.get("assigned") or {})
        if str(update.effective_user.id) in assigned:
            await update.message.reply_text("✅ عندك حساب مرتبط مسبقًا.\nإذا بدك تحذف الربط اختر 4️⃣.", reply_markup=mk_ich_menu())
            return S_ICH_MENU
        await update.message.reply_text("✍️ ابعت username تقريبي لنقترح أقرب حساب من المخزون 👇", reply_markup=ReplyKeyboardRemove())
        return S_ICH_CLAIM_QUERY
    if t.startswith("2"):
        ich = await STORE.read(F_ICHANCY, DEFAULT_ICHANCY)
        if str(update.effective_user.id) not in (ich.get("assigned") or {}):
            await update.message.reply_text("⚠️ لازم تستلم حساب أولاً (1️⃣).", reply_markup=mk_ich_menu())
            return S_ICH_MENU
        await update.message.reply_text("💰 ابعت مبلغ البوت (ل.س).\nكل 1 ليرة = 100 ايشانسي ✅", reply_markup=ReplyKeyboardRemove())
        return S_ICH_TOPUP
    if t.startswith("3"):
        ich = await STORE.read(F_ICHANCY, DEFAULT_ICHANCY)
        if str(update.effective_user.id) not in (ich.get("assigned") or {}):
            await update.message.reply_text("⚠️ لازم تستلم حساب أولاً (1️⃣).", reply_markup=mk_ich_menu())
            return S_ICH_MENU
        await update.message.reply_text("💸 ابعت مبلغ ايشانسي.\nكل 100 ايشانسي = 1 ليرة بوت ✅", reply_markup=ReplyKeyboardRemove())
        return S_ICH_WD
    if t.startswith("4"):
        ich = await STORE.read(F_ICHANCY, DEFAULT_ICHANCY)
        if str(update.effective_user.id) not in (ich.get("assigned") or {}):
            await update.message.reply_text("✅ ما عندك حساب مرتبط.", reply_markup=mk_ich_menu())
            return S_ICH_MENU
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأكيد حذف الربط", callback_data="ich:unlink:yes")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="ich:unlink:no")],
        ])
        await update.message.reply_text("⚠️ حذف الربط فقط (بدون حذف الحساب)؟", reply_markup=kb)
        return S_ICH_DEL
    await update.message.reply_text("اختار من القائمة 👇", reply_markup=mk_ich_menu())
    return S_ICH_MENU

async def ich_claim_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = norm(update.message.text)
    if len(q) < 3:
        await update.message.reply_text("اكتب اسم أطول شوي (3 أحرف أو أكثر).")
        return S_ICH_CLAIM_QUERY
    ich = await STORE.read(F_ICHANCY, DEFAULT_ICHANCY)
    stock = ich.get("stock", []) or []
    available = [a for a in stock if (a.get("status") or "available") == "available"]
    if not available:
        await update.message.reply_text("😕 المخزون فارغ حالياً.\nتواصل مع الأدمن.", reply_markup=mk_main_menu())
        return S_MAIN
    names = [a.get("username", "") for a in available]
    match = difflib.get_close_matches(q, names, n=1, cutoff=0.2)
    acc = None
    if match:
        acc = next((a for a in available if a.get("username") == match[0]), None)
    if not acc:
        acc = available[0]
    context.user_data["ich_suggest"] = {"id": acc.get("id"), "username": acc.get("username")}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ تأكيد ({acc.get('username')})", callback_data="ich:claim:yes")],
        [InlineKeyboardButton("🔄 اقتراح آخر", callback_data="ich:claim:another")],
        [InlineKeyboardButton("↩️ رجوع", callback_data="ich:claim:back")],
    ])
    await update.message.reply_text(f"✨ الاقتراح الأقرب:\n👤 <b>{acc.get('username')}</b>\n\nتأكيد؟", parse_mode=ParseMode.HTML, reply_markup=kb)
    return S_ICH_CLAIM_CONFIRM

async def ich_claim_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    act = (q.data or "").split(":")[-1]
    if act == "back":
        await q.message.reply_text("💼 حساب ايشانسي", reply_markup=mk_ich_menu())
        return S_ICH_MENU
    ich = await STORE.read(F_ICHANCY, DEFAULT_ICHANCY)
    stock = ich.get("stock", []) or []
    available = [a for a in stock if (a.get("status") or "available") == "available"]
    if not available:
        await q.message.reply_text("😕 المخزون صار فارغ.", reply_markup=mk_main_menu())
        return S_MAIN
    if act == "another":
        cur = (context.user_data.get("ich_suggest") or {}).get("id")
        alt = next((a for a in available if a.get("id") != cur), None) or available[0]
        context.user_data["ich_suggest"] = {"id": alt.get("id"), "username": alt.get("username")}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ تأكيد ({alt.get('username')})", callback_data="ich:claim:yes")],
            [InlineKeyboardButton("🔄 اقتراح آخر", callback_data="ich:claim:another")],
            [InlineKeyboardButton("↩️ رجوع", callback_data="ich:claim:back")],
        ])
        await q.message.reply_text(f"🔄 اقتراح آخر:\n👤 <b>{alt.get('username')}</b>", parse_mode=ParseMode.HTML, reply_markup=kb)
        return S_ICH_CLAIM_CONFIRM
    uid = str(q.from_user.id)
    if uid in (ich.get("assigned") or {}):
        await q.message.reply_text("✅ عندك حساب مرتبط مسبقًا.", reply_markup=mk_ich_menu())
        return S_ICH_MENU
    sug = context.user_data.get("ich_suggest") or {}
    acc_id = sug.get("id")
    acc = next((a for a in stock if a.get("id") == acc_id and (a.get("status") or "available") == "available"), None)
    if not acc:
        await q.message.reply_text("⚠️ الحساب لم يعد متاح. جرّب مرة ثانية.", reply_markup=mk_ich_menu())
        return S_ICH_MENU
    acc["status"] = "assigned"
    ich.setdefault("assigned", {})[uid] = acc["id"]
    await STORE.write(F_ICHANCY, ich)
    creds = f"username: {acc.get('username')}\npassword: {acc.get('password')}"
    await q.message.reply_text("✅ تم تسليم حسابك 🎯\n\nانسخ البيانات من المربع 👇\n\n" f"<pre>{creds}</pre>", parse_mode=ParseMode.HTML, reply_markup=mk_ich_menu())
    context.user_data.pop("ich_suggest", None)
    return S_ICH_MENU

async def ich_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amt = safe_int(update.message.text)
    if amt is None or amt <= 0:
        await update.message.reply_text("اكتب مبلغ صحيح بالأرقام 🙏")
        return S_ICH_TOPUP
    b, _ = await get_wallet(update.effective_user.id)
    if amt > b:
        await update.message.reply_text(f"❌ رصيدك <b>{b}</b> وما بكفي.", parse_mode=ParseMode.HTML)
        return S_ICH_TOPUP
    pts = amt * 100
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافق", callback_data=f"ich:topup:yes:{amt}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="ich:topup:no")],
    ])
    await update.message.reply_text(f"✅ تأكيد شحن ايشانسي\n\n💰 خصم: <b>{amt}</b>\n🎯 شحن: <b>{pts}</b>\n\nتأكيد؟", parse_mode=ParseMode.HTML, reply_markup=kb)
    return S_ICH_TOPUP

async def ich_topup_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if (q.data or "").endswith(":no"):
        await q.message.reply_text("تم الإلغاء ✅", reply_markup=mk_ich_menu())
        return S_ICH_MENU
    m = re.match(r"^ich:topup:yes:(\d+)$", q.data or "")
    if not m:
        await q.message.reply_text("⚠️ خطأ بسيط.", reply_markup=mk_ich_menu())
        return S_ICH_MENU
    amt = int(m.group(1))
    try:
        await add_wallet(q.from_user.id, db=-amt, dh=0)
    except Exception:
        b, _ = await get_wallet(q.from_user.id)
        await q.message.reply_text(f"❌ ما قدرنا نخصم. رصيدك: <b>{b}</b>", parse_mode=ParseMode.HTML, reply_markup=mk_ich_menu())
        return S_ICH_MENU
    await q.message.reply_text(f"✅ تم شحن ايشانسي 🎯\nخصمنا <b>{amt}</b> من محفظتك.", parse_mode=ParseMode.HTML, reply_markup=mk_ich_menu())
    return S_ICH_MENU

async def ich_wd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ich_amt = safe_int(update.message.text)
    if ich_amt is None or ich_amt <= 0:
        await update.message.reply_text("اكتب مبلغ ايشانسي صحيح بالأرقام 🙏")
        return S_ICH_WD
    bot_amt = ich_amt // 100
    if bot_amt <= 0:
        await update.message.reply_text("⚠️ الحد الأدنى 100 ايشانسي حتى يساوي 1 ليرة.")
        return S_ICH_WD
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ موافق", callback_data=f"ich:wd:yes:{ich_amt}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="ich:wd:no")],
    ])
    await update.message.reply_text(f"✅ تأكيد سحب ايشانسي\n\n🎯 {ich_amt} ايشانسي\n💰 إضافة: <b>{bot_amt}</b>\n\nتأكيد؟", parse_mode=ParseMode.HTML, reply_markup=kb)
    return S_ICH_WD

async def ich_wd_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if (q.data or "").endswith(":no"):
        await q.message.reply_text("تم الإلغاء ✅", reply_markup=mk_ich_menu())
        return S_ICH_MENU
    m = re.match(r"^ich:wd:yes:(\d+)$", q.data or "")
    if not m:
        await q.message.reply_text("⚠️ خطأ بسيط.", reply_markup=mk_ich_menu())
        return S_ICH_MENU
    ich_amt = int(m.group(1))
    bot_amt = ich_amt // 100
    await add_wallet(q.from_user.id, db=+bot_amt, dh=0)
    await q.message.reply_text(f"✅ تمت الإضافة لمحفظتك 💰\nأضفنا <b>{bot_amt}</b>.", parse_mode=ParseMode.HTML, reply_markup=mk_ich_menu())
    return S_ICH_MENU

async def ich_unlink_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    if (q.data or "").endswith(":no"):
        await q.message.reply_text("تم الإلغاء ✅", reply_markup=mk_ich_menu())
        return S_ICH_MENU
    ich = await STORE.read(F_ICHANCY, DEFAULT_ICHANCY)
    assigned = ich.get("assigned") or {}
    assigned.pop(str(q.from_user.id), None)
    ich["assigned"] = assigned
    await STORE.write(F_ICHANCY, ich)
    await q.message.reply_text("✅ تم حذف الربط من البوت.", reply_markup=mk_ich_menu())
    return S_ICH_MENU

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    await ensure_user(update)
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ هذا الأمر للأدمن فقط.")
        return ConversationHandler.END
    await update.message.reply_text("👑 لوحة الأدمن", reply_markup=mk_admin_menu(is_super(update.effective_user.id)))
    return S_ADMIN_MENU

async def send_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in (F_USERS, F_WALLETS, F_ORDERS, F_ICHANCY, F_ADMINS, F_SETTINGS):
            p = STORE.path(fn)
            if os.path.exists(p):
                z.write(p, arcname=fn)
    buf.seek(0)
    await context.bot.send_document(update.effective_chat.id, InputFile(buf, filename="backup.zip"), caption="💾 Backup جاهز ✅")

async def admin_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_super(update.effective_user.id):
        return ConversationHandler.END
    if not update.message.document or not (update.message.document.file_name or "").lower().endswith(".zip"):
        await update.message.reply_text("⚠️ ابعت ملف ZIP فقط.")
        return S_ADMIN_RESTORE
    f = await update.message.document.get_file()
    tmpdir = tempfile.mkdtemp(prefix="restore_")
    try:
        zpath = os.path.join(tmpdir, "in.zip")
        await f.download_to_drive(custom_path=zpath)
        exdir = os.path.join(tmpdir, "x")
        os.makedirs(exdir, exist_ok=True)
        with zipfile.ZipFile(zpath, "r") as z:
            z.extractall(exdir)
        allowed = {F_USERS, F_WALLETS, F_ORDERS, F_ICHANCY, F_ADMINS, F_SETTINGS}
        for fn in allowed:
            p = os.path.join(exdir, fn)
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as rf:
                        data = json.load(rf)
                    await STORE.write(fn, data)
                except Exception:
                    pass
        await set_maintenance(True)
        await update.message.reply_text("✅ تم الاسترجاع.\n🛠 تم تفعيل الصيانة تلقائياً (ON).", reply_markup=mk_admin_menu(True))
        return S_ADMIN_MENU
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def mk_stock_menu() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("➕ إضافة حساب"), KeyboardButton("➕ إضافة بالجملة")],
        [KeyboardButton("🗑 حذف حساب"), KeyboardButton("📊 إحصائيات")],
        [KeyboardButton("↩️ رجوع"), KeyboardButton("🏠 القائمة الرئيسية")],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def stock_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["last_menu"] = "stock"
    ich = await STORE.read(F_ICHANCY, DEFAULT_ICHANCY)
    stock = ich.get("stock", []) or []
    av = sum(1 for a in stock if (a.get("status") or "available") == "available")
    asg = len(stock) - av
    await update.message.reply_text(
        "📦 مخزون ايشانسي\n\n"
        f"✅ متاح: <b>{av}</b>\n"
        f"🔒 محجوز: <b>{asg}</b>\n",
        parse_mode=ParseMode.HTML,
        reply_markup=mk_stock_menu()
    )

async def stock_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_super(update.effective_user.id):
        return ConversationHandler.END
    txt = norm(update.message.text)
    if txt.startswith("🏠"):
        return await go_home(update, context)
    if txt.startswith("↩"):
        await update.message.reply_text("👑 لوحة الأدمن", reply_markup=mk_admin_menu(True))
        return S_ADMIN_MENU
    if txt.startswith("📊"):
        await stock_stats(update, context)
        return S_ADMIN_ICH_STOCK
    if txt.startswith("➕ إضافة بالجملة"):
        await update.message.reply_text(
            "➕ ابعت الحسابات بالجملة (كل سطر حساب):\n<code>username,password</code> أو <code>username:password</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return S_ADMIN_ICH_BULK
    if txt.startswith("➕"):
        await update.message.reply_text("👤 ابعت username:", reply_markup=ReplyKeyboardRemove())
        return S_ADMIN_ICH_ADD_U
    if txt.startswith("🗑"):
        await update.message.reply_text("🗑 ابعت username للحذف:", reply_markup=ReplyKeyboardRemove())
        return S_ADMIN_ICH_DEL_Q

    await update.message.reply_text("اختار من القائمة 👇", reply_markup=mk_stock_menu())
    return S_ADMIN_ICH_STOCK

async def stock_add_u(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    u = norm(update.message.text)
    if len(u) < 3:
        await update.message.reply_text("username غير صحيح.")
        return S_ADMIN_ICH_ADD_U
    context.user_data["stock_u"] = u
    await update.message.reply_text("🔑 ابعت password:")
    return S_ADMIN_ICH_ADD_P

async def stock_add_p(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    p = norm(update.message.text)
    if len(p) < 3:
        await update.message.reply_text("password غير صحيح.")
        return S_ADMIN_ICH_ADD_P
    u = context.user_data.get("stock_u", "")
    ich = await STORE.read(F_ICHANCY, DEFAULT_ICHANCY)
    stock = ich.get("stock", []) or []
    if any((a.get("username") or "").lower() == u.lower() for a in stock):
        await update.message.reply_text("⚠️ هذا الحساب موجود مسبقاً.")
        return S_ADMIN_ICH_STOCK
    stock.append({"id": gen_id("ACC"), "username": u, "password": p, "status": "available"})
    ich["stock"] = stock
    await STORE.write(F_ICHANCY, ich)
    context.user_data.pop("stock_u", None)
    await update.message.reply_text("✅ تم إضافة الحساب.", reply_markup=ReplyKeyboardRemove())
    await stock_stats(update, context)
    return S_ADMIN_ICH_STOCK


async def stock_bulk_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Super admin: add multiple Ichancy accounts in one message.

    Expected formats per line:
      username,password
      username:password
    """
    if not is_super(update.effective_user.id):
        return ConversationHandler.END

    raw = (update.message.text or "").strip()
    if not raw:
        await update.message.reply_text("⚠️ ابعت الحسابات برسالة واحدة (كل سطر حساب) 🙏")
        return S_ADMIN_ICH_BULK

    ich = await STORE.read(F_ICHANCY, DEFAULT_ICHANCY)
    stock = ich.get("stock", []) or []

    existing = { (a.get("username") or "").strip().lower() for a in stock if a.get("username") }

    added = 0
    skipped = 0
    bad = 0

    lines_in = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    for ln in lines_in:
        if "," in ln:
            u, p = [x.strip() for x in ln.split(",", 1)]
        elif ":" in ln:
            u, p = [x.strip() for x in ln.split(":", 1)]
        else:
            # allow 'user pass' as a convenience
            parts = ln.split()
            if len(parts) >= 2:
                u, p = parts[0].strip(), " ".join(parts[1:]).strip()
            else:
                bad += 1
                continue

        if len(u) < 3 or len(p) < 3:
            bad += 1
            continue

        key = u.lower()
        if key in existing:
            skipped += 1
            continue

        stock.append({
            "id": gen_id("ACC"),
            "username": u,
            "password": p,
            "status": "available",
            "assigned_to": None,
            "assigned_at": None,
        })
        existing.add(key)
        added += 1

    ich["stock"] = stock
    await STORE.write(F_ICHANCY, ich)

    await update.message.reply_text(
        "✅ تمّت الإضافة بالجملة\n\n"
        f"➕ تمت إضافة: {added}\n"
        f"↩️ مكررة/موجودة: {skipped}\n"
        f"⚠️ غير صالحة: {bad}",
        reply_markup=mk_stock_menu(),
    )
    return S_ADMIN_ICH_STOCK

async def stock_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = norm(update.message.text)
    ich = await STORE.read(F_ICHANCY, DEFAULT_ICHANCY)
    stock = ich.get("stock", []) or []
    exact = next((a for a in stock if (a.get("username") or "").lower() == q.lower()), None)
    if not exact:
        names = [a.get("username","") for a in stock]
        m = difflib.get_close_matches(q, names, n=1, cutoff=0.2)
        if m:
            exact = next((a for a in stock if a.get("username") == m[0]), None)
    if not exact:
        await update.message.reply_text("ما لقينا الحساب.")
        return S_ADMIN_ICH_STOCK
    if (exact.get("status") or "available") != "available":
        await update.message.reply_text("⚠️ الحساب محجوز/مسند ولا يمكن حذفه.")
        return S_ADMIN_ICH_STOCK
    stock = [a for a in stock if a.get("id") != exact.get("id")]
    ich["stock"] = stock
    await STORE.write(F_ICHANCY, ich)
    await update.message.reply_text("✅ تم حذف الحساب.")
    await stock_stats(update, context)
    return S_ADMIN_ICH_STOCK

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["last_menu"] = "admin"
    uid = update.effective_user.id
    if not await is_admin(uid):
        return ConversationHandler.END
    text = norm(update.message.text)
    if text.startswith("🏠"):
        return await go_home(update, context)
    if text.startswith("🤝") and is_super(uid):
        return await admin_referrals_entry(update, context)
    action = startswith_map(text, {"📌":"pending","🔎":"search","💰":"setbal","📦":"stock","👥":"assist","📢":"broadcast","💾":"backup","♻":"restore","🛠":"maint","↩":"back"}) or ""
    if action == "back":
        await update.message.reply_text("رجعناك للقائمة الرئيسية 👇", reply_markup=mk_main_menu())
        return S_MAIN
    if action == "pending":
        orders = await all_orders()
        pend = [o for o in orders if o.get("status") == "pending"]
        if not pend:
            await update.message.reply_text("✅ ما في طلبات معلّقة.", reply_markup=mk_admin_menu(is_super(uid)))
            return S_ADMIN_MENU
        for o in pend[:10]:
            await update.message.reply_text(order_text(o), parse_mode=ParseMode.HTML, reply_markup=order_kb(o["id"]))
        return S_ADMIN_MENU
    if action == "search":
        await update.message.reply_text("🔎 ابعت ID المستخدم:", reply_markup=ReplyKeyboardRemove())
        return S_ADMIN_SEARCH
    if action == "setbal":
        if not is_super(uid):
            await update.message.reply_text("⛔ هذا الخيار للسوبر أدمن فقط.", reply_markup=mk_admin_menu(False))
            return S_ADMIN_MENU
        await update.message.reply_text("💰 ابعت ID المستخدم:", reply_markup=ReplyKeyboardRemove())
        return S_ADMIN_SETBAL_UID
    if action == "assist":
        if not is_super(uid):
            await update.message.reply_text("⛔ هذا الخيار للسوبر أدمن فقط.", reply_markup=mk_admin_menu(False))
            return S_ADMIN_MENU
        current = (await STORE.read(F_ADMINS, DEFAULT_ADMINS)).get("assistants", []) or []
        msg = "👥 إدارة الأدمن المساعد\n\n" f"الحاليين: <code>{', '.join(map(str,current)) if current else 'لا يوجد'}</code>\n\n" "اكتب:\nadd <id>\ndel <id>"
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove())
        return S_ADMIN_ASSIST
    if action == "broadcast":
        if not is_super(uid):
            await update.message.reply_text("⛔ الرسالة الجماعية للسوبر أدمن فقط.", reply_markup=mk_admin_menu(False))
            return S_ADMIN_MENU
        await update.message.reply_text("📢 ابعت الرسالة (نص/صورة/فيديو):", reply_markup=ReplyKeyboardRemove())
        return S_ADMIN_BROADCAST
    if action == "backup":
        if not is_super(uid):
            await update.message.reply_text("⛔ Backup للسوبر أدمن فقط.", reply_markup=mk_admin_menu(False))
            return S_ADMIN_MENU
        await send_backup(update, context)
        return S_ADMIN_MENU
    if action == "restore":
        if not is_super(uid):
            await update.message.reply_text("⛔ Restore للسوبر أدمن فقط.", reply_markup=mk_admin_menu(False))
            return S_ADMIN_MENU
        await update.message.reply_text("♻️ ابعت ملف ZIP للـ Restore.\n⚠️ سيتم تفعيل الصيانة تلقائياً.", reply_markup=ReplyKeyboardRemove())
        return S_ADMIN_RESTORE
    if action == "maint":
        if not is_super(uid):
            await update.message.reply_text("⛔ الصيانة للسوبر أدمن فقط.", reply_markup=mk_admin_menu(False))
            return S_ADMIN_MENU
        new = not await maintenance_enabled()
        await set_maintenance(new)
        await update.message.reply_text(f"🛠 الصيانة: {'✅ ON' if new else '❎ OFF'}", reply_markup=mk_admin_menu(True))
        return S_ADMIN_MENU
    if action == "stock":
        if not is_super(uid):
            await update.message.reply_text("⛔ المخزون للسوبر أدمن فقط.", reply_markup=mk_admin_menu(False))
            return S_ADMIN_MENU
        await stock_stats(update, context)
        return S_ADMIN_ICH_STOCK
    if action == "":
        await update.message.reply_text("اختر من لوحة الأدمن 👇", reply_markup=mk_admin_menu(is_super(uid)))
    return S_ADMIN_MENU


async def admin_referrals_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    if not is_super(update.effective_user.id):
        await update.message.reply_text("⛔ غير مسموح.", reply_markup=mk_admin_menu(False))
        return S_ADMIN_MENU
    msg = ["🤝 <b>إدارة الإحالات</b>", "", "استخدم الأوامر التالية:", "<code>show USER_ID</code>", "<code>pay USER_ID AMOUNT</code>", "", "↩️ للرجوع: back"]
    await update.message.reply_text("\n".join(msg), parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardMarkup([[KeyboardButton("↩️ رجوع"), KeyboardButton("🏠 القائمة الرئيسية")]], resize_keyboard=True))
    return S_ADMIN_REF_MENU

async def admin_referrals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_super(update.effective_user.id):
        await update.message.reply_text("⛔ غير مسموح.", reply_markup=mk_admin_menu(False))
        return S_ADMIN_MENU
    txt = norm(update.message.text)
    if txt.startswith("🏠"):
        return await go_home(update, context)
    if txt.startswith("↩") or txt.lower().startswith("back"):
        await update.message.reply_text("👑 لوحة الأدمن", reply_markup=mk_admin_menu(True))
        return S_ADMIN_MENU
    parts = txt.split()
    if not parts:
        return S_ADMIN_REF_MENU
    cmd = parts[0].lower()
    if cmd == "show" and len(parts) >= 2:
        uid = safe_int(parts[1])
        if not uid:
            await update.message.reply_text("اكتب USER_ID صحيح.")
            return S_ADMIN_REF_MENU
        refs = await ref_get_data()
        inv = (refs.get("inviters", {}) or {}).get(str(uid)) or {"refs": {}, "pending": 0, "paid": 0}
        total = len(inv.get("refs", {}) or {})
        active = count_active_refs(inv, refs.get("period_start",""))
        pending = int(inv.get("pending", 0))
        paid = int(inv.get("paid", 0))
        remain = ref_period_remaining_days(refs.get("period_start",""))
        await update.message.reply_text(
            "👤 <b>تقرير وكيل</b>\n"
            f"ID: <code>{uid}</code>\n\n"
            f"👥 إجمالي الإحالات: <b>{total}</b>\n"
            f"✅ الإحالات النشطة: <b>{active}</b>\n"
            f"💰 الأرباح المعلّقة: <b>{pending}</b>\n"
            f"✅ تم صرفه سابقًا: <b>{paid}</b>\n\n"
            f"🗓 الأيام المتبقية للدورة: <b>{remain}</b>",
            parse_mode=ParseMode.HTML
        )
        return S_ADMIN_REF_MENU
    if cmd == "pay" and len(parts) >= 3:
        uid = safe_int(parts[1])
        amt = safe_int(parts[2])
        if not uid or not amt or amt <= 0:
            await update.message.reply_text("الصيغة الصحيحة: pay USER_ID AMOUNT")
            return S_ADMIN_REF_MENU
        refs = await ref_get_data()
        invs = refs.get("inviters", {}) or {}
        inv = invs.get(str(uid))
        if not inv:
            await update.message.reply_text("هذا الوكيل غير موجود.")
            return S_ADMIN_REF_MENU
        pending = int(inv.get("pending", 0))
        if amt > pending:
            await update.message.reply_text(f"المبلغ أكبر من المعلّق ({pending}).")
            return S_ADMIN_REF_MENU
        inv["pending"] = pending - amt
        inv["paid"] = int(inv.get("paid", 0)) + amt
        invs[str(uid)] = inv
        refs["inviters"] = invs
        await STORE.write(F_REFS, refs)
        await update.message.reply_text("✅ تم تسجيل التوزيع يدويًا.")
        return S_ADMIN_REF_MENU
    await update.message.reply_text("استخدم: show / pay / back")
    return S_ADMIN_REF_MENU

async def admin_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if not await is_admin(uid):
        return ConversationHandler.END
    target = safe_int(update.message.text)
    if not target:
        await update.message.reply_text("اكتب ID صحيح 🙏")
        return S_ADMIN_SEARCH
    users = await STORE.read(F_USERS, DEFAULT_USERS)
    wallets = await STORE.read(F_WALLETS, DEFAULT_WALLETS)
    u = users["users"].get(str(target), {})
    w = wallets["wallets"].get(str(target), {"balance": 0, "hold": 0})
    msg = "🔎 نتيجة البحث\n\n" f"👤 ID: <code>{target}</code>\n" f"👤 Username: <code>{u.get('username','')}</code>\n" f"🧑 الاسم: <b>{u.get('first_name','')}</b>\n" f"💰 Balance: <b>{w.get('balance',0)}</b>\n" f"⏳ Hold: <b>{w.get('hold',0)}</b>"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=mk_admin_menu(is_super(uid)))
    return S_ADMIN_MENU

async def admin_setbal_uid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_super(update.effective_user.id):
        return ConversationHandler.END
    target = safe_int(update.message.text)
    if not target:
        await update.message.reply_text("اكتب ID صحيح 🙏")
        return S_ADMIN_SETBAL_UID
    context.user_data["setbal_uid"] = int(target)
    await update.message.reply_text("💰 ابعت الرصيد الجديد (Balance فقط):")
    return S_ADMIN_SETBAL_AMT

async def admin_setbal_amt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_super(update.effective_user.id):
        return ConversationHandler.END
    amt = safe_int(update.message.text)
    if amt is None or amt < 0:
        await update.message.reply_text("اكتب رقم صحيح (>=0) 🙏")
        return S_ADMIN_SETBAL_AMT
    target = int(context.user_data.get("setbal_uid", 0))
    _, hold = await get_wallet(target)
    await set_wallet(target, amt, hold)
    await update.message.reply_text(f"✅ تم تعديل رصيد <code>{target}</code> إلى <b>{amt}</b>.", parse_mode=ParseMode.HTML, reply_markup=mk_admin_menu(True))
    context.user_data.pop("setbal_uid", None)
    return S_ADMIN_MENU

async def admin_assist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_super(update.effective_user.id):
        return ConversationHandler.END
    txt = norm(update.message.text)
    parts = txt.split()
    if len(parts) != 2 or parts[0] not in ("add", "del"):
        await update.message.reply_text("اكتب:\nadd 123456\ndel 123456")
        return S_ADMIN_ASSIST
    tid = safe_int(parts[1])
    if not tid:
        await update.message.reply_text("ID غير صحيح.")
        return S_ADMIN_ASSIST
    data = await STORE.read(F_ADMINS, DEFAULT_ADMINS)
    assistants = [int(x) for x in (data.get("assistants") or []) if str(x).isdigit()]
    if parts[0] == "add":
        if tid != SUPER_ADMIN_ID and tid not in assistants:
            assistants.append(int(tid))
    else:
        assistants = [x for x in assistants if x != int(tid)]
    data["assistants"] = sorted(list(set(assistants)))
    await STORE.write(F_ADMINS, data)
    await update.message.reply_text("✅ تم التحديث.", reply_markup=mk_admin_menu(True))
    return S_ADMIN_MENU

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_super(update.effective_user.id):
        return ConversationHandler.END
    users = await STORE.read(F_USERS, DEFAULT_USERS)
    uids = [int(k) for k in (users.get("users") or {}).keys() if str(k).isdigit()]
    if not uids:
        await update.message.reply_text("ما في مستخدمين.", reply_markup=mk_admin_menu(True))
        return S_ADMIN_MENU
    msg = update.message
    sent, failed = 0, 0
    for uid in uids:
        try:
            if msg.text and not msg.photo and not msg.video:
                await context.bot.send_message(uid, msg.text)
            elif msg.photo:
                await context.bot.send_photo(uid, msg.photo[-1].file_id, caption=msg.caption or "")
            elif msg.video:
                await context.bot.send_video(uid, msg.video.file_id, caption=msg.caption or "")
            else:
                await context.bot.send_message(uid, msg.caption or msg.text or "")
            sent += 1
        except (Forbidden, BadRequest):
            failed += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ تم الإرسال.\n📨 نجاح: {sent}\n⚠️ فشل: {failed}", reply_markup=mk_admin_menu(True))
    return S_ADMIN_MENU

async def apply_approve(o: Dict[str, Any]) -> None:
    t = o.get("type")
    uid = int(o.get("user_id"))
    data = o.get("data", {}) or {}
    if t == "topup":
        amt = int(data.get("amount", 0))
        await add_wallet(uid, db=+amt, dh=0)
        await add_ref_commission_if_eligible(uid, amt)
    elif t == "withdraw":
        amt = int(data.get("amount", 0))
        await add_wallet(uid, db=0, dh=-amt)
        await add_ref_commission_if_eligible(uid, amt)

async def apply_reject(o: Dict[str, Any]) -> None:
    if o.get("type") == "withdraw":
        uid = int(o.get("user_id"))
        amt = int((o.get("data") or {}).get("amount", 0))
        await add_wallet(uid, db=+amt, dh=-amt)

async def admin_order_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    uid = q.from_user.id
    if not await is_admin(uid):
        await q.answer("⛔ غير مسموح.", show_alert=True)
        return
    parts = (q.data or "").split(":")
    if len(parts) != 3:
        return
    _, act, oid = parts
    orders = await all_orders()
    o = next((x for x in orders if x.get("id") == oid), None)
    if not o or o.get("status") != "pending":
        await q.message.reply_text("ℹ️ الطلب غير موجود أو لم يعد معلّق.")
        return
    if not is_super(uid) and act == "edit":
        await q.message.reply_text("⛔ التعديل قبل القبول للسوبر أدمن فقط.")
        return
    if act == "edit":
        context.user_data["edit_oid"] = oid
        await q.message.reply_text("✏️ ابعت المبلغ الجديد (رقم فقط).", reply_markup=ReplyKeyboardRemove())
        return
    if act == "approve":
        await apply_approve(o)
        o["status"] = "approved"
        o.setdefault("history", []).append({"at": now_iso(), "by": uid, "action": "approved"})
        await save_orders(orders)
        await q.message.reply_text("✅ تم قبول الطلب.")
        try: await context.bot.send_message(o["user_id"], "✅ تم قبول طلبك.\nشكراً لثقتك 🤝")
        except Exception: pass
        return
    if act == "reject":
        await apply_reject(o)
        o["status"] = "rejected"
        o.setdefault("history", []).append({"at": now_iso(), "by": uid, "action": "rejected"})
        await save_orders(orders)
        await q.message.reply_text("✅ تم رفض الطلب.")
        try: await context.bot.send_message(o["user_id"], "❌ تم رفض طلبك.\nإذا عندك استفسار تواصل مع الدعم 🆘")
        except Exception: pass
        return

async def admin_edit_listener(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if "edit_oid" not in context.user_data:
        return await admin_menu(update, context)
    if not is_super(update.effective_user.id):
        context.user_data.pop("edit_oid", None)
        await update.message.reply_text("⛔ غير مسموح.", reply_markup=mk_admin_menu(False))
        return S_ADMIN_MENU
    amt = safe_int(update.message.text)
    if amt is None or amt <= 0:
        await update.message.reply_text("اكتب رقم صحيح أكبر من 0 🙏")
        return S_ADMIN_MENU
    oid = context.user_data.get("edit_oid")
    orders = await all_orders()
    o = next((x for x in orders if x.get("id") == oid), None)
    if not o or o.get("status") != "pending":
        context.user_data.pop("edit_oid", None)
        await update.message.reply_text("⚠️ الطلب غير موجود أو لم يعد معلّق.", reply_markup=mk_admin_menu(True))
        return S_ADMIN_MENU
    if o.get("type") == "withdraw":
        old = int((o.get("data") or {}).get("amount", 0))
        new = int(amt)
        target = int(o.get("user_id"))
        diff = new - old
        if diff > 0:
            b, _ = await get_wallet(target)
            if diff > b:
                await update.message.reply_text(f"❌ لا يمكن رفع المبلغ. رصيد المستخدم المتاح: {b}", reply_markup=mk_admin_menu(True))
                return S_ADMIN_MENU
            await add_wallet(target, db=-diff, dh=+diff)
        elif diff < 0:
            await add_wallet(target, db=+(-diff), dh=-(-diff))
        o["data"]["amount"] = new
    else:
        o.setdefault("data", {})["amount"] = int(amt)
    o.setdefault("history", []).append({"at": now_iso(), "by": update.effective_user.id, "action": f"edited_amount:{amt}"})
    await save_orders(orders)
    context.user_data.pop("edit_oid", None)
    await update.message.reply_text("✅ تم تعديل المبلغ قبل القبول.", reply_markup=mk_admin_menu(True))
    return S_ADMIN_MENU

async def main_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    await ensure_user(update)
    if not await require_sub(update, context):
        return ConversationHandler.END
    txt = norm(update.message.text)
    act = startswith_map(txt, {"💼":"ich","💰":"wallet","➕":"topup","➖":"withdraw","🧾":"cancelwd","🆘":"support","🤝":"ref"})
    if act == "wallet": return await show_wallet(update, context)
    if act == "topup": return await topup_entry(update, context)
    if act == "withdraw": return await withdraw_entry(update, context)
    if act == "cancelwd": return await cancel_last_withdraw(update, context)
    if act == "support": return await support(update, context)
    if act == "ich": return await ich_entry(update, context)
    if act == "ref": return await referral_entry(update, context)
    await update.message.reply_text("اختار من القائمة 👇", reply_markup=mk_main_menu())
    return S_MAIN


def _clear_flow_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in ("topup","wd","ich_suggest","edit_oid","setbal_uid","stock_u","broadcast"):
        context.user_data.pop(k, None)

async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_flow_context(context)
    await update.message.reply_text("🏠 رجعناك للقائمة الرئيسية 👇", reply_markup=mk_main_menu())
    return S_MAIN

async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_flow_context(context)
    lm = context.user_data.get("last_menu", "main")
    if lm == "admin":
        await update.message.reply_text("👑 لوحة الأدمن", reply_markup=mk_admin_menu(is_super(update.effective_user.id)))
        return S_ADMIN_MENU
    if lm == "stock":
        await update.message.reply_text("📦 مخزون ايشانسي", reply_markup=mk_stock_menu())
        return S_ADMIN_ICH_STOCK
    if lm == "ich":
        await update.message.reply_text("💼 حساب ايشانسي", reply_markup=mk_ich_menu())
        return S_ICH_MENU
    await update.message.reply_text("↩️ تم الرجوع 👇", reply_markup=mk_main_menu())
    return S_MAIN

async def referral_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await gate_maintenance(update, context):
        return ConversationHandler.END
    await ensure_user(update)
    if not await require_sub(update, context):
        return ConversationHandler.END
    msg = await referral_message(context, update.effective_user.id)
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=mk_main_menu())
    return S_MAIN

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for k in ("topup","wd","ich_suggest","edit_oid","setbal_uid","stock_u"):
        context.user_data.pop(k, None)
    if update.message:
        await update.message.reply_text("تم الإلغاء ✅", reply_markup=mk_main_menu())
    return S_MAIN

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error: %s", context.error)

def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("admin", admin_cmd),
            CallbackQueryHandler(cb_checksub, pattern=r"^sys:checksub$"),
        ],
        states={
            S_MAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, main_router)],
            S_TOPUP_METHOD: [CallbackQueryHandler(topup_method_cb, pattern=r"^topup:(sham|sy|back)$")],
            S_TOPUP_CODE: [
                CallbackQueryHandler(topup_code_cb, pattern=r"^topupcode:.+$"),
                CallbackQueryHandler(topup_method_cb, pattern=r"^topup:back$"),
            ],
            S_TOPUP_TXN: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_txn)],
            S_TOPUP_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, topup_amount)],
            S_TOPUP_CONFIRM: [CallbackQueryHandler(topup_confirm, pattern=r"^topup:(confirm|cancel)$")],
            S_WD_METHOD: [CallbackQueryHandler(wd_method_cb, pattern=r"^wd:(sham|sy|back)$")],
            S_WD_RECEIVER: [MessageHandler(filters.TEXT & ~filters.COMMAND, wd_receiver)],
            S_WD_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, wd_amount)],
            S_WD_CONFIRM: [CallbackQueryHandler(wd_confirm, pattern=r"^wd:(confirm|cancel)$")],
            S_ICH_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, ich_menu)],
            S_ICH_CLAIM_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ich_claim_query)],
            S_ICH_CLAIM_CONFIRM: [CallbackQueryHandler(ich_claim_cb, pattern=r"^ich:claim:(yes|another|back)$")],
            S_ICH_TOPUP: [
                CallbackQueryHandler(ich_topup_cb, pattern=r"^ich:topup:(yes:\d+|no)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ich_topup),
            ],
            S_ICH_WD: [
                CallbackQueryHandler(ich_wd_cb, pattern=r"^ich:wd:(yes:\d+|no)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ich_wd),
            ],
            S_ICH_DEL: [CallbackQueryHandler(ich_unlink_cb, pattern=r"^ich:unlink:(yes|no)$")],
            S_ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_listener)],
            S_ADMIN_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_search)],
            S_ADMIN_SETBAL_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_setbal_uid)],
            S_ADMIN_SETBAL_AMT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_setbal_amt)],
            S_ADMIN_ASSIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_assist)],
            S_ADMIN_BROADCAST: [MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO) & ~filters.COMMAND, admin_broadcast)],
            S_ADMIN_RESTORE: [MessageHandler(filters.Document.ALL & ~filters.COMMAND, admin_restore)],
            S_ADMIN_ICH_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, stock_menu)],
            S_ADMIN_ICH_ADD_U: [MessageHandler(filters.TEXT & ~filters.COMMAND, stock_add_u)],
            S_ADMIN_ICH_ADD_P: [MessageHandler(filters.TEXT & ~filters.COMMAND, stock_add_p)],
            S_ADMIN_ICH_DEL_Q: [MessageHandler(filters.TEXT & ~filters.COMMAND, stock_del)],
            S_ADMIN_REF_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_referrals_cmd)],
            S_ADMIN_ICH_BULK: [MessageHandler(filters.TEXT & ~filters.COMMAND, stock_bulk_add)],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.Regex(r"^↩️"), go_back), MessageHandler(filters.Regex(r"^🏠"), go_home)],
        name="conv",
        persistent=False,
    )
    app.add_handler(CallbackQueryHandler(admin_order_cb, pattern=r"^adm:(approve|reject|edit):.+$"), group=0)
    app.add_handler(CallbackQueryHandler(cb_checksub, pattern=r"^sys:checksub$"), group=0)
    app.add_handler(conv, group=1)
    app.add_error_handler(on_error)
    return app
    
import asyncio

def ensure_event_loop():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

import asyncio
from telegram import Update

async def main() -> None:
    # تهيئة التخزين
    await bootstrap()

    # بناء التطبيق
    app = build_app()

    log.info("Starting bot (polling only)...")

    # تشغيل التطبيق بطريقة صحيحة مع Python 3.12
    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    # إبقاء البوت شغّال
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
