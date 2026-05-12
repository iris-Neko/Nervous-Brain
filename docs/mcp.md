# MCP 服务

本项目包含 Telegram MCP helper 和 Nervos Talk MCP。二者定位不同：Telegram MCP 用于 Telegram account 侧辅助工具；Talk MCP 用于只读查询公开 Nervos Talk 论坛内容。

## Nervos Talk MCP

Talk MCP 第一版只读，只访问公开 Discourse JSON API，不发帖、不登录、不写 archive DB、不写 Qdrant。

启动：

```bash
mamba run -n nervos-brain python scripts/run_talk_mcp_server.py serve
```

指定论坛地址：

```bash
mamba run -n nervos-brain python scripts/run_talk_mcp_server.py serve \
  --base-url https://talk.nervos.org
```

### 工具

```text
talk_search(query, limit=5, category="", time_range="")
talk_get_topic(topic_id, max_posts=50)
talk_get_post(topic_id, post_number)
talk_latest(category="", limit=10)
```

输出字段会尽量兼容现有 `discourse_query` evidence 结构，便于后续 ToolRuntime 复用。

### 环境变量

```text
NERVOS_TALK_BASE_URL       默认 https://talk.nervos.org
NERVOS_TALK_API_KEY        可选，只在私有分类或限流场景需要
NERVOS_TALK_API_USER       可选，配合 API key 使用
NERVOS_TALK_REQUEST_DELAY  可选，请求间隔秒数
NERVOS_TALK_TIMEOUT_S      可选，请求超时秒数
```

公开只读模式不要求甲方提供 API key。

## Telegram MCP

Telegram MCP runner：

```bash
mamba run -n nervos-brain python scripts/run_telegram_mcp_server.py --help
```

它需要 Telegram API ID 和 API Hash，而不是 Bot token：

```text
TELEGRAM_API_ID
TELEGRAM_API_HASH
```

Telegram MCP 会产生本地 session state，应按私有运行数据处理，不提交。

## 与 Bot runtime 的关系

MCP 服务是工具侧能力，不等于 Telegram/Discord Bot runtime。普通 Bot 问答不应该触发 Talk forum 数据库更新；Talk 数据库 freshness 由定时增量 ingest 负责。
