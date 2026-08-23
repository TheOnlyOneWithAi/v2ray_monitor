"""V2Ray Monitor API: named configs, customer entitlements and user identity."""
import asyncio,hashlib,hmac,html,json,time,urllib.parse,secrets
from datetime import datetime,timezone,timedelta
from pathlib import Path
from fastapi import FastAPI,Header,HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from sqlalchemy import select
from .config import get_settings
from .db import Session,init_db
from .crypto import SecretBox
from .models import Node,Template,MonitorEntitlement,MonitorConfig
BASE_DIR=Path(__file__).resolve().parent.parent;FRONTEND_DIR=BASE_DIR/'frontend'
app=FastAPI(title='V2Ray Monitor',docs_url=None,redoc_url=None);app.mount('/static',StaticFiles(directory=str(FRONTEND_DIR)),name='static')
DEFAULT_TEMPLATE='<article class="node"><div><b>{{name}}</b><small>{{protocol}}</small></div><div class="right"><span>{{status}}</span><strong>{{ping}} ms</strong></div></article>'
LOCK=asyncio.Lock()
class SellerEntitlement(BaseModel): telegram_id:int; max_configs:int=Field(ge=1,le=1000); days:int=Field(ge=1,le=3660)
class ConfigCreate(BaseModel): name:str=Field(min_length=1,max_length=200); protocol:str=Field(min_length=2,max_length=20); config:dict
class ProbeReport(BaseModel): probe_token:str; status:str; latency_ms:float|None=None
@app.on_event('startup')
async def startup():
    await init_db()
    async with Session() as db:
        t=(await db.execute(select(Template).where(Template.name=='default'))).scalars().first()
        if t is None:db.add(Template(name='default',html=DEFAULT_TEMPLATE));await db.commit()
def seller_auth(token):
    expected=get_settings().seller_api_token
    if not expected or not token or not secrets.compare_digest(token,expected):raise HTTPException(401,'Invalid seller API token')
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
async def entitlement(uid):
    async with Session() as db:
        e=(await db.execute(select(MonitorEntitlement).where(MonitorEntitlement.telegram_id==uid))).scalar_one_or_none()
        if not e or not e.active or e.expires_at<=datetime.now(timezone.utc):raise HTTPException(402,'Monitor subscription is inactive or expired')
        count=len((await db.execute(select(MonitorConfig.id).where(MonitorConfig.telegram_id==uid,MonitorConfig.enabled.is_(True)))).scalars().all())
        return e,count
@app.get('/',response_class=HTMLResponse)
async def index():return HTMLResponse((FRONTEND_DIR/'index.html').read_text(encoding='utf-8'),headers={'Cache-Control':'no-store'})
@app.get('/health')
async def health():return {'status':'ok'}
@app.get('/api/me')
async def me(x_telegram_init_data:str|None=Header(default=None)):
    uid=validate(x_telegram_init_data);e,count=await entitlement(uid);return {'telegram_id':uid,'max_configs':e.max_configs,'used_configs':count,'expires_at':e.expires_at.isoformat()}
@app.get('/api/configs')
async def configs(q:str='',x_telegram_init_data:str|None=Header(default=None)):
    uid=validate(x_telegram_init_data);await entitlement(uid)
    async with Session() as db:rows=(await db.execute(select(MonitorConfig).where(MonitorConfig.telegram_id==uid,MonitorConfig.enabled.is_(True),MonitorConfig.name.ilike(f'%{q}%')).order_by(MonitorConfig.name))).scalars().all()
    return {'configs':[{'id':r.id,'name':r.name,'protocol':r.protocol,'status':r.status,'latency_ms':r.latency_ms,'last_checked':r.last_checked.isoformat() if r.last_checked else None} for r in rows]}
@app.post('/api/configs')
async def create_config(body:ConfigCreate,x_telegram_init_data:str|None=Header(default=None)):
    uid=validate(x_telegram_init_data);e,count=await entitlement(uid)
    if count>=e.max_configs:raise HTTPException(403,'Config limit reached')
    protocol=body.protocol.lower()
    if protocol not in {'vless','vmess','trojan','shadowsocks','ss'}:raise HTTPException(400,'Unsupported protocol')
    token=secrets.token_urlsafe(48)
    async with Session() as db:
        row=MonitorConfig(telegram_id=uid,name=body.name.strip(),protocol=protocol,config_encrypted=SecretBox().encrypt(json.dumps(body.config,separators=(',',':'))),probe_token=token)
        db.add(row);await db.commit();await db.refresh(row)
    return {'id':row.id,'name':row.name,'protocol':row.protocol,'probe_token':token}
@app.delete('/api/configs/{cid}')
async def delete_config(cid:int,x_telegram_init_data:str|None=Header(default=None)):
    uid=validate(x_telegram_init_data);await entitlement(uid)
    async with Session() as db:
        row=await db.get(MonitorConfig,cid)
        if not row or row.telegram_id!=uid:raise HTTPException(404,'Config not found')
        row.enabled=False;await db.commit()
    return {'ok':True}
@app.get('/api/probe/config/{token}')
async def probe_config(token:str):
    async with Session() as db:
        row=(await db.execute(select(MonitorConfig).where(MonitorConfig.probe_token==token,MonitorConfig.enabled.is_(True)))).scalar_one_or_none()
        if not row:raise HTTPException(401,'Invalid probe token')
        return {'id':row.id,'name':row.name,'protocol':row.protocol,'config':json.loads(SecretBox().decrypt(row.config_encrypted))}
@app.post('/api/probe/report')
async def probe_report(body:ProbeReport):
    async with Session() as db:
        row=(await db.execute(select(MonitorConfig).where(MonitorConfig.probe_token==body.probe_token,MonitorConfig.enabled.is_(True)))).scalar_one_or_none()
        if not row:raise HTTPException(401,'Invalid probe token')
        row.status='online' if body.status=='online' and body.latency_ms is not None else ('offline' if body.status=='offline' else 'unknown');row.latency_ms=body.latency_ms;row.failures=0 if row.status=='online' else row.failures+1;row.last_checked=datetime.now(timezone.utc);await db.commit()
    return {'ok':True}
@app.post('/api/seller/entitlements')
async def seller_entitlement(body:SellerEntitlement,x_seller_token:str|None=Header(default=None)):
    seller_auth(x_seller_token);now=datetime.now(timezone.utc);expiry=now+timedelta(days=body.days)
    async with Session() as db:
        row=(await db.execute(select(MonitorEntitlement).where(MonitorEntitlement.telegram_id==body.telegram_id))).scalar_one_or_none()
        if row:row.max_configs=body.max_configs;row.expires_at=expiry;row.active=True
        else:db.add(MonitorEntitlement(telegram_id=body.telegram_id,max_configs=body.max_configs,expires_at=expiry,active=True))
        await db.commit()
    return {'ok':True,'telegram_id':body.telegram_id,'max_configs':body.max_configs,'expires_at':expiry.isoformat()}
@app.get('/api/nodes')
async def nodes():
    async with Session() as db:rows=(await db.execute(select(Node).where(Node.enabled.is_(True)).order_by(Node.id))).scalars().all()
    return {'nodes':[{'id':n.id,'name':n.name,'protocol':n.protocol,'status':n.status,'latency_ms':n.latency_ms,'last_checked':n.last_checked.isoformat() if n.last_checked else None} for n in rows]}
@app.get('/api/view',response_class=HTMLResponse)
async def view():
    async with LOCK:
        async with Session() as db:t=(await db.execute(select(Template).where(Template.name=='default'))).scalars().first();rows=(await db.execute(select(Node).where(Node.enabled.is_(True)).order_by(Node.id))).scalars().all()
    template=t.html if t else DEFAULT_TEMPLATE;out=[]
    for n in rows:
        vals={'name':html.escape(n.name,quote=True),'status':html.escape(n.status,quote=True),'ping':'—' if n.latency_ms is None else str(n.latency_ms),'protocol':html.escape(n.protocol,quote=True),'last_check':n.last_checked.isoformat() if n.last_checked else '—'};item=template
        for k,v in vals.items():item=item.replace('{{'+k+'}}',v)
        out.append(item)
    return HTMLResponse('\n'.join(out),headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
