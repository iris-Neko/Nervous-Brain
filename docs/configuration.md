# 配置说明

正式运行读取 `config.yaml`。`config.yaml.example` 只是模板，不作为 runtime fallback。部署时应复制模板并填写本地配置：

```bash
cp config.yaml.example config.yaml
```

## 路径规则

配置里的路径应保持相对路径，例如：

```yaml
logging:
  log_dir: "data/logs"
telegram_bot:
  memory_db: "data/telegram_bot/memory.db"
```

运行时由 `src/nervos_brain/pathing.py` 统一解析。不要把本机绝对路径写入公开配置或文档。

## LLM 配置

`llm` 是默认模型配置，`llm_profiles` 是 graph 节点动态路由使用的模型档位。

常见必填项：

```yaml
llm:
  model: "openai/gpt-5.4"
  api_key: "<LLM_API_KEY>"
  api_base: "https://your-openai-compatible-endpoint/v1"
```

当前业务档位包括：

```text
router
low
mini_high
medium
high
```

`mini_high` 是低成本深思考档，适合 InfoGap、检索规划和轻量反思。`medium` / `high` 用于更复杂的最终回答、代码/协议/架构和一致性检查。

## Retrieval 配置

主要有三套 backend：

```text
retrieval              # docs / official docs
retrieval_forum_talk   # Nervos Talk forum
retrieval_github_code  # GitHub source code
```

`retrieval_backends` 决定 runtime 是否使用多库统一检索。默认应包含 docs、forum 和 github_code 三库，使 `qdrant_search` 能一次查询全部 configured backends。

推荐部署使用 Qdrant server：

```yaml
retrieval:
  qdrant_url: "http://127.0.0.1:6333"
```

如果清空 `qdrant_url`，系统会回退到 `qdrant_path` 本地目录模式。线上 Telegram/Discord 多进程部署不建议使用本地目录模式。

## Telegram 配置

`telegram_bot` 控制轮询、过滤、memory、feedback、debug 和耗时预算。

常见项：

```yaml
telegram_bot:
  offset_file: "data/telegram_bot/offset.txt"
  memory_db: "data/telegram_bot/memory.db"
  feedback_file: "data/telegram_bot/feedback.jsonl"
  debug_log_file: "data/telegram_bot/debug_events.jsonl"
  allowed_chat_ids: []
  append_csat: true
```

`TELEGRAM_BOT_TOKEN` 可以通过环境变量提供。

## Discord 配置

`discord_bot` 控制 Discord runtime、memory、feedback、debug、allowed guild/channel 和并发。

常见项：

```yaml
discord_bot:
  memory_db: "data/discord_bot/memory.db"
  feedback_file: "data/discord_bot/feedback.jsonl"
  debug_log_file: "data/discord_bot/debug_events.jsonl"
  mention_only_in_guild: true
  allowed_channel_ids: []
  allowed_guild_ids: []
```

`DISCORD_BOT_TOKEN` 可以通过环境变量提供。

## Logging 配置

`logging` 控制控制台和文件日志：

```yaml
logging:
  level: "INFO"
  third_party_level: "WARNING"
  log_dir: "data/logs"
  file: true
```

`data/logs/` 是 runtime 私有数据，不提交。

## 不应提交的配置

不要提交：

```text
config.yaml
.env*
LLM API key
Telegram Bot token
Discord Bot token
Qdrant API key
runtime logs
memory DB
feedback/debug 文件
```
