# ===== models.py =====
from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Boolean, UniqueConstraint
from db import Base


class Shop(Base):
    __tablename__ = "shops"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    business_type = Column(String, nullable=False)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    username = Column(String, nullable=False, unique=True, index=True)
    email = Column(String, nullable=False, unique=True, index=True)

    password_hash = Column(String, nullable=False)
    password_salt = Column(String, nullable=False)

    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, nullable=False, unique=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class TelegramLinkToken(Base):
    __tablename__ = "telegram_link_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, nullable=False, unique=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)

    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)

    used_at = Column(DateTime, nullable=True)


class TelegramLink(Base):
    __tablename__ = "telegram_links"

    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(String, nullable=False, unique=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)

    chat_id = Column(String, nullable=True)
    linked_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("telegram_user_id", name="uq_telegram_user_id"),
    )


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)

    name = Column(String, nullable=False)
    unit = Column(String, nullable=False)

    sell_price = Column(Float, nullable=False)
    cost_price = Column(Float, nullable=False, default=0.0)

    stock_qty = Column(Float, nullable=False, default=0.0)

    is_active = Column(Boolean, nullable=False, default=True)

    alert_qty = Column(Float, nullable=False, default=0.0)


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)

    type = Column(String, nullable=False)  # SALE, PURCHASE
    total_amount = Column(Float, nullable=False, default=0.0)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)

    is_void = Column(Boolean, nullable=False, default=False)
    void_reason = Column(String, nullable=True)
    void_at = Column(DateTime, nullable=True)


class TransactionItem(Base):
    __tablename__ = "transaction_items"

    id = Column(Integer, primary_key=True, index=True)

    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)

    qty = Column(Float, nullable=False)
    unit_price = Column(Float, nullable=False)
    line_total = Column(Float, nullable=False)