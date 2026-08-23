"""Subscription synchronization with stable node records."""
import json
from datetime import datetime, timezone

from sqlalchemy import select

from .config import get_settings
from .crypto import SecretBox
from .db import Session
from .fetcher import safe_fetch
from .models import Node, Subscription
from .parser import parse_subscription


def _identity(protocol: str, name: str) -> tuple[str, str]:
    return protocol.lower(), name.strip().casefold()


async def sync_subscription(sub_id: int) -> int:
    async with Session() as db:
        sub = await db.get(Subscription, sub_id)
        if not sub or not sub.enabled:
            return 0
        url = SecretBox().decrypt(sub.url_encrypted)

    text = await safe_fetch(url)
    parsed = parse_subscription(text, max_nodes=get_settings().max_nodes_per_subscription)
    box = SecretBox()

    async with Session() as db:
        sub = await db.get(Subscription, sub_id)
        if not sub or not sub.enabled:
            return 0
        existing = (await db.execute(select(Node).where(Node.subscription_id == sub.id))).scalars().all()
        by_identity: dict[tuple[str, str], list[Node]] = {}
        for node in existing:
            by_identity.setdefault(_identity(node.protocol, node.name), []).append(node)

        seen: set[int] = set()
        for item in parsed:
            candidates = by_identity.get(_identity(item.protocol, item.name), [])
            node = next((x for x in candidates if x.id not in seen), None)
            payload = json.dumps(item.config, separators=(",", ":"), ensure_ascii=False)
            if node is None:
                node = Node(subscription_id=sub.id, name=item.name, protocol=item.protocol, config_encrypted=box.encrypt(payload))
                db.add(node)
                await db.flush()
            else:
                node.name = item.name
                node.protocol = item.protocol
                node.config_encrypted = box.encrypt(payload)
                node.enabled = True
            seen.add(node.id)

        for node in existing:
            if node.id not in seen:
                await db.delete(node)
        sub.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return len(parsed)
