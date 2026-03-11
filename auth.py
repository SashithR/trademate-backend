from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from db import SessionLocal
from models import User, Shop
from passlib.context import CryptContext

router = APIRouter()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class SignupRequest(BaseModel):
    username: str
    email: str
    password: str
    shop_name: str
    shop_type: str


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/signup")
def signup(payload: SignupRequest):

    db = SessionLocal()

    existing = db.query(User).filter(
        (User.username == payload.username) |
        (User.email == payload.email)
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    shop = Shop(
        name=payload.shop_name,
        business_type=payload.shop_type
    )

    db.add(shop)
    db.commit()
    db.refresh(shop)

    hashed_password = pwd_context.hash(payload.password)

    user = User(
        username=payload.username,
        email=payload.email,
        password=hashed_password,
        shop_id=shop.id
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return {
        "message": "Account created",
        "user_id": user.id,
        "shop_id": shop.id
    }


@router.post("/auth/login")
def login(payload: LoginRequest):

    db = SessionLocal()

    user = db.query(User).filter(
        (User.username == payload.username) |
        (User.email == payload.username)
    ).first()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not pwd_context.verify(payload.password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    return {
        "user_id": user.id,
        "shop_id": user.shop_id
    }


@router.post("/auth/link_telegram")
def link_telegram(user_id: int, telegram_user_id: int):

    db = SessionLocal()

    user = db.get(User, user_id)

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.telegram_user_id = telegram_user_id
    db.commit()

    return {"message": "Telegram linked"}


@router.get("/auth/telegram_shop")
def telegram_shop(telegram_user_id: int):

    db = SessionLocal()

    user = db.query(User).filter(
        User.telegram_user_id == telegram_user_id
    ).first()

    if not user:
        return {"shop_id": None}

    return {"shop_id": user.shop_id}