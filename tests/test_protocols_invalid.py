# ============================================================
# test_protocols_invalid.py —— M2-T25 协议非法数据测试
# ============================================================
# 这个文件测试的是：当你给一个"故意搞坏"的字典时，
# 校验函数应该能检测出错误（返回非空的错误列表）。
#
# 为什么要写这种测试？
#   如果校验函数对非法数据"睁一只眼闭一只眼"，
#   那坏数据就会偷偷溜进系统，导致后面的节点莫名其妙崩溃。
#   这些测试就是故意制造各种"坏数据"来确保校验函数足够严格。
#
# 运行方式：
#   conda activate NervosBrain
#   cd 项目根目录
#   pytest tests/test_protocols_invalid.py -v
# ============================================================

import time

from nervos_brain.core_protocols.validators import (
    validate_assistant_response,
    validate_evidence,
    validate_message_envelope,
    validate_tool_call_request,
)


class TestInvalidMessageEnvelope:
    """测试非法的 MessageEnvelope 数据"""

    def test_empty_dict(self):
        """空字典应该被拦住"""
        errors = validate_message_envelope({})
        assert len(errors) > 0, "空字典不应该通过校验"

    def test_missing_context(self):
        """缺少 context 字段"""
        msg = {
            "kind": "message",
            "ts_ms": 1710000000000,
            "message_id": "msg_001",
            "content": "你好",
            # 故意不写 context
        }
        errors = validate_message_envelope(msg)
        assert any("context" in e for e in errors), \
            f"应该报 context 缺失，但收到: {errors}"

    def test_context_not_dict(self):
        """context 不是字典而是字符串"""
        msg = {
            "kind": "message",
            "ts_ms": 1710000000000,
            "message_id": "msg_001",
            "context": "这不是字典",
            "content": "你好",
        }
        errors = validate_message_envelope(msg)
        assert len(errors) > 0, "context 不是字典应该报错"

    def test_context_missing_user_id(self):
        """context 里缺少 user_id"""
        msg = {
            "kind": "message",
            "ts_ms": 1710000000000,
            "message_id": "msg_001",
            "context": {
                "platform": "discord",
                # 故意不写 user_id
            },
            "content": "你好",
        }
        errors = validate_message_envelope(msg)
        assert any("user_id" in e for e in errors), \
            f"应该报 user_id 缺失，但收到: {errors}"

    def test_invalid_kind(self):
        """kind 的值不在 message/command 里"""
        msg = {
            "kind": "invalid_kind",
            "ts_ms": 1710000000000,
            "message_id": "msg_001",
            "context": {"platform": "discord", "user_id": "u123"},
            "content": "你好",
        }
        errors = validate_message_envelope(msg)
        assert any("kind" in e for e in errors), \
            f"非法 kind 应该报错，但收到: {errors}"

    def test_ts_ms_not_int(self):
        """ts_ms 是字符串而不是整数"""
        msg = {
            "kind": "message",
            "ts_ms": "不是数字",
            "message_id": "msg_001",
            "context": {"platform": "discord", "user_id": "u123"},
            "content": "你好",
        }
        errors = validate_message_envelope(msg)
        assert any("ts_ms" in e for e in errors), \
            f"ts_ms 不是整数应该报错，但收到: {errors}"


class TestInvalidEvidence:
    """测试非法的 Evidence 数据"""

    def test_empty_dict(self):
        """空字典应该被拦住"""
        errors = validate_evidence({})
        assert len(errors) > 0, "空字典不应该通过校验"

    def test_missing_url(self):
        """缺少 url 字段——证据没有来源链接是不允许的"""
        evidence = {
            "id": "ev_001",
            "source": "qdrant",
            "title": "测试",
            # 故意不写 url
            "anchor": "section:1",
            "snippet": "测试内容",
            "score": 0.9,
            "payload": {},
            "hash": "abc",
            "retrieved_ts_ms": 1710000000000,
        }
        errors = validate_evidence(evidence)
        assert any("url" in e for e in errors), \
            f"缺少 url 应该报错，但收到: {errors}"

    def test_invalid_source(self):
        """source 不在白名单里"""
        evidence = {
            "id": "ev_001",
            "source": "google",  # 不在白名单里
            "title": "测试",
            "url": "https://example.com",
            "anchor": "section:1",
            "snippet": "测试内容",
            "score": 0.9,
            "payload": {},
            "hash": "abc",
            "retrieved_ts_ms": 1710000000000,
        }
        errors = validate_evidence(evidence)
        assert any("source" in e for e in errors), \
            f"非法 source 应该报错，但收到: {errors}"

    def test_score_out_of_range(self):
        """score 超过 1.0"""
        evidence = {
            "id": "ev_001",
            "source": "qdrant",
            "title": "测试",
            "url": "https://example.com",
            "anchor": "section:1",
            "snippet": "测试内容",
            "score": 1.5,  # 超过 1.0
            "payload": {},
            "hash": "abc",
            "retrieved_ts_ms": 1710000000000,
        }
        errors = validate_evidence(evidence)
        assert any("score" in e for e in errors), \
            f"score 超范围应该报错，但收到: {errors}"

    def test_score_negative(self):
        """score 为负数"""
        evidence = {
            "id": "ev_001",
            "source": "qdrant",
            "title": "测试",
            "url": "https://example.com",
            "anchor": "section:1",
            "snippet": "测试内容",
            "score": -0.1,
            "payload": {},
            "hash": "abc",
            "retrieved_ts_ms": 1710000000000,
        }
        errors = validate_evidence(evidence)
        assert any("score" in e for e in errors), \
            f"负数 score 应该报错，但收到: {errors}"

    def test_snippet_too_long(self):
        """snippet 超过 1200 字符上限"""
        evidence = {
            "id": "ev_001",
            "source": "qdrant",
            "title": "测试",
            "url": "https://example.com",
            "anchor": "section:1",
            "snippet": "A" * 1201,  # 故意超长
            "score": 0.9,
            "payload": {},
            "hash": "abc",
            "retrieved_ts_ms": 1710000000000,
        }
        errors = validate_evidence(evidence)
        assert any("snippet" in e or "1200" in e for e in errors), \
            f"超长 snippet 应该报错，但收到: {errors}"

    def test_payload_not_dict(self):
        """payload 不是字典"""
        evidence = {
            "id": "ev_001",
            "source": "qdrant",
            "title": "测试",
            "url": "https://example.com",
            "anchor": "section:1",
            "snippet": "测试内容",
            "score": 0.9,
            "payload": "这不是字典",
            "hash": "abc",
            "retrieved_ts_ms": 1710000000000,
        }
        errors = validate_evidence(evidence)
        assert any("payload" in e for e in errors), \
            f"payload 不是字典应该报错，但收到: {errors}"


class TestInvalidAssistantResponse:
    """测试非法的 AssistantResponse 数据"""

    def test_empty_dict(self):
        """空字典应该被拦住"""
        errors = validate_assistant_response({})
        assert len(errors) > 0, "空字典不应该通过校验"

    def test_missing_text(self):
        """缺少 text 字段"""
        resp = {
            "request_id": "req_001",
            # 故意不写 text
            "citations": [],
        }
        errors = validate_assistant_response(resp)
        assert any("text" in e for e in errors), \
            f"缺少 text 应该报错，但收到: {errors}"

    def test_orphan_citation_in_text(self):
        """正文里引用了 [2]，但 citations 里只有 [1]"""
        resp = {
            "request_id": "req_001",
            "text": "根据 [1] 和 [2] 的说明...",
            "citations": [
                {
                    "label": "[1]",
                    "url": "https://example.com",
                    "anchor": "section:1",
                    "title": "测试来源",
                },
                # 故意不给 [2] 的 citation
            ],
        }
        errors = validate_assistant_response(resp)
        assert any("[2]" in e for e in errors), \
            f"孤儿引用 [2] 应该报错，但收到: {errors}"

    def test_citations_not_list(self):
        """citations 不是列表"""
        resp = {
            "request_id": "req_001",
            "text": "你好",
            "citations": "这不是列表",
        }
        errors = validate_assistant_response(resp)
        assert any("citations" in e or "列表" in e for e in errors), \
            f"citations 不是列表应该报错，但收到: {errors}"

    def test_citation_missing_fields(self):
        """citation 里缺少必填字段"""
        resp = {
            "request_id": "req_001",
            "text": "根据 [1] 的说明...",
            "citations": [
                {
                    "label": "[1]",
                    # 故意不写 url、anchor、title
                },
            ],
        }
        errors = validate_assistant_response(resp)
        assert len(errors) > 0, "citation 缺字段应该报错"


class TestInvalidToolCallRequest:
    """测试非法的 ToolCallRequest 数据"""

    def test_empty_dict(self):
        """空字典应该被拦住"""
        errors = validate_tool_call_request({})
        assert len(errors) > 0, "空字典不应该通过校验"

    def test_invalid_tool_name(self):
        """tool 不在白名单里"""
        now = int(time.time() * 1000)
        req = {
            "request_id": "req_001",
            "step_id": "step_1",
            "tool": "hack_the_planet",  # 不在白名单里
            "args": {},
            "timeout_ms": 5000,
            "issued_ts_ms": now,
            "deadline_ts_ms": now + 10000,
            "idempotency_key": "idem_001",
            "allow_parallel": True,
        }
        errors = validate_tool_call_request(req)
        assert any("tool" in e for e in errors), \
            f"非法工具名应该报错，但收到: {errors}"

    def test_deadline_before_issued(self):
        """deadline 比 issued 还早（截止时间在发出之前，不合理）"""
        now = int(time.time() * 1000)
        req = {
            "request_id": "req_001",
            "step_id": "step_1",
            "tool": "qdrant_search",
            "args": {"query": "test"},
            "timeout_ms": 5000,
            "issued_ts_ms": now,
            "deadline_ts_ms": now - 1000,  # 故意早于 issued
            "idempotency_key": "idem_002",
            "allow_parallel": True,
        }
        errors = validate_tool_call_request(req)
        assert any("deadline" in e for e in errors), \
            f"deadline 早于 issued 应该报错，但收到: {errors}"

    def test_args_not_dict(self):
        """args 不是字典"""
        now = int(time.time() * 1000)
        req = {
            "request_id": "req_001",
            "step_id": "step_1",
            "tool": "qdrant_search",
            "args": "这不是字典",
            "timeout_ms": 5000,
            "issued_ts_ms": now,
            "deadline_ts_ms": now + 10000,
            "idempotency_key": "idem_003",
            "allow_parallel": True,
        }
        errors = validate_tool_call_request(req)
        assert any("args" in e for e in errors), \
            f"args 不是字典应该报错，但收到: {errors}"
