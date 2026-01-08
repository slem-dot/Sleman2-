"""Admin command (minimal gate)"""

from telegram import Update
from telegram.ext import ContextTypes

from bot.database import get_db
from bot.services.database import is_user_admin
from bot.keyboards.admin import get_admin_menu

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    db = await get_db()
    async with db.get_session() as session:
        ok = await is_user_admin(session, tg_user.id)

    if not ok:
        await update.message.reply_text("❌ ليس لديك صلاحية الوصول.")
        return

    await update.message.reply_text(
        "👨‍💼 لوحة الإدارة
اختر القسم المطلوب:",
        reply_markup=get_admin_menu(tg_user.id, int(context.bot_data.get("super_admin_id", 0))),
    )
