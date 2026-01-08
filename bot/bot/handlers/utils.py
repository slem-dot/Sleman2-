"""Generic callback query handler"""

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.channel import check_subscription
from bot.keyboards.main import get_main_menu
from bot.keyboards.balance import get_balance_menu
from bot.keyboards.eish import get_eish_menu

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data or ""

    # subscription verify
    if data == "check_subscription":
        ok = await check_subscription(context, query.from_user.id)
        if ok:
            await query.edit_message_text("✅ تم التحقق من اشتراكك!

مرحباً بك في البوت.")
            await context.bot.send_message(chat_id=query.from_user.id, text="القائمة الرئيسية:", reply_markup=get_main_menu())
        else:
            await query.edit_message_text("❌ لم يتم التحقق من اشتراكك بعد. اشترك ثم أعد المحاولة.")
        return

    # back routing for inline menus
    if data in ("main:back", "balance:back"):
        try:
            await query.edit_message_text("القائمة الرئيسية:")
        except Exception:
            pass
        await context.bot.send_message(chat_id=query.from_user.id, text="القائمة الرئيسية:", reply_markup=get_main_menu())
        return

    if data == "balance:topup" or data == "balance:withdraw":
        await query.edit_message_text("✅ تم استلام اختيارك.
(تم تجهيز الهيكل، ويمكنك توصيل تدفقات الشحن/السحب التفصيلية لاحقاً).")
        return

    if data.startswith("copy_username:"):
        username = data.split(":", 1)[1]
        await query.edit_message_text(f"👤 اسم المستخدم:\n`{username}`\n\nانسخه من الأعلى.", parse_mode="Markdown")
        return

    if data.startswith("copy_password:"):
        password = data.split(":", 1)[1]
        await query.edit_message_text(f"🔐 كلمة المرور:\n`{password}`\n\nانسخه من الأعلى.", parse_mode="Markdown")
        return

    if data.startswith("eish:"):
        # simple nav
        if data == "eish:back":
            await query.edit_message_text("👤 قسم حساب ايشانسي:", reply_markup=get_eish_menu())
            return

    # fallback: ignore
    return
