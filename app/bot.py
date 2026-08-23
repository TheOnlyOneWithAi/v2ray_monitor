import asyncio, logging
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from sqlalchemy import select
from .config import get_settings
from .db import Session
from .models import Subscription, Template
from .crypto import SecretBox
from .sync import sync_subscription
router=Router(); pending={}
def admin(m:Message): return bool(m.from_user and m.from_user.id in get_settings().admins)
def menu(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Open Monitor',web_app=WebAppInfo(url=get_settings().webapp_url))]])
@router.message(Command('start'))
async def start(m):
    if not admin(m): return await m.answer('Access denied.')
    await m.answer('/addsub Name | https://...\n/list\n/sync\n/settemplate then send HTML\n/help',reply_markup=menu())
@router.message(Command('help'))
async def help_(m):
    if admin(m): await m.answer('/addsub Name | URL\n/list\n/sync\n/settemplate\n/template')
@router.message(Command('addsub'))
async def addsub(m):
    if not admin(m): return
    raw=m.text.partition(' ')[2].strip()
    if '|' not in raw: return await m.answer('Usage: /addsub Name | https://subscription')
    name,url=[x.strip() for x in raw.split('|',1)]
    if not name or not url.startswith('https://'): return await m.answer('Name and HTTPS URL are required.')
    async with Session() as db:
        s=Subscription(name=name,url_encrypted=SecretBox().encrypt(url)); db.add(s); await db.commit(); sid=s.id
    try: count=await sync_subscription(sid); await m.answer(f'Added. Parsed {count} nodes.')
    except Exception as e: await m.answer(f'Added, sync failed: {type(e).__name__}')
@router.message(Command('list'))
async def list_(m):
    if not admin(m): return
    async with Session() as db: rows=(await db.execute(select(Subscription))).scalars().all()
    await m.answer('\n'.join(f'{x.id}: {x.name} ({"on" if x.enabled else "off"})' for x in rows) or 'No subscriptions.')
@router.message(Command('sync'))
async def sync_(m):
    if not admin(m): return
    async with Session() as db: rows=(await db.execute(select(Subscription))).scalars().all()
    ok=0
    for s in rows:
        try: ok+=await sync_subscription(s.id)
        except Exception: logging.exception('subscription sync failed')
    await m.answer(f'Sync complete: {ok} nodes.')
@router.message(Command('settemplate'))
async def settemplate(m):
    if admin(m): pending[m.from_user.id]='template'; await m.answer('Send complete HTML now. Placeholders: {{name}}, {{status}}, {{ping}}, {{protocol}}, {{last_check}}')
@router.message()
async def text(m):
    if not admin(m) or pending.get(m.from_user.id)!='template': return
    html=m.text or ''
    if len(html)>100_000: return await m.answer('Template too large.')
    async with Session() as db:
        t=(await db.execute(select(Template).where(Template.name=='default'))).scalars().first()
        if not t: db.add(Template(name='default',html=html))
        else: t.html=html
        await db.commit()
    pending.pop(m.from_user.id,None); await m.answer('Template saved.')
async def run_bot():
    s=get_settings()
    if not s.bot_token: return
    bot=Bot(s.bot_token); dp=Dispatcher(); dp.include_router(router); await dp.start_polling(bot)
