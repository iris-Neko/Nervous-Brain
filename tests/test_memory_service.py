from sqlalchemy import create_engine

from nervos_brain.memory import MemoryService
from nervos_brain.memory import build_session_factory
from nervos_brain.memory import init_memory_schema


def _build_memory_service() -> MemoryService:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    init_memory_schema(engine)
    session_factory = build_session_factory(engine)
    return MemoryService(session_factory)


def test_channel_memory_isolated():
    """M5-T14: 频道记忆不串台。"""
    memory = _build_memory_service()

    ch_x = {"platform": "discord", "guild_id": "g1", "channel_id": "x"}
    ch_y = {"platform": "discord", "guild_id": "g1", "channel_id": "y"}

    memory.upsert_channel_fact(
        key=ch_x,
        fact_key="default_sdk",
        fact_value="js",
        confidence=0.95,
        source_event_ids=["ev1"],
    )

    x_facts = memory.list_channel_facts(key=ch_x)
    y_facts = memory.list_channel_facts(key=ch_y)

    assert len(x_facts) == 1
    assert x_facts[0]["key"] == "default_sdk"
    assert x_facts[0]["value"] == "js"
    assert y_facts == []


def test_channel_memory_shared_same_channel():
    """M5-T15: 同频道共享记忆。"""
    memory = _build_memory_service()

    same_channel = {"platform": "discord", "guild_id": "g1", "channel_id": "x"}

    # 用户 A 在频道写入共识。
    memory.upsert_channel_fact(
        key=same_channel,
        fact_key="default_sdk",
        fact_value="js",
        confidence=0.9,
        source_event_ids=["ev-a1"],
    )

    # 用户 B 读取同一频道，应看到同一事实。
    facts_for_user_b = memory.list_channel_facts(key=same_channel)
    assert len(facts_for_user_b) == 1
    assert facts_for_user_b[0]["key"] == "default_sdk"
    assert facts_for_user_b[0]["value"] == "js"


def test_user_memory_survives_channel_change():
    """M5-T16: 用户偏好跨频道保留。"""
    memory = _build_memory_service()

    user_key = {"platform": "discord", "user_id": "u100"}

    memory.upsert_user_fact(
        key=user_key,
        fact_key="language",
        fact_value="zh-CN",
        confidence=0.92,
        source_event_ids=["ev-u1"],
    )

    facts = memory.list_user_facts(key=user_key)
    assert len(facts) == 1
    assert facts[0]["key"] == "language"
    assert facts[0]["value"] == "zh-CN"


def test_recent_message_events_are_isolated_by_group_and_user():
    """同群不同用户、同用户不同群的短期上下文不能串。"""
    memory = _build_memory_service()

    memory.write_message_event(
        platform="telegram",
        user_id="u-a",
        guild_id="-1001",
        channel_id="-1001",
        thread_id=None,
        role="user",
        content="A asks about CKB",
        created_ts_ms=1000,
    )
    memory.write_message_event(
        platform="telegram",
        user_id="u-b",
        guild_id="-1001",
        channel_id="-1001",
        thread_id=None,
        role="user",
        content="B asks about Fiber",
        created_ts_ms=1100,
    )
    memory.write_message_event(
        platform="telegram",
        user_id="u-a",
        guild_id="-1002",
        channel_id="-1002",
        thread_id=None,
        role="user",
        content="A in another group",
        created_ts_ms=1200,
    )

    rows = memory.list_recent_message_events(
        platform="telegram",
        user_id="u-a",
        guild_id="-1001",
        channel_id="-1001",
        limit=20,
    )

    assert [row["content"] for row in rows] == ["A asks about CKB"]


def test_recent_message_events_limit_and_order():
    """最近消息按时间升序返回，且限制为最近 N 条。"""
    memory = _build_memory_service()

    for idx in range(25):
        memory.write_message_event(
            platform="telegram",
            user_id="u-a",
            guild_id="-1001",
            channel_id="-1001",
            thread_id=None,
            role="assistant" if idx % 2 else "user",
            content=f"msg-{idx}",
            created_ts_ms=1000 + idx,
        )

    rows = memory.list_recent_message_events(
        platform="telegram",
        user_id="u-a",
        guild_id="-1001",
        channel_id="-1001",
        limit=20,
    )

    assert len(rows) == 20
    assert rows[0]["content"] == "msg-5"
    assert rows[-1]["content"] == "msg-24"
    assert {row["role"] for row in rows} == {"user", "assistant"}


def test_ask_user_thread_suspend_resume():
    """M5-T17: AskUser 挂起恢复。"""
    memory = _build_memory_service()

    thread_key = {
        "platform": "discord",
        "guild_id": "g1",
        "channel_id": "x",
        "thread_id": "t-001",
    }
    now = 1_710_000_000_000

    checkpoint_id = memory.suspend_thread(
        key=thread_key,
        missing_params=["sdk_language", "sdk_version"],
        resume_node="RetrieverPlanner",
        context_payload={
            "origin_question": "写一个交易记账app示例吧",
            "ask_user_question": "你想用什么技术栈？",
        },
        ttl_hours=24,
        now_ts_ms=now,
    )
    restored = memory.resume_thread(key=thread_key, now_ts_ms=now + 1_000)

    assert restored is not None
    assert restored["checkpoint_id"] == checkpoint_id
    assert restored["resume_node"] == "RetrieverPlanner"
    assert restored["missing_params"] == ["sdk_language", "sdk_version"]
    assert restored["context_payload"]["origin_question"] == "写一个交易记账app示例吧"


def test_thread_checkpoint_can_complete():
    """补参完成后应可将 checkpoint 标记为 completed。"""
    memory = _build_memory_service()

    thread_key = {
        "platform": "discord",
        "guild_id": "g1",
        "channel_id": "x",
        "thread_id": "t-002",
    }
    now = 1_710_000_000_000

    memory.suspend_thread(
        key=thread_key,
        missing_params=["sdk_language"],
        resume_node="RetrieverPlanner",
        ttl_hours=24,
        now_ts_ms=now,
    )

    assert memory.complete_thread(key=thread_key, now_ts_ms=now + 1_000) is True
    assert memory.resume_thread(key=thread_key, now_ts_ms=now + 2_000) is None


def test_privacy_filter_blocks_secrets_and_pii():
    """M5-T13: 隐私过滤器最小行为。"""
    memory = _build_memory_service()
    user_key = {"platform": "discord", "user_id": "u100"}

    # secret
    try:
        memory.upsert_user_fact(
            key=user_key,
            fact_key="token",
            fact_value="api_key=sk-1234567890abcdef",
            confidence=0.9,
            source_event_ids=["ev-1"],
        )
        assert False, "含 secret 的值不应允许入库"
    except ValueError:
        pass

    # pii
    try:
        memory.upsert_user_fact(
            key=user_key,
            fact_key="contact",
            fact_value="my email is test@example.com",
            confidence=0.9,
            source_event_ids=["ev-2"],
        )
        assert False, "含 PII 的值不应允许入库"
    except ValueError:
        pass
