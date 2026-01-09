from __future__ import annotations
from bot.storage import JSONStorage

async def ensure_wallet(storage: JSONStorage, user_id: int) -> dict[str, int]:
    wallet = await storage.get_wallet()
    uid = str(user_id)
    if uid not in wallet:
        wallet[uid] = {"balance": 0, "hold": 0}
        await storage.save_wallet(wallet)
    return wallet[uid]

async def get_wallet(storage: JSONStorage, user_id: int) -> dict[str, int]:
    w = await ensure_wallet(storage, user_id)
    w["balance"] = max(0, int(w.get("balance", 0)))
    w["hold"] = max(0, int(w.get("hold", 0)))
    return w

async def add_balance(storage: JSONStorage, user_id: int, amount: int) -> dict[str, int]:
    wallet = await storage.get_wallet()
    uid = str(user_id)
    w = wallet.get(uid, {"balance": 0, "hold": 0})
    w["balance"] = max(0, int(w.get("balance", 0)) + int(amount))
    w["hold"] = max(0, int(w.get("hold", 0)))
    wallet[uid] = w
    await storage.save_wallet(wallet)
    return w

async def reserve_withdraw(storage: JSONStorage, user_id: int, amount: int) -> tuple[bool, dict[str, int], str]:
    if amount <= 0:
        return False, await get_wallet(storage, user_id), "amount_invalid"

    wallet = await storage.get_wallet()
    uid = str(user_id)
    w = wallet.get(uid, {"balance": 0, "hold": 0})
    balance = int(w.get("balance", 0))
    hold = int(w.get("hold", 0))

    if balance < amount:
        return False, {"balance": balance, "hold": hold}, "insufficient"

    balance -= amount
    hold += amount

    w = {"balance": max(0, balance), "hold": max(0, hold)}
    wallet[uid] = w
    await storage.save_wallet(wallet)
    return True, w, "ok"

async def release_hold(storage: JSONStorage, user_id: int, amount: int) -> dict[str, int]:
    wallet = await storage.get_wallet()
    uid = str(user_id)
    w = wallet.get(uid, {"balance": 0, "hold": 0})
    balance = int(w.get("balance", 0))
    hold = int(w.get("hold", 0))

    hold = max(0, hold - amount)
    balance = max(0, balance + amount)

    w = {"balance": balance, "hold": hold}
    wallet[uid] = w
    await storage.save_wallet(wallet)
    return w

async def finalize_withdraw(storage: JSONStorage, user_id: int, amount: int) -> dict[str, int]:
    wallet = await storage.get_wallet()
    uid = str(user_id)
    w = wallet.get(uid, {"balance": 0, "hold": 0})
    balance = int(w.get("balance", 0))
    hold = int(w.get("hold", 0))

    hold = max(0, hold - amount)

    w = {"balance": max(0, balance), "hold": hold}
    wallet[uid] = w
    await storage.save_wallet(wallet)
    return w
