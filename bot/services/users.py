from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from telegram import User

from bot.storage import JSONStorage

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

async def create_or_update_user(storage: JSONStorage, tg_user: User) -> dict[str, Any]:
    users = await storage.get_users()
    uid = str(tg_user.id)

    existing = users.get(uid, {})
    created_at = existing.get("created_at") or _now_iso()

    users[uid] = {
        "id": tg_user.id,
        "username": tg_user.username,
        "first_name": tg_user.first_name,
        "last_name": tg_user.last_name,
        "created_at": created_at,
        "updated_at": _now_iso(),
        "ichancy": existing.get("ichancy"),
    }
    await storage.save_users(users)
    return users[uid]

async def set_ichancy_account(storage: JSONStorage, user_id: int, username: str, password: str) -> None:
    users = await storage.get_users()
    uid = str(user_id)
    if uid not in users:
        users[uid] = {"id": user_id, "created_at": _now_iso()}
    users[uid]["ichancy"] = {"username": username, "password": password, "updated_at": _now_iso()}
    users[uid]["updated_at"] = _now_iso()
    await storage.save_users(users)

async def get_user(storage: JSONStorage, user_id: int) -> dict[str, Any] | None:
    users = await storage.get_users()
    return users.get(str(user_id))
