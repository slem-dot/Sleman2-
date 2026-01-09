import asyncio
import os
import sys
import importlib.util

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# المسار الحقيقي لمجلد bot
BOT_MAIN_PATH = os.path.join(BASE_DIR, "Almokhtar", "bot", "main.py")

if not os.path.exists(BOT_MAIN_PATH):
    raise RuntimeError(f"bot.main.py not found at: {BOT_MAIN_PATH}")

# تحميل bot.main يدوياً
spec = importlib.util.spec_from_file_location("bot.main", BOT_MAIN_PATH)
bot_main = importlib.util.module_from_spec(spec)
sys.modules["bot.main"] = bot_main
spec.loader.exec_module(bot_main)

if __name__ == "__main__":
    asyncio.run(bot_main.main())
