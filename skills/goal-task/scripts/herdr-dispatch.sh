#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  herdr-dispatch.sh --name <agent-name> --kind <kind> --cwd <worktree-or-dir> --prompt <text> \
    [--pane <source-pane-id>] [--direction right|down] [--role <role>] \
    [--exclude-vendor <vendor>] [--gateway-mode auto|magpie|direct] \
    [--wait] [--timeout <ms>] [-- <agent-args>...]
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
ROLE=""
EXCLUDE_VENDOR=""
GATEWAY_MODE="${HERDR_GATEWAY_MODE:-auto}"
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
    --role)
      [[ $# -ge 2 ]] || { echo "Error: --role requires a value" >&2; exit 2; }
      ROLE="$2"
      shift 2
      ;;
    --exclude-vendor)
      [[ $# -ge 2 ]] || { echo "Error: --exclude-vendor requires a value" >&2; exit 2; }
      EXCLUDE_VENDOR="$2"
      shift 2
      ;;
    --gateway-mode)
      [[ $# -ge 2 ]] || { echo "Error: --gateway-mode requires a value" >&2; exit 2; }
      GATEWAY_MODE="$2"
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

if [[ "$GATEWAY_MODE" != "auto" && "$GATEWAY_MODE" != "magpie" && "$GATEWAY_MODE" != "direct" ]]; then
  echo "Error: --gateway-mode must be 'auto', 'magpie', or 'direct' (got '$GATEWAY_MODE')" >&2
  exit 2
fi

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

ROLE_ROUTE=0
LOGICAL_GROUP=""
REQUESTED_MODEL=""
MODEL_INDEX=-1
MODEL_PREFIX=""
GATEWAY_ONLINE=0
for ((i = 0; i < ${#AGENT_ARGS[@]}; i++)); do
  case "${AGENT_ARGS[$i]}" in
    -m|--model)
      if [[ $((i + 1)) -lt ${#AGENT_ARGS[@]} ]]; then
        REQUESTED_MODEL="${AGENT_ARGS[$((i + 1))]}"
        MODEL_INDEX=$((i + 1))
      fi
      ;;
    --model=*)
      REQUESTED_MODEL="${AGENT_ARGS[$i]#--model=}"
      MODEL_INDEX=$i
      MODEL_PREFIX="--model="
      ;;
  esac
done

case "$ROLE" in
  "") ;;
  team_lead|arbitration|planner|research_arbiter) LOGICAL_GROUP="group/steering" ;;
  planner_reviewer|code_reviewer|research_primary|research_co)
    LOGICAL_GROUP="group/reviewing"
    [[ "$EXCLUDE_VENDOR" == "google" ]] && LOGICAL_GROUP="group/reviewing-openai"
    ;;
  writer|designer|explorer|pr_monitor|mechanical) LOGICAL_GROUP="group/coding" ;;
  writer_escalated) LOGICAL_GROUP="group/coding-escalated" ;;
  *) echo "Error: unsupported --role '$ROLE'" >&2; exit 2 ;;
esac

if [[ "$ROLE" == "writer_escalated" || "$REQUESTED_MODEL" == "group/coding-escalated" || "$REQUESTED_MODEL" == "magpie/group/coding-escalated" ]]; then
  if [[ "$NAME" =~ -r1$ ]]; then
    echo "Error: writer_escalated is strictly reserved for V3 Round 2+ fix escalation and cannot be dispatched on Round 1 ($NAME)" >&2
    exit 2
  fi
fi

if [[ "$REQUESTED_MODEL" == group/* || "$REQUESTED_MODEL" == magpie/group/* ]]; then
  LOGICAL_GROUP="${REQUESTED_MODEL#magpie/}"
fi

if [[ -n "$LOGICAL_GROUP" ]]; then
  ROLE_ROUTE=1
  if [[ "$GATEWAY_MODE" == "magpie" || "${HERDR_MOCK_MAGPIE_ONLINE:-}" == "1" ]]; then
    GATEWAY_ONLINE=1
  elif [[ "$GATEWAY_MODE" == "direct" || "${HERDR_MOCK_MAGPIE_ONLINE:-}" == "0" ]]; then
    GATEWAY_ONLINE=0
  elif curl -fsS --max-time 0.2 "${HERDR_MAGPIE_HEALTH_URL:-http://127.0.0.1:3425/api/hello}" >/dev/null 2>&1; then
    GATEWAY_ONLINE=1
  else
    GATEWAY_ONLINE=0
  fi

  FALLBACK_MODEL=""
  case "$LOGICAL_GROUP" in
    group/steering)
      case "$KIND" in claude) FALLBACK_MODEL="opus" ;; copilot) FALLBACK_MODEL="gemini-4-argon" ;; codex) FALLBACK_MODEL="gpt-6.1-sol" ;; esac ;;
    group/reviewing-openai)
      case "$KIND" in copilot) FALLBACK_MODEL="gpt-5.6-luna" ;; codex) FALLBACK_MODEL="gpt-6.1-sol" ;; esac ;;
    group/reviewing)
      case "$KIND" in copilot) FALLBACK_MODEL="gemini-4-argon" ;; codex) FALLBACK_MODEL="gpt-6.1-sol" ;; cursor) FALLBACK_MODEL="kimi" ;; esac ;;
    group/coding)
      if [[ "$ROLE" == "pr_monitor" ]]; then
        [[ "$KIND" == "copilot" ]] && FALLBACK_MODEL="mai-code-2-flash"
      else
        case "$KIND" in copilot|codex) FALLBACK_MODEL="gpt-5.6-luna" ;; esac
      fi
      ;;
    group/coding-escalated)
      [[ "$KIND" == "codex" ]] && FALLBACK_MODEL="gpt-6.1-sol"
      ;;
  esac
  [[ -n "$FALLBACK_MODEL" ]] || FALLBACK_MODEL="$REQUESTED_MODEL"

  if [[ "$MODEL_INDEX" -ge 0 ]]; then
    if [[ "$REQUESTED_MODEL" == group/* || "$REQUESTED_MODEL" == magpie/group/* ]]; then
      if [[ "$MODEL_PREFIX" == "--model=" ]]; then
        AGENT_ARGS[$MODEL_INDEX]="--model=$([[ "$GATEWAY_ONLINE" -eq 1 ]] && printf '%s' "$LOGICAL_GROUP" || printf '%s' "$FALLBACK_MODEL")"
      else
        AGENT_ARGS[$MODEL_INDEX]="$([[ "$GATEWAY_ONLINE" -eq 1 ]] && printf '%s' "$LOGICAL_GROUP" || printf '%s' "$FALLBACK_MODEL")"
      fi
    fi
  elif [[ "$GATEWAY_ONLINE" -eq 1 ]]; then
    case "$KIND" in
      codex) AGENT_ARGS+=(-c 'model_provider="magpie"' -m "$LOGICAL_GROUP") ;;
      claude) AGENT_ARGS+=(--model "$LOGICAL_GROUP") ;;
      copilot)
        if [[ -f "$HOME/.copilot/providers.json" || "${HERDR_COPILOT_MAGPIE_BYOK:-0}" == "1" || "${HERDR_MOCK_MAGPIE_ONLINE:-}" == "1" ]]; then
          AGENT_ARGS+=(--model "magpie/$LOGICAL_GROUP")
        else
          AGENT_ARGS+=(--model "$FALLBACK_MODEL")
          GATEWAY_ONLINE=0
        fi
        ;;
    esac
  else
    case "$KIND" in copilot|codex|claude|cursor) AGENT_ARGS+=(--model "$FALLBACK_MODEL") ;; esac
  fi
  RESOLVED_MODEL="$FALLBACK_MODEL"
  if [[ "$MODEL_INDEX" -ge 0 && "$REQUESTED_MODEL" != group/* && "$REQUESTED_MODEL" != magpie/group/* ]]; then
    RESOLVED_MODEL="$REQUESTED_MODEL"
  fi
  [[ "$GATEWAY_ONLINE" -eq 1 && "$KIND" != "copilot" ]] && RESOLVED_MODEL="$LOGICAL_GROUP"
  [[ "$GATEWAY_ONLINE" -eq 1 && "$KIND" == "copilot" && "${AGENT_ARGS[*]}" == *"magpie/$LOGICAL_GROUP"* ]] && RESOLVED_MODEL="magpie/$LOGICAL_GROUP"
else
  RESOLVED_MODEL="$REQUESTED_MODEL"
fi

# Pre-register folder trust for CWD & shared roots so headless panes never block on startup folder-trust dialogs.
if [[ -z "${MOCK_HERDR_LOG:-}" && "${HERDR_DISPATCH_SKIP_TRUST:-0}" != "1" ]]; then
  python3 - "$KIND" "$CWD" <<'PY' || true
import json, os, pathlib, re, sys

kind, cwd_arg = sys.argv[1], sys.argv[2]
home = pathlib.Path.home()
candidates = []
extra_env = [p for p in os.environ.get("HERDR_EXTRA_ADD_DIRS", "").split(":") if p.strip()]

def remember(raw: str) -> None:
    if not raw:
        return
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
    "/Applications/Xcode.app",
    "/Volumes/SSD",
    *extra_env,
):
    remember(raw)

# Handoff sibling root. Trusting only .../handoff/<goal> does not cover
# realpath'd siblings; session --add-dir still needs the ancestor
# (/Volumes/SSD or ~/Development). Register the handoff directory itself.
probe = pathlib.Path(cwd_arg).expanduser()
for _ in range(8):
    if probe.name == "handoff":
        remember(str(probe))
        break
    if probe.parent == probe:
        break
    probe = probe.parent

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
      if [[ -n "${MOCK_HERDR_LOG:-}" || -d "$arg" ]]; then
        EXISTING_ADD_DIRS+=("$arg")
      fi
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
        caller_dir="${arg#--add-dir=}"
        if [[ -n "${MOCK_HERDR_LOG:-}" || -d "$caller_dir" ]]; then
          EXISTING_ADD_DIRS+=("$caller_dir")
        fi
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
  # or /var/folders+/private/var/folders. /Volumes/SSD is the Mac mini handoff
  # volume: trustedFolders of the goal directory alone does not authorize
  # realpath'd handoff siblings, so the session --add-dir list must include
  # that ancestor. PTY 1024B headroom is the omitted shell(env|jq|rg) entries
  # on readonly_receipt_flags, not fewer temp roots.
  BASELINE_ADD_DIRS=(
    "$CWD"
    "$HOME/Development"
    "$HOME/.copilot"
    "$HOME/.agents"
    "$HOME/.codex"
    "$HOME/orca"
    "/Volumes/SSD"
    "/Applications/Xcode.app"
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
  # If cwd is .../handoff/<goal> and that handoff directory's realpath is not
  # already under /Volumes/SSD, ~/Development, or ~/orca, add the realpath so
  # sibling goal dirs are inside the session allowlist (Parent ≥ Child).
  handoff_root=""
  probe="$CWD"
  for _ in 1 2 3 4 5 6 7 8; do
    [[ "$(basename "$probe")" == "handoff" ]] && handoff_root="$probe" && break
    next="$(dirname "$probe")"
    [[ "$next" == "$probe" ]] && break
    probe="$next"
  done
  if [[ -n "$handoff_root" ]]; then
    handoff_real="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$handoff_root" 2>/dev/null || true)"
    if [[ -n "$handoff_real" ]]; then
      covered=0
      for ancestor in "/Volumes/SSD" "$HOME/Development" "$HOME/orca"; do
        anc_real="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$ancestor" 2>/dev/null || true)"
        if [[ -n "$anc_real" && ( "$handoff_real" == "$anc_real" || "$handoff_real" == "$anc_real"/* ) ]]; then
          covered=1
          break
        fi
      done
      if [[ "$covered" -eq 0 ]]; then
        BASELINE_ADD_DIRS+=("$handoff_real")
      fi
    fi
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
      [[ -d "$req_dir" || -n "${MOCK_HERDR_LOG:-}" ]] || continue
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

python3 - "$NEW_PANE_ID" "$NAME" "$ROLE_ROUTE" "$LOGICAL_GROUP" "$RESOLVED_MODEL" "$GATEWAY_ONLINE" "${HERDR_INCLUDE_ROUTE_META:-0}" <<'PY'
import json, sys
payload = {"ok": True, "pane_id": sys.argv[1], "agent": sys.argv[2]}
if sys.argv[3] == "1" or sys.argv[7] == "1":
    payload.update({
        "logical_group": sys.argv[4],
        "resolved_model": sys.argv[5],
        "gateway_mode": "magpie" if sys.argv[6] == "1" else "direct_fallback",
    })
print(json.dumps(payload))
PY
