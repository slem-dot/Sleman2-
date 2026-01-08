"""Reply-keyboard message router"""

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.channel import check_subscription, get_subscription_keyboard
from bot.keyboards.main import get_main_menu
from bot.keyboards.balance import get_balance_menu
from bot.keyboards.eish import get_eish_menu

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    text = (update.message.text or "").strip()

    # Subscription gate
    if not await check_subscription(context, tg_user.id):
        await update.message.reply_text(
            "⚠️ يجب الاشتراك في القناة أولاً لاستخدام البوت.",
            reply_markup=get_subscription_keyboard(context),
        )
        return

    if text in ("رجوع", "🔙 رجوع"):
        await update.message.reply_text("القائمة الرئيسية:", reply_markup=get_main_menu())
        return

    if text == "رصيدي":
        await update.message.reply_text("اختر عملية الرصيد:", reply_markup=get_balance_menu())
        return

    if text == "حساب ايشانسي":
        await update.message.reply_text("👤 قسم حساب ايشانسي:", reply_markup=get_eish_menu())
        return

    if text == "📞 التواصل مع الدعم":
        await update.message.reply_text(f"الدعم: {context.bot_data.get('support_username')}")
        return

    # Default
    await update.message.reply_text("اختر من القائمة 👇", reply_markup=get_main_menu())
