from __future__ import annotations
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from bot.handlers.utils import is_admin
from bot.keyboards.admin import admin_keyboard
from bot.keyboards.main import user_main_keyboard
from bot.utils.texts import (
    ADMIN_ONLY, ADMIN_MENU, NO_PENDING, PENDING_TITLE,
    ASK_USER_ID, USER_NOT_FOUND, ASK_ADJUST_AMOUNT, ADJUST_DONE,
    ASK_EDIT_VALUES, EDIT_DONE, ORDER_UPDATED
)
from bot.utils.validators import parse_int, safe_str
from bot.utils.constants import (
    STATUS_APPROVED, STATUS_REJECTED,
    ORDER_TOPUP, ORDER_WITHDRAW, ORDER_ICH_CREATE,
    CB_ORDER_APPROVE, CB_ORDER_REJECT, CB_ORDER_EDIT
)
from bot.services import orders as orders_svc
from bot.services import wallet as wallet_svc
from bot.services import users as users_svc

logger = logging.getLogger(__name__)

(AD_ST_MENU, AD_ST_FIND_USER, AD_ST_ADJUST_USER, AD_ST_ADJUST_AMOUNT) = range(4)

async def admin_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_admin(update, context):
        await update.message.reply_text(ADMIN_ONLY)
        return ConversationHandler.END
    await update.message.reply_text(ADMIN_MENU, reply_markup=admin_keyboard())
    return AD_ST_MENU

async def admin_menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    if not is_admin(update, context):
        await update.message.reply_text(ADMIN_ONLY)
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    storage = context.application.bot_data["storage"]

    if text == "⬅️ رجوع":
        await update.message.reply_text("رجعناك لقائمة المستخدم.", reply_markup=user_main_keyboard())
        return ConversationHandler.END

    if text == "📌 الطلبات المعلقة":
        pending = await orders_svc.list_pending(storage, limit=20)
        if not pending:
            await update.message.reply_text(NO_PENDING)
            return AD_ST_MENU

        await update.message.reply_text(PENDING_TITLE)
        for o in pending:
            await update.message.reply_text(
                _format_order_admin(o),
                reply_markup=_order_inline_kb(o["id"]),
                disable_web_page_preview=True,
            )
        return AD_ST_MENU

    if text == "🔍 بحث مستخدم":
        await update.message.reply_text(ASK_USER_ID)
        return AD_ST_FIND_USER

    if text == "💳 تعديل رصيد":
        await update.message.reply_text(ASK_USER_ID)
        return AD_ST_ADJUST_USER

    await update.message.reply_text("اختر خياراً من لوحة الأدمن.", reply_markup=admin_keyboard())
    return AD_ST_MENU

async def admin_find_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not is_admin(update, context):
        return ConversationHandler.END

    user_id = _extract_user_id(update)
    if not user_id:
        await update.message.reply_text("لم أفهم. أرسل ID رقمياً أو حوّل رسالة.")
        return AD_ST_FIND_USER

    storage = context.application.bot_data["storage"]
    u = await users_svc.get_user(storage, user_id)
    if not u:
        await update.message.reply_text(USER_NOT_FOUND)
        return AD_ST_MENU

    w = await wallet_svc.get_wallet(storage, user_id)
    ich = u.get("ichancy")
    ich_txt = f"{ich}" if ich else "لا يوجد"
    await update.message.reply_text(
        f"👤 المستخدم: {user_id}\n"
        f"username: @{u.get('username')}\n"
        f"الاسم: {u.get('first_name')} {u.get('last_name')}\n"
        f"ichancy: {ich_txt}\n"
        f"wallet: balance={w['balance']}, hold={w['hold']}\n"
    )
    return AD_ST_MENU

async def admin_adjust_user_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not is_admin(update, context):
        return ConversationHandler.END

    user_id = _extract_user_id(update)
    if not user_id:
        await update.message.reply_text("أرسل ID رقمياً أو حوّل رسالة.")
        return AD_ST_ADJUST_USER

    context.user_data["admin_target_user"] = int(user_id)
    await update.message.reply_text(ASK_ADJUST_AMOUNT)
    return AD_ST_ADJUST_AMOUNT

async def admin_adjust_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not is_admin(update, context):
        return ConversationHandler.END

    amount = parse_int(update.message.text or "")
    if amount is None:
        await update.message.reply_text("القيمة غير صحيحة. مثال: 1000 أو -500")
        return AD_ST_ADJUST_AMOUNT

    user_id = context.user_data.get("admin_target_user")
    if not user_id:
        await update.message.reply_text("لم يتم اختيار مستخدم.")
        return AD_ST_MENU

    storage = context.application.bot_data["storage"]
    w = await wallet_svc.add_balance(storage, int(user_id), int(amount))
    await update.message.reply_text(f"{ADJUST_DONE}\nwallet الآن: balance={w['balance']}, hold={w['hold']}")
    return AD_ST_MENU

async def admin_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    storage = context.application.bot_data["storage"]
    order = await orders_svc.get_order(storage, order_id)
    if not order:
        await q.message.reply_text("الطلب غير موجود.")
        return

    if action == CB_ORDER_EDIT:
        context.user_data["edit_order_id"] = int(order_id)
        await q.message.reply_text(ASK_EDIT_VALUES)
        return

    if action == CB_ORDER_APPROVE:
        await _approve_order(context, q, order)
        return

    if action == CB_ORDER_REJECT:
        await _reject_order(context, q, order)
        return

async def admin_message_listener_for_edits(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not is_admin(update, context):
        return

    edit_order_id = context.user_data.get("edit_order_id")
    if not edit_order_id:
        return

    storage = context.application.bot_data["storage"]
    order = await orders_svc.get_order(storage, int(edit_order_id))
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
        amount = parse_int(text)
        if amount is None or amount <= 0:
            await update.message.reply_text("أرسل مبلغ صحيح (رقم موجب).")
            return
        data_obj = dict(order.get("data", {}) or {})
        data_obj["amount"] = int(amount)
        patch = {"data": data_obj}

    updated = await orders_svc.update_order(storage, int(edit_order_id), patch)
    context.user_data.pop("edit_order_id", None)
    if updated:
        await update.message.reply_text(EDIT_DONE)
    else:
        await update.message.reply_text("تعذر تحديث الطلب.")

async def _approve_order(context: ContextTypes.DEFAULT_TYPE, q, order: dict) -> None:
    storage = context.application.bot_data["storage"]
    if order.get("status") != "pending":
        await q.message.reply_text("هذا الطلب ليس معلقاً.")
        return

    otype = order.get("type")
    user_id = int(order.get("user_id"))
    data = order.get("data", {}) or {}

    try:
        if otype == ORDER_TOPUP:
            amount = int(data.get("amount", 0))
            await wallet_svc.add_balance(storage, user_id, amount)
        elif otype == ORDER_WITHDRAW:
            amount = int(data.get("amount", 0))
            await wallet_svc.finalize_withdraw(storage, user_id, amount)
        elif otype == ORDER_ICH_CREATE:
            username = safe_str(data.get("username"), 64)
            password = safe_str(data.get("password"), 128)
            await users_svc.set_ichancy_account(storage, user_id, username, password)

        await orders_svc.update_order(storage, int(order["id"]), {"status": STATUS_APPROVED})
        await q.message.reply_text(ORDER_UPDATED.format(status=STATUS_APPROVED))
        try:
            await context.bot.send_message(chat_id=user_id, text=f"✅ تم قبول طلبك #{order['id']} ({otype}).")
        except Exception:
            pass
    except Exception as e:
        logger.exception("Approve failed: %s", e)
        await q.message.reply_text("تعذر قبول الطلب بسبب خطأ.")

async def _reject_order(context: ContextTypes.DEFAULT_TYPE, q, order: dict) -> None:
    storage = context.application.bot_data["storage"]
    if order.get("status") != "pending":
        await q.message.reply_text("هذا الطلب ليس معلقاً.")
        return

    otype = order.get("type")
    user_id = int(order.get("user_id"))
    data = order.get("data", {}) or {}

    try:
        if otype == ORDER_WITHDRAW:
            amount = int(data.get("amount", 0))
            await wallet_svc.release_hold(storage, user_id, amount)

        await orders_svc.update_order(storage, int(order["id"]), {"status": STATUS_REJECTED})
        await q.message.reply_text(ORDER_UPDATED.format(status=STATUS_REJECTED))
        try:
            await context.bot.send_message(chat_id=user_id, text=f"❌ تم رفض طلبك #{order['id']} ({otype}).")
        except Exception:
            pass
    except Exception as e:
        logger.exception("Reject failed: %s", e)
        await q.message.reply_text("تعذر رفض الطلب بسبب خطأ.")

def _order_inline_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ تعديل", callback_data=f"{CB_ORDER_EDIT}:{order_id}"),
            InlineKeyboardButton("✅ قبول", callback_data=f"{CB_ORDER_APPROVE}:{order_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"{CB_ORDER_REJECT}:{order_id}"),
        ]
    ])

def _format_order_admin(order: dict) -> str:
    return (
        f"🧾 #{order.get('id')} | {order.get('type')} | {order.get('status')}\n"
        f"user_id: {order.get('user_id')}\n"
        f"data: {order.get('data')}\n"
        f"created: {order.get('created_at')}"
    )

def _extract_user_id(update: Update) -> int | None:
    if update.message and update.message.forward_from:
        return int(update.message.forward_from.id)
    if update.message:
        n = parse_int(update.message.text or "")
        if n and n > 0:
            return int(n)
    return None
