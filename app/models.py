from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
class Base(DeclarativeBase): pass
def now(): return datetime.now(timezone.utc)
class Subscription(Base):
    __tablename__='subscriptions'; id:Mapped[int]=mapped_column(Integer,primary_key=True); name:Mapped[str]=mapped_column(String(120)); url_encrypted:Mapped[str]=mapped_column(Text); enabled:Mapped[bool]=mapped_column(Boolean,default=True); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now); nodes:Mapped[list['Node']]=relationship(back_populates='subscription',cascade='all, delete-orphan')
class Node(Base):
    __tablename__='nodes'; id:Mapped[int]=mapped_column(Integer,primary_key=True); subscription_id:Mapped[int]=mapped_column(ForeignKey('subscriptions.id',ondelete='CASCADE')); name:Mapped[str]=mapped_column(String(200)); protocol:Mapped[str]=mapped_column(String(16)); config_encrypted:Mapped[str]=mapped_column(Text); status:Mapped[str]=mapped_column(String(16),default='unknown'); latency_ms:Mapped[float|None]=mapped_column(Float,nullable=True); last_checked:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True); failures:Mapped[int]=mapped_column(Integer,default=0); enabled:Mapped[bool]=mapped_column(Boolean,default=True); subscription:Mapped[Subscription]=relationship(back_populates='nodes')
class Template(Base):
    __tablename__='templates'; id:Mapped[int]=mapped_column(Integer,primary_key=True); name:Mapped[str]=mapped_column(String(80),unique=True); html:Mapped[str]=mapped_column(Text); updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now)
