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
import json, os, pathlib, re, sys

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
    sp = str(p)
    if sp not in candidates:
        candidates.append(sp)
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
        raw_text = toml_path.read_text(encoding="utf-8")
        # Drop adjacent duplicate [projects."..."] trust blocks before appending.
        # Only the repeated header plus its trust_level line is removed, so a
        # later unrelated table in the same split chunk is kept.
        seen_hdrs = set()
        cleaned_blocks = []
        dup_trust = re.compile(
            r'^\[projects\."[^"]+"\][ \t]*\r?\n(?:trust_level[ \t]*=[ \t]*"trusted"[ \t]*\r?\n)?'
        )
        for part in re.split(r'(?m)(?=^\[projects\."[^"]+"\][ \t]*$)', raw_text):
            m = re.match(r'^(\[projects\."[^"]+"\])', part)
            if m:
                if m.group(1) in seen_hdrs:
                    cleaned_blocks.append(dup_trust.sub("", part, count=1))
                    continue
                seen_hdrs.add(m.group(1))
            cleaned_blocks.append(part)
        text = "".join(cleaned_blocks)
        appended = []
        for item in candidates:
            hdr = f'[projects."{item}"]'
            if item and hdr not in seen_hdrs and hdr not in text:
                seen_hdrs.add(hdr)
                appended.append(f'\n{hdr}\ntrust_level = "trusted"\n')
        if appended or text != raw_text:
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
  # symlink and /private literals (Copilot realpath-dedupes the pair later;
  # the parent static scan still needs both). Do not drop /tmp+/private/tmp
  # or /var/folders+/private/var/folders. PTY 1024B headroom is the omitted
  # shell(env|jq|rg) entries on readonly_receipt_flags, not fewer temp roots.
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

# herdr agent get → one scalar field (interactive_ready / agent_status).
agent_json_field() {
  local field="$1"
  local raw
  raw="$(herdr agent get "$NAME" 2>/dev/null || true)"
  [[ -n "$raw" ]] || return 1
  printf '%s' "$raw" | python3 -c '
import json, sys
field = sys.argv[1]
try:
    data = json.loads(sys.stdin.read())
except Exception:
    sys.exit(1)

def walk(node):
    if isinstance(node, dict):
        if field in node and not isinstance(node[field], (dict, list)):
            return node[field]
        for value in node.values():
            found = walk(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = walk(value)
            if found is not None:
                return found
    return None

found = walk(data)
if found is None:
    sys.exit(1)
if isinstance(found, bool):
    print("true" if found else "false")
else:
    print(found)
' "$field"
}

# TUI must be interactive before prompt, or the text sits in the composer.
wait_for_interactive_ready() {
  local attempt
  for ((attempt = 1; attempt <= 20; attempt++)); do
    if [[ "$(agent_json_field interactive_ready || true)" == "true" ]]; then
      return 0
    fi
    if [[ "$attempt" -lt 20 ]]; then
      sleep 0.5
    fi
  done
  return 0
}

# One Enter submits a composer that prompt left unsent. Do not repeat.
submit_prompt_enter() {
  herdr agent send-keys "$NAME" enter >/dev/null 2>&1 \
    || herdr pane send-keys --pane "$NEW_PANE_ID" Enter >/dev/null 2>&1 \
    || herdr pane send-keys "$NEW_PANE_ID" enter >/dev/null 2>&1 \
    || true
}

agent_is_idle() {
  local status
  status="$(agent_json_field agent_status || true)"
  [[ "$status" == "idle" || "$status" == "done" ]]
}

wait_for_interactive_ready

PROMPT_CMD=(herdr agent prompt "$NAME" "$PROMPT")
if [[ "$WAIT_FLAG" -eq 1 ]]; then
  PROMPT_CMD+=(--wait)
fi
if [[ -n "$TIMEOUT_MS" ]]; then
  PROMPT_CMD+=(--timeout "$TIMEOUT_MS")
fi
if ! PROMPT_ERR="$("${PROMPT_CMD[@]}" 2>&1)"; then
  # --wait's ~5s initial-state check false-positives as agent_prompt_stalled.
  if [[ "$PROMPT_ERR" == *"agent_prompt_stalled"* ]]; then
    submit_prompt_enter
    if [[ "$WAIT_FLAG" -eq 1 ]]; then
      WAIT_CMD=(herdr agent wait "$NAME")
      if [[ -n "$TIMEOUT_MS" ]]; then
        WAIT_CMD+=(--timeout "$TIMEOUT_MS")
      fi
      "${WAIT_CMD[@]}" >/dev/null
    fi
  else
    printf '%s\n' "$PROMPT_ERR" >&2
    exit 1
  fi
elif [[ "$WAIT_FLAG" -eq 0 ]]; then
  sleep "${HERDR_PROMPT_IDLE_GRACE:-2}"
  if agent_is_idle; then
    submit_prompt_enter
  fi
fi

python3 - "$NEW_PANE_ID" "$NAME" <<'PY'
import json, sys
print(json.dumps({"ok": True, "pane_id": sys.argv[1], "agent": sys.argv[2]}))
PY
