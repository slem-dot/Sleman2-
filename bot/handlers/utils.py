from __future__ import annotations
import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot.utils.texts import ERR_GENERIC

logger = logging.getLogger(__name__)

def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    admin_id = int(context.application.bot_data.get("super_admin_id", 0))
    user = update.effective_user
    return bool(user and int(user.id) == admin_id)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error: %s", context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(ERR_GENERIC)
    except Exception:
        pass
