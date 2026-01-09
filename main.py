import asyncio
import os
import sys

# ✅ Ensure project root is on PYTHONPATH (important on Railway)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from bot.main import main

if __name__ == "__main__":
    asyncio.run(main())
