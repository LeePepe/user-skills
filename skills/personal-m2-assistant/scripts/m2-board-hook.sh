#!/bin/sh
set -eu

if [ "${1:-}" = "--install" ]; then
  shift
  SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
  HOME_DIR="${HOME:-$(cd ~ && pwd)}"
  for candidate in \
    "$SCRIPT_DIR/m2-router.py" \
    "$HOME_DIR/Development/user-skills/skills/personal-m2-assistant/scripts/m2-router.py" \
    "$HOME_DIR/.copilot/skills/personal-m2-assistant/scripts/m2-router.py"; do
    if [ -f "$candidate" ]; then
      exec python3 "$candidate" --install-hook "$@"
    fi
  done
  exit 1
fi

# Consume the JSON hook payload immediately so Copilot CLI can close the hook pipe in 0ms.
cat >/dev/null 2>&1 || true

# Only trigger inside Herdr environment.
[ "${HERDR_ENV:-}" = "1" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

HOME_DIR="${HOME:-$(cd ~ && pwd)}"
STATE_FILE="${M2_SUPERVISION_STATE_FILE:-/tmp/m2-supervision-state.json}"
HOOK_STATE_DIR="${M2_BOARD_HOOK_STATE_DIR:-/tmp/m2-board-hook}"
LOCK_DIR="${M2_BOARD_HOOK_LOCK_DIR:-$HOOK_STATE_DIR/m2-board-sync.lock}"
HASH_FILE="$HOOK_STATE_DIR/last-board.sha256"
LAST_SYNC_FILE="$HOOK_STATE_DIR/last-linear-sync.epoch"
DIRTY_FILE="$HOOK_STATE_DIR/linear-sync.dirty"
LOG_FILE="$HOOK_STATE_DIR/m2-board-hook.log"
HUB_ISSUE="${M2_SYNC_HUB_ISSUE:-TIA-463}"
COOLDOWN_SEC="${M2_BOARD_HOOK_COOLDOWN_SEC:-60}"

resolve_router_script() {
  if [ -n "${M2_ROUTER_SCRIPT:-}" ] && [ -f "${M2_ROUTER_SCRIPT}" ]; then
    printf '%s\n' "${M2_ROUTER_SCRIPT}"
    return 0
  fi
  SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
  for candidate in \
    "$SCRIPT_DIR/m2-router.py" \
    "$HOME_DIR/Development/user-skills/skills/personal-m2-assistant/scripts/m2-router.py" \
    "$HOME_DIR/.copilot/skills/personal-m2-assistant/scripts/m2-router.py"; do
    if [ -f "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

run_local_status() {
  if [ -n "${M2_HOOK_HERDR_AGENT_JSON:-}" ]; then
    python3 "$ROUTER_SCRIPT" --status --no-sync-linear --state-file "$STATE_FILE" --herdr-agent-json "${M2_HOOK_HERDR_AGENT_JSON}" >>"$LOG_FILE" 2>&1 || true
  else
    python3 "$ROUTER_SCRIPT" --status --no-sync-linear --state-file "$STATE_FILE" >>"$LOG_FILE" 2>&1 || true
  fi
}

run_worker() {
  ROUTER_SCRIPT="$(resolve_router_script)" || exit 0
  mkdir -p "$HOOK_STATE_DIR" 2>/dev/null || true

  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    touch "$DIRTY_FILE" 2>/dev/null || true
    exit 0
  fi
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT HUP INT TERM

  # 1. Refresh local supervision ledger without any network call (0 tokens, 0 API requests).
  run_local_status

  # 2. Compute SHA-256 fingerprint of local slot status + summaries.
  NEW_HASH="$(python3 "$ROUTER_SCRIPT" --board-fingerprint --state-file "$STATE_FILE" 2>/dev/null || true)"
  [ -n "$NEW_HASH" ] || exit 0

  OLD_HASH=""
  if [ -f "$HASH_FILE" ]; then
    OLD_HASH="$(cat "$HASH_FILE" 2>/dev/null || true)"
  fi

  if [ "$NEW_HASH" = "$OLD_HASH" ] && [ ! -f "$DIRTY_FILE" ]; then
    exit 0
  fi

  printf '%s\n' "$NEW_HASH" >"$HASH_FILE"
  touch "$DIRTY_FILE" 2>/dev/null || true

  # 3. 60s throttle gate (sleep for trailing-edge sync or leave dirty marker when deferring).
  NOW_TS="$(date +%s)"
  LAST_TS=0
  if [ -f "$LAST_SYNC_FILE" ]; then
    LAST_TS="$(cat "$LAST_SYNC_FILE" 2>/dev/null || echo 0)"
  fi
  case "$LAST_TS" in
    ''|*[!0-9]*) LAST_TS=0 ;;
  esac

  ELAPSED=$((NOW_TS - LAST_TS))
  if [ "$ELAPSED" -lt "$COOLDOWN_SEC" ]; then
    if [ "${M2_HOOK_DEFER_ON_COOLDOWN:-0}" = "1" ]; then
      exit 0
    fi
    REMAINING=$((COOLDOWN_SEC - ELAPSED))
    sleep "$REMAINING"
    run_local_status
    NEW_HASH="$(python3 "$ROUTER_SCRIPT" --board-fingerprint --state-file "$STATE_FILE" 2>/dev/null || printf '%s' "$NEW_HASH")"
    printf '%s\n' "$NEW_HASH" >"$HASH_FILE"
  fi

  # 4. Sync local device shard to Linear Hub Issue (TIA-463).
  if python3 "$ROUTER_SCRIPT" --status --no-herdr --sync-linear --hub-issue "$HUB_ISSUE" --state-file "$STATE_FILE" >>"$LOG_FILE" 2>&1; then
    date +%s >"$LAST_SYNC_FILE"
    rm -f "$DIRTY_FILE" 2>/dev/null || true
  fi
}

if [ "${M2_HOOK_FOREGROUND:-0}" = "1" ]; then
  run_worker </dev/null >/dev/null 2>&1 || true
  exit 0
fi

(
  run_worker
) </dev/null >/dev/null 2>&1 &

exit 0
