from nervos_brain.response_normalizer.platform_formatter import (
    format_response_to_outbound,
    get_platform_capabilities,
)


def _base_response(text: str) -> dict:
    return {
        "request_id": "req_001",
        "text": text,
        "citations": [
            {
                "label": "[1]",
                "url": "https://example.com",
                "anchor": "a1",
                "title": "Doc 1",
            }
        ],
    }


def _entity_types(segment: dict) -> set[str]:
    return {str(entity.get("type")) for entity in segment.get("entities", [])}


def test_discord_chunk_limit_2000():
    response = _base_response("A" * 2105)
    context = {"platform": "discord", "user_id": "u1"}
    outbound = format_response_to_outbound(response=response, context=context)

    assert outbound["context"]["platform"] == "discord"
    assert len(outbound["segments"]) == 2
    assert all(seg["char_count"] <= 2000 for seg in outbound["segments"])


def test_telegram_chunk_limit_4096():
    response = _base_response("A" * 4500)
    context = {"platform": "telegram", "user_id": "u1"}
    outbound = format_response_to_outbound(response=response, context=context)

    assert outbound["context"]["platform"] == "telegram"
    assert len(outbound["segments"]) == 2
    assert all(seg["char_count"] <= 4096 for seg in outbound["segments"])


def test_plain_mode_strips_markdown():
    response = _base_response("Hello **World** [1] and `code`")
    context = {"platform": "discord", "user_id": "u1"}
    outbound = format_response_to_outbound(
        response=response,
        context=context,
        render_mode="plain",
    )

    text = outbound["segments"][0]["text"]
    assert "**" not in text
    assert "`" not in text
    assert "[1]" in text


def test_telegram_markdown_escapes_and_citations():
    response = _base_response("Fiber_open [1] (safe)")
    context = {"platform": "telegram", "user_id": "u1"}
    outbound = format_response_to_outbound(
        response=response,
        context=context,
        render_mode="markdown",
    )
    text = outbound["segments"][0]["text"]
    assert "Fiber_open" in text
    assert "[1]" in text
    assert "[1]" in outbound["segments"][0]["citation_labels"]


def test_telegram_markdown_converts_headings_and_bold():
    response = _base_response(
        "### 你要记住\n"
        "- send_transaction **不代表已经上链确认**。[1]\n"
        "1. **Python 版最小可跑流程**"
    )
    context = {"platform": "telegram", "user_id": "u1"}
    outbound = format_response_to_outbound(
        response=response,
        context=context,
        render_mode="markdown",
    )

    text = outbound["segments"][0]["text"]
    assert "###" not in text
    assert "**" not in text
    assert "你要记住" in text
    assert "不代表已经上链确认" in text
    assert "Python 版最小可跑流程" in text
    assert "[1]" in text
    assert "bold" in _entity_types(outbound["segments"][0])


def test_telegram_markdown_preserves_code_blocks():
    response = _base_response(
        "### 示例\n"
        "```python\n"
        "tx_hash = rpc(\"send_transaction\", [signed_tx])\n"
        "print(\"tx_hash:\", tx_hash)\n"
        "```\n"
        "**结束**"
    )
    context = {"platform": "telegram", "user_id": "u1"}
    outbound = format_response_to_outbound(
        response=response,
        context=context,
        render_mode="markdown",
    )

    text = outbound["segments"][0]["text"]
    assert "```" not in text
    assert 'rpc("send_transaction", [signed_tx])' in text
    assert "结束" in text
    assert "pre" in _entity_types(outbound["segments"][0])


def test_telegram_long_code_block_splits_with_entities():
    code = "\n".join(f"print({idx})" for idx in range(700))
    response = _base_response(
        "### 长代码示例\n"
        "```python\n"
        f"{code}\n"
        "```\n"
        "结束 [1]"
    )
    context = {"platform": "telegram", "user_id": "u1"}
    outbound = format_response_to_outbound(
        response=response,
        context=context,
        render_mode="markdown",
    )

    assert len(outbound["segments"]) > 1
    assert all(seg["char_count"] <= 4096 for seg in outbound["segments"])
    assert all(seg.get("parse_mode_enabled") is False for seg in outbound["segments"])
    assert any("pre" in _entity_types(seg) for seg in outbound["segments"])
    assert not any("```" in seg["text"] for seg in outbound["segments"])
    assert "[1]" in outbound["segments"][-1]["citation_labels"]


def test_capabilities_profile():
    discord_caps = get_platform_capabilities("discord")
    tg_caps = get_platform_capabilities("telegram")

    assert discord_caps["max_chars_per_segment"] == 2000
    assert tg_caps["max_chars_per_segment"] == 4096
