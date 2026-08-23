from datetime import datetime, timezone
from sqlalchemy import select, delete
from .models import Subscription, Node
from .db import Session
from .crypto import SecretBox
from .fetcher import safe_fetch
from .parser import parse_subscription

async def sync_subscription(sub_id:int):
    async with Session() as db:
        sub=await db.get(Subscription,sub_id)
        if not sub or not sub.enabled: return 0
        url=SecretBox().decrypt(sub.url_encrypted)
        text=await safe_fetch(url)
        parsed=parse_subscription(text)
        await db.execute(delete(Node).where(Node.subscription_id==sub.id))
        box=SecretBox()
        for p in parsed:
            db.add(Node(subscription_id=sub.id,name=p.name,protocol=p.protocol,config_encrypted=box.encrypt(__import__('json').dumps(p.config,separators=(',',':')))))
        sub.updated_at=datetime.now(timezone.utc)
        await db.commit()
        return len(parsed)
