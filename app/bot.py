"""Telegram admin control plane. No secret node data is ever sent to users."""
import io
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Document, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy import delete, select

from .config import get_settings
from .crypto import SecretBox
from .db import Session
from .models import Node, Subscription, Template
from .sync import sync_subscription

router = Router()
pending: dict[int, str] = {}
MAX_TEMPLATE = 100_000


def is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in get_settings().admins)


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Open Monitor", web_app=WebAppInfo(url=get_settings().webapp_url))]]
    )


async def denied(message: Message) -> None:
    await message.answer("Access denied.")


@router.message(Command("start"))
async def start(message: Message):
    if not is_admin(message):
        return await denied(message)
    await message.answer(
        "V2Ray Monitor admin\n\n"
        "/addsub Name | https://subscription\n"
        "/list\n/sync [id]\n/delsub ID\n/toggle ID\n/nodes [id]\n"
        "/settemplate (then send HTML or a .html file)\n/template\n/help",
        reply_markup=menu(),
    )


@router.message(Command("help"))
async def help_command(message: Message):
    if is_admin(message):
        await message.answer(
            "/addsub Name | HTTPS URL\n/list\n/sync [subscription_id]\n"
            "/delsub ID\n/toggle ID\n/nodes [subscription_id]\n"
            "/settemplate\n/template"
        )


@router.message(Command("addsub"))
async def addsub(message: Message):
    if not is_admin(message):
        return
    raw = (message.text or "").partition(" ")[2].strip()
    if "|" not in raw:
        return await message.answer("Usage: /addsub Name | https://subscription")
    name, url = (part.strip() for part in raw.split("|", 1))
    if not name or len(name) > 120 or not url.startswith("https://"):
        return await message.answer("A name and an HTTPS URL are required.")
    try:
        SecretBox()  # fail before creating a database record if encryption is misconfigured
        async with Session() as db:
            sub = Subscription(name=name, url_encrypted=SecretBox().encrypt(url))
            db.add(sub)
            await db.commit()
            sub_id = sub.id
        count = await sync_subscription(sub_id)
        await message.answer(f"Added subscription #{sub_id}. Parsed {count} nodes.")
    except Exception:
        logging.exception("add subscription failed")
        await message.answer("Subscription could not be added or synced. Check server logs.")


@router.message(Command("list"))
async def list_subscriptions(message: Message):
    if not is_admin(message):
        return
    async with Session() as db:
        rows = (await db.execute(select(Subscription).order_by(Subscription.id))).scalars().all()
    if not rows:
        return await message.answer("No subscriptions.")
    await message.answer("\n".join(f"#{x.id} {x.name} — {'ON' if x.enabled else 'OFF'}" for x in rows))


@router.message(Command("sync"))
async def sync_command(message: Message):
    if not is_admin(message):
        return
    arg = (message.text or "").partition(" ")[2].strip()
    try:
        async with Session() as db:
            rows = (await db.execute(select(Subscription).order_by(Subscription.id))).scalars().all()
        if arg:
            sub_id = int(arg)
            rows = [x for x in rows if x.id == sub_id]
        total = 0
        for sub in rows:
            try:
                total += await sync_subscription(sub.id)
            except Exception:
                logging.exception("subscription sync failed: id=%s", sub.id)
        await message.answer(f"Sync complete. Parsed/updated {total} nodes.")
    except ValueError:
        await message.answer("Invalid subscription ID.")


@router.message(Command("delsub"))
async def delete_subscription(message: Message):
    if not is_admin(message):
        return
    try:
        sub_id = int((message.text or "").partition(" ")[2].strip())
    except ValueError:
        return await message.answer("Usage: /delsub ID")
    async with Session() as db:
        sub = await db.get(Subscription, sub_id)
        if not sub:
            return await message.answer("Subscription not found.")
        await db.delete(sub)
        await db.commit()
    await message.answer(f"Subscription #{sub_id} deleted.")


@router.message(Command("toggle"))
async def toggle_subscription(message: Message):
    if not is_admin(message):
        return
    try:
        sub_id = int((message.text or "").partition(" ")[2].strip())
    except ValueError:
        return await message.answer("Usage: /toggle ID")
    async with Session() as db:
        sub = await db.get(Subscription, sub_id)
        if not sub:
            return await message.answer("Subscription not found.")
        sub.enabled = not sub.enabled
        await db.commit()
        state = "ON" if sub.enabled else "OFF"
    await message.answer(f"Subscription #{sub_id}: {state}")


@router.message(Command("nodes"))
async def node_stats(message: Message):
    if not is_admin(message):
        return
    arg = (message.text or "").partition(" ")[2].strip()
    try:
        async with Session() as db:
            query = select(Node).order_by(Node.id)
            rows = (await db.execute(query)).scalars().all()
        if arg:
            sub_id = int(arg)
            rows = [x for x in rows if x.subscription_id == sub_id]
    except ValueError:
        return await message.answer("Invalid subscription ID.")
    online = sum(x.status == "online" for x in rows)
    await message.answer(f"Nodes: {len(rows)}\nOnline: {online}\nOffline: {len(rows) - online}")


@router.message(Command("settemplate"))
async def settemplate(message: Message):
    if not is_admin(message):
        return
    pending[message.from_user.id] = "template"
    await message.answer(
        "Send complete HTML as the next message or upload an .html file.\n"
        "Safe placeholders: {{name}}, {{status}}, {{ping}}, {{protocol}}, {{last_check}}\n"
        "Sensitive placeholders are not supported."
    )


async def save_template(user_id: int, content: str, message: Message) -> None:
    if len(content.encode("utf-8")) > MAX_TEMPLATE:
        return await message.answer("Template is too large (100 KB maximum).")
    async with Session() as db:
        template = (await db.execute(select(Template).where(Template.name == "default"))).scalars().first()
        if template is None:
            db.add(Template(name="default", html=content))
        else:
            template.html = content
        await db.commit()
    pending.pop(user_id, None)
    await message.answer("Template saved.")


@router.message(Command("template"))
async def show_template(message: Message):
    if not is_admin(message):
        return
    async with Session() as db:
        template = (await db.execute(select(Template).where(Template.name == "default"))).scalars().first()
    if not template:
        return await message.answer("No template configured.")
    # Never echo secrets; the template itself contains only admin-authored HTML.
    content = template.html
    if len(content) > 3500:
        content = content[:3500] + "\n…"
    await message.answer(content)


@router.message()
async def text_template(message: Message):
    if not is_admin(message) or pending.get(message.from_user.id) != "template":
        return
    if not message.text:
        return
    await save_template(message.from_user.id, message.text, message)


@router.message(lambda message: message.document is not None)
async def document_template(message: Message, bot: Bot):
    if not is_admin(message) or pending.get(message.from_user.id) != "template":
        return
    document: Document = message.document
    if document.file_size and document.file_size > MAX_TEMPLATE:
        return await message.answer("Template is too large (100 KB maximum).")
    if document.file_name and not document.file_name.lower().endswith((".html", ".htm")):
        return await message.answer("Only .html files are accepted.")
    file = await bot.get_file(document.file_id)
    buffer = io.BytesIO()
    await bot.download_file(file.file_path, buffer)
    await save_template(message.from_user.id, buffer.getvalue().decode("utf-8-sig"), message)


async def run_bot() -> None:
    settings = get_settings()
    if not settings.bot_token:
        return
    bot = Bot(settings.bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)
