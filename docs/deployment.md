# 新服务器部署

本文档描述从 fresh clone 到 Bot 可运行的标准流程。默认目标环境是 Linux、`mamba`、Docker、Docker Qdrant server 和 `nervos-brain` Python 环境。

## 1. 准备系统依赖

需要先准备：

```text
git
git-lfs
mamba 或 miniforge
Docker 和 Docker Compose
```

确认命令可用：

```bash
git --version
git lfs version
mamba --version
docker --version
docker compose version
```

如果 `docker ps` 报 `permission denied while trying to connect to the docker API`，说明当前用户没有 Docker daemon 权限。处理方式见 [故障排查](troubleshooting.md)。

## 2. Clone 仓库和数据

```bash
git lfs install
git clone https://github.com/iris-Neko/Nervos-Brain.git
cd Nervos-Brain
git checkout dev  # 当前交付/部署分支
git lfs pull
```

`git lfs pull` 很重要。部分 archive DB / Qdrant fallback 文件由 LFS 管理，如果只拿到 LFS pointer，后续迁移会失败。

## 3. 创建 Python 环境

```bash
mamba env create -f environment.yml
# 如果 nervos-brain 环境已存在，改用：
# mamba env update -n nervos-brain -f environment.yml --prune
mamba run -n nervos-brain python --version
```

本项目测试和脚本默认从仓库运行，不要求 `pip install -e .`。

## 4. 创建本地配置

```bash
cp config.yaml.example config.yaml
```

在 `config.yaml` 中填写 LLM provider、Telegram Bot、Discord Bot 等本地配置。最低必填项：

```text
llm.api_key 或对应 provider 的 API key
llm.api_base，如果使用 OpenAI-compatible endpoint
TELEGRAM_BOT_TOKEN，如果启动 Telegram Bot
DISCORD_BOT_TOKEN，如果启动 Discord Bot
```

也可以用环境变量提供平台 token：

```bash
export TELEGRAM_BOT_TOKEN="<TELEGRAM_BOT_TOKEN>"
export DISCORD_BOT_TOKEN="<DISCORD_BOT_TOKEN>"
```

`config.yaml` 不应提交。`config.yaml.example` 只是模板，runtime 不会把它当正式配置 fallback。

## 5. 启动 Qdrant server 并重建 collection

推荐部署使用 Docker Qdrant server，避免 Telegram 和 Discord 多进程同时访问本地 Qdrant 目录产生锁冲突。

```bash
bash bootstrap_qdrant_server.sh
```

脚本会执行：

```text
1. docker compose -f docker-compose.qdrant.yml up -d
2. 等待 http://127.0.0.1:6333 可用
3. 从 SQLite archive DB 重建 docs/forum/github_code 三套 collection
4. 打印 Qdrant collection 状态
```

手动检查：

```bash
curl http://127.0.0.1:6333/collections
```

## 6. 启动 Telegram Bot

```bash
export TELEGRAM_BOT_TOKEN="<TELEGRAM_BOT_TOKEN>"
bash restart_telegram_bot.sh
```

日志默认在：

```text
data/logs/telegram_bot_polling.stdout.log
data/logs/telegram_bot_polling.stderr.log
```

## 7. 启动 Discord Bot

```bash
export DISCORD_BOT_TOKEN="<DISCORD_BOT_TOKEN>"
mamba run -n nervos-brain python scripts/run_discord_bot.py
```

这个命令是前台运行，适合首次验证。长期运行请用 `tmux`、`systemd`、`supervisor` 或部署方已有进程管理；当前仓库暂未提供 Discord 专用 restart 脚本。Discord 配置见 `config.yaml.example` 的 `discord_bot` 区块。

## 8. 部署后验收

建议至少运行：

```bash
bash -n bootstrap_qdrant_server.sh restart_telegram_bot.sh
mamba run -n nervos-brain pytest tests/test_qdrant_server_migration.py tests/test_retrieval_unit.py -q
mamba run -n nervos-brain python -m py_compile \
  scripts/migrate_qdrant_server_from_archive.py \
  scripts/run_talk_mcp_server.py \
  scripts/run_talk_forum_ingest.py
```

更多验收项目见 [测试与验收](testing-and-acceptance.md)。
