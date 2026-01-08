"""Configuration management for the bot"""

import os
from typing import List
from dataclasses import dataclass

@dataclass
class BotConfig:
    # Telegram
    BOT_TOKEN: str
    SUPER_ADMIN_ID: int
    REQUIRED_CHANNEL: str
    SUPPORT_USERNAME: str

    # Database
    DATABASE_URL: str

    # Business rules
    MIN_TOPUP: int = 10000
    MIN_WITHDRAW: int = 50000
    SYRIATEL_CODES: List[str] = None

    # Optional
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    def __post_init__(self):
        self.SUPER_ADMIN_ID = int(self.SUPER_ADMIN_ID)
        self.MIN_TOPUP = int(self.MIN_TOPUP)
        self.MIN_WITHDRAW = int(self.MIN_WITHDRAW)

        if self.SYRIATEL_CODES is None:
            self.SYRIATEL_CODES = []
        elif isinstance(self.SYRIATEL_CODES, str):
            self.SYRIATEL_CODES = [c.strip() for c in self.SYRIATEL_CODES.split(",") if c.strip()]

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            BOT_TOKEN=os.getenv("BOT_TOKEN", ""),
            SUPER_ADMIN_ID=os.getenv("SUPER_ADMIN_ID", "0"),
            REQUIRED_CHANNEL=os.getenv("REQUIRED_CHANNEL", "@broichancy"),
            SUPPORT_USERNAME=os.getenv("SUPPORT_USERNAME", "@BroBot_Support"),
            DATABASE_URL=os.getenv("DATABASE_URL", ""),
            MIN_TOPUP=os.getenv("MIN_TOPUP", "10000"),
            MIN_WITHDRAW=os.getenv("MIN_WITHDRAW", "50000"),
            SYRIATEL_CODES=os.getenv("SYRIATEL_CODES", ""),
            DEBUG=os.getenv("DEBUG", "False").lower() == "true",
            LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
        )

    def validate(self) -> bool:
        required = [self.BOT_TOKEN, self.SUPER_ADMIN_ID, self.REQUIRED_CHANNEL, self.SUPPORT_USERNAME, self.DATABASE_URL]
        return all(required)
