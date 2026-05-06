"""M5 MemoryService 的最小 SQLAlchemy 数据模型。"""

from __future__ import annotations

from sqlalchemy import BigInteger
from sqlalchemy import Float
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column


class Base(DeclarativeBase):
    """MemoryService ORM 基类。"""


class MessageEvent(Base):
    """message_events：保存可追溯的消息事件。"""

    __tablename__ = "message_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    guild_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class MemoryFactModel(Base):
    """memory_facts：用户/频道事实卡片。"""

    __tablename__ = "memory_facts"
    __table_args__ = (
        UniqueConstraint(
            "namespace",
            "platform",
            "user_id",
            "guild_id",
            "channel_id",
            "fact_key",
            name="uq_memory_fact_scope_key",
        ),
    )

    fact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(16), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    guild_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fact_key: Mapped[str] = mapped_column(String(128), nullable=False)
    fact_value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_event_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    updated_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class MemorySummary(Base):
    """memory_summaries：会话/日/周/项目摘要。"""

    __tablename__ = "memory_summaries"

    summary_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    summary_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    namespace: Mapped[str] = mapped_column(String(16), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    guild_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ThreadCheckpoint(Base):
    """thread_checkpoints：AskUser 挂起/恢复检查点。"""

    __tablename__ = "thread_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "platform",
            "guild_id",
            "channel_id",
            "thread_id",
            name="uq_thread_checkpoint_scope",
        ),
    )

    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    guild_id: Mapped[str] = mapped_column(String(128), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    missing_params_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    resume_node: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    updated_ts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
