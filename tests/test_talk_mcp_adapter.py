from __future__ import annotations

import builtins
import sys
from types import ModuleType
from typing import Any

import pytest
import requests

from nervos_brain.tool_runtime.talk_mcp_adapter import (
    TalkMCPClient,
    TalkMCPConfig,
    TalkMCPError,
    build_fastmcp_server,
)


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class _FakeSession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []
        self.responses: dict[str, dict[str, Any]] = {}
        self.status_codes: dict[str, int] = {}

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: int = 0) -> _FakeResponse:
        path = url.replace("https://talk.nervos.org", "")
        self.calls.append({"url": url, "path": path, "params": params or {}, "timeout": timeout})
        return _FakeResponse(self.responses.get(path, {}), self.status_codes.get(path, 200))


def _client(session: _FakeSession) -> TalkMCPClient:
    return TalkMCPClient(TalkMCPConfig(request_delay=0), session=session)  # type: ignore[arg-type]


def test_talk_search_returns_discourse_evidence_shape():
    session = _FakeSession()
    session.responses["/search.json"] = {
        "topics": [{"id": 42, "title": "CCC transfer tutorial", "slug": "ccc-transfer", "tags": ["ccc"]}],
        "posts": [
            {
                "topic_id": 42,
                "post_number": 1,
                "username": "alice",
                "created_at": "2026-01-01T00:00:00.000Z",
                "blurb": "<p>Use <code>ccc</code> to transfer CKB.</p>",
                "score": 3.5,
            }
        ],
    }

    rows = _client(session).search("ccc transfer", limit=5, category="english", time_range="after:2026-01-01")

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "discourse"
    assert row["title"] == "CCC transfer tutorial"
    assert row["url"] == "https://talk.nervos.org/t/ccc-transfer/42/1"
    assert row["anchor"] == "doc:nervos-talk-42#post:1"
    assert "`ccc`" in row["snippet"]
    assert row["payload"]["source"] == "nervos_talk"
    assert row["payload"]["tags"] == ["ccc"]
    assert session.calls[0]["params"]["q"] == "ccc transfer category:english after:2026-01-01"


def test_talk_latest_returns_topics():
    session = _FakeSession()
    session.responses["/latest.json"] = {
        "topic_list": {
            "topics": [
                {
                    "id": 7,
                    "title": "Fiber update",
                    "slug": "fiber-update",
                    "excerpt": "<p>Latest discussion</p>",
                    "tags": ["fiber"],
                    "last_poster_username": "bob",
                    "created_at": "2026-01-02T00:00:00.000Z",
                }
            ]
        }
    }

    rows = _client(session).latest(limit=10)

    assert rows[0]["title"] == "Fiber update"
    assert rows[0]["url"] == "https://talk.nervos.org/t/fiber-update/7"
    assert rows[0]["payload"]["type"] == "forum_topic"
    assert rows[0]["payload"]["tags"] == ["fiber"]


def test_talk_get_topic_and_post():
    session = _FakeSession()
    session.responses["/t/99.json"] = {
        "id": 99,
        "title": "DOB Cookbook",
        "slug": "dob-cookbook",
        "posts_count": 2,
        "tags": ["spore"],
        "post_stream": {
            "posts": [
                {"topic_id": 99, "post_number": 1, "username": "alice", "created_at": "2026-01-01", "cooked": "<p>first</p>"},
                {"topic_id": 99, "post_number": 2, "username": "bob", "created_at": "2026-01-02", "cooked": "<pre><code>dob</code></pre>"},
            ]
        },
    }

    topic = _client(session).get_topic(99, max_posts=50)
    post = _client(session).get_post(99, 2)

    assert topic["title"] == "DOB Cookbook"
    assert topic["url"] == "https://talk.nervos.org/t/dob-cookbook/99"
    assert len(topic["posts"]) == 2
    assert post["post_number"] == 2
    assert "`dob`" in post["content"]


def test_talk_client_errors_and_limit_clamp():
    session = _FakeSession()
    session.responses["/search.json"] = {"topics": [], "posts": []}
    client = _client(session)

    with pytest.raises(TalkMCPError, match="query cannot be empty"):
        client.search("   ")

    rows = client.search("ckb", limit=999)
    assert rows == []

    session.status_codes["/t/404.json"] = 404
    with pytest.raises(TalkMCPError, match="Nervos Talk request failed"):
        client.get_topic(404)


def test_talk_config_from_env(monkeypatch):
    monkeypatch.setenv("NERVOS_TALK_BASE_URL", "https://forum.example.org")
    monkeypatch.setenv("NERVOS_TALK_API_KEY", "key")
    monkeypatch.setenv("NERVOS_TALK_API_USER", "system")
    monkeypatch.setenv("NERVOS_TALK_REQUEST_DELAY", "0.2")
    monkeypatch.setenv("NERVOS_TALK_TIMEOUT_S", "20")

    cfg = TalkMCPConfig.from_env()

    assert cfg.base_url == "https://forum.example.org"
    assert cfg.api_key == "key"
    assert cfg.api_username == "system"
    assert cfg.request_delay == 0.2
    assert cfg.timeout_s == 20


def test_build_fastmcp_server_registers_read_only_tools(monkeypatch):
    registered: dict[str, Any] = {}

    class FakeFastMCP:
        def __init__(self, name: str, **_kwargs: Any) -> None:
            self.name = name

        def tool(self):
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn
            return decorator

    mcp_module = ModuleType("mcp")
    server_module = ModuleType("mcp.server")
    fastmcp_module = ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.server", server_module)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_module)

    server = build_fastmcp_server(_client(_FakeSession()))

    assert server.name == "nervos-talk-mcp"
    assert set(registered) == {"talk_search", "talk_get_topic", "talk_get_post", "talk_latest"}


def test_build_fastmcp_server_missing_dependency(monkeypatch):
    monkeypatch.delitem(sys.modules, "mcp.server.fastmcp", raising=False)
    monkeypatch.delitem(sys.modules, "mcp.server", raising=False)
    monkeypatch.delitem(sys.modules, "mcp", raising=False)
    original_import = builtins.__import__

    def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "mcp.server.fastmcp":
            raise ImportError("blocked mcp import for test")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(TalkMCPError, match="Missing dependency: mcp"):
        build_fastmcp_server(_client(_FakeSession()))
