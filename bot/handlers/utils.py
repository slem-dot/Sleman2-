"""Utility handlers"""

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode


async def subscription_check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "✅ تم التحقق من اشتراكك بنجاح.\n\n"
        "يمكنك الآن استخدام البوت بكل الميزات المتاحة.",
        parse_mode=ParseMode.HTML
    )
