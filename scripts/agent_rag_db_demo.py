#!/usr/bin/env python3
"""Minimal practical Agent demo:
retrieve evidence from local DB, then call LLM to answer with citations.

Usage examples:
  python scripts/agent_rag_db_demo.py
  python scripts/agent_rag_db_demo.py --sample fiber
  python scripts/agent_rag_db_demo.py --question "CKB Cell Model 是什么？" --source github_docs
  python scripts/agent_rag_db_demo.py --question "如何开 Fiber 通道？" --topic nervosnetwork/fiber --top-k 6
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow running from repo root without package install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nervos_brain.graph_engine.llm import call_llm, get_model_name
from nervos_brain.retrieval import (
    ArchiveStore,
    MultiRetriever,
    QdrantStore,
    load_retrieval_config,
)

SAMPLES: dict[str, dict[str, str]] = {
    "fiber": {
        "question": "如何在 Nervos Fiber 中开通支付通道？给我一个简短步骤。",
        "topic": "nervosnetwork/fiber",
    },
    "cell": {
        "question": "CKB 的 Cell Model 是什么？请用通俗中文解释。",
        "topic": "nervosnetwork/ckb",
    },
    "ccc": {
        "question": "CCC SDK 常见的 capacity 处理思路是什么？",
        "topic": "ckb-devrel/ccc",
    },
}


def _build_retriever() -> MultiRetriever:
    cfg = load_retrieval_config()
    qdrant = QdrantStore(config=cfg, qdrant_location=cfg.qdrant_path)
    archive = ArchiveStore(db_path=cfg.archive_db, config=cfg)
    retriever = MultiRetriever(qdrant_store=qdrant, archive_store=archive, config=cfg)
    retriever.rebuild_bm25()
    return retriever


def _citation_labels(text: str) -> set[str]:
    return set(re.findall(r"\[(\d+)\]", text))


def _build_filters(
    source: str | None,
    doc_type: str | None,
    topic: str | None,
) -> dict[str, str]:
    filters: dict[str, str] = {}
    if source:
        filters["source"] = source
    if doc_type:
        filters["type"] = doc_type
    if topic:
        filters["topic"] = topic
    return filters


@dataclass
class AgentResult:
    question: str
    answer: str
    model: str
    evidence: list[dict[str, Any]]
    citations: list[dict[str, str]]
    elapsed_s: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "model": self.model,
            "evidence_count": len(self.evidence),
            "citations": self.citations,
            "elapsed_s": round(self.elapsed_s, 3),
        }


class DBEvidenceAgent:
    """Simple retrieval-then-generation agent."""

    SYSTEM_PROMPT = (
        "你是 Nervos 技术助手。"
        "你只能基于给定证据回答，不要编造。"
        "回答后请在关键结论后附 [1]/[2] 这种引用编号。"
        "如果证据不足，明确说证据不足并指出缺什么。"
        "输出语言默认中文。"
    )

    def __init__(self, retriever: MultiRetriever) -> None:
        self._retriever = retriever

    def answer(
        self,
        *,
        question: str,
        filters: dict[str, str] | None = None,
        top_k: int = 5,
        model: str | None = None,
    ) -> AgentResult:
        start = time.time()
        hits = self._retriever.search(
            query=question,
            filters=filters or None,
            top_k=max(1, min(int(top_k), 10)),
        )

        if not hits:
            elapsed = time.time() - start
            return AgentResult(
                question=question,
                answer="没有检索到可用证据，请调整问题或放宽过滤条件。",
                model=model or get_model_name(),
                evidence=[],
                citations=[],
                elapsed_s=elapsed,
            )

        evidence_block = []
        for idx, ev in enumerate(hits, start=1):
            evidence_block.append(
                "\n".join(
                    [
                        f"[{idx}] title={ev.get('title', '')}",
                        f"url={ev.get('url', '')}",
                        f"anchor={ev.get('anchor', '')}",
                        f"snippet={str(ev.get('snippet', ''))[:800]}",
                    ]
                )
            )
        user_prompt = (
            f"问题：{question}\n\n"
            "证据如下（按相关性排序）：\n"
            + "\n\n".join(evidence_block)
            + "\n\n请给出准确、简洁、可执行的回答，并在对应句子后标 [n]。"
        )

        answer_text = call_llm(
            self.SYSTEM_PROMPT,
            user_prompt,
            model=model,
        ).strip()
        if not answer_text:
            answer_text = "模型未返回内容，请重试。"

        citations: list[dict[str, str]] = []
        labels_in_text = _citation_labels(answer_text)
        for idx, ev in enumerate(hits, start=1):
            label = str(idx)
            if labels_in_text and label not in labels_in_text:
                continue
            citations.append(
                {
                    "label": f"[{idx}]",
                    "title": str(ev.get("title", "")),
                    "url": str(ev.get("url", "")),
                    "anchor": str(ev.get("anchor", "")),
                }
            )

        elapsed = time.time() - start
        return AgentResult(
            question=question,
            answer=answer_text,
            model=model or get_model_name(),
            evidence=hits,
            citations=citations,
            elapsed_s=elapsed,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a practical RAG agent against local Qdrant+SQLite evidence DB.",
    )
    parser.add_argument("--question", default="", help="User question.")
    parser.add_argument(
        "--sample",
        choices=sorted(SAMPLES.keys()),
        default="fiber",
        help="Use built-in sample question/topic.",
    )
    parser.add_argument(
        "--source",
        default="github_docs",
        help="Filter payload.source (default: github_docs). Empty to disable.",
    )
    parser.add_argument(
        "--doc-type",
        default="github_doc",
        help="Filter payload.type (default: github_doc). Empty to disable.",
    )
    parser.add_argument("--topic", default="", help="Filter payload.topic (owner/repo).")
    parser.add_argument("--top-k", type=int, default=5, help="Max evidence chunks to use.")
    parser.add_argument("--model", default="", help="Optional model override.")
    parser.add_argument("--json", action="store_true", help="Print final result as JSON.")
    args = parser.parse_args()

    sample = SAMPLES[args.sample]
    question = args.question.strip() or sample["question"]
    topic = args.topic.strip() or sample["topic"]

    source = args.source.strip() or None
    doc_type = args.doc_type.strip() or None
    filters = _build_filters(source=source, doc_type=doc_type, topic=topic or None)
    model = args.model.strip() or None

    if not os.path.exists("data/archive.db"):
        raise SystemExit("archive DB not found: data/archive.db")

    retriever = _build_retriever()
    agent = DBEvidenceAgent(retriever)
    result = agent.answer(
        question=question,
        filters=filters,
        top_k=args.top_k,
        model=model,
    )

    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return

    print("=" * 72)
    print("Agent RAG Demo (DB Evidence)")
    print("=" * 72)
    print(f"question: {result.question}")
    print(f"model: {result.model}")
    print(f"filters: {filters}")
    print(f"evidence_count: {len(result.evidence)}")
    print(f"elapsed: {result.elapsed_s:.2f}s")
    print("-" * 72)
    print(result.answer)
    print("-" * 72)
    if result.citations:
        print("citations:")
        for c in result.citations:
            print(f"  {c['label']} {c['title']} | {c['url']}")
    else:
        print("citations: (none)")


if __name__ == "__main__":
    main()

