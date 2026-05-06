#!/usr/bin/env python3
"""Run a Telegram MCP server (adapted from dryeab/mcp-telegram).

Environment:
  TELEGRAM_API_ID   (or API_ID)
  TELEGRAM_API_HASH (or API_HASH)

Examples:
  python scripts/run_telegram_mcp_server.py login --phone +1234567890
  python scripts/run_telegram_mcp_server.py serve
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nervos_brain.tool_runtime.telegram_mcp_adapter import (
    TelegramMCPClient,
    TelegramMCPConfig,
    TelegramMCPError,
    build_fastmcp_server,
    build_fastmcp_server_lite,
)


def _build_client(state_dir: str | None) -> TelegramMCPClient:
    cfg = TelegramMCPConfig.from_env()
    if state_dir:
        cfg = TelegramMCPConfig(
            api_id=cfg.api_id,
            api_hash=cfg.api_hash,
            state_dir=Path(state_dir).expanduser().resolve(),
        )
    return TelegramMCPClient(cfg)


def _cmd_login(args: argparse.Namespace) -> int:
    if not args.phone:
        print("Missing --phone for login.")
        return 2

    client = _build_client(args.state_dir)
    asyncio.run(client.login_interactive(phone=args.phone))
    print(f"Login finished. Session stored at: {client.session_path}.session")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    client = _build_client(args.state_dir)
    server = build_fastmcp_server(client)
    server.run()
    return 0


def _cmd_serve_lite() -> int:
    server = build_fastmcp_server_lite()
    server.run()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram MCP server runner.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_login = sub.add_parser("login", help="Interactive Telegram login.")
    p_login.add_argument("--phone", required=True, help="Telegram phone number.")
    p_login.add_argument(
        "--state-dir",
        default=None,
        help="Optional session directory override.",
    )

    p_serve = sub.add_parser("serve", help="Run MCP server.")
    p_serve.add_argument(
        "--state-dir",
        default=None,
        help="Optional session directory override.",
    )

    sub.add_parser(
        "serve-lite",
        help="Run lite MCP server (no Telegram credentials required).",
    )

    args = parser.parse_args()
    try:
        if args.cmd == "login":
            return _cmd_login(args)
        if args.cmd == "serve":
            return _cmd_serve(args)
        if args.cmd == "serve-lite":
            return _cmd_serve_lite()
        return 2
    except TelegramMCPError as exc:
        print(f"Telegram MCP error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
