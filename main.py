import asyncio
import os
import sys

# ==============================
# FIX PYTHON PATH FOR RAILWAY
# ==============================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# إذا كان المشروع داخل مجلد (مثل Almokhtar/)
PROJECT_DIR = os.path.join(BASE_DIR, "Almokhtar")

if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# الآن الاستيراد سيعمل
from bot.main import main

if __name__ == "__main__":
    asyncio.run(main())
