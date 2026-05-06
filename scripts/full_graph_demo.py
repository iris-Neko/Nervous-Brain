#!/usr/bin/env python3
"""端到端演示脚本：用真实 LLM 跑完整 GraphEngine 工作流。

用法:
    # 需要设置 API key 环境变量，例如:
    export OPENAI_API_KEY="sk-..."
    # 可选：指定模型
    export CHAT_MODEL_FAST="gpt-4o-mini"

    python scripts/full_graph_demo.py "怎么用 Fiber SDK 开通支付通道？"
    python scripts/full_graph_demo.py  # 使用默认问题
"""

import sys
import time
from pathlib import Path

from sqlalchemy import create_engine

from nervos_brain.graph_engine.full_graph import (
    FullGraphRuntime,
    build_full_graph,
    invoke_full_graph,
)
from nervos_brain.graph_engine.provider_registry import ProviderCapabilityRegistry
from nervos_brain.memory import MemoryService, build_session_factory, init_memory_schema
from nervos_brain.retrieval import (
    build_configured_retriever,
)


def build_runtime() -> FullGraphRuntime:
    retriever = build_configured_retriever()

    memory_db = Path("data") / "memory.db"
    memory_db.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{memory_db}", future=True)
    init_memory_schema(engine)
    memory_service = MemoryService(build_session_factory(engine))

    return FullGraphRuntime(
        multi_retriever=retriever,
        memory_service=memory_service,
        provider_registry=ProviderCapabilityRegistry(),
        provider_max_cost="high",
    )


def main():
    question = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Nervos CKB 的 Cell Model 是什么？"

    print(f"\n{'='*60}")
    print(f"  Nervos Brain — Full Graph Demo")
    print(f"{'='*60}")
    print(f"\n问题: {question}\n")

    runtime = build_runtime()
    state = {
        "request_id": f"demo-{int(time.time())}",
        "user_message": {
            "content": question,
            "context": {
                "platform": "discord",
                "user_id": "demo_user",
                "guild_id": "demo_guild",
                "channel_id": "demo_channel",
                "thread_id": "demo_thread",
            },
        },
        "user_memory_key": {"platform": "discord", "user_id": "demo_user"},
        "channel_memory_key": {
            "platform": "discord",
            "guild_id": "demo_guild",
            "channel_id": "demo_channel",
        },
        "thread_key": {
            "platform": "discord",
            "guild_id": "demo_guild",
            "channel_id": "demo_channel",
            "thread_id": "demo_thread",
        },
        "memory_pointers": [],
        "memory_facts": [],
        "info_needs": [],
        "evidence": [],
        "conflicts": [],
        "retry_count": 0,
        "budget": {
            "max_prompt_tokens": 4000,
            "max_evidence_chunks": 10,
            "max_memory_facts": 5,
            "max_tool_calls": 3,
        },
        "route": "graph",
        "locale": "zh-CN",
    }

    print("构建图...")
    graph = build_full_graph()

    print("执行中...\n")
    start = time.time()
    result = invoke_full_graph(state, runtime=runtime, compiled_graph=graph)
    elapsed = time.time() - start

    print(f"{'─'*60}")
    print(f"耗时: {elapsed:.2f}s")
    print(f"重试次数: {result.get('retry_count', 0)}")
    print(f"证据数量: {len(result.get('evidence', []))}")
    print(f"冲突数量: {len(result.get('conflicts', []))}")
    print(f"{'─'*60}\n")

    response = result.get("_final_response", {})
    if response.get("need_user_input"):
        print(f"[系统反问] {response.get('ask_user_question', '')}\n")
    else:
        print(f"[回答]\n{response.get('text', '(无回答)')}\n")
        if response.get("citations"):
            print("引用:")
            for c in response["citations"]:
                print(f"  {c.get('label', '?')} {c.get('title', '')} — {c.get('url', '')}")
        if response.get("_chunks"):
            print(f"\n(输出共 {len(response['_chunks'])} 段)")

    print()


if __name__ == "__main__":
    main()
