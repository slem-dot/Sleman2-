from __future__ import annotations
import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.channel import is_subscribed, send_subscribe_gate
from bot.services.users import create_or_update_user

logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message:
        return

    try:
        await create_or_update_user(context.application.bot_data["storage"], user)
    except Exception as e:
        logger.exception("Failed to create/update user: %s", e)

    ok = await is_subscribed(update, context)
    if not ok:
        await send_subscribe_gate(update, context)
        return

    show_menu = context.application.bot_data.get("show_menu_callable")
    if callable(show_menu):
        await show_menu(update, context)
