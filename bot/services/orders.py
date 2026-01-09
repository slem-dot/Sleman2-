from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from bot.storage import JSONStorage
from bot.utils.constants import STATUS_PENDING

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

async def create_order(storage: JSONStorage, order_type: str, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
    orders_obj = await storage.get_orders()
    next_id = int(orders_obj.get("next_id", 1))
    order = {
        "id": next_id,
        "type": order_type,
        "status": STATUS_PENDING,
        "user_id": int(user_id),
        "data": data,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    orders = list(orders_obj.get("orders", []))
    orders.append(order)
    orders_obj["orders"] = orders
    orders_obj["next_id"] = next_id + 1
    await storage.save_orders(orders_obj)
    return order

async def list_pending(storage: JSONStorage, limit: int = 20) -> list[dict[str, Any]]:
    orders_obj = await storage.get_orders()
    orders = list(orders_obj.get("orders", []))
    pending = [o for o in orders if o.get("status") == STATUS_PENDING]
    pending.sort(key=lambda x: int(x.get("id", 0)))
    return pending[:limit]

async def get_order(storage: JSONStorage, order_id: int) -> dict[str, Any] | None:
    orders_obj = await storage.get_orders()
    for o in orders_obj.get("orders", []):
        if int(o.get("id", 0)) == int(order_id):
            return o
    return None

async def update_order(storage: JSONStorage, order_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    orders_obj = await storage.get_orders()
    orders = list(orders_obj.get("orders", []))
    updated = None
    for i, o in enumerate(orders):
        if int(o.get("id", 0)) == int(order_id):
            o.update(patch)
            o["updated_at"] = _now_iso()
            orders[i] = o
            updated = o
            break
    if updated is not None:
        orders_obj["orders"] = orders
        await storage.save_orders(orders_obj)
    return updated
