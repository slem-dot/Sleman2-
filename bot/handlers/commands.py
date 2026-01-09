"""Command handlers"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot.database import get_db
from bot.services.database import create_or_update_user
from bot.handlers.channel import check_subscription, get_subscription_keyboard
from bot.keyboards.main import get_main_menu

logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user

    db = await get_db()
    async with db.get_session() as session:
        await create_or_update_user(
            session,
            user_id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name or "-",
            last_name=tg_user.last_name,
        )

    is_subscribed = await check_subscription(context, tg_user.id)
    if not is_subscribed:
        required_channel = context.bot_data.get("required_channel") or "@broichancy"

        await update.message.reply_text(
            "👋 مرحباً بك في بوت ايشانسي!\n\n"
            "⚠️ يجب الاشتراك في القناة أولاً لاستخدام البوت:\n"
            f"🔗 {required_channel}\n\n"
            "بعد الاشتراك اضغط على زر التحقق.",
            reply_markup=get_subscription_keyboard(context),
        )
        return

    await update.message.reply_text(
        "👋 أهلاً وسهلاً بك في بوت ايشانسي!\n"
        "اختر من القائمة أدناه:",
        reply_markup=get_main_menu(),
    )
