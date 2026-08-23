import html
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from .db import init_db, Session
from .models import Node, Template
from .crypto import SecretBox
from .probe import XrayProbe
import asyncio, json
from datetime import datetime,timezone
app=FastAPI(title='V2Ray Monitor',docs_url=None,redoc_url=None)
_probe_lock=asyncio.Semaphore(10)
DEFAULT='''<div class="node"><b>{{name}}</b><span class="status">{{status}}</span><span>{{ping}} ms</span></div>'''
@app.on_event('startup')
async def startup():
    await init_db()
    async with Session() as db:
        if not (await db.execute(select(Template))).scalars().first(): db.add(Template(name='default',html=DEFAULT)); await db.commit()
@app.get('/',response_class=HTMLResponse)
async def index(): return open('frontend/index.html',encoding='utf8').read()
@app.get('/api/nodes')
async def nodes():
    async with Session() as db:
        rows=(await db.execute(select(Node).where(Node.enabled==True).order_by(Node.id))).scalars().all()
        return {'nodes':[{'id':n.id,'name':n.name,'protocol':n.protocol,'status':n.status,'latency_ms':n.latency_ms,'last_checked':n.last_checked.isoformat() if n.last_checked else None} for n in rows]}
@app.get('/api/view',response_class=HTMLResponse)
async def view():
    async with Session() as db:
        t=(await db.execute(select(Template).where(Template.name=='default'))).scalars().first(); rows=(await db.execute(select(Node).where(Node.enabled==True).order_by(Node.id))).scalars().all()
    tpl=t.html if t else DEFAULT
    allowed={'name','status','ping','protocol','last_check'}
    out=[]
    for n in rows:
        vals={'name':html.escape(n.name),'status':html.escape(n.status),'ping':'—' if n.latency_ms is None else str(n.latency_ms),'protocol':html.escape(n.protocol),'last_check':n.last_checked.isoformat() if n.last_checked else '—'}
        s=tpl
        for k,v in vals.items(): s=s.replace('{{'+k+'}}',v)
        out.append(s)
    return '\n'.join(out)
@app.post('/api/nodes/{node_id}/probe')
async def probe(node_id:int):
    async with Session() as db:
        n=await db.get(Node,node_id)
        if not n or not n.enabled: raise HTTPException(404,'node not found')
        config=json.loads(SecretBox().decrypt(n.config_encrypted)); protocol=n.protocol
    class N: pass
    x=N(); x.config=config; x.protocol=protocol
    async with _probe_lock:
        try: ms=await asyncio.wait_for(XrayProbe().probe(x),timeout=12); status='online'
        except Exception: ms=None; status='offline'
    async with Session() as db:
        n=await db.get(Node,node_id); n.status=status; n.latency_ms=ms; n.failures=0 if ms is not None else n.failures+1; n.last_checked=datetime.now(timezone.utc); await db.commit()
    return {'id':node_id,'status':status,'latency_ms':ms}
