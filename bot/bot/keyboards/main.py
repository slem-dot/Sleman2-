from telegram import ReplyKeyboardMarkup

def get_main_menu() -> ReplyKeyboardMarkup:
    keyboard = [
        ["حساب ايشانسي", "رصيدي"],
        ["🎁 الإحالات", "📞 التواصل مع الدعم"],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
