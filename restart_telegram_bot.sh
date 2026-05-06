#!/usr/bin/env bash
set -Eeuo pipefail

ENV_NAME="${ENV_NAME:-nervous-brain}"
SCRIPT_PATH="${SCRIPT_PATH:-scripts/run_telegram_bot_polling.py}"
MAMBA_BIN="${MAMBA_BIN:-${MICROMAMBA_BIN:-mamba}}"
LOG_DIR="${LOG_DIR:-data/logs}"
STARTUP_WAIT_SECONDS="${STARTUP_WAIT_SECONDS:-3}"
DEBUG="${DEBUG:-0}"
DRY_RUN="${DRY_RUN:-0}"
DROP_PENDING_ON_START="${DROP_PENDING_ON_START:-0}"

ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$ROOT"

mkdir -p "$LOG_DIR"
STDOUT_LOG="$LOG_DIR/telegram_bot_polling.stdout.log"
STDERR_LOG="$LOG_DIR/telegram_bot_polling.stderr.log"
PID_FILE="$LOG_DIR/telegram_bot_polling.pid"
SCRIPT_NAME="$(basename "$SCRIPT_PATH")"

print_log_tail() {
  local file="$1"
  local label="$2"
  if [[ -s "$file" ]]; then
    echo "----- $label tail -----" >&2
    tail -n 80 "$file" >&2 || true
    echo "----- end $label -----" >&2
  fi
}

bot_pids() {
  pgrep -f "$SCRIPT_PATH|$SCRIPT_NAME" 2>/dev/null | grep -v "^$$$" || true
}

if [[ ! -f "$SCRIPT_PATH" ]]; then
  echo "Cannot find $SCRIPT_PATH under project root: $ROOT" >&2
  echo "Put this script in the repository root, or run with PROJECT_ROOT=/path/to/repo." >&2
  exit 1
fi

if ! command -v "$MAMBA_BIN" >/dev/null 2>&1; then
  echo "Cannot find mamba command: $MAMBA_BIN" >&2
  echo "Install mamba/micromamba, initialize your shell, or set MAMBA_BIN=/path/to/mamba." >&2
  exit 1
fi

echo "[1/3] Stopping existing Telegram bot processes..."
mapfile -t existing_pids < <(bot_pids)
for pid in "${existing_pids[@]}"; do
  if [[ -n "$pid" ]]; then
    echo "  stop pid=$pid"
    kill "$pid" 2>/dev/null || true
  fi
done

sleep 1
mapfile -t remaining_pids < <(bot_pids)
if (( ${#remaining_pids[@]} > 0 )); then
  echo "  force stop: ${remaining_pids[*]}"
  kill -9 "${remaining_pids[@]}" 2>/dev/null || true
fi

sleep 0.5
mapfile -t still_running < <(bot_pids)
if (( ${#still_running[@]} > 0 )); then
  echo "Failed to stop bot process(es): ${still_running[*]}" >&2
  exit 1
fi

echo "[2/3] Starting Telegram bot..."
args=(run -n "$ENV_NAME" python "$SCRIPT_PATH")
if [[ "$DEBUG" == "1" || "$DEBUG" == "true" ]]; then
  args+=(--debug)
fi
if [[ "$DRY_RUN" == "1" || "$DRY_RUN" == "true" ]]; then
  args+=(--dry-run)
fi
if [[ "$DROP_PENDING_ON_START" == "1" || "$DROP_PENDING_ON_START" == "true" ]]; then
  args+=(--drop-pending-on-start)
fi

nohup "$MAMBA_BIN" "${args[@]}" >"$STDOUT_LOG" 2>"$STDERR_LOG" &
bot_pid="$!"
echo "$bot_pid" > "$PID_FILE"

echo "  pid=$bot_pid"
echo "  stdout=$STDOUT_LOG"
echo "  stderr=$STDERR_LOG"

for _ in $(seq 1 "$STARTUP_WAIT_SECONDS"); do
  if ! kill -0 "$bot_pid" 2>/dev/null; then
    echo "Bot process exited during startup." >&2
    print_log_tail "$STDERR_LOG" "stderr"
    print_log_tail "$STDOUT_LOG" "stdout"
    exit 1
  fi
  sleep 1
done

echo "[3/3] Current bot processes:"
mapfile -t current_pids < <(bot_pids)
if (( ${#current_pids[@]} == 0 )); then
  echo "No bot process found after startup. Check $STDERR_LOG" >&2
  print_log_tail "$STDERR_LOG" "stderr"
  print_log_tail "$STDOUT_LOG" "stdout"
  exit 1
fi

pid_list="$(IFS=,; echo "${current_pids[*]}")"
ps -fp "$pid_list"
echo "Telegram bot restarted."
