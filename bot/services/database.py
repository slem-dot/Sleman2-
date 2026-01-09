import logging
from typing import Optional
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import User, Wallet, AdminRole

logger = logging.getLogger(__name__)

async def create_or_update_user(
    session: AsyncSession,
    user_id: int,
    username: Optional[str],
    first_name: str,
    last_name: Optional[str] = None,
) -> User:
    stmt = select(User).where(User.user_id == user_id)
    res = await session.execute(stmt)
    user = res.scalar_one_or_none()

    if user:
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        user.last_seen = datetime.utcnow()
        user.is_active = True
    else:
        user = User(user_id=user_id, username=username, first_name=first_name, last_name=last_name)
        session.add(user)
        session.add(Wallet(user_id=user_id))

    return user

async def is_user_admin(session: AsyncSession, user_id: int) -> bool:
    stmt = select(AdminRole).where(AdminRole.user_id == user_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none() is not None

async def ensure_admin_user(session: AsyncSession, super_admin_id: int) -> None:
    stmt = select(AdminRole).where(AdminRole.user_id == super_admin_id)
    res = await session.execute(stmt)
    admin = res.scalar_one_or_none()
    if not admin:
        session.add(AdminRole(user_id=super_admin_id, role="super", granted_by=super_admin_id))
        logger.info(f"Created super admin role for ID: {super_admin_id}")
