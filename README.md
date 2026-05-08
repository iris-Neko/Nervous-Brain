# Nervos Brain

Nervos Brain is a CKB/Nervos-focused Agentic RAG bot for Telegram and Discord.
It combines official documentation, Nervos Talk/forum discussions, and GitHub
source code into a multi-backend retrieval runtime backed by SQLite archives,
Qdrant vector indexes, and LangGraph answer generation.

## What Is Included

- Telegram and Discord online bot runtimes.
- Multi-step graph workflow for clarification, retrieval planning, tool calls,
  answer generation, self-checking, and platform formatting.
- Three retrieval corpora:
  - `retrieval`: official docs/GitHub documentation.
  - `retrieval_forum_talk`: Nervos Talk/community discussions.
  - `retrieval_github_code`: Nervos-related GitHub source code.
- Dual-layer storage:
  - SQLite archive DBs store full source text and metadata.
  - Qdrant stores shallow vector payloads for fast retrieval.
  - JSONL source exports preserve raw crawl output where available.
- GitHub code crawler for public Nervos-related repositories.
- Qdrant server mode for running Telegram and Discord as separate processes
  without Qdrant local directory lock conflicts.

## Clone With Data

The GitHub source-code database artifacts are stored with Git LFS. Install Git
LFS before cloning or pull LFS objects after cloning:

```bash
git lfs install
git clone https://github.com/iris-Neko/Nervos-Brain.git
cd Nervos-Brain
git checkout dev
git lfs pull
```

LFS-tracked code corpus artifacts:

```text
data/github_code/archive.db
data/qdrant_github_code/collection/nervos_github_code/storage.sqlite
data/qdrant_github_code/meta.json
data/sources/github_code.jsonl
```

The current GitHub code corpus contains `23125` records across `169` repositories.
The local artifact size is roughly `334M`.

## Environment

Create the Python environment:

```bash
mamba env create -f environment.yml
```

Run commands through the environment:

```bash
mamba run -n nervos-brain python -m pytest -q
```

The project uses a `src/` layout. Tests are configured to import from `src`
without requiring `pip install -e .`.

## Configuration

Copy the template and fill in local secrets:

```bash
cp config.yaml.example config.yaml
```

Do not commit `config.yaml`. It may contain LLM API keys, Telegram tokens, or
Discord tokens. Runtime path handling is centralized in
`src/nervos_brain/pathing.py`, and relative paths are resolved from the project
root.

Important environment variables:

```bash
TELEGRAM_BOT_TOKEN=...
DISCORD_BOT_TOKEN=...
NERVOS_BRAIN_CONFIG=/optional/path/to/config.yaml
```

## Qdrant Server Mode

For deployment, use Qdrant server mode so Telegram and Discord can run as
separate processes and share the same vector service:

```bash
docker compose -f docker-compose.qdrant.yml up -d
curl http://127.0.0.1:6333/
```

The compose file binds Qdrant to localhost only:

```text
127.0.0.1:6333
```

Rebuild server collections from SQLite archives:

```bash
PYTHONPATH=src mamba run -n nervos-brain python scripts/migrate_qdrant_server_from_archive.py --recreate
```

`config.yaml.example` defaults to:

```yaml
retrieval:
  qdrant_url: "http://127.0.0.1:6333"
```

Clear `qdrant_url` to fall back to local Qdrant directory mode using
`qdrant_path`.

## Retrieval Data Layout

Main data paths:

```text
data/archive.db                                      # docs archive
data/forum_talk/archive.db                           # forum archive
data/github_code/archive.db                          # source-code archive
data/qdrant_local/                                   # docs local Qdrant fallback
data/qdrant_talk_forum/                              # forum local Qdrant fallback
data/qdrant_github_code/                             # source-code local Qdrant fallback
data/sources/github_docs.jsonl                       # docs source export
data/sources/github_code.jsonl                       # code source export
```

Runtime-private data is intentionally ignored:

```text
data/logs/
data/telegram_bot/
data/discord_bot/
data/tmp/
data/qdrant_server/
```

## Rebuild GitHub Code Corpus

To recrawl the GitHub source-code corpus, use a GitHub token to avoid rate
limits:

```bash
export GITHUB_TOKEN="..."
```

Dry-run crawl to JSONL:

```bash
mamba run -n nervos-brain python scripts/run_github_code_ingest.py \
  --github-token "$GITHUB_TOKEN" \
  --no-ingest \
  --jsonl-out data/sources/github_code.jsonl
```

Full ingest:

```bash
mamba run -n nervos-brain python scripts/run_github_code_ingest.py \
  --github-token "$GITHUB_TOKEN" \
  --jsonl-out data/sources/github_code.jsonl
```

Default GitHub targets are public, non-archived repositories under:

```text
nervosnetwork
web5fans
ckb-devrel
RGBPlusPlus
appfi5
```

`RGBPlusPlus` is treated specially during corpus construction because its
repositories may be forks but are still part of the requested source set.

## Run The Bots

Telegram polling runtime:

```bash
export TELEGRAM_BOT_TOKEN="..."
bash restart_telegram_bot.sh
```

Or run directly:

```bash
mamba run -n nervos-brain python scripts/run_telegram_bot_polling.py
```

Discord runtime:

```bash
export DISCORD_BOT_TOKEN="..."
mamba run -n nervos-brain python scripts/run_discord_bot.py
```

Telegram supports concurrent update workers through `telegram_bot.max_worker_threads`.
Discord uses `discord_bot.max_worker_threads`; messages in the same Discord
channel stay ordered, while different channels can be processed concurrently.

## Architecture

High-level runtime flow:

1. Telegram or Discord receives an update/message.
2. A platform adapter converts it into a shared message envelope and graph state.
3. The full graph assesses missing information, plans retrieval, executes tools,
   merges evidence, composes an answer, self-checks, and formats output.
4. The platform runtime sends message segments back to Telegram or Discord.
5. Optional feedback/debug/memory data is written to local runtime paths.

Retrieval flow:

1. Crawlers produce normalized `RawDocument` rows.
2. `IngestionPipeline` writes documents through `DualLayerWriter`.
3. `ArchiveStore` stores full raw text in SQLite.
4. Qdrant stores compact vector payloads.
5. `MultiRetriever` combines vector, BM25, fuzzy, and exact search.
6. `CompositeRetriever` queries configured backends and merges evidence.

## Tests

Common checks:

```bash
mamba run -n nervos-brain python -m pytest -q
```

Focused retrieval/Qdrant checks:

```bash
mamba run -n nervos-brain python -m pytest -q \
  tests/test_retrieval_unit.py \
  tests/test_retrieval_advanced.py \
  tests/test_composite_retriever.py \
  tests/test_qdrant_server_migration.py
```

Bot runtime checks:

```bash
mamba run -n nervos-brain python -m pytest -q \
  tests/test_telegram_bot_runtime.py \
  tests/test_discord_bot_runtime.py
```

## Security Notes

Never commit:

- `config.yaml`, `.env*`, API keys, Telegram bot tokens, Discord bot tokens.
- Runtime logs, Telegram/Discord memory DBs, debug events, feedback files, and
  attachment downloads.
- Temporary crawler workspaces and local Qdrant server runtime storage.

If a token is pasted into a chat, issue tracker, or commit by mistake, revoke it
and create a new one.
