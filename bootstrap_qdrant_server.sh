#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MAMBA_BIN="${MAMBA_BIN:-mamba}"
MAMBA_ENV="${MAMBA_ENV:-nervos-brain}"
QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
QDRANT_RECREATE="${QDRANT_RECREATE:-1}"
QDRANT_BATCH_SIZE="${QDRANT_BATCH_SIZE:-256}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Cannot find docker. Install Docker first." >&2
  exit 2
fi

if ! command -v "$MAMBA_BIN" >/dev/null 2>&1; then
  echo "Cannot find mamba command: $MAMBA_BIN" >&2
  echo "Set MAMBA_BIN=/path/to/mamba if needed." >&2
  exit 2
fi

if ! docker info >/dev/null 2>&1; then
  echo "Cannot access Docker daemon. Check Docker service and user permissions." >&2
  exit 2
fi

echo "[1/4] Starting Qdrant Docker server..."
docker compose -f docker-compose.qdrant.yml up -d

echo "[2/4] Waiting for Qdrant at ${QDRANT_URL}..."
for _ in $(seq 1 60); do
  if curl -fsS "${QDRANT_URL}/collections" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "${QDRANT_URL}/collections" >/dev/null 2>&1; then
  echo "Qdrant did not become ready at ${QDRANT_URL}" >&2
  exit 2
fi

echo "[3/4] Rebuilding Qdrant collections from tracked archive DBs..."
migrate_args=(
  run -n "$MAMBA_ENV"
  python scripts/migrate_qdrant_server_from_archive.py
  --url "$QDRANT_URL"
  --public-default-backends
  --batch-size "$QDRANT_BATCH_SIZE"
)
if [[ "$QDRANT_RECREATE" != "0" ]]; then
  migrate_args+=(--recreate)
fi
"$MAMBA_BIN" "${migrate_args[@]}"

echo "[4/4] Collection status:"
curl -fsS "${QDRANT_URL}/collections" || true
echo
