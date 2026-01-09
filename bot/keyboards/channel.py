from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def subscribe_keyboard(channel_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ اشترك بالقناة", url=f"https://t.me/{channel_username.lstrip('@')}")],
        [InlineKeyboardButton("🔄 تحقق من الاشتراك", callback_data="chk_sub")],
    ])
