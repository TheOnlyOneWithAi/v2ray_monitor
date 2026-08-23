import asyncio, logging
from .db import init_db, Session
from .models import Subscription, Node
from .config import get_settings
from .sync import sync_subscription
from .crypto import SecretBox
from .probe import XrayProbe
from sqlalchemy import select
import json
async def sync_loop():
    while True:
        try:
            async with Session() as db: subs=(await db.execute(select(Subscription).where(Subscription.enabled==True))).scalars().all()
            for s in subs:
                try: await sync_subscription(s.id)
                except Exception: logging.exception('sync failed')
        except Exception: logging.exception('sync loop failed')
        await asyncio.sleep(get_settings().sync_interval)
async def probe_loop():
    while True:
        try:
            async with Session() as db: nodes=(await db.execute(select(Node).where(Node.enabled==True))).scalars().all()
            sem=asyncio.Semaphore(10)
            async def one(n):
                async with sem:
                    try:
                        c=json.loads(SecretBox().decrypt(n.config_encrypted)); x=type('N',(),{})(); x.config=c; x.protocol=n.protocol
                        ms=await asyncio.wait_for(XrayProbe().probe(x),timeout=get_settings().probe_timeout+4); status='online'
                    except Exception: ms=None; status='offline'
                    async with Session() as db2:
                        row=await db2.get(Node,n.id); row.status=status; row.latency_ms=ms; row.failures=0 if ms is not None else row.failures+1; await db2.commit()
            await asyncio.gather(*(one(n) for n in nodes))
        except Exception: logging.exception('probe loop failed')
        await asyncio.sleep(max(30,get_settings().sync_interval))
