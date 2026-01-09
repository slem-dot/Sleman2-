"""Admin handlers"""

from telegram import Update
from telegram.ext import ContextTypes
import os

SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0"))


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id != SUPER_ADMIN_ID:
        await update.message.reply_text("❌ هذا الأمر مخصص للإدارة فقط.")
        return

    await update.message.reply_text(
        "👨‍💼 لوحة الإدارة\n\n"
        "اختر من القائمة أدناه:"
    )
