"""M5 MemoryService 最小实现。"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Engine
from sqlalchemy import create_engine
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

from nervos_brain.core_protocols import ChannelMemoryKey
from nervos_brain.core_protocols import Fact
from nervos_brain.core_protocols import ThreadKey
from nervos_brain.core_protocols import UserMemoryKey

from .models import Base
from .models import MemoryFactModel
from .models import MessageEvent
from .models import ThreadCheckpoint
from .privacy import check_memory_text


def build_postgres_engine(database_url: str) -> Engine:
    """按 Postgres DSN 创建 SQLAlchemy Engine。"""
    return create_engine(database_url, future=True)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    """创建 Session 工厂。"""
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def init_memory_schema(engine: Engine) -> None:
    """初始化 MemoryService 表结构。"""
    Base.metadata.create_all(engine)


class MemoryService:
    """M5 最小 MemoryService：facts + thread checkpoint。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def write_message_event(
        self,
        *,
        platform: str,
        user_id: str,
        guild_id: str | None,
        channel_id: str | None,
        thread_id: str | None,
        role: str,
        content: str,
        created_ts_ms: int | None = None,
    ) -> str:
        """写入 message_events（最小版本）。"""
        allow, reasons = check_memory_text(content)
        if not allow:
            raise ValueError(f"message event blocked by privacy filter: {reasons}")

        event_id = uuid.uuid4().hex
        now_ms = created_ts_ms or _now_ms()
        row = MessageEvent(
            event_id=event_id,
            platform=platform,
            user_id=user_id,
            guild_id=guild_id,
            channel_id=channel_id,
            thread_id=thread_id,
            role=role,
            content=content,
            created_ts_ms=now_ms,
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()
        return event_id

    def list_recent_message_events(
        self,
        *,
        platform: str,
        user_id: str,
        guild_id: str | None = None,
        channel_id: str | None = None,
        thread_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """读取同一用户在指定群/频道里的最近消息事件，返回时间升序。"""
        safe_limit = max(1, min(int(limit or 20), 100))
        with self._session_factory() as session:
            stmt = (
                select(MessageEvent)
                .where(MessageEvent.platform == platform)
                .where(MessageEvent.user_id == user_id)
            )
            if guild_id is not None:
                stmt = stmt.where(MessageEvent.guild_id == guild_id)
            if channel_id is not None:
                stmt = stmt.where(MessageEvent.channel_id == channel_id)
            if thread_id is not None:
                stmt = stmt.where(MessageEvent.thread_id == thread_id)
            stmt = stmt.order_by(MessageEvent.created_ts_ms.desc()).limit(safe_limit)
            rows = session.execute(stmt).scalars().all()

        return [self._row_to_message_event(row) for row in reversed(rows)]

    def upsert_user_fact(
        self,
        *,
        key: UserMemoryKey,
        fact_key: str,
        fact_value: str,
        confidence: float,
        source_event_ids: Sequence[str],
        updated_ts_ms: int | None = None,
    ) -> str:
        """写入/更新 UserMemory fact。"""
        return self._upsert_fact(
            namespace="user",
            platform=key["platform"],
            user_id=key["user_id"],
            guild_id=None,
            channel_id=None,
            fact_key=fact_key,
            fact_value=fact_value,
            confidence=confidence,
            source_event_ids=source_event_ids,
            updated_ts_ms=updated_ts_ms,
        )

    def upsert_channel_fact(
        self,
        *,
        key: ChannelMemoryKey,
        fact_key: str,
        fact_value: str,
        confidence: float,
        source_event_ids: Sequence[str],
        updated_ts_ms: int | None = None,
    ) -> str:
        """写入/更新 ChannelMemory fact。"""
        return self._upsert_fact(
            namespace="channel",
            platform=key["platform"],
            user_id=None,
            guild_id=key["guild_id"],
            channel_id=key["channel_id"],
            fact_key=fact_key,
            fact_value=fact_value,
            confidence=confidence,
            source_event_ids=source_event_ids,
            updated_ts_ms=updated_ts_ms,
        )

    def list_user_facts(
        self,
        *,
        key: UserMemoryKey,
        min_confidence: float = 0.0,
    ) -> list[Fact]:
        """读取 UserMemory facts。"""
        with self._session_factory() as session:
            stmt = (
                select(MemoryFactModel)
                .where(MemoryFactModel.namespace == "user")
                .where(MemoryFactModel.platform == key["platform"])
                .where(MemoryFactModel.user_id == key["user_id"])
                .where(MemoryFactModel.confidence >= min_confidence)
                .order_by(MemoryFactModel.updated_ts_ms.desc())
            )
            rows = session.execute(stmt).scalars().all()
        return [self._row_to_fact(row) for row in rows]

    def list_channel_facts(
        self,
        *,
        key: ChannelMemoryKey,
        min_confidence: float = 0.0,
    ) -> list[Fact]:
        """读取 ChannelMemory facts（严格按 channel 隔离）。"""
        with self._session_factory() as session:
            stmt = (
                select(MemoryFactModel)
                .where(MemoryFactModel.namespace == "channel")
                .where(MemoryFactModel.platform == key["platform"])
                .where(MemoryFactModel.guild_id == key["guild_id"])
                .where(MemoryFactModel.channel_id == key["channel_id"])
                .where(MemoryFactModel.confidence >= min_confidence)
                .order_by(MemoryFactModel.updated_ts_ms.desc())
            )
            rows = session.execute(stmt).scalars().all()
        return [self._row_to_fact(row) for row in rows]

    def suspend_thread(
        self,
        *,
        key: ThreadKey,
        missing_params: Sequence[str],
        resume_node: str,
        context_payload: dict[str, Any] | None = None,
        ttl_hours: int = 24,
        now_ts_ms: int | None = None,
    ) -> str:
        """写入/更新 AskUser 挂起检查点。"""
        now = now_ts_ms or _now_ms()
        expires_ts_ms = now + ttl_hours * 60 * 60 * 1000
        payload_obj: dict[str, Any] = {
            "missing_params": list(missing_params),
        }
        if isinstance(context_payload, dict) and context_payload:
            payload_obj["context_payload"] = context_payload
        payload_json = json.dumps(payload_obj, ensure_ascii=False)

        with self._session_factory() as session:
            stmt = (
                select(ThreadCheckpoint)
                .where(ThreadCheckpoint.platform == key["platform"])
                .where(ThreadCheckpoint.guild_id == key["guild_id"])
                .where(ThreadCheckpoint.channel_id == key["channel_id"])
                .where(ThreadCheckpoint.thread_id == key["thread_id"])
            )
            existing = session.execute(stmt).scalar_one_or_none()
            if existing is None:
                checkpoint_id = uuid.uuid4().hex
                row = ThreadCheckpoint(
                    checkpoint_id=checkpoint_id,
                    platform=key["platform"],
                    guild_id=key["guild_id"],
                    channel_id=key["channel_id"],
                    thread_id=key["thread_id"],
                    missing_params_json=payload_json,
                    resume_node=resume_node,
                    expires_ts_ms=expires_ts_ms,
                    status="pending",
                    updated_ts_ms=now,
                    version=1,
                )
                session.add(row)
            else:
                checkpoint_id = existing.checkpoint_id
                existing.missing_params_json = payload_json
                existing.resume_node = resume_node
                existing.expires_ts_ms = expires_ts_ms
                existing.status = "pending"
                existing.updated_ts_ms = now
                existing.version += 1
            session.commit()
            return checkpoint_id

    def resume_thread(
        self,
        *,
        key: ThreadKey,
        now_ts_ms: int | None = None,
    ) -> dict[str, object] | None:
        """读取线程检查点，过期则返回 None。"""
        now = now_ts_ms or _now_ms()
        with self._session_factory() as session:
            stmt = (
                select(ThreadCheckpoint)
                .where(ThreadCheckpoint.platform == key["platform"])
                .where(ThreadCheckpoint.guild_id == key["guild_id"])
                .where(ThreadCheckpoint.channel_id == key["channel_id"])
                .where(ThreadCheckpoint.thread_id == key["thread_id"])
                .where(ThreadCheckpoint.status == "pending")
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return None
            if row.expires_ts_ms <= now:
                row.status = "expired"
                row.updated_ts_ms = now
                session.commit()
                return None

            missing_params: list[str]
            context_payload: dict[str, Any]
            try:
                loaded = json.loads(row.missing_params_json)
            except Exception:
                loaded = []
            if isinstance(loaded, dict):
                raw_missing = loaded.get("missing_params", [])
                missing_params = (
                    [str(x) for x in raw_missing if str(x).strip()]
                    if isinstance(raw_missing, list)
                    else []
                )
                raw_ctx = loaded.get("context_payload", {})
                context_payload = raw_ctx if isinstance(raw_ctx, dict) else {}
            elif isinstance(loaded, list):
                missing_params = [str(x) for x in loaded if str(x).strip()]
                context_payload = {}
            else:
                missing_params = []
                context_payload = {}

            return {
                "checkpoint_id": row.checkpoint_id,
                "missing_params": missing_params,
                "resume_node": row.resume_node,
                "context_payload": context_payload,
                "expires_ts_ms": row.expires_ts_ms,
                "version": row.version,
            }

    def complete_thread(
        self,
        *,
        key: ThreadKey,
        now_ts_ms: int | None = None,
    ) -> bool:
        """将 pending 线程检查点标记为 completed。"""
        now = now_ts_ms or _now_ms()
        with self._session_factory() as session:
            stmt = (
                select(ThreadCheckpoint)
                .where(ThreadCheckpoint.platform == key["platform"])
                .where(ThreadCheckpoint.guild_id == key["guild_id"])
                .where(ThreadCheckpoint.channel_id == key["channel_id"])
                .where(ThreadCheckpoint.thread_id == key["thread_id"])
                .where(ThreadCheckpoint.status == "pending")
            )
            row = session.execute(stmt).scalar_one_or_none()
            if row is None:
                return False
            row.status = "completed"
            row.updated_ts_ms = now
            row.version += 1
            session.commit()
            return True

    def _upsert_fact(
        self,
        *,
        namespace: str,
        platform: str,
        user_id: str | None,
        guild_id: str | None,
        channel_id: str | None,
        fact_key: str,
        fact_value: str,
        confidence: float,
        source_event_ids: Sequence[str],
        updated_ts_ms: int | None,
    ) -> str:
        allow, reasons = check_memory_text(fact_value)
        if not allow:
            raise ValueError(f"fact blocked by privacy filter: {reasons}")

        now = updated_ts_ms or _now_ms()
        source_ids_json = json.dumps(list(source_event_ids), ensure_ascii=False)

        with self._session_factory() as session:
            stmt = (
                select(MemoryFactModel)
                .where(MemoryFactModel.namespace == namespace)
                .where(MemoryFactModel.platform == platform)
                .where(MemoryFactModel.user_id == user_id)
                .where(MemoryFactModel.guild_id == guild_id)
                .where(MemoryFactModel.channel_id == channel_id)
                .where(MemoryFactModel.fact_key == fact_key)
            )
            existing = session.execute(stmt).scalar_one_or_none()
            if existing is None:
                fact_id = uuid.uuid4().hex
                row = MemoryFactModel(
                    fact_id=fact_id,
                    namespace=namespace,
                    platform=platform,
                    user_id=user_id,
                    guild_id=guild_id,
                    channel_id=channel_id,
                    fact_key=fact_key,
                    fact_value=fact_value,
                    confidence=confidence,
                    source_event_ids=source_ids_json,
                    updated_ts_ms=now,
                )
                session.add(row)
            else:
                fact_id = existing.fact_id
                existing.fact_value = fact_value
                existing.confidence = confidence
                existing.source_event_ids = source_ids_json
                existing.updated_ts_ms = now
            session.commit()
            return fact_id

    @staticmethod
    def _row_to_fact(row: MemoryFactModel) -> Fact:
        return {
            "id": row.fact_id,
            "namespace": row.namespace,  # type: ignore[typeddict-item]
            "key": row.fact_key,
            "value": row.fact_value,
            "confidence": row.confidence,
            "updated_ts_ms": row.updated_ts_ms,
            "source_event_ids": json.loads(row.source_event_ids),
        }

    @staticmethod
    def _row_to_message_event(row: MessageEvent) -> dict[str, Any]:
        return {
            "event_id": row.event_id,
            "platform": row.platform,
            "user_id": row.user_id,
            "guild_id": row.guild_id,
            "channel_id": row.channel_id,
            "thread_id": row.thread_id,
            "role": row.role,
            "content": row.content,
            "created_ts_ms": row.created_ts_ms,
        }


def _now_ms() -> int:
    return int(time.time() * 1000)
