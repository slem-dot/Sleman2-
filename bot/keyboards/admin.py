from telegram import ReplyKeyboardMarkup, KeyboardButton

def admin_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("📌 الطلبات المعلقة"), KeyboardButton("🔍 بحث مستخدم")],
        [KeyboardButton("💳 تعديل رصيد"), KeyboardButton("⬅️ رجوع")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
