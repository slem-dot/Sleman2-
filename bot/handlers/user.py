from __future__ import annotations
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from bot.handlers.channel import is_subscribed, send_subscribe_gate
from bot.keyboards.main import user_main_keyboard, ichancy_keyboard
from bot.utils.texts import (
    WELCOME, SUPPORT_TEXT, WALLET_TEXT, ICH_MENU,
    ICH_CREATE_ASK_USER, ICH_CREATE_ASK_PASS, ICH_AMOUNT_ASK,
    TOPUP_ASK_OP, TOPUP_ASK_AMOUNT, WITHDRAW_ASK_RECEIVER, WITHDRAW_ASK_AMOUNT
)
from bot.utils.validators import parse_int, safe_str
from bot.utils.constants import (
    ORDER_TOPUP, ORDER_WITHDRAW, ORDER_ICH_CREATE, ORDER_ICH_TOPUP, ORDER_ICH_WITHDRAW,
    CB_ORDER_APPROVE, CB_ORDER_REJECT, CB_ORDER_EDIT
)
from bot.services import wallet as wallet_svc
from bot.services import orders as orders_svc

logger = logging.getLogger(__name__)

(
    ST_NONE,
    ST_TOPUP_OP,
    ST_TOPUP_AMOUNT,
    ST_WITHDRAW_RECEIVER,
    ST_WITHDRAW_AMOUNT,
    ST_ICH_MENU,
    ST_ICH_CREATE_USER,
    ST_ICH_CREATE_PASS,
    ST_ICH_AMOUNT_TOPUP,
    ST_ICH_AMOUNT_WITHDRAW,
) = range(11)

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ok = await is_subscribed(update, context)
    if not ok:
        await send_subscribe_gate(update, context)
        return

    if update.message:
        await update.message.reply_text(WELCOME, reply_markup=user_main_keyboard())
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(WELCOME, reply_markup=user_main_keyboard())

async def user_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END

    ok = await is_subscribed(update, context)
    if not ok:
        await send_subscribe_gate(update, context)
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    storage = context.application.bot_data["storage"]
    cfg = context.application.bot_data["config"]

    if text == "💰 محفظتي":
        w = await wallet_svc.get_wallet(storage, update.effective_user.id)
        await update.message.reply_text(
            WALLET_TEXT.format(balance=w["balance"], hold=w["hold"]),
            reply_markup=user_main_keyboard(),
        )
        return ConversationHandler.END

    if text == "🆘 دعم":
        await update.message.reply_text(
            SUPPORT_TEXT.format(support=cfg.support_username),
            reply_markup=user_main_keyboard(),
        )
        return ConversationHandler.END

    if text == "💼 حساب ايشانسي":
        await update.message.reply_text(ICH_MENU, reply_markup=ichancy_keyboard())
        return ST_ICH_MENU

    if text == "➕ شحن رصيد البوت":
        context.user_data.clear()
        await update.message.reply_text(TOPUP_ASK_OP, reply_markup=user_main_keyboard())
        return ST_TOPUP_OP

    if text == "➖ سحب رصيد من البوت":
        context.user_data.clear()
        await update.message.reply_text(WITHDRAW_ASK_RECEIVER, reply_markup=user_main_keyboard())
        return ST_WITHDRAW_RECEIVER

    await update.message.reply_text("اختر من الأزرار بالأسفل 👇", reply_markup=user_main_keyboard())
    return ConversationHandler.END

async def topup_get_op(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END

    op = safe_str(update.message.text, 64)
    if len(op) < 3:
        await update.message.reply_text("رقم العملية غير صحيح. أعد الإرسال.")
        return ST_TOPUP_OP

    context.user_data["topup_op"] = op
    min_topup = int(context.application.bot_data["config"].min_topup)
    await update.message.reply_text(TOPUP_ASK_AMOUNT.format(min_topup=min_topup))
    return ST_TOPUP_AMOUNT

async def topup_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END

    amount = parse_int(update.message.text or "")
    min_topup = int(context.application.bot_data["config"].min_topup)
    if amount is None or amount < min_topup:
        await update.message.reply_text(f"المبلغ غير صحيح. يجب أن يكون >= {min_topup}.")
        return ST_TOPUP_AMOUNT

    op = context.user_data.get("topup_op")
    storage = context.application.bot_data["storage"]

    order = await orders_svc.create_order(
        storage=storage,
        order_type=ORDER_TOPUP,
        user_id=update.effective_user.id,
        data={"operation_no": op, "amount": amount},
    )

    await update.message.reply_text(
        f"✅ تم إرسال طلب الشحن للأدمن.\nرقم الطلب: #{order['id']}",
        reply_markup=user_main_keyboard(),
    )

    admin_id = int(context.application.bot_data["super_admin_id"])
    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=_format_order_for_admin(order),
            reply_markup=_order_inline_kb(order["id"]),
        )
    except Exception as e:
        logger.warning("Failed to notify admin: %s", e)

    context.user_data.clear()
    return ConversationHandler.END

async def withdraw_get_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END

    receiver = safe_str(update.message.text, 64)
    if len(receiver) < 3:
        await update.message.reply_text("رقم المستلم غير صحيح. أعد الإرسال.")
        return ST_WITHDRAW_RECEIVER

    context.user_data["withdraw_receiver"] = receiver
    min_withdraw = int(context.application.bot_data["config"].min_withdraw)
    await update.message.reply_text(WITHDRAW_ASK_AMOUNT.format(min_withdraw=min_withdraw))
    return ST_WITHDRAW_AMOUNT

async def withdraw_get_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END

    amount = parse_int(update.message.text or "")
    min_withdraw = int(context.application.bot_data["config"].min_withdraw)
    if amount is None or amount < min_withdraw:
        await update.message.reply_text(f"المبلغ غير صحيح. يجب أن يكون >= {min_withdraw}.")
        return ST_WITHDRAW_AMOUNT

    storage = context.application.bot_data["storage"]
    ok, w, reason = await wallet_svc.reserve_withdraw(storage, update.effective_user.id, amount)
    if not ok:
        if reason == "insufficient":
            await update.message.reply_text("❌ رصيدك لا يكفي لإتمام السحب.", reply_markup=user_main_keyboard())
        else:
            await update.message.reply_text("❌ تعذر تنفيذ العملية. أعد المحاولة.", reply_markup=user_main_keyboard())
        context.user_data.clear()
        return ConversationHandler.END

    receiver = context.user_data.get("withdraw_receiver")
    order = await orders_svc.create_order(
        storage=storage,
        order_type=ORDER_WITHDRAW,
        user_id=update.effective_user.id,
        data={"receiver_no": receiver, "amount": amount},
    )

    await update.message.reply_text(
        f"✅ تم حجز المبلغ مباشرة (Hold).\nرقم الطلب: #{order['id']}",
        reply_markup=user_main_keyboard(),
    )

    admin_id = int(context.application.bot_data["super_admin_id"])
    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=_format_order_for_admin(order),
            reply_markup=_order_inline_kb(order["id"]),
        )
    except Exception as e:
        logger.warning("Failed to notify admin: %s", e)

    context.user_data.clear()
    return ConversationHandler.END

async def ich_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    if text == "⬅️ رجوع":
        await update.message.reply_text("رجعناك للقائمة الرئيسية.", reply_markup=user_main_keyboard())
        return ConversationHandler.END

    if text.startswith("1)"):
        context.user_data.clear()
        await update.message.reply_text(ICH_CREATE_ASK_USER, reply_markup=ichancy_keyboard())
        return ST_ICH_CREATE_USER

    if text.startswith("2)"):
        context.user_data.clear()
        await update.message.reply_text(ICH_AMOUNT_ASK, reply_markup=ichancy_keyboard())
        return ST_ICH_AMOUNT_TOPUP

    if text.startswith("3)"):
        context.user_data.clear()
        await update.message.reply_text(ICH_AMOUNT_ASK, reply_markup=ichancy_keyboard())
        return ST_ICH_AMOUNT_WITHDRAW

    await update.message.reply_text("اختر خياراً صحيحاً من قائمة ايشانسي.", reply_markup=ichancy_keyboard())
    return ST_ICH_MENU

async def ich_create_get_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    username = safe_str(update.message.text, 64)
    if len(username) < 3:
        await update.message.reply_text("اسم المستخدم غير صحيح. أعد الإرسال.")
        return ST_ICH_CREATE_USER
    context.user_data["ich_user"] = username
    await update.message.reply_text(ICH_CREATE_ASK_PASS)
    return ST_ICH_CREATE_PASS

async def ich_create_get_pass(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    password = safe_str(update.message.text, 128)
    if len(password) < 3:
        await update.message.reply_text("كلمة المرور غير صحيحة. أعد الإرسال.")
        return ST_ICH_CREATE_PASS

    storage = context.application.bot_data["storage"]
    order = await orders_svc.create_order(
        storage=storage,
        order_type=ORDER_ICH_CREATE,
        user_id=update.effective_user.id,
        data={"username": context.user_data.get("ich_user"), "password": password},
    )

    await update.message.reply_text(
        f"✅ تم إرسال طلب إنشاء حساب ايشانسي للأدمن.\nرقم الطلب: #{order['id']}",
        reply_markup=user_main_keyboard(),
    )

    admin_id = int(context.application.bot_data["super_admin_id"])
    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=_format_order_for_admin(order),
            reply_markup=_order_inline_kb(order["id"]),
        )
    except Exception as e:
        logger.warning("Failed to notify admin: %s", e)

    context.user_data.clear()
    return ConversationHandler.END

async def ich_topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    amount = parse_int(update.message.text or "")
    if amount is None or amount <= 0:
        await update.message.reply_text("المبلغ غير صحيح. أعد الإرسال.")
        return ST_ICH_AMOUNT_TOPUP

    storage = context.application.bot_data["storage"]
    order = await orders_svc.create_order(
        storage=storage,
        order_type=ORDER_ICH_TOPUP,
        user_id=update.effective_user.id,
        data={"amount": amount},
    )

    await update.message.reply_text(
        f"✅ تم إرسال طلب شحن ايشانسي للأدمن.\nرقم الطلب: #{order['id']}",
        reply_markup=user_main_keyboard(),
    )

    admin_id = int(context.application.bot_data["super_admin_id"])
    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=_format_order_for_admin(order),
            reply_markup=_order_inline_kb(order["id"]),
        )
    except Exception as e:
        logger.warning("Failed to notify admin: %s", e)

    context.user_data.clear()
    return ConversationHandler.END

async def ich_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.effective_user:
        return ConversationHandler.END
    amount = parse_int(update.message.text or "")
    if amount is None or amount <= 0:
        await update.message.reply_text("المبلغ غير صحيح. أعد الإرسال.")
        return ST_ICH_AMOUNT_WITHDRAW

    storage = context.application.bot_data["storage"]
    order = await orders_svc.create_order(
        storage=storage,
        order_type=ORDER_ICH_WITHDRAW,
        user_id=update.effective_user.id,
        data={"amount": amount},
    )

    await update.message.reply_text(
        f"✅ تم إرسال طلب سحب من ايشانسي للأدمن.\nرقم الطلب: #{order['id']}",
        reply_markup=user_main_keyboard(),
    )

    admin_id = int(context.application.bot_data["super_admin_id"])
    try:
        await context.bot.send_message(
            chat_id=admin_id,
            text=_format_order_for_admin(order),
            reply_markup=_order_inline_kb(order["id"]),
        )
    except Exception as e:
        logger.warning("Failed to notify admin: %s", e)

    context.user_data.clear()
    return ConversationHandler.END

def _order_inline_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ تعديل", callback_data=f"{CB_ORDER_EDIT}:{order_id}"),
            InlineKeyboardButton("✅ قبول", callback_data=f"{CB_ORDER_APPROVE}:{order_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"{CB_ORDER_REJECT}:{order_id}"),
        ]
    ])

def _format_order_for_admin(order: dict) -> str:
    otype = order.get("type")
    status = order.get("status")
    oid = order.get("id")
    uid = order.get("user_id")
    data = order.get("data", {})
    return (
        f"🧾 طلب جديد #{oid}\n"
        f"النوع: {otype}\n"
        f"الحالة: {status}\n"
        f"user_id: {uid}\n"
        f"البيانات: {data}\n"
        f"الوقت: {order.get('created_at')}"
    )
