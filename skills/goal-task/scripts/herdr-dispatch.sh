#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  herdr-dispatch.sh --name <agent-name> --kind <kind> --cwd <worktree-or-dir> --prompt <text> \
    [--pane <source-pane-id>] [--direction right|down] [--wait] [--timeout <ms>] [-- <agent-args>...]
EOF
}

NAME=""
KIND=""
CWD=""
PROMPT=""
SRC_PANE="${HERDR_PANE_ID:-}"
DIRECTION="right"
WAIT_FLAG=0
TIMEOUT_MS=""
AGENT_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      [[ $# -ge 2 ]] || { echo "Error: --name requires a value" >&2; exit 2; }
      NAME="$2"
      shift 2
      ;;
    --kind)
      [[ $# -ge 2 ]] || { echo "Error: --kind requires a value" >&2; exit 2; }
      KIND="$2"
      shift 2
      ;;
    --cwd)
      [[ $# -ge 2 ]] || { echo "Error: --cwd requires a value" >&2; exit 2; }
      CWD="$2"
      shift 2
      ;;
    --prompt)
      [[ $# -ge 2 ]] || { echo "Error: --prompt requires a value" >&2; exit 2; }
      PROMPT="$2"
      shift 2
      ;;
    --pane)
      [[ $# -ge 2 ]] || { echo "Error: --pane requires a value" >&2; exit 2; }
      SRC_PANE="$2"
      shift 2
      ;;
    --direction)
      [[ $# -ge 2 ]] || { echo "Error: --direction requires a value" >&2; exit 2; }
      DIRECTION="$2"
      shift 2
      ;;
    --wait)
      WAIT_FLAG=1
      shift
      ;;
    --timeout)
      [[ $# -ge 2 ]] || { echo "Error: --timeout requires a value" >&2; exit 2; }
      TIMEOUT_MS="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --)
      shift
      AGENT_ARGS=("$@")
      break
      ;;
    *)
      echo "Error: unknown option '$1'" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ "${HERDR_ENV:-}" != "1" ]]; then
  echo "Error: HERDR_ENV=1 is required to run herdr-dispatch.sh" >&2
  exit 1
fi

if [[ -z "$SRC_PANE" ]]; then
  echo "Error: source pane ID is required (--pane or HERDR_PANE_ID)" >&2
  exit 1
fi

if [[ -z "$NAME" || -z "$KIND" || -z "$CWD" || -z "$PROMPT" ]]; then
  echo "Error: --name, --kind, --cwd, and --prompt are required" >&2
  usage
  exit 2
fi

if [[ "$DIRECTION" != "right" && "$DIRECTION" != "down" ]]; then
  echo "Error: --direction must be 'right' or 'down' (got '$DIRECTION')" >&2
  exit 2
fi

if [[ "$KIND" == "cursor-agent" ]]; then
  KIND="cursor"
fi

if [[ ${#AGENT_ARGS[@]} -gt 0 ]]; then
  case "${AGENT_ARGS[0]}" in
    codex|copilot|claude|cursor|cursor-agent)
      AGENT_ARGS=("${AGENT_ARGS[@]:1}")
      ;;
  esac
fi

SPLIT_JSON="$(
  herdr pane split \
    --pane "$SRC_PANE" \
    --direction "$DIRECTION" \
    --cwd "$CWD" \
    --no-focus
)"

NEW_PANE_ID="$(
  printf '%s' "$SPLIT_JSON" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(1)
p = ((d.get("result") or {}).get("pane") or {}).get("pane_id", "")
if not isinstance(p, str) or not p.strip():
    sys.exit(1)
print(p.strip())
'
)"

test -n "$NEW_PANE_ID"

START_CMD=(herdr agent start "$NAME" --kind "$KIND" --pane "$NEW_PANE_ID")
if [[ ${#AGENT_ARGS[@]} -gt 0 ]]; then
  START_CMD+=(-- "${AGENT_ARGS[@]}")
fi
"${START_CMD[@]}" >/dev/null

PROMPT_CMD=(herdr agent prompt "$NAME" "$PROMPT")
if [[ "$WAIT_FLAG" -eq 1 ]]; then
  PROMPT_CMD+=(--wait)
fi
if [[ -n "$TIMEOUT_MS" ]]; then
  PROMPT_CMD+=(--timeout "$TIMEOUT_MS")
fi
"${PROMPT_CMD[@]}" >/dev/null

python3 - "$NEW_PANE_ID" "$NAME" <<'PY'
import json, sys
print(json.dumps({"ok": True, "pane_id": sys.argv[1], "agent": sys.argv[2]}))
PY
