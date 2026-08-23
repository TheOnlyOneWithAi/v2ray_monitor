from aiogram import Bot,Dispatcher,Router
from aiogram.filters import Command
from aiogram.types import Message,InlineKeyboardButton,InlineKeyboardMarkup,WebAppInfo
from sqlalchemy import select
from .config import get_settings
from .crypto import SecretBox
from .db import Session
from .models import Subscription,Node,Template
from .sync import sync_subscription
router=Router();pending={};MAX_TEMPLATE=100_000
def admin(m):return bool(m.from_user and m.from_user.id in get_settings().admins)
def menu():
    s=get_settings();return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🌐 Open Monitor',web_app=WebAppInfo(url=s.webapp_url))]])
@router.message(Command('start'))
async def start(m:Message):
    s=get_settings()
    if admin(m):return await m.answer('V2Ray Monitor Admin\n/addsub Name | https://subscription\n/list\n/sync [id]\n/delsub ID\n/toggle ID\n/nodes [id]\n/settemplate\n/setjoin @channel | URL\n/setjoinon\n/setjoinoff\n/setcard NUMBER | HOLDER\n/settings',reply_markup=menu())
    await m.answer('🌐 V2Ray Monitor\nبرای مشاهده وضعیت کانفیگ‌ها از دکمه زیر استفاده کنید.',reply_markup=menu())
@router.message(Command('addsub'))
async def addsub(m):
    if not admin(m):return
    raw=(m.text or '').partition(' ')[2]
    if '|' not in raw:return await m.answer('Usage: /addsub Name | https://subscription')
    name,url=(x.strip() for x in raw.split('|',1))
    if not name or len(name)>120 or not url.startswith('https://'):return await m.answer('Name and HTTPS URL required.')
    async with Session() as db:
        x=Subscription(name=name,url_encrypted=SecretBox().encrypt(url));db.add(x);await db.commit();sid=x.id
    try:n=await sync_subscription(sid);await m.answer(f'Added #{sid}; {n} nodes.')
    except Exception:await m.answer('Added, but sync failed; use /sync later.')
@router.message(Command('list'))
async def listing(m):
    if not admin(m):return
    async with Session() as db:r=(await db.execute(select(Subscription).order_by(Subscription.id))).scalars().all()
    await m.answer('\n'.join(f'#{x.id} {x.name} — {"ON" if x.enabled else "OFF"}' for x in r) or 'No subscriptions.')
@router.message(Command('sync'))
async def sync(m):
    if not admin(m):return
    arg=(m.text or '').partition(' ')[2].strip();total=0
    async with Session() as db:r=(await db.execute(select(Subscription).order_by(Subscription.id))).scalars().all()
    if arg:
        try:r=[x for x in r if x.id==int(arg)]
        except ValueError:return await m.answer('Invalid ID.')
    for x in r:
        try:total+=await sync_subscription(x.id)
        except Exception:pass
    await m.answer(f'Sync complete: {total} nodes.')
@router.message(Command('delsub'))
async def delete(m):
    if not admin(m):return
    try:sid=int((m.text or '').partition(' ')[2])
    except ValueError:return await m.answer('Usage: /delsub ID')
    async with Session() as db:x=await db.get(Subscription,sid);await db.delete(x) if x else None;await db.commit()
    await m.answer(f'Subscription #{sid} deleted.' if x else 'Subscription not found.')
@router.message(Command('toggle'))
async def toggle(m):
    if not admin(m):return
    try:sid=int((m.text or '').partition(' ')[2])
    except ValueError:return await m.answer('Usage: /toggle ID')
    async with Session() as db:
        x=await db.get(Subscription,sid)
        if not x:return await m.answer('Not found.')
        x.enabled=not x.enabled;await db.commit()
    await m.answer(f'#{sid}: {"ON" if x.enabled else "OFF"}')
@router.message(Command('nodes'))
async def nodes(m):
    if not admin(m):return
    arg=(m.text or '').partition(' ')[2].strip()
    async with Session() as db:r=(await db.execute(select(Node).order_by(Node.id))).scalars().all()
    if arg:
        try:r=[x for x in r if x.subscription_id==int(arg)]
        except ValueError:return await m.answer('Invalid ID.')
    on=sum(x.status=='online' for x in r);await m.answer(f'Nodes: {len(r)}\nOnline: {on}\nOffline: {len(r)-on}')
async def setv(m,key):
    if not admin(m):return
    raw=(m.text or '').partition(' ')[2]
    if '|' not in raw:return await m.answer('Use VALUE | SECOND VALUE')
    a,b=(x.strip() for x in raw.split('|',1))
    async with Session() as db:
        name='__setting__:'+key;t=(await db.execute(select(Template).where(Template.name==name))).scalars().first()
        if t:t.html=a+'|'+b
        else:db.add(Template(name=name,html=a+'|'+b))
        await db.commit()
    await m.answer('Saved.')
@router.message(Command('setjoin'))
async def setjoin(m):await setv(m,'force_join')
@router.message(Command('setcard'))
async def setcard(m):await setv(m,'card')
async def toggle_setting(m,key,value):
    if not admin(m):return
    async with Session() as db:
        name='__setting__:'+key;t=(await db.execute(select(Template).where(Template.name==name))).scalars().first()
        if t:t.html=value
        else:db.add(Template(name=name,html=value))
        await db.commit()
    await m.answer('Saved.')
@router.message(Command('setjoinon'))
async def joinon(m):await toggle_setting(m,'force_join_enabled','true')
@router.message(Command('setjoinoff'))
async def joinoff(m):await toggle_setting(m,'force_join_enabled','false')
@router.message(Command('settings'))
async def settings_cmd(m):
    if not admin(m):return
    async with Session() as db:
        ts=(await db.execute(select(Template).where(Template.name.like('__setting__:%')))).scalars().all()
    await m.answer('\n'.join(f'{x.name}: {x.html}' for x in ts) or 'No runtime settings.')
@router.message(Command('settemplate'))
async def settemplate(m):
    if not admin(m):return
    pending[m.from_user.id]=True;await m.answer('Send HTML or .html file. Placeholders: {{name}}, {{status}}, {{ping}}, {{protocol}}, {{last_check}}')
async def save_template(m,content):
    if len(content.encode())>MAX_TEMPLATE:return await m.answer('Template too large.')
    async with Session() as db:
        t=(await db.execute(select(Template).where(Template.name=='default'))).scalars().first()
        if t:t.html=content
        else:db.add(Template(name='default',html=content))
        await db.commit()
    pending.pop(m.from_user.id,None);await m.answer('Template saved.')
@router.message(lambda m:m.document is not None)
async def document(m:Message,bot:Bot):
    if not admin(m) or not pending.get(m.from_user.id):return
    d=m.document
    if d.file_name and not d.file_name.lower().endswith(('.html','.htm')):return await m.answer('Only HTML files.')
    f=await bot.get_file(d.file_id);b=__import__('io').BytesIO();await bot.download_file(f.file_path,b);await save_template(m,b.getvalue().decode('utf-8-sig'))
@router.message(lambda m:m.text is not None)
async def template_text(m):
    if admin(m) and pending.get(m.from_user.id):await save_template(m,m.text)
async def run_bot():
    s=get_settings()
    if not s.bot_token:return
    bot=Bot(s.bot_token);dp=Dispatcher();dp.include_router(router);await dp.start_polling(bot)
