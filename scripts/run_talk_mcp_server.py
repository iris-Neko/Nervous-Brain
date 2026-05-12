#!/usr/bin/env python3
"""Run the read-only Nervos Talk MCP server.

Environment:
  NERVOS_TALK_BASE_URL       default: https://talk.nervos.org
  NERVOS_TALK_API_KEY        optional, only for private/rate-limited forums
  NERVOS_TALK_API_USER       optional, username for API key
  NERVOS_TALK_REQUEST_DELAY  optional seconds between requests
  NERVOS_TALK_TIMEOUT_S      optional request timeout seconds
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nervos_brain.tool_runtime.talk_mcp_adapter import (  # noqa: E402
    TalkMCPClient,
    TalkMCPConfig,
    TalkMCPError,
    build_fastmcp_server,
)


def _cmd_serve(args: argparse.Namespace) -> int:
    cfg = TalkMCPConfig.from_env()
    if args.base_url:
        cfg = TalkMCPConfig(
            base_url=args.base_url,
            request_delay=cfg.request_delay,
            timeout_s=cfg.timeout_s,
            api_key=cfg.api_key,
            api_username=cfg.api_username,
        )
    server = build_fastmcp_server(TalkMCPClient(cfg))
    server.run()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Nervos Talk read-only MCP server runner.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_serve = sub.add_parser("serve", help="Run MCP server.")
    p_serve.add_argument("--base-url", default="", help="Optional Talk/Discourse base URL override.")

    args = parser.parse_args()
    try:
        if args.cmd == "serve":
            return _cmd_serve(args)
        return 2
    except TalkMCPError as exc:
        print(f"Talk MCP error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
