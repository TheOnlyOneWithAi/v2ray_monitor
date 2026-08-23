import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from .config import get_settings
from .crypto import SecretBox
from .db import Session
from .models import Node, Subscription
from .probe import XrayProbe
from .sync import sync_subscription

_SYNC_LOCK = asyncio.Lock()


async def sync_loop() -> None:
    while True:
        try:
            async with _SYNC_LOCK:
                async with Session() as db:
                    subs = (await db.execute(select(Subscription).where(Subscription.enabled.is_(True)))).scalars().all()
                for sub in subs:
                    try:
                        await sync_subscription(sub.id)
                    except Exception:
                        logging.exception("subscription sync failed: id=%s", sub.id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("sync loop failed")
        await asyncio.sleep(get_settings().sync_interval)


async def probe_loop() -> None:
    while True:
        try:
            async with Session() as db:
                nodes = (await db.execute(select(Node).where(Node.enabled.is_(True)))).scalars().all()
            sem = asyncio.Semaphore(get_settings().probe_concurrency)

            async def one(node_id: int, encrypted: str, protocol: str) -> None:
                async with sem:
                    try:
                        config = json.loads(SecretBox().decrypt(encrypted))
                        probe_node = type("ProbeNode", (), {})()
                        probe_node.config = config
                        probe_node.protocol = protocol
                        ms = await asyncio.wait_for(XrayProbe().probe(probe_node), timeout=get_settings().probe_timeout + 5)
                        status = "online"
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        ms, status = None, "offline"
                    async with Session() as db2:
                        row = await db2.get(Node, node_id)
                        if row is None:
                            return
                        row.status = status
                        row.latency_ms = ms
                        row.failures = 0 if ms is not None else row.failures + 1
                        row.last_checked = datetime.now(timezone.utc)
                        await db2.commit()

            await asyncio.gather(*(one(n.id, n.config_encrypted, n.protocol) for n in nodes))
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("probe loop failed")
        await asyncio.sleep(get_settings().probe_interval)
