#!/usr/bin/env python3
"""
Ichancy Telegram Bot - Main Entry Point
"""

import logging
from dotenv import load_dotenv

load_dotenv()

from bot.bot import IchancyBot  # noqa: E402

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def main():
    try:
        logger.info("Starting Ichancy Telegram Bot...")
        IchancyBot().run()
    except Exception as e:
        logger.exception(f"Failed to start bot: {e}")
        raise

if __name__ == '__main__':
    main()
