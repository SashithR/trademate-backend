from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime
from db import Base


class Shop(Base):
    __tablename__ = "shops"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    business_type = Column(String, nullable=False)


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)

    name = Column(String, nullable=False)
    unit = Column(String, nullable=False)              # bottle, kg, packet
    sell_price = Column(Float, nullable=False)

    stock_qty = Column(Float, nullable=False, default=0.0)  # supports 0.5, 0.25


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    shop_id = Column(Integer, ForeignKey("shops.id"), nullable=False)

    type = Column(String, nullable=False)              # SALE, PURCHASE
    total_amount = Column(Float, nullable=False, default=0.0)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False)


class TransactionItem(Base):
    __tablename__ = "transaction_items"

    id = Column(Integer, primary_key=True, index=True)

    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)

    qty = Column(Float, nullable=False)                # supports 0.5, 0.25
    unit_price = Column(Float, nullable=False)
    line_total = Column(Float, nullable=False)
