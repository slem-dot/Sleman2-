from __future__ import annotations
import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot.keyboards.channel import subscribe_keyboard
from bot.utils.texts import NEED_SUB, SUB_OK, SUB_FAIL
from bot.utils.constants import CB_CHECK_SUB

logger = logging.getLogger(__name__)

def _is_member_status(status: str) -> bool:
    return status in ("member", "administrator", "creator")

async def is_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    required_channel: str = context.application.bot_data.get("required_channel", "")
    if not required_channel:
        return True

    user = update.effective_user
    if not user:
        return False

    try:
        member = await context.bot.get_chat_member(chat_id=required_channel, user_id=user.id)
        return _is_member_status(getattr(member, "status", ""))
    except Exception as e:
        logger.warning("Subscription check failed: %s", e)
        return False

async def send_subscribe_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    required_channel: str = context.application.bot_data.get("required_channel", "")
    if update.message:
        await update.message.reply_text(
            NEED_SUB,
            reply_markup=subscribe_keyboard(required_channel),
            disable_web_page_preview=True,
        )
    elif update.callback_query and update.callback_query.message:
        await update.callback_query.message.reply_text(
            NEED_SUB,
            reply_markup=subscribe_keyboard(required_channel),
            disable_web_page_preview=True,
        )

async def on_check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()

    ok = await is_subscribed(update, context)
    if ok:
        await q.message.reply_text(SUB_OK)
        show_menu = context.application.bot_data.get("show_menu_callable")
        if callable(show_menu):
            await show_menu(update, context)
    else:
        await q.message.reply_text(
            SUB_FAIL,
            reply_markup=subscribe_keyboard(context.application.bot_data.get("required_channel", "")),
        )
