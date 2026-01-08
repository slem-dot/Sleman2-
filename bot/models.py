"""SQLAlchemy models for database tables"""

from datetime import datetime
import uuid

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, CheckConstraint, text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    user_id = Column(BigInteger, primary_key=True)
    username = Column(String(255))
    first_name = Column(String(255), nullable=False)
    last_name = Column(String(255))
    joined_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    last_seen = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    is_active = Column(Boolean, default=True)
    is_banned = Column(Boolean, default=False)
    ban_reason = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))

    wallet = relationship("Wallet", back_populates="user", uselist=False)
    eish_account = relationship("EishAccount", back_populates="user", uselist=False)
    orders = relationship("Order", back_populates="user")
    admin_role = relationship("AdminRole", back_populates="user", uselist=False)

class Wallet(Base):
    __tablename__ = "wallets"
    user_id = Column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    balance = Column(BigInteger, default=0)
    hold = Column(BigInteger, default=0)
    created_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        CheckConstraint("balance >= 0", name="check_balance_non_negative"),
        CheckConstraint("hold >= 0", name="check_hold_non_negative"),
    )
    user = relationship("User", back_populates="wallet")

class EishAccount(Base):
    __tablename__ = "eish_accounts"
    user_id = Column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    eish_username = Column(String(255), nullable=False, unique=True)
    eish_password = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    deleted_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    user = relationship("User", back_populates="eish_account")

class Order(Base):
    __tablename__ = "orders"
    order_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    type = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    payload = Column(JSONB)
    amount = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    admin_id = Column(BigInteger, ForeignKey("users.user_id"))
    admin_message_ids = Column(JSONB)

    __table_args__ = (
        CheckConstraint("type IN ('bot_topup','bot_withdraw','eish_topup','eish_withdraw')", name="check_order_type"),
        CheckConstraint("status IN ('pending','approved','rejected','canceled')", name="check_order_status"),
        CheckConstraint("amount > 0", name="check_amount_positive"),
    )

    user = relationship("User", back_populates="orders")
    admin = relationship("User", foreign_keys=[admin_id])

class SyriatelCode(Base):
    __tablename__ = "syriatel_codes"
    code = Column(String(50), primary_key=True)
    is_active = Column(Boolean, default=True)
    note = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))

class AdminRole(Base):
    __tablename__ = "admin_roles"
    user_id = Column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
    granted_by = Column(BigInteger, ForeignKey("users.user_id"))

    __table_args__ = (CheckConstraint("role IN ('super','admin')", name="check_admin_role"),)

    user = relationship("User", back_populates="admin_role")
    granter = relationship("User", foreign_keys=[granted_by])

class EishPool(Base):
    __tablename__ = "eish_pool"
    id = Column(Integer, primary_key=True)
    username = Column(String(255), nullable=False, unique=True)
    password = Column(String(255), nullable=False)
    status = Column(String(20), default="available")
    assigned_to = Column(BigInteger, ForeignKey("users.user_id"))
    assigned_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (CheckConstraint("status IN ('available','assigned')", name="check_eish_pool_status"),)

class Maintenance(Base):
    __tablename__ = "maintenance"
    id = Column(Integer, primary_key=True, default=1)
    enabled = Column(Boolean, default=False)
    message = Column(Text)
    updated_at = Column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"))
