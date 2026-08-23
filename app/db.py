from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, text
from .config import get_settings
from .models import Base, Node

s=get_settings(); engine=create_async_engine(s.database_url, echo=False); Session=async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    import os
    os.makedirs('data',exist_ok=True)
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
        # create_all does not add columns to existing SQLite tables. Keep upgrades
        # backwards-compatible with already-installed Monitor databases.
        if c.dialect.name == 'sqlite':
            cols = {row[1] for row in (await c.execute(text('PRAGMA table_info(subscriptions)'))).fetchall()}
            if 'telegram_id' not in cols:
                await c.execute(text('ALTER TABLE subscriptions ADD COLUMN telegram_id INTEGER'))
                await c.execute(text('CREATE INDEX IF NOT EXISTS ix_subscriptions_telegram_id ON subscriptions (telegram_id)'))

async def get_nodes(session: AsyncSession): return (await session.execute(select(Node).order_by(Node.id))).scalars().all()
