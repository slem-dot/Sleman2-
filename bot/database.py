"""Database connection and session management"""

import logging
from typing import Optional
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from bot.config import BotConfig

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, config: BotConfig):
        self.config = config
        self.engine = None
        self.async_session = None

    async def connect(self):
        db_url = self.config.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
        self.engine = create_async_engine(
            db_url,
            echo=self.config.DEBUG,
            pool_size=10,
            max_overflow=10,
            pool_pre_ping=True,
        )
        self.async_session = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        logger.info("Database connected successfully")

    async def disconnect(self):
        if self.engine:
            await self.engine.dispose()
            logger.info("Database disconnected")

    @asynccontextmanager
    async def get_session(self):
        if not self.async_session:
            raise RuntimeError("Database not connected")
        session = self.async_session()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def health_check(self) -> bool:
        try:
            async with self.get_session() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

_db_instance: Optional[Database] = None

async def init_db(config: BotConfig) -> Database:
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(config)
        await _db_instance.connect()
    return _db_instance

async def get_db() -> Database:
    if _db_instance is None:
        raise RuntimeError("Database not initialized")
    return _db_instance
