#!/usr/bin/env python3
"""Export Telegram beta CSAT and BadCase review data."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nervos_brain.tool_runtime.feedback import FeedbackJsonlStore  # noqa: E402


def _parse_ts_ms(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SystemExit(f"Invalid timestamp: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _format_ts(value: Any) -> str:
    try:
        ts_ms = int(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


def _render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Telegram 群测复盘摘要",
        "",
        f"- 总回答数: {summary['total_questions']}",
        f"- 评分数: {summary['total_ratings']}",
        f"- 评分覆盖率: {summary['rating_coverage']:.1%}",
        f"- 平均 CSAT: {summary['average_csat']}",
        f"- BadCase 数: {summary['bad_case_count']}",
        f"- 文字反馈数: {summary['comment_count']}",
        f"- 分数分布: {json.dumps(summary['score_counts'], ensure_ascii=False)}",
        "",
        "## 低分样例",
    ]
    bad_cases = summary.get("bad_cases", [])
    if not bad_cases:
        lines.append("")
        lines.append("本时间段内没有低分 BadCase。")
        return "\n".join(lines)

    for idx, row in enumerate(bad_cases, start=1):
        preview = str(row.get("final_text_preview", "") or "").replace("\n", " ")[:180]
        trace = str(row.get("trace_summary", "") or "").replace("\n", " ")[:180]
        lines.extend(
            [
                "",
                f"### BadCase {idx}",
                "",
                f"- request_id: `{row.get('request_id', '')}`",
                f"- score: {row.get('score', '')}",
                f"- user_id: `{row.get('user_id', '')}`",
                f"- chat_id: `{row.get('chat_id', '')}`",
                f"- created: {_format_ts(row.get('created_ts_ms'))}",
                f"- tool_calls: {row.get('tool_calls', 0)}",
                f"- evidence_count: {row.get('evidence_count', 0)}",
                f"- trace_summary: {trace}",
                f"- final_text_preview: {preview}",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export Telegram beta feedback summary from JSONL storage."
    )
    parser.add_argument(
        "--feedback-file",
        default="data/telegram_bot/feedback.jsonl",
        help="Input JSONL feedback file.",
    )
    parser.add_argument(
        "--since",
        default="",
        help="Inclusive start timestamp, ISO-8601 or epoch milliseconds.",
    )
    parser.add_argument(
        "--until",
        default="",
        help="Inclusive end timestamp, ISO-8601 or epoch milliseconds.",
    )
    parser.add_argument(
        "--bad-case-limit",
        type=int,
        default=20,
        help="Maximum BadCase rows included in the export.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format.",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Optional output file path. Defaults to stdout.",
    )
    args = parser.parse_args()

    store = FeedbackJsonlStore(Path(args.feedback_file).expanduser())
    summary = store.summary(
        since_ts_ms=_parse_ts_ms(args.since),
        until_ts_ms=_parse_ts_ms(args.until),
        bad_case_limit=max(0, int(args.bad_case_limit)),
    )
    if args.format == "markdown":
        content = _render_markdown(summary)
    else:
        content = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)

    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content + "\n", encoding="utf-8")
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
