#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram Gift Shop Bot — self purchase flow.

A user starts the bot, chooses a Gift, pays with Telegram Stars (XTR), and
that same user's Telegram account receives the Gift. No recipient selection.
"""

import asyncio
import logging
import os
import secrets
import time
from typing import Optional

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DATABASE_PATH", "shop.sqlite3").strip()
FORCE_CHANNEL = os.getenv("FORCE_CHANNEL", "").strip()
GIFT_TEXT = os.getenv("GIFT_TEXT", "Gift delivered by the shop").strip()[:128]
AUTO_SYNC_SECONDS = max(60, int(os.getenv("AUTO_SYNC_SECONDS", "300")))
MARKUP_PERCENT = max(0, int(os.getenv("MARKUP_PERCENT", "10")))
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS must contain at least one Telegram user ID")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gift-shop")
router = Router()
db: Optional[aiosqlite.Connection] = None

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gift_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    star_cost INTEGER NOT NULL,
    sell_price INTEGER NOT NULL,
    remaining_count INTEGER,
    personal_remaining_count INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    gift_id TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'XTR',
    status TEXT NOT NULL,
    payload TEXT UNIQUE NOT NULL,
    charge_id TEXT,
    error TEXT,
    created_at INTEGER NOT NULL,
    paid_at INTEGER,
    fulfilled_at INTEGER,
    FOREIGN KEY(buyer_id) REFERENCES users(id),
    FOREIGN KEY(recipient_id) REFERENCES users(id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);
CREATE INDEX IF NOT EXISTS idx_orders_buyer ON orders(buyer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
"""


def now() -> int:
    return int(time.time())


async def init_db():
    global db
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    await db.commit()


async def q1(sql, args=()):
    async with db.execute(sql, args) as cur:
        return await cur.fetchone()


async def qall(sql, args=()):
    async with db.execute(sql, args) as cur:
        return await cur.fetchall()


async def save_user(user):
    t = now()
    await db.execute(
        """INSERT INTO users(id,username,first_name,created_at,updated_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET username=excluded.username,
           first_name=excluded.first_name, updated_at=excluded.updated_at""",
        (user.id, user.username, user.first_name or "", t, t),
    )
    await db.commit()


async def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def member_ok(bot: Bot, uid: int) -> bool:
    if not FORCE_CHANNEL:
        return True
    try:
        member = await bot.get_chat_member(FORCE_CHANNEL, uid)
        return member.status in {"creator", "administrator", "member"}
    except Exception:
        return False


async def require_join(message: Message, bot: Bot) -> bool:
    if await member_ok(bot, message.from_user.id):
        return True
    channel = FORCE_CHANNEL.lstrip("@")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 عضویت در کانال", url=f"https://t.me/{channel}")],
        [InlineKeyboardButton(text="✅ بررسی عضویت", callback_data="check_join")],
    ])
    await message.answer("ابتدا عضو کانال شوید.", reply_markup=kb)
    return False


def home_kb(admin=False):
    b = InlineKeyboardBuilder()
    b.button(text="🎁 Gifts", callback_data="products")
    b.button(text="📦 سفارش‌های من", callback_data="orders")
    if admin:
        b.button(text="⚙️ مدیریت", callback_data="admin")
    b.adjust(2, 1)
    return b.as_markup()


async def products_kb():
    rows = await qall("SELECT * FROM products WHERE active=1 ORDER BY star_cost ASC, id ASC")
    b = InlineKeyboardBuilder()
    for p in rows:
        stock = "∞" if p["remaining_count"] is None else str(p["remaining_count"])
        b.button(text=f"{p['title']} — {p['sell_price']} ⭐ ({stock})", callback_data=f"product:{p['id']}")
    b.button(text="🔄 بروزرسانی Gifts", callback_data="sync")
    b.button(text="⬅️ خانه", callback_data="home")
    b.adjust(1)
    return b.as_markup()


def admin_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Sync Gifts", callback_data="sync")
    b.button(text="📊 آمار", callback_data="stats")
    b.button(text="💰 موجودی Stars", callback_data="star_balance")
    b.button(text="📦 سفارش‌ها", callback_data="all_orders")
    b.button(text="⬅️ خانه", callback_data="home")
    b.adjust(2, 2, 1)
    return b.as_markup()


async def sync_gifts(bot: Bot) -> int:
    gifts = await bot.get_available_gifts()
    seen = set()
    t = now()
    for gift in gifts.gifts:
        gid = str(gift.id)
        seen.add(gid)
        cost = int(gift.star_count)
        sell = max(cost, (cost * (100 + MARKUP_PERCENT) + 99) // 100)
        remaining = getattr(gift, "remaining_count", None)
        personal = getattr(gift, "personal_remaining_count", None)
        await db.execute(
            """INSERT INTO products(gift_id,title,star_cost,sell_price,remaining_count,
               personal_remaining_count,active,updated_at)
               VALUES(?,?,?,?,?,?,1,?)
               ON CONFLICT(gift_id) DO UPDATE SET title=excluded.title,
               star_cost=excluded.star_cost,sell_price=excluded.sell_price,
               remaining_count=excluded.remaining_count,
               personal_remaining_count=excluded.personal_remaining_count,
               active=1,updated_at=excluded.updated_at""",
            (gid, f"Gift {gid}", cost, sell, remaining, personal, t),
        )
    if seen:
        marks = ",".join("?" for _ in seen)
        await db.execute(f"UPDATE products SET active=0,updated_at=? WHERE gift_id NOT IN ({marks})", (t, *seen))
    else:
        await db.execute("UPDATE products SET active=0,updated_at=?", (t,))
    await db.commit()
    return len(seen)


async def auto_sync(bot: Bot):
    while True:
        try:
            log.info("Gift sync: %s", await sync_gifts(bot))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Gift sync failed")
        await asyncio.sleep(AUTO_SYNC_SECONDS)


@router.message(CommandStart())
async def start(message: Message, bot: Bot):
    await save_user(message.from_user)
    if not await require_join(message, bot):
        return
    await message.answer(
        f"سلام {message.from_user.first_name or 'دوست'} 👋\n\n🎁 فروشگاه Telegram Gifts\n\nGift را انتخاب کن؛ پرداخت را انجام بده و همان Gift مستقیماً برای خودت ارسال می‌شود.",
        reply_markup=home_kb(await is_admin(message.from_user.id)),
    )


@router.message(Command("paysupport"))
async def paysupport(message: Message):
    await message.answer("برای پشتیبانی پرداخت، شماره سفارش و مشکل را ارسال کنید.")


@router.callback_query(F.data == "check_join")
async def check_join(c: CallbackQuery, bot: Bot):
    if await member_ok(bot, c.from_user.id):
        await c.message.edit_text("عضویت تأیید شد ✅", reply_markup=home_kb(await is_admin(c.from_user.id)))
    else:
        await c.answer("هنوز عضویت تأیید نشده.", show_alert=True)


@router.callback_query(F.data == "home")
async def home(c: CallbackQuery):
    await c.message.edit_text("🏠 خانه\n\n🎁 Gift را انتخاب کن و برای خودت بخر.", reply_markup=home_kb(await is_admin(c.from_user.id)))


@router.callback_query(F.data == "products")
async def products(c: CallbackQuery):
    await c.message.edit_text("🎁 Gift موردنظر را انتخاب کن:", reply_markup=await products_kb())


@router.callback_query(F.data == "sync")
async def sync_callback(c: CallbackQuery, bot: Bot):
    if not await is_admin(c.from_user.id):
        await c.answer("دسترسی ندارید.", show_alert=True)
        return
    try:
        n = await sync_gifts(bot)
        await c.answer(f"{n} Gift همگام شد.", show_alert=True)
        await c.message.edit_text("🎁 کاتالوگ بروزرسانی شد.", reply_markup=await products_kb())
    except Exception as e:
        log.exception("manual sync failed")
        await c.answer(f"Sync failed: {e}", show_alert=True)


@router.callback_query(F.data.startswith("product:"))
async def product_detail(c: CallbackQuery):
    pid = int(c.data.split(":", 1)[1])
    p = await q1("SELECT * FROM products WHERE id=? AND active=1", (pid,))
    if not p:
        await c.answer("این Gift دیگر در دسترس نیست.", show_alert=True)
        return
    if p["remaining_count"] is not None and p["remaining_count"] <= 0:
        await c.answer("این Gift تمام شده است.", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    b.button(text="🛒 خرید برای خودم", callback_data=f"buy:{pid}")
    b.button(text="⬅️ بازگشت", callback_data="products")
    b.adjust(1)
    await c.message.edit_text(
        f"🎁 <b>{p['title']}</b>\n\n💰 قیمت: <b>{p['sell_price']} ⭐</b>\n📦 قیمت پایه: {p['star_cost']} ⭐\n\nبعد از پرداخت، Gift مستقیماً برای همین اکانت Telegram ارسال می‌شود.",
        parse_mode=ParseMode.HTML,
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data.startswith("buy:"))
async def buy_self(c: CallbackQuery, bot: Bot):
    """Create an invoice whose recipient is ALWAYS the buyer himself."""
    pid = int(c.data.split(":", 1)[1])
    p = await q1("SELECT * FROM products WHERE id=? AND active=1", (pid,))
    if not p:
        await c.answer("Gift موجود نیست.", show_alert=True)
        return
    if p["remaining_count"] is not None and p["remaining_count"] <= 0:
        await c.answer("این Gift تمام شده است.", show_alert=True)
        return

    # Self purchase: buyer_id == recipient_id == Telegram user who pressed Buy.
    buyer_id = c.from_user.id
    recipient_id = c.from_user.id
    payload = "gift:" + secrets.token_hex(16)

    cur = await db.execute(
        """INSERT INTO orders(buyer_id,recipient_id,product_id,gift_id,amount,currency,
           status,payload,created_at) VALUES(?,?,?,?,?,'XTR','awaiting_payment',?,?)""",
        (buyer_id, recipient_id, pid, p["gift_id"], int(p["sell_price"]), payload, now()),
    )
    await db.commit()
    order_id = cur.lastrowid

    try:
        await bot.send_invoice(
            chat_id=buyer_id,
            title=p["title"][:32],
            description="خرید Gift برای خودت",
            payload=payload,
            currency="XTR",
            prices=[LabeledPrice(label=p["title"][:32], amount=int(p["sell_price"]))],
            provider_token="",
        )
        await c.answer()
    except Exception as e:
        log.exception("invoice creation failed for order %s", order_id)
        await db.execute("UPDATE orders SET status='failed',error=? WHERE id=?", (str(e), order_id))
        await db.commit()
        await c.answer("ساخت فاکتور ناموفق بود.", show_alert=True)


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    order = await q1(
        """SELECT o.*,p.active,p.sell_price,p.remaining_count FROM orders o
           JOIN products p ON p.id=o.product_id WHERE o.payload=?""",
        (query.invoice_payload,),
    )
    if not order:
        await query.answer(ok=False, error_message="Order not found.")
        return
    if order["status"] != "awaiting_payment":
        await query.answer(ok=False, error_message="This order is no longer payable.")
        return
    if order["buyer_id"] != query.from_user.id or order["recipient_id"] != query.from_user.id:
        await query.answer(ok=False, error_message="This invoice belongs to another user.")
        return
    if query.currency != "XTR" or order["sell_price"] != query.total_amount or not order["active"]:
        await query.answer(ok=False, error_message="Price or product changed.")
        return
    if order["remaining_count"] is not None and order["remaining_count"] <= 0:
        await query.answer(ok=False, error_message="Gift is sold out.")
        return
    await query.answer(ok=True)


async def fulfill(bot: Bot, order, charge_id: str):
    try:
        result = await bot.send_gift(
            user_id=int(order["recipient_id"]),
            gift_id=str(order["gift_id"]),
            text=GIFT_TEXT or None,
        )
        if result is not True:
            raise RuntimeError("Telegram did not confirm Gift delivery")
        await db.execute("UPDATE orders SET status='fulfilled',fulfilled_at=?,error=NULL WHERE id=?", (now(), order["id"]))
        await db.commit()
        return True
    except Exception as e:
        log.exception("Gift delivery failed for order %s", order["id"])
        await db.execute("UPDATE orders SET status='fulfillment_failed',error=? WHERE id=?", (str(e), order["id"]))
        await db.commit()
        try:
            await bot.refund_star_payment(user_id=int(order["buyer_id"]), telegram_payment_charge_id=charge_id)
            await db.execute("UPDATE orders SET status='refunded',error=? WHERE id=?", (f"delivery failed; payment refunded: {e}", order["id"]))
            await db.commit()
        except Exception:
            log.exception("refund failed for order %s", order["id"])
        return False


@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot):
    payment = message.successful_payment
    if not payment:
        return
    order = await q1("SELECT * FROM orders WHERE payload=?", (payment.invoice_payload,))
    if not order:
        log.error("unknown successful payment payload %s", payment.invoice_payload)
        return
    if order["buyer_id"] != message.from_user.id or order["recipient_id"] != message.from_user.id:
        log.error("payment user mismatch for order %s", order["id"])
        return
    if order["status"] in {"fulfilled", "refunded"}:
        return
    if payment.currency != "XTR" or payment.total_amount != order["amount"]:
        try:
            await bot.refund_star_payment(user_id=message.from_user.id, telegram_payment_charge_id=payment.telegram_payment_charge_id)
        except Exception:
            log.exception("mismatched payment refund failed")
        await db.execute("UPDATE orders SET status='refunded',error='payment mismatch' WHERE id=?", (order["id"],))
        await db.commit()
        await message.answer("پرداخت با سفارش مطابقت نداشت و refund شد.")
        return

    cur = await db.execute(
        "UPDATE orders SET status='paid',charge_id=?,paid_at=? WHERE id=? AND status='awaiting_payment'",
        (payment.telegram_payment_charge_id, now(), order["id"]),
    )
    await db.commit()
    if cur.rowcount != 1:
        order = await q1("SELECT * FROM orders WHERE id=?", (order["id"],))
        if order["status"] == "fulfilled":
            return
        if order["status"] != "paid":
            return

    ok = await fulfill(bot, order, payment.telegram_payment_charge_id)
    await message.answer("✅ پرداخت موفق بود؛ Gift مستقیماً برای خودت ارسال شد." if ok else "❌ تحویل ناموفق بود؛ سیستم refund را انجام می‌دهد.")


@router.callback_query(F.data == "orders")
async def orders(c: CallbackQuery):
    rows = await qall(
        """SELECT o.id,o.amount,o.status,p.title FROM orders o JOIN products p ON p.id=o.product_id
           WHERE o.buyer_id=? ORDER BY o.id DESC LIMIT 20""", (c.from_user.id,)
    )
    if not rows:
        await c.message.edit_text("هنوز سفارشی نداری.", reply_markup=home_kb(await is_admin(c.from_user.id)))
        return
    names = {"awaiting_payment":"در انتظار پرداخت","paid":"پرداخت شده","fulfilled":"تحویل شد","fulfillment_failed":"خطای تحویل","refunded":"Refund شد","failed":"ناموفق"}
    text = "📦 سفارش‌های من:\n\n" + "\n".join(f"#{r['id']} — {r['title']} — {r['amount']} ⭐ — {names.get(r['status'],r['status'])}" for r in rows)
    await c.message.edit_text(text, reply_markup=home_kb(await is_admin(c.from_user.id)))


@router.callback_query(F.data == "admin")
async def admin(c: CallbackQuery):
    if not await is_admin(c.from_user.id):
        await c.answer("دسترسی ندارید.", show_alert=True)
        return
    await c.message.edit_text("⚙️ پنل مدیریت", reply_markup=admin_kb())


@router.callback_query(F.data == "stats")
async def stats(c: CallbackQuery):
    if not await is_admin(c.from_user.id):
        await c.answer("دسترسی ندارید.", show_alert=True)
        return
    users = await q1("SELECT COUNT(*) c FROM users")
    orders_count = await q1("SELECT COUNT(*) c FROM orders")
    sales = await q1("SELECT COALESCE(SUM(amount),0) s FROM orders WHERE status='fulfilled'")
    await c.message.edit_text(f"👥 Users: {users['c']}\n📦 Orders: {orders_count['c']}\n⭐ فروش تحویل‌شده: {sales['s']} Stars", reply_markup=admin_kb())


@router.callback_query(F.data == "star_balance")
async def star_balance(c: CallbackQuery, bot: Bot):
    if not await is_admin(c.from_user.id):
        await c.answer("دسترسی ندارید.", show_alert=True)
        return
    try:
        bal = await bot.get_my_star_balance()
        await c.message.edit_text(f"💰 موجودی Stars ربات: {bal.amount} ⭐", reply_markup=admin_kb())
    except Exception as e:
        await c.answer(str(e), show_alert=True)


@router.callback_query(F.data == "all_orders")
async def all_orders(c: CallbackQuery):
    if not await is_admin(c.from_user.id):
        await c.answer("دسترسی ندارید.", show_alert=True)
        return
    rows = await qall("SELECT o.id,o.status,o.amount,p.title,o.buyer_id FROM orders o JOIN products p ON p.id=o.product_id ORDER BY o.id DESC LIMIT 30")
    text = "📦 آخرین سفارش‌ها:\n\n" + "\n".join(f"#{r['id']} — {r['title']} — {r['amount']}⭐ — {r['status']} — user={r['buyer_id']}" for r in rows)
    await c.message.edit_text(text[:4000], reply_markup=admin_kb())


@router.message(Command("syncgifts"))
async def sync_command(message: Message, bot: Bot):
    if await is_admin(message.from_user.id):
        try:
            await message.answer(f"✅ {await sync_gifts(bot)} Gift synchronized.")
        except Exception as e:
            await message.answer(f"❌ Sync failed: {e}")


@router.message(Command("stats"))
async def stats_command(message: Message):
    if await is_admin(message.from_user.id):
        u = await q1("SELECT COUNT(*) c FROM users")
        o = await q1("SELECT COUNT(*) c FROM orders")
        f = await q1("SELECT COUNT(*) c FROM orders WHERE status='fulfilled'")
        await message.answer(f"Users: {u['c']}\nOrders: {o['c']}\nFulfilled: {f['c']}")


@router.message(Command("balance"))
async def balance_command(message: Message, bot: Bot):
    if not await is_admin(message.from_user.id):
        return
    try:
        bal = await bot.get_my_star_balance()
        await message.answer(f"Bot Stars balance: {bal.amount} ⭐")
    except Exception as e:
        await message.answer(f"Balance error: {e}")


async def main():
    await init_db()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await sync_gifts(bot)
    except Exception:
        log.exception("initial Gift sync failed")
    task = asyncio.create_task(auto_sync(bot))
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await bot.session.close()
        if db:
            await db.close()


if __name__ == "__main__":
    asyncio.run(main())
