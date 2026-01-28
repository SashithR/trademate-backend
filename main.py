# ===== main.py =====
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy import func

from db import Base, engine, SessionLocal
from models import Shop, Product, Transaction, TransactionItem

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Trade Mate API")

# CORS for React frontend
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
    cost_price: float
    stock_qty: float


class TxItemIn(BaseModel):
    product_id: int
    qty: float
    unit_price: float                   # PURCHASE: cost price, SALE: sell price
    sell_price: Optional[float] = None  # PURCHASE: required selling price


class TxCreate(BaseModel):
    shop_id: int
    note: Optional[str] = None
    items: List[TxItemIn]


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

        if payload.cost_price < 0:
            raise HTTPException(status_code=400, detail="Cost price must be >= 0")
        if payload.sell_price < 0:
            raise HTTPException(status_code=400, detail="Sell price must be >= 0")

        p = Product(
            shop_id=payload.shop_id,
            name=payload.name,
            unit=payload.unit,
            sell_price=payload.sell_price,
            cost_price=payload.cost_price,
            stock_qty=payload.stock_qty,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        return {
            "id": p.id,
            "name": p.name,
            "unit": p.unit,
            "sell_price": p.sell_price,
            "cost_price": p.cost_price,
            "stock_qty": p.stock_qty,
        }
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
                "cost_price": p.cost_price,
                "stock_qty": p.stock_qty,
            }
            for p in products
        ]
    finally:
        db.close()


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

    tx = Transaction(
        shop_id=shop_id,
        type=tx_type,
        note=note,
        created_at=datetime.utcnow(),
        is_void=False,
    )
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
            if it.sell_price is None:
                raise HTTPException(status_code=400, detail="Sell price is required for purchase")
            if it.sell_price < 0:
                raise HTTPException(status_code=400, detail="Sell price must be >= 0")

            product.stock_qty += it.qty
            product.sell_price = float(it.sell_price)
            product.cost_price = float(it.unit_price)

        else:
            raise HTTPException(status_code=400, detail="Invalid transaction type")

        line_total = float(it.qty) * float(it.unit_price)
        total += line_total

        tx_item = TransactionItem(
            transaction_id=tx.id,
            product_id=product.id,
            qty=float(it.qty),
            unit_price=float(it.unit_price),
            line_total=float(line_total),
        )
        db.add(tx_item)

    tx.total_amount = float(total)
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
                Product.cost_price.label("cost_price"),
                func.coalesce(func.sum(TransactionItem.qty), 0).label("qty_sold"),
            )
            .join(TransactionItem, TransactionItem.product_id == Product.id)
            .join(Transaction, TransactionItem.transaction_id == Transaction.id)
            .filter(Product.shop_id == shop_id)
            .filter(Transaction.shop_id == shop_id)
            .filter(Transaction.type == "SALE", Transaction.is_void == False)
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
                    "cost_price": p.cost_price,
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
                "cost_price": r.cost_price,
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
                "cost_price": p.cost_price,
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
        sales = (
            db.query(Transaction)
            .filter(Transaction.shop_id == shop_id, Transaction.type == "SALE", Transaction.is_void == False)
            .all()
        )
        purchases = (
            db.query(Transaction)
            .filter(Transaction.shop_id == shop_id, Transaction.type == "PURCHASE", Transaction.is_void == False)
            .all()
        )

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


@app.get("/transactions/recent")
def recent_transactions(shop_id: int, limit: int = 10):
    db = SessionLocal()
    try:
        txs = (
            db.query(Transaction)
            .filter(Transaction.shop_id == shop_id)
            .order_by(Transaction.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": t.id,
                "type": t.type,
                "total_amount": t.total_amount,
                "created_at": t.created_at,
                "is_void": t.is_void,
                "note": t.note,
            }
            for t in txs
        ]
    finally:
        db.close()


@app.post("/transactions/{tx_id}/void")
def void_transaction(tx_id: int, shop_id: int, reason: Optional[str] = None):
    db = SessionLocal()
    try:
        tx = (
            db.query(Transaction)
            .filter(Transaction.id == tx_id, Transaction.shop_id == shop_id)
            .first()
        )
        if not tx:
            raise HTTPException(status_code=404, detail="Transaction not found")

        if tx.is_void:
            raise HTTPException(status_code=400, detail="Transaction already void")

        items = db.query(TransactionItem).filter(TransactionItem.transaction_id == tx.id).all()
        if not items:
            raise HTTPException(status_code=400, detail="No items found for this transaction")

        for it in items:
            product = db.get(Product, it.product_id)
            if not product or product.shop_id != shop_id:
                raise HTTPException(status_code=404, detail="Product not found while voiding")

            if tx.type == "SALE":
                product.stock_qty += float(it.qty)

            elif tx.type == "PURCHASE":
                if product.stock_qty - float(it.qty) < 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot void purchase. Stock would go below 0 for {product.name}"
                    )
                product.stock_qty -= float(it.qty)

            else:
                raise HTTPException(status_code=400, detail="Invalid transaction type")

        tx.is_void = True
        tx.void_reason = reason
        tx.void_at = datetime.utcnow()

        db.commit()
        return {"ok": True, "transaction_id": tx.id, "type": tx.type, "voided": True}
    finally:
        db.close()
