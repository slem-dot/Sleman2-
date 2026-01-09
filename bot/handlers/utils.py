"""
Utility callback handlers
"""

from telegram import Update
from telegram.ext import ContextTypes


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Generic callback query handler
    """
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data or ""

    # رجوع للقائمة الرئيسية
    if data == "main:back":
        from bot.keyboards.main import get_main_menu
        await query.message.reply_text(
            "🔙 تم الرجوع إلى القائمة الرئيسية",
            reply_markup=get_main_menu()
        )
        return

    # رجوع من الرصيد
    if data == "balance:back":
        from bot.keyboards.balance import get_balance_menu
        await query.edit_message_text(
            "اختر عملية:",
            reply_markup=get_balance_menu()
        )
        return

    # رجوع من حساب ايشانسي
    if data == "eish:back":
        from bot.keyboards.eish import get_eish_menu
        await query.edit_message_text(
            "👤 قسم حساب ايشانسي",
            reply_markup=get_eish_menu()
        )
        return

    # افتراضي
    await query.edit_message_text("⚠️ خيار غير معروف")
