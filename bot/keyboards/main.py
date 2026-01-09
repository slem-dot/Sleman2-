from telegram import ReplyKeyboardMarkup, KeyboardButton

def user_main_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("💼 حساب ايشانسي"), KeyboardButton("💰 محفظتي")],
        [KeyboardButton("➕ شحن رصيد البوت"), KeyboardButton("➖ سحب رصيد من البوت")],
        [KeyboardButton("🆘 دعم")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def ichancy_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("1) إنشاء حساب ايشانسي"), KeyboardButton("2) شحن حساب ايشانسي")],
        [KeyboardButton("3) سحب من حساب ايشانسي")],
        [KeyboardButton("⬅️ رجوع")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
