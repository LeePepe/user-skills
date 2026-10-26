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

# Reject over-long names before pane split so a failed start cannot leave an orphan pane.
if [[ ! "$NAME" =~ ^[a-z][a-z0-9_-]{0,31}$ ]]; then
  echo "Error: --name '$NAME' must match ^[a-z][a-z0-9_-]{0,31}$ (<=32 chars) before splitting pane" >&2
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

# Pre-register folder trust for CWD & shared roots so headless panes never block on startup folder-trust dialogs.
if [[ -z "${MOCK_HERDR_LOG:-}" && "${HERDR_DISPATCH_SKIP_TRUST:-0}" != "1" ]]; then
  python3 - "$KIND" "$CWD" <<'PY' || true
import json, os, pathlib, sys

kind, cwd_arg = sys.argv[1], sys.argv[2]
home = pathlib.Path.home()
candidates = []
extra_env = [p for p in os.environ.get("HERDR_EXTRA_ADD_DIRS", "").split(":") if p.strip()]
for raw in (
    cwd_arg,
    str(home / "Development"),
    str(home / "Development" / "handoff"),
    str(home / "Development" / "worktrees"),
    str(home / "orca"),
    str(home / "orca" / "workspaces"),
    "/tmp",
    "/private/tmp",
    "/var/folders",
    "/private/var/folders",
    *extra_env,
):
    if not raw:
        continue
    p = pathlib.Path(raw).expanduser()
    candidates.append(str(p))
    try:
        r = str(p.resolve())
        if r not in candidates:
            candidates.append(r)
    except Exception:
        pass

if kind == "copilot":
    cfg_path = home / ".copilot" / "config.json"
    if cfg_path.is_file():
        lines = cfg_path.read_text(encoding="utf-8").splitlines()
        comments = [l for l in lines if l.startswith("//")]
        body = "\n".join(l for l in lines if not l.startswith("//"))
        data = json.loads(body) if body.strip() else {}
        legacy = data.pop("trusted_folders", [])
        tf = data.setdefault("trustedFolders", [])
        changed = bool(legacy)
        for item in legacy + candidates:
            if item and item not in tf:
                tf.append(item)
                changed = True
        if changed:
            prefix = ("\n".join(comments) + "\n") if comments else ""
            cfg_path.write_text(prefix + json.dumps(data, indent=2) + "\n", encoding="utf-8")
elif kind == "codex":
    toml_path = home / ".codex" / "config.toml"
    if toml_path.is_file():
        text = toml_path.read_text(encoding="utf-8")
        appended = []
        for item in candidates:
            hdr = f'[projects."{item}"]'
            if item and hdr not in text:
                appended.append(f'\n{hdr}\ntrust_level = "trusted"\n')
        if appended:
            toml_path.write_text(text + "".join(appended), encoding="utf-8")
PY
fi

# For copilot subagents, auto-inject safe path & tool allowlists (additively merging baseline + extra dirs) so background panes do not block.
if [[ "$KIND" == "copilot" ]]; then
  HAS_ALLOW_OR_DENY=0
  HAS_NO_ASK=0
  EXISTING_ADD_DIRS=()
  prev_was_add_dir=0
  for arg in "${AGENT_ARGS[@]+"${AGENT_ARGS[@]}"}"; do
    if [[ "$prev_was_add_dir" -eq 1 ]]; then
      EXISTING_ADD_DIRS+=("$arg")
      prev_was_add_dir=0
      continue
    fi
    case "$arg" in
      --allow-tool|--allow-tool=*|--deny-tool|--deny-tool=*)
        HAS_ALLOW_OR_DENY=1
        ;;
      --add-dir)
        prev_was_add_dir=1
        ;;
      --add-dir=*)
        EXISTING_ADD_DIRS+=("${arg#--add-dir=}")
        ;;
      --no-ask-user)
        HAS_NO_ASK=1
        ;;
    esac
  done
  if [[ "$HAS_ALLOW_OR_DENY" -eq 0 ]]; then
    AGENT_ARGS+=(
      --allow-tool write
      --allow-tool shell
      --allow-tool url
      --deny-tool "shell(rm -rf /:*)"
      --deny-tool "shell(herdr server stop:*)"
    )
  fi
  # Always keep the static baseline, then append caller --add-dir values and
  # colon-separated HERDR_EXTRA_ADD_DIRS. Exact-string dedupe preserves both
  # symlink and /private literals (Copilot realpath-dedupes the pair later).
  BASELINE_ADD_DIRS=(
    "$CWD"
    "$HOME/Development"
    "$HOME/.copilot"
    "$HOME/.agents"
    "$HOME/.codex"
    "$HOME/orca"
    "/tmp"
    "/private/tmp"
    "/var/folders"
    "/private/var/folders"
  )
  if [[ -n "${HERDR_EXTRA_ADD_DIRS:-}" ]]; then
    IFS=':' read -r -a EXTRA_DIRS_ARR <<< "$HERDR_EXTRA_ADD_DIRS"
    for extra_d in "${EXTRA_DIRS_ARR[@]+"${EXTRA_DIRS_ARR[@]}"}"; do
      [[ -n "$extra_d" ]] && BASELINE_ADD_DIRS+=("$extra_d")
    done
  fi
  for req_dir in "${BASELINE_ADD_DIRS[@]}"; do
    already_present=0
    for ex_dir in "${EXISTING_ADD_DIRS[@]+"${EXISTING_ADD_DIRS[@]}"}"; do
      if [[ "$ex_dir" == "$req_dir" ]]; then
        already_present=1
        break
      fi
    done
    if [[ "$already_present" -eq 0 ]]; then
      AGENT_ARGS+=(--add-dir "$req_dir")
      EXISTING_ADD_DIRS+=("$req_dir")
    fi
  done
  if [[ "$HAS_NO_ASK" -eq 0 ]]; then
    AGENT_ARGS+=(--no-ask-user)
  fi
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

START_OK=0
START_ERR=""
for attempt in 1 2 3 4 5; do
  if START_ERR="$("${START_CMD[@]}" 2>&1 >/dev/null)"; then
    START_OK=1
    break
  fi
  if [[ "$START_ERR" == *"agent_pane_busy"* && "$attempt" -lt 5 ]]; then
    sleep "${HERDR_DISPATCH_RETRY_SLEEP:-1}"
    continue
  fi
  printf '%s\n' "$START_ERR" >&2
  exit 1
done

if [[ "$START_OK" -ne 1 ]]; then
  printf '%s\n' "$START_ERR" >&2
  exit 1
fi

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
