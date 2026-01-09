from __future__ import annotations

import os
import json
import time
import tempfile
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# =========================
# Config
# =========================

def _to_int(x: str | None, default: int) -> int:
    try:
        return int(x) if x is not None else default
    except Exception:
        return default

@dataclass(frozen=True)
class Config:
    BOT_TOKEN: str
    SUPER_ADMIN_ID: int
    REQUIRED_CHANNEL: str
    SUPPORT_USERNAME: str
    DATA_DIR: str
    MIN_TOPUP: int
    MIN_WITHDRAW: int
    LOG_LEVEL: str

    @staticmethod
    def from_env() -> "Config":
        return Config(
            BOT_TOKEN=os.getenv("BOT_TOKEN", "").strip(),
            SUPER_ADMIN_ID=_to_int(os.getenv("SUPER_ADMIN_ID"), 0),
            REQUIRED_CHANNEL=os.getenv("REQUIRED_CHANNEL", "").strip(),
            SUPPORT_USERNAME=os.getenv("SUPPORT_USERNAME", "@support").strip(),
            DATA_DIR=os.getenv("DATA_DIR", "data").strip() or "data",
            MIN_TOPUP=_to_int(os.getenv("MIN_TOPUP"), 15000),
            MIN_WITHDRAW=_to_int(os.getenv("MIN_WITHDRAW"), 50000),
            LOG_LEVEL=(os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"),
        )

    def validate(self) -> Tuple[bool, str]:
        if not self.BOT_TOKEN:
            return False, "Missing BOT_TOKEN"
        if not self.SUPER_ADMIN_ID:
            return False, "Missing SUPER_ADMIN_ID"
        if not self.REQUIRED_CHANNEL.startswith("@"):
            return False, "REQUIRED_CHANNEL must start with @"
        if not self.SUPPORT_USERNAME.startswith("@"):
            return False, "SUPPORT_USERNAME must start with @"
        return True, "OK"

# =========================
# Basic texts
# =========================

WELCOME = "أهلاً بك 👋\nاختر من القائمة بالأسفل."
NEED_SUB = "⚠️ يجب الاشتراك بالقناة أولاً."
SUB_OK = "✅ تم التحقق من الاشتراك."
SUB_FAIL = "❌ لم يتم الاشتراك بعد."
ERR_GENERIC = "حدث خطأ غير متوقع."

# =========================
# Storage (JSON)
# =========================

class JSONStorage:
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self._ensure("users.json", {})
        self._ensure("wallet.json", {})
        self._ensure("orders.json", {"next_id": 1, "orders": []})

    def _path(self, name: str) -> str:
        return os.path.join(self.data_dir, name)

    def _ensure(self, name: str, default: Any):
        p = self._path(name)
        if not os.path.exists(p):
            self._write_atomic(name, default)

    def read(self, name: str, default: Any):
        try:
            with open(self._path(name), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def write(self, name: str, data: Any):
        self._write_atomic(name, data)

    def _write_atomic(self, name: str, data: Any):
        fd, tmp = tempfile.mkstemp(dir=self.data_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._path(name))

# =========================
# Keyboards
# =========================

def kb_main():
    return ReplyKeyboardMarkup(
        [
            ["💼 حساب ايشانسي", "💰 محفظتي"],
            ["➕ شحن رصيد البوت", "➖ سحب رصيد من البوت"],
            ["🆘 دعم"],
        ],
        resize_keyboard=True,
    )

def kb_sub(channel: str):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ اشترك بالقناة", url=f"https://t.me/{channel.lstrip('@')}")],
            [InlineKeyboardButton("🔄 تحقق", callback_data="chk_sub")],
        ]
    )

# =========================
# Subscription
# =========================

async def is_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(
            context.application.bot_data["cfg"].REQUIRED_CHANNEL,
            update.effective_user.id,
        )
        return member.status in ("member", "administrator", "creator")
    except Exception:
        return False

# =========================
# Handlers
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_subscribed(update, context):
        await update.message.reply_text(
            NEED_SUB,
            reply_markup=kb_sub(context.application.bot_data["cfg"].REQUIRED_CHANNEL),
        )
        return
    await update.message.reply_text(WELCOME, reply_markup=kb_main())

async def check_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if await is_subscribed(update, context):
        await q.message.reply_text(SUB_OK, reply_markup=kb_main())
    else:
        await q.message.reply_text(SUB_FAIL)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Error", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(ERR_GENERIC)

# =========================
# Main (IMPORTANT FIX)
# =========================

def main():
    load_dotenv()
    cfg = Config.from_env()
    ok, msg = cfg.validate()

    logging.basicConfig(level=getattr(logging, cfg.LOG_LEVEL))
    if not ok:
        logging.critical(msg)
        return

    storage = JSONStorage(cfg.DATA_DIR)

    app: Application = ApplicationBuilder().token(cfg.BOT_TOKEN).build()
    app.bot_data["cfg"] = cfg
    app.bot_data["storage"] = storage

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_sub, pattern="^chk_sub$"))
    app.add_error_handler(error_handler)

    logging.info("✅ Bot started (polling)")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
