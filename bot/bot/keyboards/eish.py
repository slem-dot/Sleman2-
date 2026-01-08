from telegram import InlineKeyboardMarkup, InlineKeyboardButton

def get_eish_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("➕ إنشاء حساب", callback_data="eish:create"),
            InlineKeyboardButton("💰 شحن حساب ايشانسي", callback_data="eish:topup"),
        ],
        [
            InlineKeyboardButton("💸 سحب من حساب ايشانسي", callback_data="eish:withdraw"),
            InlineKeyboardButton("👤 حسابي", callback_data="eish:my_account"),
        ],
        [
            InlineKeyboardButton("🗑️ حذف حساب ايشانسي", callback_data="eish:delete"),
            InlineKeyboardButton("🌐 موقع ايشانسي", url="https://www.ichancy.com"),
        ],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main:back")],
    ]
    return InlineKeyboardMarkup(keyboard)
