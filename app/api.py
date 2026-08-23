"""Public monitor API with optional Telegram WebApp identity + force-join enforcement."""
import asyncio,hashlib,hmac,html,json,time,urllib.parse
from pathlib import Path
from fastapi import FastAPI,Header,HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from aiogram import Bot
from sqlalchemy import select
from .config import get_settings
from .db import Session,init_db
from .models import Node,Template
BASE_DIR=Path(__file__).resolve().parent.parent;FRONTEND_DIR=BASE_DIR/'frontend'
app=FastAPI(title='V2Ray Monitor',docs_url=None,redoc_url=None);app.mount('/static',StaticFiles(directory=str(FRONTEND_DIR)),name='static')
DEFAULT_TEMPLATE='<article class="node"><div><b>{{name}}</b><small>{{protocol}}</small></div><div class="right"><span>{{status}}</span><strong>{{ping}} ms</strong></div></article>'
LOCK=asyncio.Lock()
@app.on_event('startup')
async def startup():
    await init_db()
    async with Session() as db:
        t=(await db.execute(select(Template).where(Template.name=='default'))).scalars().first()
        if t is None:db.add(Template(name='default',html=DEFAULT_TEMPLATE));await db.commit()
async def runtime(key,default=''):
    async with Session() as db:
        t=(await db.execute(select(Template).where(Template.name=='__setting__:'+key))).scalars().first()
        return t.html if t else default
def validate(init_data):
    s=get_settings()
    if not init_data or not s.bot_token:raise HTTPException(401,'Telegram WebApp identity required')
    try:
        p=dict(urllib.parse.parse_qsl(init_data,keep_blank_values=True));received=p.pop('hash');auth=int(p.get('auth_date','0'))
        if time.time()-auth>86400:raise ValueError('expired')
        data='\n'.join(f'{k}={p[k]}' for k in sorted(p));secret=hmac.new(b'WebAppData',s.bot_token.encode(),hashlib.sha256).digest();expected=hmac.new(secret,data.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,received):raise ValueError('hash')
        return int(json.loads(p['user'])['id'])
    except Exception as e:raise HTTPException(401,'Invalid Telegram WebApp identity') from e
async def authorize(init_data):
    s=get_settings();enabled=(await runtime('force_join_enabled','true' if s.force_join_enabled else 'false')).lower()=='true'
    if not enabled:return
    channel=await runtime('force_join_channel',s.force_join_channel)
    if not channel:raise HTTPException(503,'Force-join channel is not configured')
    uid=validate(init_data)
    bot=Bot(s.bot_token)
    try:
        member=await bot.get_chat_member(channel,uid)
        if str(member.status) not in {'member','administrator','creator'}:raise HTTPException(403,'Join the required channel first')
    except HTTPException:raise
    except Exception as e:raise HTTPException(403,'Unable to verify channel membership') from e
@app.get('/',response_class=HTMLResponse)
async def index():return HTMLResponse((FRONTEND_DIR/'index.html').read_text(encoding='utf-8'),headers={'Cache-Control':'no-store'})
@app.get('/health')
async def health():return {'status':'ok'}
@app.get('/api/nodes')
async def nodes(x_telegram_init_data:str|None=Header(default=None)):
    await authorize(x_telegram_init_data)
    async with Session() as db:rows=(await db.execute(select(Node).where(Node.enabled.is_(True)).order_by(Node.id))).scalars().all()
    return {'nodes':[{'id':n.id,'name':n.name,'protocol':n.protocol,'status':n.status,'latency_ms':n.latency_ms,'last_checked':n.last_checked.isoformat() if n.last_checked else None} for n in rows]}
@app.get('/api/view',response_class=HTMLResponse)
async def view(x_telegram_init_data:str|None=Header(default=None)):
    await authorize(x_telegram_init_data)
    async with LOCK:
        async with Session() as db:t=(await db.execute(select(Template).where(Template.name=='default'))).scalars().first();rows=(await db.execute(select(Node).where(Node.enabled.is_(True)).order_by(Node.id))).scalars().all()
    template=t.html if t else DEFAULT_TEMPLATE;out=[]
    for n in rows:
        vals={'name':html.escape(n.name,quote=True),'status':html.escape(n.status,quote=True),'ping':'—' if n.latency_ms is None else str(n.latency_ms),'protocol':html.escape(n.protocol,quote=True),'last_check':n.last_checked.isoformat() if n.last_checked else '—'};item=template
        for k,v in vals.items():item=item.replace('{{'+k+'}}',v)
        out.append(item)
    return HTMLResponse('\n'.join(out),headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
