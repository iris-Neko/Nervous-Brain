#!/usr/bin/env python3
"""Offline Telegram pipeline demo (no bot token needed).

Flow:
  Telegram Bot update JSON
    -> MessageEnvelope
    -> GraphState skeleton
    -> OutboundMessage (platform formatter)
    -> Telegram Bot API sendMessage payload preview
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nervos_brain.response_normalizer.platform_formatter import format_response_to_outbound
from nervos_brain.tool_runtime.telegram_bot_protocol_adapter import (
    message_envelope_to_graph_state,
    outbound_message_to_telegram_requests,
    telegram_update_to_message_envelope,
)


def _default_update() -> dict:
    return {
        "update_id": 123456789,
        "message": {
            "message_id": 101,
            "date": 1711111111,
            "text": "/ask Fiber 开通支付通道的最小步骤是什么？",
            "chat": {"id": -1001234567890, "type": "supergroup"},
            "from": {"id": 424242, "language_code": "zh-CN"},
            "message_thread_id": 77,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline TG protocol pipeline demo.")
    parser.add_argument(
        "--update-json",
        default="",
        help="Optional path to Telegram update JSON file.",
    )
    args = parser.parse_args()

    update: dict
    if args.update_json:
        update = json.loads(Path(args.update_json).read_text(encoding="utf-8"))
    else:
        update = _default_update()

    envelope = telegram_update_to_message_envelope(update)
    state = message_envelope_to_graph_state(envelope)

    # Mocked assistant response (in real runtime this comes from full graph result)
    response = {
        "request_id": state["request_id"],
        "text": "你可以先创建通道，再进行链下交易 [1]。",
        "citations": [
            {
                "label": "[1]",
                "url": "https://github.com/nervosnetwork/fiber",
                "anchor": "doc:github-nervosnetwork-fiber#blob:x",
                "title": "fiber docs",
            }
        ],
    }

    outbound = format_response_to_outbound(
        response=response,
        context=envelope["context"],
        render_mode="markdown",
    )
    tg_requests = outbound_message_to_telegram_requests(outbound)

    print("=== MessageEnvelope ===")
    print(json.dumps(envelope, ensure_ascii=False, indent=2))
    print("\n=== GraphState Skeleton ===")
    print(json.dumps(state, ensure_ascii=False, indent=2))
    print("\n=== OutboundMessage ===")
    print(json.dumps(outbound, ensure_ascii=False, indent=2))
    print("\n=== Telegram sendMessage requests ===")
    print(json.dumps(tg_requests, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

