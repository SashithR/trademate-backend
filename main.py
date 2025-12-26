from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy import func

from db import Base, engine, SessionLocal
from models import Shop, Product, Transaction, TransactionItem

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Trade Mate API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



class ShopCreate(BaseModel):
    name: str
    business_type: str


class ProductCreate(BaseModel):
    shop_id: int
    name: str
    unit: str
    sell_price: float
    stock_qty: float


class TxItemIn(BaseModel):
    product_id: int
    qty: float
    unit_price: float


class TxCreate(BaseModel):
    shop_id: int
    note: Optional[str] = None
    items: List[TxItemIn]


# NEW: stock adjust request model
class StockAdjustRequest(BaseModel):
    shop_id: int
    product_id: int
    delta_qty: float
    reason: Optional[str] = None


@app.post("/shops")
def create_shop(payload: ShopCreate):
    db = SessionLocal()
    try:
        shop = Shop(name=payload.name, business_type=payload.business_type)
        db.add(shop)
        db.commit()
        db.refresh(shop)
        return {"id": shop.id, "name": shop.name, "business_type": shop.business_type}
    finally:
        db.close()


@app.post("/products")
def create_product(payload: ProductCreate):
    db = SessionLocal()
    try:
        shop = db.get(Shop, payload.shop_id)
        if not shop:
            raise HTTPException(status_code=404, detail="Shop not found")

        p = Product(
            shop_id=payload.shop_id,
            name=payload.name,
            unit=payload.unit,
            sell_price=payload.sell_price,
            stock_qty=payload.stock_qty,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        return {"id": p.id, "name": p.name, "unit": p.unit, "stock_qty": p.stock_qty}
    finally:
        db.close()


@app.get("/products")
def list_products(shop_id: int):
    db = SessionLocal()
    try:
        products = db.query(Product).filter(Product.shop_id == shop_id).all()
        return [
            {
                "id": p.id,
                "name": p.name,
                "unit": p.unit,
                "sell_price": p.sell_price,
                "stock_qty": p.stock_qty,
            }
            for p in products
        ]
    finally:
        db.close()


# NEW: adjust stock from website
@app.post("/stock/adjust")
def adjust_stock(payload: StockAdjustRequest):
    db = SessionLocal()
    try:
        product = (
            db.query(Product)
            .filter(Product.shop_id == payload.shop_id, Product.id == payload.product_id)
            .first()
        )
        if not product:
            raise HTTPException(status_code=404, detail="Product not found for this shop")

        old_qty = float(product.stock_qty)
        new_qty = old_qty + float(payload.delta_qty)

        if new_qty < 0:
            raise HTTPException(status_code=400, detail="Stock cannot go below zero")

        product.stock_qty = new_qty
        db.commit()
        db.refresh(product)

        return {
            "product_id": product.id,
            "name": product.name,
            "old_stock": old_qty,
            "delta_qty": float(payload.delta_qty),
            "new_stock": float(product.stock_qty),
            "reason": payload.reason,
        }
    finally:
        db.close()


def _create_transaction(db, shop_id: int, tx_type: str, note: Optional[str], items: List[TxItemIn]):
    if not items:
        raise HTTPException(status_code=400, detail="Items required")

    shop = db.get(Shop, shop_id)
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    tx = Transaction(shop_id=shop_id, type=tx_type, note=note, created_at=datetime.utcnow())
    db.add(tx)
    db.flush()  # create tx.id

    total = 0.0

    for it in items:
        if it.qty <= 0:
            raise HTTPException(status_code=400, detail="Qty must be > 0")
        if it.unit_price < 0:
            raise HTTPException(status_code=400, detail="Unit price must be >= 0")

        product = db.get(Product, it.product_id)
        if not product or product.shop_id != shop_id:
            raise HTTPException(status_code=404, detail=f"Product not found: {it.product_id}")

        if tx_type == "SALE":
            if product.stock_qty - it.qty < 0:
                raise HTTPException(status_code=400, detail=f"Not enough stock for {product.name}")
            product.stock_qty -= it.qty

        elif tx_type == "PURCHASE":
            product.stock_qty += it.qty

        else:
            raise HTTPException(status_code=400, detail="Invalid transaction type")

        line_total = it.qty * it.unit_price
        total += line_total

        tx_item = TransactionItem(
            transaction_id=tx.id,
            product_id=product.id,
            qty=it.qty,
            unit_price=it.unit_price,
            line_total=line_total,
        )
        db.add(tx_item)

    tx.total_amount = total
    db.commit()
    db.refresh(tx)

    return {"transaction_id": tx.id, "type": tx.type, "total_amount": tx.total_amount}


@app.get("/products/top")
def top_products(shop_id: int, limit: int = 10):
    db = SessionLocal()
    try:
        rows = (
            db.query(
                Product.id.label("product_id"),
                Product.name.label("name"),
                Product.unit.label("unit"),
                Product.sell_price.label("sell_price"),
                func.coalesce(func.sum(TransactionItem.qty), 0).label("qty_sold"),
            )
            .join(TransactionItem, TransactionItem.product_id == Product.id)
            .join(Transaction, TransactionItem.transaction_id == Transaction.id)
            .filter(Product.shop_id == shop_id)
            .filter(Transaction.shop_id == shop_id)
            .filter(Transaction.type == "SALE")
            .group_by(Product.id)
            .order_by(func.sum(TransactionItem.qty).desc())
            .limit(limit)
            .all()
        )

        if not rows:
            products = (
                db.query(Product)
                .filter(Product.shop_id == shop_id)
                .order_by(Product.id.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "product_id": p.id,
                    "name": p.name,
                    "unit": p.unit,
                    "sell_price": p.sell_price,
                    "qty_sold": 0,
                }
                for p in products
            ]

        return [
            {
                "product_id": r.product_id,
                "name": r.name,
                "unit": r.unit,
                "sell_price": r.sell_price,
                "qty_sold": float(r.qty_sold),
            }
            for r in rows
        ]
    finally:
        db.close()


@app.get("/products/search")
def search_products(shop_id: int, q: str, limit: int = 10):
    db = SessionLocal()
    try:
        q = (q or "").strip()
        products = (
            db.query(Product)
            .filter(Product.shop_id == shop_id)
            .filter(Product.name.ilike(f"%{q}%"))
            .order_by(Product.name.asc())
            .limit(limit)
            .all()
        )
        return [
            {
                "product_id": p.id,
                "name": p.name,
                "unit": p.unit,
                "sell_price": p.sell_price,
                "stock_qty": p.stock_qty,
            }
            for p in products
        ]
    finally:
        db.close()


@app.post("/sales")
def create_sale(payload: TxCreate):
    db = SessionLocal()
    try:
        return _create_transaction(db, payload.shop_id, "SALE", payload.note, payload.items)
    finally:
        db.close()


@app.post("/purchases")
def create_purchase(payload: TxCreate):
    db = SessionLocal()
    try:
        return _create_transaction(db, payload.shop_id, "PURCHASE", payload.note, payload.items)
    finally:
        db.close()


@app.get("/summary/today")
def today_summary(shop_id: int):
    db = SessionLocal()
    try:
        sales = db.query(Transaction).filter(Transaction.shop_id == shop_id, Transaction.type == "SALE").all()
        purchases = db.query(Transaction).filter(Transaction.shop_id == shop_id, Transaction.type == "PURCHASE").all()

        sales_sum = sum(t.total_amount for t in sales)
        purchases_sum = sum(t.total_amount for t in purchases)

        return {
            "shop_id": shop_id,
            "sales_total": sales_sum,
            "purchases_total": purchases_sum,
            "net_cash": sales_sum - purchases_sum,
        }
    finally:
        db.close()
