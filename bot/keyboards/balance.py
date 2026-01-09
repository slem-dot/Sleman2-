from telegram import InlineKeyboardMarkup, InlineKeyboardButton

def get_balance_menu() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("💳 شحن رصيد في البوت", callback_data="balance:topup"),
            InlineKeyboardButton("💸 سحب رصيد من البوت", callback_data="balance:withdraw"),
        ],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main:back")],
    ]
    return InlineKeyboardMarkup(keyboard)
