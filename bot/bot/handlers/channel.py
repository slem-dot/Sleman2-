"""Channel subscription helpers"""

import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

async def check_subscription(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        required_channel = context.bot_data.get("required_channel", "@broichancy")
        channel_username = required_channel.lstrip("@")
        chat_member = await context.bot.get_chat_member(chat_id=f"@{channel_username}", user_id=user_id)
        return chat_member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.error(f"Error checking subscription: {e}")
        # Fail-open to avoid blocking users if Telegram API is limited
        return True

def get_subscription_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    required_channel = context.bot_data.get("required_channel", "@broichancy")
    channel_username = required_channel.lstrip("@")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 اشترك بالقناة", url=f"https://t.me/{channel_username}")],
        [InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="check_subscription")],
    ])
