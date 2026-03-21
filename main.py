# ===== main.py =====
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Tuple
from datetime import datetime, timedelta, timezone
from sqlalchemy import func
import io
import secrets
import hashlib
import hmac

from db import Base, engine, SessionLocal
from models import (
    Shop, Product, Transaction, TransactionItem,
    User, AuthToken, TelegramLink, TelegramLinkToken
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Trade Mate API")
APP_TZ = timezone(timedelta(hours=5, minutes=30))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------
# Helpers: password + auth
# ---------------------------
def _pbkdf2_hash(password: str, salt_hex: str) -> str:
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return dk.hex()

def _make_password(password: str) -> Tuple[str, str]:
    salt = secrets.token_bytes(16).hex()
    ph = _pbkdf2_hash(password, salt)
    return ph, salt

def _verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    calc = _pbkdf2_hash(password, password_salt)
    return hmac.compare_digest(calc, password_hash)

def _now() -> datetime:
    return datetime.now(APP_TZ).replace(tzinfo=None)

def _make_token() -> str:
    return secrets.token_urlsafe(32)

def _get_user_from_auth(db, authorization: Optional[str]) -> User:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    token = parts[1].strip()
    row = db.query(AuthToken).filter(AuthToken.token == token).first()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid token")

    if row.expires_at <= _now():
        raise HTTPException(status_code=401, detail="Token expired")

    user = db.get(User, row.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user

def get_current_user(authorization: Optional[str] = Header(default=None)):
    db = SessionLocal()
    try:
        user = _get_user_from_auth(db, authorization)
        return user
    finally:
        db.close()

# ---------------------------
# Helpers: stock status
# ---------------------------
def _stock_status(stock_qty: float, alert_qty: float) -> str:
    stock_qty = float(stock_qty or 0.0)
    alert_qty = float(alert_qty or 0.0)

    if alert_qty <= 0:
        return "GREEN"
    if stock_qty <= alert_qty:
        return "RED"
    if stock_qty <= alert_qty * 2:
        return "YELLOW"
    return "GREEN"


# ---------------------------
# Pydantic Models
# ---------------------------
class SignUpRequest(BaseModel):
    username: str
    email: str
    password: str
    shop_name: str
    shop_type: str

class SignInRequest(BaseModel):
    username_or_email: str
    password: str

class AuthResponse(BaseModel):
    token: str
    user_id: int
    username: str
    email: str
    shop_id: int
    shop_name: str
    shop_type: str

class TelegramLinkTokenResponse(BaseModel):
    link_token: str
    expires_at: str

class TelegramConsumeTokenRequest(BaseModel):
    link_token: str
    telegram_user_id: str
    chat_id: Optional[str] = None

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
    alert_qty: float = 0.0

class TxItemIn(BaseModel):
    product_id: int
    qty: float
    unit_price: float
    sell_price: Optional[float] = None
    alert_qty: Optional[float] = None

class TxCreate(BaseModel):
    shop_id: int
    note: Optional[str] = None
    items: List[TxItemIn]

class StockAdjustRequest(BaseModel):
    shop_id: int
    product_id: int
    delta_qty: float
    reason: Optional[str] = None

class AlertQtyUpdate(BaseModel):
    shop_id: int
    alert_qty: float

class CashOutCreate(BaseModel):
    shop_id: int
    amount: float
    note: Optional[str] = None
 


# ---------------------------
# AUTH: Signup / Signin / Me
# ---------------------------
@app.post("/auth/signup", response_model=AuthResponse)
def auth_signup(payload: SignUpRequest):
    db = SessionLocal()
    try:
        u = (payload.username or "").strip()
        e = (payload.email or "").strip().lower()
        p = payload.password or ""
        shop_name = (payload.shop_name or "").strip()
        shop_type = (payload.shop_type or "").strip()

        if len(u) < 3:
            raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
        if "@" not in e or "." not in e:
            raise HTTPException(status_code=400, detail="Enter a valid email")
        if len(p) < 4:
            raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
        if len(shop_name) < 2:
            raise HTTPException(status_code=400, detail="Shop name is required")
        if len(shop_type) < 2:
            raise HTTPException(status_code=400, detail="Shop type is required")

        if db.query(User).filter(User.username == u).first():
            raise HTTPException(status_code=400, detail="Username already exists")
        if db.query(User).filter(User.email == e).first():
            raise HTTPException(status_code=400, detail="Email already exists")

        shop = Shop(name=shop_name, business_type=shop_type)
        db.add(shop)
        db.flush()

        ph, salt = _make_password(p)
        user = User(username=u, email=e, password_hash=ph, password_salt=salt, shop_id=shop.id)
        db.add(user)
        db.flush()

        token = _make_token()
        now = _now()
        exp = now + timedelta(days=7)
        db.add(AuthToken(token=token, user_id=user.id, created_at=now, expires_at=exp))

        db.commit()

        return AuthResponse(
            token=token,
            user_id=user.id,
            username=user.username,
            email=user.email,
            shop_id=shop.id,
            shop_name=shop.name,
            shop_type=shop.business_type,
        )
    finally:
        db.close()

@app.post("/auth/login", response_model=AuthResponse)
def auth_login(payload: SignInRequest):
    db = SessionLocal()
    try:
        key = (payload.username_or_email or "").strip()
        password = payload.password or ""

        user = None
        if "@" in key:
            user = db.query(User).filter(User.email == key.lower()).first()
        else:
            user = db.query(User).filter(User.username == key).first()

        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if not _verify_password(password, user.password_hash, user.password_salt):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        shop = db.get(Shop, user.shop_id)
        if not shop:
            raise HTTPException(status_code=500, detail="Shop not found for user")

        token = _make_token()
        now = _now()
        exp = now + timedelta(days=7)
        db.add(AuthToken(token=token, user_id=user.id, created_at=now, expires_at=exp))
        db.commit()

        return AuthResponse(
            token=token,
            user_id=user.id,
            username=user.username,
            email=user.email,
            shop_id=shop.id,
            shop_name=shop.name,
            shop_type=shop.business_type,
        )
    finally:
        db.close()

@app.get("/auth/me", response_model=AuthResponse)
def auth_me(current_user: User = Depends(get_current_user), authorization: Optional[str] = Header(default=None)):
    db = SessionLocal()
    try:
        shop = db.get(Shop, current_user.shop_id)
        if not shop:
            raise HTTPException(status_code=500, detail="Shop not found")

        parts = authorization.split()
        token = parts[1].strip()

        return AuthResponse(
            token=token,
            user_id=current_user.id,
            username=current_user.username,
            email=current_user.email,
            shop_id=shop.id,
            shop_name=shop.name,
            shop_type=shop.business_type,
        )
    finally:
        db.close()


# ---------------------------
# TELEGRAM LINKING
# ---------------------------
@app.post("/auth/telegram/link-token", response_model=TelegramLinkTokenResponse)
def create_telegram_link_token(current_user: User = Depends(get_current_user)):
    db = SessionLocal()
    try:
        token = _make_token()
        now = _now()
        exp = now + timedelta(minutes=10)

        row = TelegramLinkToken(
            token=token,
            user_id=current_user.id,
            shop_id=current_user.shop_id,
            created_at=now,
            expires_at=exp,
            used_at=None,
        )
        db.add(row)
        db.commit()

        return TelegramLinkTokenResponse(
            link_token=token,
            expires_at=exp.isoformat(),
        )
    finally:
        db.close()

@app.post("/telegram/consume-link-token")
def telegram_consume_link_token(payload: TelegramConsumeTokenRequest):
    db = SessionLocal()
    try:
        tok = (payload.link_token or "").strip()
        if not tok:
            raise HTTPException(status_code=400, detail="Missing link_token")

        row = db.query(TelegramLinkToken).filter(TelegramLinkToken.token == tok).first()
        if not row:
            raise HTTPException(status_code=404, detail="Invalid link token")

        now = _now()
        if row.expires_at <= now:
            raise HTTPException(status_code=400, detail="Link token expired")

        if row.used_at is not None:
            raise HTTPException(status_code=400, detail="Link token already used")

        tg_user_id = (payload.telegram_user_id or "").strip()
        if not tg_user_id:
            raise HTTPException(status_code=400, detail="Missing telegram_user_id")

        existing = db.query(TelegramLink).filter(TelegramLink.telegram_user_id == tg_user_id).first()
        if existing:
            existing.shop_id = row.shop_id
            existing.chat_id = payload.chat_id or existing.chat_id
            existing.linked_at = now
        else:
            db.add(TelegramLink(
                telegram_user_id=tg_user_id,
                shop_id=row.shop_id,
                chat_id=payload.chat_id,
                linked_at=now,
            ))

        row.used_at = now

        db.commit()
        return {"ok": True, "shop_id": row.shop_id}
    finally:
        db.close()

@app.get("/telegram/shop")
def telegram_get_shop(telegram_user_id: str):
    db = SessionLocal()
    try:
        tg_user_id = (telegram_user_id or "").strip()
        if not tg_user_id:
            raise HTTPException(status_code=400, detail="Missing telegram_user_id")

        link = db.query(TelegramLink).filter(TelegramLink.telegram_user_id == tg_user_id).first()
        if not link:
            return {"linked": False}

        shop = db.get(Shop, link.shop_id)
        return {
            "linked": True,
            "shop_id": link.shop_id,
            "shop_name": shop.name if shop else None,
            "shop_type": shop.business_type if shop else None,
        }
    finally:
        db.close()


# ---------------------------
# Shops
# ---------------------------
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


# ---------------------------
# Products
# ---------------------------
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
        if payload.stock_qty < 0:
            raise HTTPException(status_code=400, detail="Stock qty must be >= 0")
        if payload.alert_qty < 0:
            raise HTTPException(status_code=400, detail="Alert qty must be >= 0")

        p = Product(
            shop_id=payload.shop_id,
            name=payload.name,
            unit=payload.unit,
            sell_price=payload.sell_price,
            cost_price=payload.cost_price,
            stock_qty=payload.stock_qty,
            is_active=True,
            alert_qty=float(payload.alert_qty or 0.0),
        )
        db.add(p)
        db.commit()
        db.refresh(p)

        return {
            "id": p.id,
            "name": p.name,
            "unit": p.unit,
            "sell_price": float(p.sell_price),
            "cost_price": float(p.cost_price),
            "stock_qty": float(p.stock_qty),
            "alert_qty": float(p.alert_qty or 0.0),
            "stock_status": _stock_status(p.stock_qty, p.alert_qty),
        }
    finally:
        db.close()

@app.get("/products")
def list_products(shop_id: int):
    db = SessionLocal()
    try:
        products = (
            db.query(Product)
            .filter(Product.shop_id == shop_id, Product.is_active == True)
            .all()
        )
        return [
            {
                "id": p.id,
                "name": p.name,
                "unit": p.unit,
                "sell_price": float(p.sell_price),
                "cost_price": float(p.cost_price),
                "stock_qty": float(p.stock_qty),
                "alert_qty": float(p.alert_qty or 0.0),
                "stock_status": _stock_status(p.stock_qty, p.alert_qty),
            }
            for p in products
        ]
    finally:
        db.close()

@app.delete("/products/{product_id}")
def delete_product(product_id: int, shop_id: int):
    db = SessionLocal()
    try:
        p = (
            db.query(Product)
            .filter(
                Product.id == product_id,
                Product.shop_id == shop_id,
                Product.is_active == True,
            )
            .first()
        )
        if not p:
            raise HTTPException(status_code=404, detail="Product not found")

        p.is_active = False
        db.commit()
        return {"ok": True, "deleted": True, "product_id": p.id}
    finally:
        db.close()

@app.put("/products/{product_id}/alert")
def update_product_alert(product_id: int, payload: AlertQtyUpdate):
    db = SessionLocal()
    try:
        if payload.alert_qty < 0:
            raise HTTPException(status_code=400, detail="Alert qty must be >= 0")

        p = (
            db.query(Product)
            .filter(
                Product.id == product_id,
                Product.shop_id == payload.shop_id,
                Product.is_active == True,
            )
            .first()
        )
        if not p:
            raise HTTPException(status_code=404, detail="Product not found")

        p.alert_qty = float(payload.alert_qty)
        db.commit()
        db.refresh(p)

        return {
            "ok": True,
            "product_id": p.id,
            "name": p.name,
            "alert_qty": float(p.alert_qty),
            "stock_qty": float(p.stock_qty),
            "stock_status": _stock_status(p.stock_qty, p.alert_qty),
        }
    finally:
        db.close()


# ---------------------------
# Stock Adjust
# ---------------------------
@app.post("/stock/adjust")
def adjust_stock(payload: StockAdjustRequest):
    db = SessionLocal()
    try:
        product = (
            db.query(Product)
            .filter(
                Product.shop_id == payload.shop_id,
                Product.id == payload.product_id,
                Product.is_active == True,
            )
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
            "alert_qty": float(product.alert_qty or 0.0),
            "stock_status": _stock_status(product.stock_qty, product.alert_qty),
        }
    finally:
        db.close()

@app.get("/stock/low")
def low_stock(shop_id: int):
    db = SessionLocal()
    try:
        products = (
            db.query(Product)
            .filter(Product.shop_id == shop_id, Product.is_active == True)
            .order_by(Product.stock_qty.asc())
            .all()
        )

        result = []
        for p in products:
            a = float(p.alert_qty or 0.0)
            if a > 0 and float(p.stock_qty) <= a:
                result.append({
                    "product_id": p.id,
                    "name": p.name,
                    "unit": p.unit,
                    "stock_qty": float(p.stock_qty),
                    "alert_qty": float(p.alert_qty),
                    "stock_status": _stock_status(p.stock_qty, p.alert_qty),
                })

        return result
    finally:
        db.close()


# ---------------------------
# Transactions: Sales & Purchases
# ---------------------------
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
        created_at=_now(),
        is_void=False,
    )
    db.add(tx)
    db.flush()

    total = 0.0

    for it in items:
        if it.qty <= 0:
            raise HTTPException(status_code=400, detail="Qty must be > 0")
        if it.unit_price < 0:
            raise HTTPException(status_code=400, detail="Unit price must be >= 0")
        if it.alert_qty is not None and it.alert_qty < 0:
            raise HTTPException(status_code=400, detail="Alert qty must be >= 0")

        product = db.get(Product, it.product_id)
        if not product or product.shop_id != shop_id or product.is_active == False:
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

            if it.alert_qty is not None:
                product.alert_qty = float(it.alert_qty)

        else:
            raise HTTPException(status_code=400, detail="Invalid transaction type")

        line_total = float(it.qty) * float(it.unit_price)
        total += line_total

        db.add(TransactionItem(
            transaction_id=tx.id,
            product_id=product.id,
            qty=float(it.qty),
            unit_price=float(it.unit_price),
            line_total=float(line_total),
        ))

    tx.total_amount = float(total)
    db.commit()
    db.refresh(tx)

    return {"transaction_id": tx.id, "type": tx.type, "total_amount": float(tx.total_amount)}

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


@app.post("/cash-out")
def create_cash_out(payload: CashOutCreate):
    db = SessionLocal()
    try:
        if payload.amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be > 0")

        shop = db.get(Shop, payload.shop_id)
        if not shop:
            raise HTTPException(status_code=404, detail="Shop not found")

        tx = Transaction(
            shop_id=payload.shop_id,
            type="CASH_OUT",
            total_amount=float(payload.amount),
            note=payload.note,
            created_at=_now(),
            is_void=False,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        return {
            "transaction_id": tx.id,
            "type": tx.type,
            "total_amount": float(tx.total_amount),
            "note": tx.note,
        }
    finally:
        db.close()


# ---------------------------
# Product Search + Top Products
# ---------------------------
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
                Product.stock_qty.label("stock_qty"),
                Product.alert_qty.label("alert_qty"),
                func.coalesce(func.sum(TransactionItem.qty), 0).label("qty_sold"),
            )
            .join(TransactionItem, TransactionItem.product_id == Product.id)
            .join(Transaction, TransactionItem.transaction_id == Transaction.id)
            .filter(Product.shop_id == shop_id)
            .filter(Product.is_active == True)
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
                .filter(Product.shop_id == shop_id, Product.is_active == True)
                .order_by(Product.id.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "product_id": p.id,
                    "name": p.name,
                    "unit": p.unit,
                    "sell_price": float(p.sell_price),
                    "cost_price": float(p.cost_price),
                    "stock_qty": float(p.stock_qty),
                    "alert_qty": float(p.alert_qty or 0.0),
                    "stock_status": _stock_status(p.stock_qty, p.alert_qty),
                    "qty_sold": 0.0,
                }
                for p in products
            ]

        return [
            {
                "product_id": r.product_id,
                "name": r.name,
                "unit": r.unit,
                "sell_price": float(r.sell_price),
                "cost_price": float(r.cost_price),
                "stock_qty": float(r.stock_qty),
                "alert_qty": float(r.alert_qty or 0.0),
                "stock_status": _stock_status(r.stock_qty, r.alert_qty),
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
            .filter(Product.shop_id == shop_id, Product.is_active == True)
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
                "sell_price": float(p.sell_price),
                "cost_price": float(p.cost_price),
                "stock_qty": float(p.stock_qty),
                "alert_qty": float(p.alert_qty or 0.0),
                "stock_status": _stock_status(p.stock_qty, p.alert_qty),
            }
            for p in products
        ]
    finally:
        db.close()


# ---------------------------
# Reports (summary, detail, pdf)
# ---------------------------
def _period_range(period: str):
    period = (period or "").lower().strip()
    today = _now().date()

    if period == "daily":
        start_d = today
        end_d = today
    elif period == "weekly":
        start_d = today - timedelta(days=6)
        end_d = today
    elif period == "monthly":
        start_d = today.replace(day=1)
        end_d = today
    else:
        raise HTTPException(status_code=400, detail="Invalid period. Use daily|weekly|monthly")

    start_dt = datetime.combine(start_d, datetime.min.time())
    end_dt = datetime.combine(end_d + timedelta(days=1), datetime.min.time())
    return period, start_d, end_d, start_dt, end_dt

@app.get("/reports/summary")
def reports_summary(shop_id: int, period: str = "daily"):
    db = SessionLocal()
    try:
        period, start_d, end_d, start_dt, end_dt = _period_range(period)

        sales_total = (
            db.query(func.coalesce(func.sum(Transaction.total_amount), 0.0))
            .filter(
                Transaction.shop_id == shop_id,
                Transaction.type == "SALE",
                Transaction.is_void == False,
                Transaction.created_at >= start_dt,
                Transaction.created_at < end_dt,
            )
            .scalar()
            or 0.0
        )

        purchases_total = (
            db.query(func.coalesce(func.sum(Transaction.total_amount), 0.0))
            .filter(
                Transaction.shop_id == shop_id,
                Transaction.type == "PURCHASE",
                Transaction.is_void == False,
                Transaction.created_at >= start_dt,
                Transaction.created_at < end_dt,
            )
            .scalar()
            or 0.0
        )

        cash_out_total = (
            db.query(func.coalesce(func.sum(Transaction.total_amount), 0.0))
            .filter(
                Transaction.shop_id == shop_id,
                Transaction.type == "CASH_OUT",
                Transaction.is_void == False,
                Transaction.created_at >= start_dt,
                Transaction.created_at < end_dt,
            )
            .scalar()
            or 0.0
        )

        sale_ids = (
            db.query(Transaction.id)
            .filter(
                Transaction.shop_id == shop_id,
                Transaction.type == "SALE",
                Transaction.is_void == False,
                Transaction.created_at >= start_dt,
                Transaction.created_at < end_dt,
            )
            .all()
        )
        sale_ids = [x[0] for x in sale_ids]

        profit = 0.0
        if sale_ids:
            items = (
                db.query(TransactionItem.product_id, TransactionItem.qty, TransactionItem.unit_price)
                .filter(TransactionItem.transaction_id.in_(sale_ids))
                .all()
            )
            for product_id, qty, unit_price in items:
                p = db.get(Product, product_id)
                if not p:
                    continue
                profit += (float(unit_price) - float(p.cost_price)) * float(qty)

        return {
            "shop_id": shop_id,
            "period": period,
            "start_date": start_d.isoformat(),
            "end_date": end_d.isoformat(),
            "sales_total": float(sales_total),
            "purchases_total": float(purchases_total),
            "cash_out_total": float(cash_out_total),
            "profit": float(profit),
            "net_cash": float(sales_total) - float(purchases_total) - float(cash_out_total),
        }
    finally:
        db.close()

@app.get("/reports/detail")
def reports_detail(shop_id: int, period: str = "daily"):
    db = SessionLocal()
    try:
        period, start_d, end_d, start_dt, end_dt = _period_range(period)

        txs = (
            db.query(Transaction)
            .filter(
                Transaction.shop_id == shop_id,
                Transaction.is_void == False,
                Transaction.created_at >= start_dt,
                Transaction.created_at < end_dt,
            )
            .order_by(Transaction.created_at.asc(), Transaction.id.asc())
            .all()
        )

        result = []
        for t in txs:
            items = (
                db.query(TransactionItem)
                .filter(TransactionItem.transaction_id == t.id)
                .all()
            )

            detailed_items = []
            for it in items:
                p = db.get(Product, it.product_id)
                detailed_items.append({
                    "product_id": it.product_id,
                    "product_name": p.name if p else f"Product #{it.product_id}",
                    "qty": float(it.qty),
                    "unit_price": float(it.unit_price),
                    "line_total": float(it.line_total),
                })

            result.append({
                "id": t.id,
                "type": t.type,
                "total_amount": float(t.total_amount),
                "note": t.note,
                "created_at": t.created_at.isoformat(),
                "items": detailed_items,
            })

        return {
            "shop_id": shop_id,
            "period": period,
            "start_date": start_d.isoformat(),
            "end_date": end_d.isoformat(),
            "transactions": result,
        }
    finally:
        db.close()

@app.get("/reports/pdf")
def reports_pdf(shop_id: int, period: str = "daily"):
    db = SessionLocal()
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        shop = db.get(Shop, shop_id)
        if not shop:
            raise HTTPException(status_code=404, detail="Shop not found")

        period, start_d, end_d, start_dt, end_dt = _period_range(period)
        summary = reports_summary(shop_id=shop_id, period=period)
        detail = reports_detail(shop_id=shop_id, period=period)

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        width, height = A4

        def fmt_dt(iso_str: str):
            try:
                dt = datetime.fromisoformat(iso_str)
            except Exception:
                return ("-", "-")
            return (dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"))

        def new_page(title: str):
            c.showPage()
            yy = height - 50
            c.setFont("Helvetica-Bold", 12)
            c.drawString(40, yy, title)
            yy -= 18
            return yy

        y = height - 50
        c.setFont("Helvetica-Bold", 18)
        c.drawString(40, y, "Trade Mate Report")
        y -= 26

        c.setFont("Helvetica", 12)
        c.drawString(40, y, f"Shop: {shop.name} ({shop.business_type})")
        y -= 16
        c.drawString(40, y, f"Period: {period.title()}  |  From: {start_d.isoformat()}  To: {end_d.isoformat()}")
        y -= 22

        c.setFont("Helvetica-Bold", 13)
        c.drawString(40, y, "Summary")
        y -= 18

        c.setFont("Helvetica", 11)
        c.drawString(40, y, f"Sales Total: {summary['sales_total']:.2f}")
        y -= 14
        c.drawString(40, y, f"Purchases Total: {summary['purchases_total']:.2f}")
        y -= 14
        c.drawString(40, y, f"Cash Out Total: {summary['cash_out_total']:.2f}")
        y -= 14
        c.drawString(40, y, f"Profit (estimated): {summary['profit']:.2f}")
        y -= 14
        c.drawString(40, y, f"Net Cash: {summary['net_cash']:.2f}")
        y -= 22

        c.setFont("Helvetica-Bold", 13)
        c.drawString(40, y, "Transactions (Detailed)")
        y -= 18

        tx_col = {"id": 40, "type": 90, "date": 170, "time": 255, "total": 345, "note": 430}

        def draw_tx_header(ypos: float):
            c.setFont("Helvetica-Bold", 10)
            c.drawString(tx_col["id"], ypos, "Tx")
            c.drawString(tx_col["type"], ypos, "Type")
            c.drawString(tx_col["date"], ypos, "Date")
            c.drawString(tx_col["time"], ypos, "Time")
            c.drawRightString(tx_col["total"] + 70, ypos, "Total")
            c.drawString(tx_col["note"], ypos, "Note")
            ypos -= 6
            c.setLineWidth(1)
            c.line(40, ypos, width - 40, ypos)
            ypos -= 10
            return ypos

        item_col = {"name": 60, "qty": 290, "unit": 350, "ptotal": 460}

        def draw_item_header(ypos: float):
            c.setFont("Helvetica-Bold", 9)
            c.drawString(item_col["name"], ypos, "Product")
            c.drawRightString(item_col["qty"] + 30, ypos, "Qty")
            c.drawRightString(item_col["unit"] + 50, ypos, "Unit price")
            c.drawRightString(item_col["ptotal"] + 80, ypos, "Product total")
            ypos -= 10
            c.setFont("Helvetica", 9)
            return ypos

        y = draw_tx_header(y)

        txs = detail.get("transactions", [])
        if not txs:
            c.setFont("Helvetica", 11)
            c.drawString(40, y, "No transactions found for this period.")
            y -= 20
        else:
            for tx in txs:
                if y < 140:
                    y = new_page("Transactions (Continued)")
                    y = draw_tx_header(y)

                tx_date, tx_time = fmt_dt(tx["created_at"])
                note_text = tx.get("note") or "-"
                if len(note_text) > 18:
                    note_text = note_text[:18] + "..."

                c.setFont("Helvetica", 10)
                c.drawString(tx_col["id"], y, f"#{tx['id']}")
                c.drawString(tx_col["type"], y, tx["type"])
                c.drawString(tx_col["date"], y, tx_date)
                c.drawString(tx_col["time"], y, tx_time)
                c.drawRightString(tx_col["total"] + 70, y, f"{tx['total_amount']:.2f}")
                c.drawString(tx_col["note"], y, note_text)
                y -= 14

                if y < 110:
                    y = new_page("Transactions (Continued)")
                    y = draw_tx_header(y)

                items = tx.get("items", [])

                if tx["type"] != "CASH_OUT" and items:
                    y = draw_item_header(y)

                    for it in items:
                        if y < 90:
                            y = new_page("Transactions (Continued)")
                            y = draw_tx_header(y)
                            y = draw_item_header(y)

                        pname = it.get("product_name", "Unknown")
                        if len(pname) > 34:
                            pname = pname[:34] + "..."

                        qty = float(it["qty"])
                        unit_price = float(it["unit_price"])
                        product_total = float(it["line_total"])

                        c.setFont("Helvetica", 9)
                        c.drawString(item_col["name"], y, f"• {pname}")
                        c.drawRightString(item_col["qty"] + 30, y, f"{qty:g}")
                        c.drawRightString(item_col["unit"] + 50, y, f"{unit_price:.2f}")
                        c.drawRightString(item_col["ptotal"] + 80, y, f"{product_total:.2f}")
                        y -= 12

                y -= 8
        c.showPage()
        c.save()

        buf.seek(0)
        filename = f"trade_mate_report_{shop_id}_{period}_{start_d.isoformat()}_{end_d.isoformat()}.pdf"
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}

        return StreamingResponse(buf, media_type="application/pdf", headers=headers)
    finally:
        db.close()