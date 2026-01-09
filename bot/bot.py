"""Main bot class"""

import logging

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from bot.config import BotConfig
from bot.database import init_db, get_db
from bot.handlers import (
    commands,
    user as user_handlers,
    admin as admin_handlers,
    utils as utils_handlers,
)

logger = logging.getLogger(__name__)


class IchancyBot:
    def __init__(self):
        self.config = BotConfig.from_env()
        if not self.config.validate():
            raise ValueError(
                "Invalid configuration. Check env vars "
                "(BOT_TOKEN, SUPER_ADMIN_ID, REQUIRED_CHANNEL, SUPPORT_USERNAME, DATABASE_URL)."
            )
        self.application: Application | None = None

    def run(self):
        """Blocking runner for Railway / local."""

        async def _post_init(app: Application):
            # Put shared config in bot_data
            app.bot_data["required_channel"] = self.config.REQUIRED_CHANNEL
            app.bot_data["support_username"] = self.config.SUPPORT_USERNAME
            app.bot_data["min_topup"] = self.config.MIN_TOPUP
            app.bot_data["min_withdraw"] = self.config.MIN_WITHDRAW
            app.bot_data["super_admin_id"] = int(self.config.SUPER_ADMIN_ID)

            # Init database
            await init_db(self.config)

            # Ensure super admin exists
            async with (await get_db()).get_session() as session:
                from bot.services.database import ensure_admin_user
                await ensure_admin_user(session, int(self.config.SUPER_ADMIN_ID))

        async def _post_shutdown(app: Application):
            try:
                db = await get_db()
                await db.disconnect()
            except Exception:
                pass

        # Build application
        self.application = (
            Application.builder()
            .token(self.config.BOT_TOKEN)
            .post_init(_post_init)
            .post_shutdown(_post_shutdown)
            .build()
        )

        # Register handlers
        self._register_handlers()

        logger.info("Bot is running (polling)...")
        # ✅ الصحيح مع PTB v20+
        self.application.run_polling()

    def _register_handlers(self):
        assert self.application is not None

        # Commands
        self.application.add_handler(
            CommandHandler("start", commands.start)
        )
        self.application.add_handler(
            CommandHandler("admin", admin_handlers.admin_command)
        )

        # Text messages (ReplyKeyboard)
        self.application.add_handler(
            MessageHandler(
                filters
