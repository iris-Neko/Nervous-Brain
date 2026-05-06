# ============================================================
# test_m3_smoke.py —— M3 冒烟测试
# ============================================================
# 这个文件验证 M3-T2 到 M3-T10 的所有内容都能正常运行。
#
# 运行方式：
#   conda activate NervosBrain
#   cd 项目根目录
#   pytest tests/test_m3_smoke.py -v -s
#   （-s 参数让 print 输出也显示出来，方便看流程）
# ============================================================

from nervos_brain.graph_engine.mini_graph import build_mini_graph
from nervos_brain.graph_engine.sample_data import (
    make_insufficient_state,
    make_sufficient_state,
)
from nervos_brain.response_normalizer.normalizer import (
    chunk_for_platform,
    normalize_citations,
    sanitize_markdown,
    validate_response_shape,
)


class TestSampleData:
    """M3-T2: 测试样例数据能不能正常构造"""

    def test_sufficient_state_has_evidence(self):
        """信息充足的样例应该有证据"""
        state = make_sufficient_state()
        assert state["request_id"] == "req_demo_001"
        assert len(state["evidence"]) > 0
        assert len(state["info_needs"]) == 0

    def test_insufficient_state_has_info_needs(self):
        """缺参数的样例应该有信息缺口"""
        state = make_insufficient_state()
        assert state["request_id"] == "req_demo_002"
        assert len(state["evidence"]) == 0
        assert len(state["info_needs"]) > 0
        assert state["info_needs"][0]["required"] is True


class TestMiniGraph:
    """M3-T3/T4/T5/T6: 测试 LangGraph 最小流程"""

    def test_sufficient_goes_to_answer(self):
        """信息充足 → 应该走 AnswerComposer → 得到带引用的回答"""
        graph = build_mini_graph()
        state = make_sufficient_state()
        result = graph.invoke(state)

        resp = result["_final_response"]
        assert "request_id" in resp
        assert "text" in resp
        assert "citations" in resp
        assert len(resp["citations"]) > 0
        # 回答里应该有 [1] 这样的引用
        assert "[1]" in resp["text"]

    def test_insufficient_goes_to_ask_user(self):
        """缺参数 → 应该走 AskUser → 得到反问"""
        graph = build_mini_graph()
        state = make_insufficient_state()
        result = graph.invoke(state)

        resp = result["_final_response"]
        assert resp["need_user_input"] is True
        assert "ask_user_question" in resp
        assert len(resp["citations"]) == 0


class TestValidateResponseShape:
    """M3-T7: 测试最小输出类型检查"""

    def test_valid_response(self):
        """合法回答应该通过"""
        resp = {
            "request_id": "req_001",
            "text": "Cell 是 CKB 的基本单元。",
            "citations": [],
        }
        ok, errors = validate_response_shape(resp)
        assert ok is True
        assert errors == []

    def test_missing_text(self):
        """缺 text 应该不通过"""
        resp = {"request_id": "req_001", "citations": []}
        ok, errors = validate_response_shape(resp)
        assert ok is False
        assert any("text" in e for e in errors)


class TestNormalizeCitations:
    """M3-T8: 测试引用编号修复"""

    def test_normal_case(self):
        """正常情况：编号和 citations 对应"""
        text = "Cell 很重要 [1]。"
        cits = [{"label": "[1]", "url": "a", "anchor": "a", "title": "A"}]
        new_text, new_cits = normalize_citations(text, cits)
        assert "[1]" in new_text
        assert len(new_cits) == 1

    def test_orphan_removed(self):
        """孤儿引用（text 里有 [3] 但 citations 里没有）应该被删掉"""
        text = "根据 [1] 和 [3] 的说明"
        cits = [{"label": "[1]", "url": "a", "anchor": "a", "title": "A"}]
        new_text, new_cits = normalize_citations(text, cits)
        assert "[3]" not in new_text
        assert "[1]" in new_text
        assert len(new_cits) == 1

    def test_unused_citation_removed(self):
        """text 里没用到的 citation 应该被删掉"""
        text = "只有 [1] 的内容"
        cits = [
            {"label": "[1]", "url": "a", "anchor": "a", "title": "A"},
            {"label": "[2]", "url": "b", "anchor": "b", "title": "B"},
        ]
        new_text, new_cits = normalize_citations(text, cits)
        assert len(new_cits) == 1
        assert new_cits[0]["label"] == "[1]"

    def test_empty_citations(self):
        """没有引用时，正文里的引用标记应该被清掉"""
        text = "这里有个假引用 [1]"
        new_text, new_cits = normalize_citations(text, [])
        assert "[1]" not in new_text
        assert new_cits == []


class TestSanitizeMarkdown:
    """M3-T9: 测试 Markdown 修复"""

    def test_unclosed_code_block(self):
        """未闭合的代码块应该被自动闭合"""
        text = "看代码：\n```python\nprint('hello')"
        fixed = sanitize_markdown(text)
        # 修复后应该有偶数个 ```
        assert fixed.count("```") % 2 == 0

    def test_think_tags_removed(self):
        """<think> 标签应该被清理"""
        text = "你好<think>这是内部推理过程</think>世界"
        fixed = sanitize_markdown(text)
        assert "<think>" not in fixed
        assert "内部推理" not in fixed
        assert "你好" in fixed
        assert "世界" in fixed

    def test_excessive_newlines(self):
        """超过 2 个连续空行应该被压缩"""
        text = "第一段\n\n\n\n\n第二段"
        fixed = sanitize_markdown(text)
        # 最多只有 2 个连续换行
        assert "\n\n\n" not in fixed
        assert "第一段" in fixed
        assert "第二段" in fixed


class TestChunkForPlatform:
    """M3-T10: 测试平台切段"""

    def test_short_text_no_split(self):
        """短文本不需要切分"""
        text = "这是一句很短的话。"
        chunks = chunk_for_platform(text, max_chars=2000)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_splits(self):
        """长文本应该被切成多段"""
        # 构造一个超过 200 字符的文本
        text = ("这是一个段落。" * 20 + "\n\n") * 5
        chunks = chunk_for_platform(text, max_chars=200)
        assert len(chunks) > 1
        # 每段都不应超过限制
        for chunk in chunks:
            assert len(chunk) <= 200, f"段落超长: {len(chunk)} 字符"

    def test_all_content_preserved(self):
        """切分后的内容拼起来应该包含原始内容的所有文字"""
        text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
        chunks = chunk_for_platform(text, max_chars=20)
        joined = " ".join(chunks)
        assert "第一段内容" in joined
        assert "第二段内容" in joined
        assert "第三段内容" in joined
