from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, delete
from .config import get_settings
from .models import Base, Node

s=get_settings(); engine=create_async_engine(s.database_url, echo=False); Session=async_sessionmaker(engine, expire_on_commit=False)
async def init_db():
    import os
    os.makedirs('data',exist_ok=True)
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
async def get_nodes(session: AsyncSession): return (await session.execute(select(Node).order_by(Node.id))).scalars().all()
