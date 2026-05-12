# 检索数据与 Qdrant 重建

Nervos Brain 的检索采用双层存储：SQLite archive 保存完整文本，Qdrant 保存向量和轻量 payload。archive DB 是可复现源，Docker Qdrant server 是运行时服务。

## 三套检索库

```text
retrieval              -> collection: nervos_docs
retrieval_forum_talk   -> collection: nervos_talk_user_discussions
retrieval_github_code  -> collection: nervos_github_code
```

对应 archive DB：

```text
data/archive.db
data/forum_talk/archive.db
data/github_code/archive.db
```

## 为什么 Docker Qdrant 数据不提交

`data/qdrant_server/` 是 Docker Qdrant 的运行目录，包含服务运行状态和本机存储细节，不适合作为公开仓库源数据提交。

公开仓库应保留可复现源：

```text
SQLite archive DB
source JSONL
必要的 Git LFS 大文件
迁移/重建脚本
```

新机器通过 archive DB 重建 Qdrant collection：

```bash
bash bootstrap_qdrant_server.sh
```

或手动执行：

```bash
docker compose -f docker-compose.qdrant.yml up -d
mamba run -n nervos-brain python scripts/migrate_qdrant_server_from_archive.py \
  --public-default-backends \
  --recreate
```

## Git LFS

GitHub code corpus 的部分大文件使用 Git LFS。clone 后必须执行：

```bash
git lfs pull
```

如果 archive DB 仍是 LFS pointer，迁移脚本会提示运行 `git lfs pull`。

## 本地 Qdrant fallback

仓库中也可能包含本地 Qdrant fallback 数据目录：

```text
data/qdrant_local/
data/qdrant_talk_forum/
data/qdrant_github_code/
```

它们主要用于本地或离线 fallback。线上 Telegram/Discord 多进程部署建议使用 Docker Qdrant server。

## Talk forum 增量更新

论坛已经完成全量爬取后，日常只需要增量更新：

```bash
mamba run -n nervos-brain python scripts/run_talk_forum_ingest.py --latest-pages 3 --incremental
```

推荐用 `deploy/systemd/` 里的用户 timer 每 24 小时执行一次。普通 Bot 问答不触发爬虫，也不直接写 archive/Qdrant。

增量更新可以在 Bot 运行时执行，不需要停机。Qdrant server 写入后，向量检索通常能直接使用新增内容；但 Bot 进程内的 BM25/fuzzy/exact 索引是启动时快照。若希望所有检索路径马上包含新增论坛内容，增量更新结束后重启 Bot 即可。

## GitHub docs/code 增量更新

GitHub docs/code 使用 repo commit 级别的真增量：每周检查目标 repo 的 default branch commit，只有发生变化的 repo 会重新爬取。成功写入新版本后，会清理该 repo 旧 commit 的 archive/Qdrant 记录，避免旧代码旧文档长期污染检索结果。

手动执行：

```bash
mamba run -n nervos-brain python scripts/run_github_docs_ingest.py --incremental
mamba run -n nervos-brain python scripts/run_github_code_ingest.py --incremental
```

文件边界：

```text
data/ingest_state/        # 本机运行 state，忽略，不提交
data/manifests/           # 公开数据 manifest，可提交
data/tmp/*_delta.jsonl    # 增量导出临时 JSONL，忽略，不提交
```

state 是当前机器的运行游标，不应提交。它回答的是“这台机器下次从哪里继续检查”，换一台机器后这个答案可能是错的。

manifest 是公开数据版本说明，可以提交。它回答的是“当前公开检索数据覆盖了哪些 repo/branch/commit”，方便交付方和其他开发者 clone 后确认数据版本。

## 新增资料或重爬时的安全边界

不要把以下内容写入公开数据：

```text
API key
Bot token
私有配置
群聊原文
runtime debug events
feedback JSONL
memory DB
日志片段
```

新增公开资料时，优先写到临时 JSONL 路径验证，再合并到公开 source JSONL，避免覆盖 canonical source 文件。例如先输出到：

```text
data/tmp/<source>_delta.jsonl
```

人工检查无私密内容后，再合并/dedupe 到 `data/sources/*.jsonl` 或执行对应 ingest。
