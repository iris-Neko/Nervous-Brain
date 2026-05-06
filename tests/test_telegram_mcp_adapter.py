from nervos_brain.tool_runtime.telegram_mcp_adapter import (
    parse_entity,
    parse_telegram_message_url,
)


def test_parse_entity_numeric():
    assert parse_entity("-100123456") == -100123456
    assert parse_entity("alice") == "alice"


def test_parse_telegram_message_url_username():
    parsed = parse_telegram_message_url("https://t.me/nervosnetwork/123")
    assert parsed == ("nervosnetwork", 123)


def test_parse_telegram_message_url_channel_style():
    parsed = parse_telegram_message_url("https://t.me/c/1234567890/88")
    assert parsed == (1234567890, 88)


def test_parse_telegram_message_url_invalid():
    assert parse_telegram_message_url("https://example.com/not-telegram") is None

