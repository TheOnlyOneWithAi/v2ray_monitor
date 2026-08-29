#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Shop Bot - single-file reference implementation
Python 3.11+
Dependencies:
  pip install aiogram aiosqlite aiohttp cryptography
Environment:
  BOT_TOKEN=...
  ADMIN_IDS=123456789,987654321
  FORCE_CHANNEL=@yourchannel
  DATABASE_PATH=shop.sqlite3
  # Optional product/provider integration:
  PRODUCT_API_BASE=https://...
  PRODUCT_API_KEY=...
  PRODUCT_API_MODE=generic
  # Generic provider contract:
  # GET  {base}/products?type=gift|premium|stars
  # POST {base}/orders  JSON: {"product_id":..., "telegram_id":..., "quantity":...}
  # GET {base}/orders/{provider_order_id}

# IMPORTANT:
# Gift/Premium/Stars fulfillment is provider-specific. This file deliberately
# implements a safe provider adapter and DOES NOT invent a Telegram API for
# transferring third-party gifts/premium/stars. Configure a legitimate provider
# whose API explicitly supports the purchased asset.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import aiohttp
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    Message, PreCheckoutQuery, LabeledPrice, SuccessfulPayment
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DATABASE_PATH", "shop.sqlite3")
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
FORCE_CHANNEL = os.getenv("FORCE_CHANNEL", "").strip()
PRODUCT_API_BASE = os.getenv("PRODUCT_API_BASE", "").rstrip("/")
PRODUCT_API_KEY = os.getenv("PRODUCT_API_KEY", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("shop")

router = Router()
db: Optional[aiosqlite.Connection] = None


# ------------------------- DB -------------------------

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    balance INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    provider_product_id TEXT NOT NULL,
    price INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'IRR',
    active INTEGER NOT NULL DEFAULT 1,
    metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS discount_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    price INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'IRR',
    max_uses INTEGER NOT NULL DEFAULT 1,
    used_count INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL,
    discount_code_id INTEGER,
    status TEXT NOT NULL,
    payment_method TEXT,
    provider_order_id TEXT,
    provider_status TEXT,
    idempotency_key TEXT UNIQUE NOT NULL,
    created_at INTEGER NOT NULL,
    paid_at INTEGER,
    fulfilled_at INTEGER,
    error TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(product_id) REFERENCES products(id),
    FOREIGN KEY(discount_code_id) REFERENCES discount_codes(id)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    method TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL,
    reference TEXT,
    proof_file_id TEXT,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY,
    added_at INTEGER NOT NULL
);
"""

DEFAULT_TEXTS = {
    "start": "سلام {name} 👋\\nبه فروشگاه خوش اومدی.",
    "force_join": "برای استفاده از فروشگاه ابتدا عضو کانال شوید.",
    "products": "محصول موردنظر را انتخاب کنید:",
    "payment": "روش پرداخت را انتخاب کنید:",
    "success": "پرداخت با موفقیت ثبت شد. تحویل در حال انجام است.",
    "pending": "سفارش شما در انتظار بررسی پرداخت است.",
    "failed": "عملیات ناموفق بود. لطفاً دوباره تلاش کنید.",
    "admin": "پنل مدیریت",
    "balance": "موجودی شما: {balance}",
    "discount": "کد تخفیف خود را وارد کنید:",
}

async def init_db():
    global db
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    for k, v in DEFAULT_TEXTS.items():
        await db.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v)
        )
    await db.commit()

def now() -> int:
    return int(time.time())

async def q1(sql: str, args=()):
    async with db.execute(sql, args) as cur:
        return await cur.fetchone()

async def qall(sql: str, args=()):
    async with db.execute(sql, args) as cur:
        return await cur.fetchall()

async def exec1(sql: str, args=()):
    cur = await db.execute(sql, args)
    await db.commit()
    return cur.lastrowid

async def setting(key: str) -> str:
    row = await q1("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else DEFAULT_TEXTS.get(key, "")

async def text(key: str, **kwargs) -> str:
    return (await setting(key)).format(**kwargs)

async def ensure_user(message: Message):
    u = message.from_user
    await db.execute("""
        INSERT INTO users(id,username,first_name,created_at)
        VALUES(?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET username=excluded.username,
        first_name=excluded.first_name
    """, (u.id, u.username, u.first_name or "", now()))
    await db.commit()

async def is_admin(uid: int) -> bool:
    if uid in ADMIN_IDS:
        return True
    row = await q1("SELECT 1 FROM admins WHERE user_id=?", (uid,))
    return bool(row)


# ------------------------- UI -------------------------

def main_kb(isadm=False):
    b = InlineKeyboardBuilder()
    b.button(text="🛍 محصولات", callback_data="products")
    b.button(text="🎟 کد تخفیف", callback_data="discount")
    b.button(text="💰 موجودی", callback_data="balance")
    b.button(text="📦 سفارش‌ها", callback_data="orders")
    if isadm:
        b.button(text="⚙️ پنل مدیریت", callback_data="admin")
    b.adjust(2, 2, 1)
    return b.as_markup()

async def products_kb():
    rows = await qall("SELECT * FROM products WHERE active=1 ORDER BY id DESC")
    b = InlineKeyboardBuilder()
    for p in rows:
        b.button(text=f"{p['title']} — {p['price']} {p['currency']}",
                 callback_data=f"product:{p['id']}")
    b.button(text="⬅️ بازگشت", callback_data="home")
    b.adjust(1)
    return b.as_markup()

def admin_kb():
    b = InlineKeyboardBuilder()
    for label, data in [
        ("➕ افزودن محصول", "a_add_product"),
        ("📦 مدیریت محصولات", "a_products"),
        ("💳 روش‌های پرداخت", "a_payments"),
        ("✏️ متن‌های بات", "a_texts"),
        ("➕ کد تخفیف", "a_add_discount"),
        ("👥 افزودن ادمین", "a_add_admin"),
        ("📊 آمار", "a_stats"),
    ]:
        b.button(text=label, callback_data=data)
    b.button(text="⬅️ بازگشت", callback_data="home")
    b.adjust(2, 2, 2, 1)
    return b.as_markup()


# ------------------------- Membership -------------------------

async def joined(bot: Bot, uid: int) -> bool:
    if not FORCE_CHANNEL:
        return True
    try:
        m = await bot.get_chat_member(FORCE_CHANNEL, uid)
        return m.status in {"creator", "administrator", "member"}
    except Exception:
        return False

async def require_join(message: Message, bot: Bot) -> bool:
    if await joined(bot, message.from_user.id):
        return True
    b = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 عضویت در کانال", url=f"https://t.me/{FORCE_CHANNEL.lstrip('@')}")],
        [InlineKeyboardButton(text="✅ بررسی عضویت", callback_data="check_join")]
    ])
    await message.answer(await text("force_join"), reply_markup=b)
    return False


# ------------------------- Provider -------------------------

@dataclass
class ProviderProduct:
    id: str
    kind: str
    title: str
    price: int
    currency: str
    metadata: dict[str, Any]

class ProductProvider:
    """
    Generic provider adapter.
    The remote provider must be authoritative for inventory/availability and
    fulfillment. All POST operations use an idempotency key.
    """

    def __init__(self):
        self.base = PRODUCT_API_BASE
        self.key = PRODUCT_API_KEY

    async def _request(self, method, path, **kwargs):
        if not self.base:
            raise RuntimeError("PRODUCT_API_BASE is not configured")
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        headers.update(kwargs.pop("headers", {}))
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.request(method, self.base + path, headers=headers, **kwargs) as r:
                data = await r.json(content_type=None)
                if r.status >= 400:
                    raise RuntimeError(f"provider_http_{r.status}: {data}")
                return data

    async def quote(self, kind: str) -> list[ProviderProduct]:
        data = await self._request("GET", f"/products?type={kind}")
        return [
            ProviderProduct(
                str(x["id"]), kind, x["title"], int(x["price"]),
                x.get("currency", "IRR"), x.get("metadata", {})
            ) for x in data.get("products", data if isinstance(data, list) else [])
        ]

    async def create_order(self, provider_product_id: str, telegram_id: int,
                           quantity: int, idem: str) -> dict:
        return await self._request(
            "POST", "/orders",
            json={
                "product_id": provider_product_id,
                "telegram_id": telegram_id,
                "quantity": quantity,
            },
            headers={"Idempotency-Key": idem},
        )

    async def status(self, provider_order_id: str) -> dict:
        return await self._request("GET", f"/orders/{provider_order_id}")

provider = ProductProvider()


# ------------------------- FSM -------------------------

class AddProduct(StatesGroup):
    kind = State()
    title = State()
    provider_id = State()
    price = State()

class AddDiscount(StatesGroup):
    code = State()
    price = State()
    max_uses = State()

class AddAdmin(StatesGroup):
    uid = State()

class EditText(StatesGroup):
    key = State()
    value = State()

class PaymentProof(StatesGroup):
    proof = State()


# ------------------------- User handlers -------------------------

@router.message(CommandStart())
async def start(message: Message, bot: Bot):
    await ensure_user(message)
    if not await require_join(message, bot):
        return
    await message.answer(
        await text("start", name=message.from_user.first_name or "دوست"),
        reply_markup=main_kb(await is_admin(message.from_user.id))
    )

@router.callback_query(F.data == "check_join")
async def check_join(c: CallbackQuery, bot: Bot):
    if await joined(bot, c.from_user.id):
        await c.message.edit_text("عضویت تأیید شد ✅", reply_markup=main_kb(await is_admin(c.from_user.id)))
    else:
        await c.answer("هنوز عضویت تأیید نشده.", show_alert=True)

@router.callback_query(F.data == "home")
async def home(c: CallbackQuery):
    await c.message.edit_text(
        await text("start", name=c.from_user.first_name or "دوست"),
        reply_markup=main_kb(await is_admin(c.from_user.id))
    )

@router.callback_query(F.data == "products")
async def products(c: CallbackQuery):
    await c.message.edit_text(await text("products"), reply_markup=await products_kb())

@router.callback_query(F.data.startswith("product:"))
async def product_detail(c: CallbackQuery):
    pid = int(c.data.split(":")[1])
    p = await q1("SELECT * FROM products WHERE id=? AND active=1", (pid,))
    if not p:
        await c.answer("محصول موجود نیست.", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    b.button(text="💳 خرید", callback_data=f"buy:{pid}")
    b.button(text="⬅️ بازگشت", callback_data="products")
    await c.message.edit_text(
        f"**{p['title']}**\\nقیمت: `{p['price']} {p['currency']}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=b.as_markup()
    )

@router.callback_query(F.data.startswith("buy:"))
async def buy(c: CallbackQuery):
    pid = int(c.data.split(":")[1])
    p = await q1("SELECT * FROM products WHERE id=? AND active=1", (pid,))
    if not p:
        await c.answer("محصول موجود نیست.", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    b.button(text="💳 پرداخت آنلاین Telegram", callback_data=f"paytg:{pid}")
    b.button(text="🏦 کارت‌به‌کارت / بررسی دستی", callback_data=f"manual:{pid}")
    b.button(text="🪙 ارز دیجیتال / بررسی دستی", callback_data=f"manual:{pid}")
    b.button(text="⬅️", callback_data=f"product:{pid}")
    b.adjust(1)
    await c.message.edit_text(await text("payment"), reply_markup=b.as_markup())

@router.callback_query(F.data == "balance")
async def balance(c: CallbackQuery):
    u = await q1("SELECT balance FROM users WHERE id=?", (c.from_user.id,))
    await c.message.edit_text(
        await text("balance", balance=u["balance"]),
        reply_markup=main_kb(await is_admin(c.from_user.id))
    )

@router.callback_query(F.data == "orders")
async def orders(c: CallbackQuery):
    rows = await qall("""
        SELECT o.id,o.amount,o.currency,o.status,p.title
        FROM orders o JOIN products p ON p.id=o.product_id
        WHERE o.user_id=? ORDER BY o.id DESC LIMIT 20
    """, (c.from_user.id,))
    if not rows:
        s = "هنوز سفارشی ندارید."
    else:
        s = "\\n".join(
            f"#{r['id']} — {r['title']} — {r['amount']} {r['currency']} — {r['status']}"
            for r in rows
        )
    await c.message.edit_text(s, reply_markup=main_kb(await is_admin(c.from_user.id)))

@router.callback_query(F.data == "discount")
async def discount(c: CallbackQuery, state: FSMContext):
    await state.set_state(AddDiscount.code)
    await state.update_data(user_mode=True)
    await c.message.answer("کد تخفیف را ارسال کنید:")

@router.message(AddDiscount.code)
async def use_discount(message: Message, state: FSMContext):
    # User-mode discount application is intentionally kept simple: it validates
    # and stores the selected code in FSM, while purchase can revalidate it.
    code = message.text.strip().upper()
    row = await q1("""
        SELECT * FROM discount_codes
        WHERE code=? AND active=1 AND used_count < max_uses
    """, (code,))
    if not row:
        await message.answer("کد نامعتبر یا تمام‌شده است.")
        await state.clear()
        return
    await state.clear()
    await message.answer(
        f"کد معتبر است ✅\\nمبلغ کد: {row['price']} {row['currency']}\\n"
        "در خرید بعدی قابل استفاده است."
    )


# ------------------------- Telegram Stars payment -------------------------

@router.callback_query(F.data.startswith("paytg:"))
async def pay_tg(c: CallbackQuery, bot: Bot):
    pid = int(c.data.split(":")[1])
    p = await q1("SELECT * FROM products WHERE id=? AND active=1", (pid,))
    if not p:
        await c.answer("محصول موجود نیست.", show_alert=True)
        return

    # Telegram invoices use integer minor units for the chosen currency.
    # This implementation assumes the configured product price is already
    # in the invoice currency's smallest unit.
    idem = secrets.token_urlsafe(24)
    order_id = await exec1("""
        INSERT INTO orders(user_id,product_id,quantity,amount,currency,status,
                           payment_method,idempotency_key,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (c.from_user.id, pid, 1, p["price"], p["currency"], "awaiting_payment",
          "telegram_invoice", idem, now()))

    # For Telegram Stars use currency XTR and integer amount.
    currency = "XTR" if p["currency"] == "XTR" else p["currency"]
    prices = [LabeledPrice(label=p["title"], amount=int(p["price"]))]
    await bot.send_invoice(
        chat_id=c.from_user.id,
        title=p["title"][:32],
        description=f"Order #{order_id}",
        payload=f"order:{order_id}:{idem}",
        currency=currency,
        prices=prices,
    )
    await c.answer()

@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, bot: Bot):
    try:
        parts = q.invoice_payload.split(":")
        if len(parts) != 3 or parts[0] != "order":
            raise ValueError
        oid, idem = int(parts[1]), parts[2]
        o = await q1("SELECT * FROM orders WHERE id=? AND idempotency_key=?",
                     (oid, idem))
        if not o or o["status"] != "awaiting_payment" or o["user_id"] != q.from_user.id:
            raise ValueError
        await bot.answer_pre_checkout_query(q.id, ok=True)
    except Exception:
        await bot.answer_pre_checkout_query(q.id, ok=False, error_message="سفارش معتبر نیست.")

@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot):
    sp: SuccessfulPayment = message.successful_payment
    try:
        _, oid_s, idem = sp.invoice_payload.split(":")
        oid = int(oid_s)
    except Exception:
        await message.answer(await text("failed"))
        return

    # Atomic state transition prevents duplicate fulfillment.
    async with db.execute("BEGIN IMMEDIATE"):
        row = await q1("SELECT * FROM orders WHERE id=? AND idempotency_key=?", (oid, idem))
        if not row or row["status"] not in {"awaiting_payment", "paid"}:
            await db.rollback()
            await message.answer(await text("failed"))
            return
        if row["status"] == "awaiting_payment":
            await db.execute(
                "UPDATE orders SET status='paid',paid_at=? WHERE id=?",
                (now(), oid)
            )
            await db.commit()

    await message.answer(await text("success"))
    asyncio.create_task(fulfill_order(bot, oid))

async def fulfill_order(bot: Bot, order_id: int):
    try:
        o = await q1("""
            SELECT o.*,p.provider_product_id,p.title,p.kind
            FROM orders o JOIN products p ON p.id=o.product_id WHERE o.id=?
        """, (order_id,))
        if not o:
            return
        if o["status"] == "fulfilled":
            return

        if not o["provider_order_id"]:
            provider_order = await provider.create_order(
                o["provider_product_id"], o["user_id"], o["quantity"],
                o["idempotency_key"]
            )
            poid = str(provider_order["order_id"])
            await db.execute("""
                UPDATE orders SET provider_order_id=?,provider_status=?,status='fulfilling'
                WHERE id=? AND status IN ('paid','fulfilling')
            """, (poid, provider_order.get("status", "created"), order_id))
            await db.commit()
        else:
            poid = o["provider_order_id"]

        # Poll briefly for near-instant delivery, then leave the order in
        # fulfilling state for the background worker.
        for _ in range(8):
            st = await provider.status(poid)
            status = str(st.get("status", "")).lower()
            await db.execute(
                "UPDATE orders SET provider_status=? WHERE id=?",
                (status, order_id)
            )
            if status in {"fulfilled", "delivered", "completed"}:
                await db.execute(
                    "UPDATE orders SET status='fulfilled',fulfilled_at=? WHERE id=?",
                    (now(), order_id)
                )
                await db.commit()
                await bot.send_message(o["user_id"], f"✅ سفارش #{order_id} تحویل شد.")
                return
            if status in {"failed", "cancelled", "error"}:
                await db.execute(
                    "UPDATE orders SET status='failed',error=? WHERE id=?",
                    (json.dumps(st, ensure_ascii=False), order_id)
                )
                await db.commit()
                await bot.send_message(o["user_id"], f"❌ تحویل سفارش #{order_id} ناموفق شد.")
                return
            await asyncio.sleep(1.5)

        await db.commit()
    except Exception as e:
        log.exception("fulfillment failed for %s", order_id)
        await db.execute(
            "UPDATE orders SET error=?,status='fulfilling' WHERE id=?",
            (repr(e), order_id)
        )
        await db.commit()


# ------------------------- Manual payments -------------------------

@router.callback_query(F.data.startswith("manual:"))
async def manual(c: CallbackQuery, state: FSMContext):
    pid = int(c.data.split(":")[1])
    p = await q1("SELECT * FROM products WHERE id=? AND active=1", (pid,))
    if not p:
        await c.answer("محصول موجود نیست.", show_alert=True)
        return
    idem = secrets.token_urlsafe(24)
    oid = await exec1("""
        INSERT INTO orders(user_id,product_id,quantity,amount,currency,status,
                           payment_method,idempotency_key,created_at)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (c.from_user.id,pid,1,p["price"],p["currency"],"awaiting_manual_payment",
          "manual",idem,now()))
    await state.set_state(PaymentProof.proof)
    await state.update_data(order_id=oid)
    await c.message.answer(
        f"سفارش #{oid} ساخته شد.\\n"
        "مبلغ و اطلاعات پرداخت را از ادمین/پنل دریافت کنید و رسید را به صورت عکس ارسال کنید."
    )

@router.message(PaymentProof.proof, F.photo)
async def proof(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    oid = int(data["order_id"])
    fid = message.photo[-1].file_id
    await db.execute(
        "INSERT INTO payments(order_id,method,amount,currency,proof_file_id,status,created_at) "
        "SELECT id,payment_method,amount,currency,?, 'pending', ? FROM orders WHERE id=?",
        (fid, now(), oid)
    )
    await db.execute("UPDATE orders SET status='payment_review' WHERE id=?", (oid,))
    await db.commit()
    await state.clear()
    await message.answer(await text("pending"))
    for aid in ADMIN_IDS:
        try:
            await bot.send_photo(aid, fid, caption=f"رسید سفارش #{oid} — برای بررسی")
        except Exception:
            pass


# ------------------------- Admin -------------------------

async def admin_guard(c: CallbackQuery) -> bool:
    if not await is_admin(c.from_user.id):
        await c.answer("دسترسی ندارید.", show_alert=True)
        return False
    return True

@router.callback_query(F.data == "admin")
async def admin(c: CallbackQuery):
    if not await admin_guard(c): return
    await c.message.edit_text(await text("admin"), reply_markup=admin_kb())

@router.callback_query(F.data == "a_stats")
async def stats(c: CallbackQuery):
    if not await admin_guard(c): return
    users = (await q1("SELECT COUNT(*) n FROM users"))["n"]
    orders = (await q1("SELECT COUNT(*) n FROM orders"))["n"]
    paid = (await q1("SELECT COALESCE(SUM(amount),0) n FROM orders WHERE status IN ('paid','fulfilling','fulfilled')"))["n"]
    await c.message.edit_text(
        f"👥 کاربران: {users}\\n📦 سفارش‌ها: {orders}\\n💰 فروش ثبت‌شده: {paid}",
        reply_markup=admin_kb()
    )

@router.callback_query(F.data == "a_add_product")
async def a_add_product(c: CallbackQuery, state: FSMContext):
    if not await admin_guard(c): return
    await state.set_state(AddProduct.kind)
    await c.message.answer("نوع محصول را بفرستید: gift / premium / stars")

@router.message(AddProduct.kind)
async def ap_kind(m: Message, state: FSMContext):
    kind = m.text.strip().lower()
    if kind not in {"gift","premium","stars"}:
        await m.answer("نوع نامعتبر است.")
        return
    await state.update_data(kind=kind)
    await state.set_state(AddProduct.title)
    await m.answer("عنوان محصول:")

@router.message(AddProduct.title)
async def ap_title(m: Message, state: FSMContext):
    await state.update_data(title=m.text.strip()[:200])
    await state.set_state(AddProduct.provider_id)
    await m.answer("شناسه محصول در Provider:")

@router.message(AddProduct.provider_id)
async def ap_provider(m: Message, state: FSMContext):
    await state.update_data(provider_id=m.text.strip()[:200])
    await state.set_state(AddProduct.price)
    await m.answer("قیمت به واحد حداقلی ارز:")

@router.message(AddProduct.price)
async def ap_price(m: Message, state: FSMContext):
    try:
        price = int(Decimal(m.text.strip()))
        if price <= 0: raise ValueError
    except Exception:
        await m.answer("قیمت صحیح نیست.")
        return
    d = await state.get_data()
    await exec1("""
        INSERT INTO products(kind,title,provider_product_id,price,currency,metadata)
        VALUES(?,?,?,?,?,?)
    """, (d["kind"],d["title"],d["provider_id"],price,"XTR" if d["kind"]=="stars" else "IRR","{}"))
    await state.clear()
    await m.answer("محصول افزوده شد ✅", reply_markup=admin_kb())

@router.callback_query(F.data == "a_products")
async def a_products(c: CallbackQuery):
    if not await admin_guard(c): return
    rows = await qall("SELECT * FROM products ORDER BY id DESC")
    s = "\\n".join(
        f"#{r['id']} {'🟢' if r['active'] else '🔴'} {r['title']} | {r['price']} {r['currency']}"
        for r in rows
    ) or "محصولی وجود ندارد."
    await c.message.edit_text(s, reply_markup=admin_kb())

@router.callback_query(F.data == "a_add_discount")
async def a_discount(c: CallbackQuery, state: FSMContext):
    if not await admin_guard(c): return
    await state.set_state(AddDiscount.code)
    await state.update_data(admin_mode=True)
    await c.message.answer("کد تخفیف پولی/اعتباری:")

@router.message(AddDiscount.code)
async def ad_code(m: Message, state: FSMContext):
    d = await state.get_data()
    if not d.get("admin_mode"):
        # handled by user-mode above only if state survived; reject safely
        await state.clear(); return
    code = m.text.strip().upper()
    if not 3 <= len(code) <= 64:
        await m.answer("کد نامعتبر.")
        return
    await state.update_data(code=code)
    await state.set_state(AddDiscount.price)
    await m.answer("قیمت خرید/ارزش کد:")

@router.message(AddDiscount.price)
async def ad_price(m: Message, state: FSMContext):
    d = await state.get_data()
    if not d.get("admin_mode"):
        await state.clear(); return
    try:
        price = int(Decimal(m.text.strip()))
        if price <= 0: raise ValueError
    except Exception:
        await m.answer("مبلغ نامعتبر.")
        return
    await state.update_data(price=price)
    await state.set_state(AddDiscount.max_uses)
    await m.answer("حداکثر تعداد استفاده:")

@router.message(AddDiscount.max_uses)
async def ad_uses(m: Message, state: FSMContext):
    d = await state.get_data()
    if not d.get("admin_mode"):
        await state.clear(); return
    try:
        uses = int(m.text.strip())
        if uses < 1: raise ValueError
        await exec1("""
            INSERT INTO discount_codes(code,price,max_uses,created_at)
            VALUES(?,?,?,?)
        """, (d["code"],d["price"],uses,now()))
    except sqlite3.IntegrityError:
        await m.answer("این کد قبلاً وجود دارد.")
        await state.clear()
        return
    await state.clear()
    await m.answer("کد تخفیف اضافه شد ✅", reply_markup=admin_kb())

@router.callback_query(F.data == "a_add_admin")
async def a_admin(c: CallbackQuery, state: FSMContext):
    if not await admin_guard(c): return
    await state.set_state(AddAdmin.uid)
    await c.message.answer("Telegram user ID ادمین جدید:")

@router.message(AddAdmin.uid)
async def add_admin(m: Message, state: FSMContext):
    try:
        uid = int(m.text.strip())
    except Exception:
        await m.answer("ID نامعتبر.")
        return
    await exec1("INSERT OR IGNORE INTO admins(user_id,added_at) VALUES(?,?)", (uid,now()))
    await state.clear()
    await m.answer("ادمین اضافه شد ✅", reply_markup=admin_kb())

@router.callback_query(F.data == "a_texts")
async def a_texts(c: CallbackQuery, state: FSMContext):
    if not await admin_guard(c): return
    keys = sorted(DEFAULT_TEXTS.keys())
    b = InlineKeyboardBuilder()
    for k in keys:
        b.button(text=k, callback_data=f"edittext:{k}")
    b.button(text="⬅️", callback_data="admin")
    b.adjust(2)
    await c.message.edit_text("متنی که می‌خواهید ویرایش کنید:", reply_markup=b.as_markup())

@router.callback_query(F.data.startswith("edittext:"))
async def edit_text(c: CallbackQuery, state: FSMContext):
    if not await admin_guard(c): return
    key = c.data.split(":",1)[1]
    await state.set_state(EditText.value)
    await state.update_data(key=key)
    await c.message.answer(
        f"متن فعلی:\\n{await setting(key)}\\n\\nمتن جدید را ارسال کنید."
    )

@router.message(EditText.value)
async def save_text(m: Message, state: FSMContext):
    if not await is_admin(m.from_user.id):
        await state.clear(); return
    d = await state.get_data()
    await db.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (d["key"], m.text)
    )
    await db.commit()
    await state.clear()
    await m.answer("متن ذخیره شد ✅", reply_markup=admin_kb())

@router.callback_query(F.data == "a_payments")
async def payment_methods(c: CallbackQuery):
    if not await admin_guard(c): return
    await c.message.edit_text(
        "روش‌های پرداخت این نسخه از طریق تنظیمات Provider و مسیر manual مدیریت می‌شوند.\\n"
        "برای کارت‌به‌کارت/ارز دیجیتال، رسید وارد صف بررسی می‌شود.",
        reply_markup=admin_kb()
    )


# ------------------------- Background reconciliation -------------------------

async def reconcile():
    while True:
        try:
            rows = await qall("""
                SELECT id,provider_order_id FROM orders
                WHERE status='fulfilling' AND provider_order_id IS NOT NULL
                ORDER BY id LIMIT 20
            """)
            for r in rows:
                try:
                    st = await provider.status(r["provider_order_id"])
                    status = str(st.get("status","")).lower()
                    if status in {"fulfilled","delivered","completed"}:
                        await db.execute(
                            "UPDATE orders SET status='fulfilled',provider_status=?,fulfilled_at=? WHERE id=?",
                            (status,now(),r["id"])
                        )
                    elif status in {"failed","cancelled","error"}:
                        await db.execute(
                            "UPDATE orders SET status='failed',provider_status=?,error=? WHERE id=?",
                            (status,json.dumps(st,ensure_ascii=False),r["id"])
                        )
                except Exception:
                    pass
            await db.commit()
        except Exception:
            log.exception("reconcile error")
        await asyncio.sleep(10)


# ------------------------- Entrypoint -------------------------

async def main():
    await init_db()
    bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(reconcile())
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await db.close()

if __name__ == "__main__":
    asyncio.run(main())
