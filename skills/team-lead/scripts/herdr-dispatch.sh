#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  herdr-dispatch.sh --name <agent-name> --kind <kind> --cwd <worktree-or-dir> --prompt <text> \
    [--pane <source-pane-id>] [--direction right|down] [--new-workspace] [--workspace-label <label>] \
    [--role <role>] [--exclude-vendor <vendor>] [--gateway-mode auto|magpie|direct] \
    [--auto-worktree|--no-auto-worktree] [--wait] [--timeout <ms>] [-- <agent-args>...]
EOF
}

NAME=""
KIND=""
CWD=""
PROMPT=""
SRC_PANE="${HERDR_PANE_ID:-}"
DIRECTION="right"
NEW_WORKSPACE=0
WORKSPACE_LABEL=""
NEW_WORKSPACE_ID=""
WAIT_FLAG=0
TIMEOUT_MS=""
ROLE=""
EXCLUDE_VENDOR=""
GATEWAY_MODE="${HERDR_GATEWAY_MODE:-auto}"
AUTO_WORKTREE_REQUESTED=0
NO_AUTO_WORKTREE=0
AUTO_PIVOTED_WORKTREE=0
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
    --new-workspace)
      NEW_WORKSPACE=1
      shift
      ;;
    --workspace-label)
      [[ $# -ge 2 ]] || { echo "Error: --workspace-label requires a value" >&2; exit 2; }
      WORKSPACE_LABEL="$2"
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
    --auto-worktree)
      AUTO_WORKTREE_REQUESTED=1
      NO_AUTO_WORKTREE=0
      shift
      ;;
    --no-auto-worktree)
      NO_AUTO_WORKTREE=1
      shift
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

# Entry coordinators (normal CLI, Grok Bot local-exec-daemon, Cindy) launching a Goal Steering TL
# outside a Herdr pane automatically bootstrap a background Herdr workspace.
if [[ "$ROLE" == "team_lead" && ( "${HERDR_ENV:-}" != "1" || -z "$SRC_PANE" ) ]]; then
  NEW_WORKSPACE=1
fi

if [[ "$NEW_WORKSPACE" -eq 0 ]]; then
  if [[ "${HERDR_ENV:-}" != "1" ]]; then
    echo "Error: HERDR_ENV=1 is required to run herdr-dispatch.sh (or pass --new-workspace / --role team_lead)" >&2
    exit 1
  fi

  if [[ -z "$SRC_PANE" ]]; then
    echo "Error: source pane ID is required (--pane, HERDR_PANE_ID, or --new-workspace)" >&2
    exit 1
  fi
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

# Code-bearing agents must share one Goal worktree instead of mutating a primary checkout.
if [[ "$ROLE" == "team_lead" || "$ROLE" == "writer" || "$ROLE" == "writer_escalated" ]] \
  && [[ "$NO_AUTO_WORKTREE" -ne 1 ]] \
  && [[ "${HERDR_AUTO_WORKTREE:-1}" != "0" ]] \
  && [[ -z "${MOCK_HERDR_LOG:-}" || "${HERDR_FORCE_AUTO_WORKTREE:-0}" == "1" || "$AUTO_WORKTREE_REQUESTED" -eq 1 ]] \
  && [[ -d "$CWD/.git" ]]; then
  if [[ -n "$WORKSPACE_LABEL" ]]; then
    GOAL_SLUG="$WORKSPACE_LABEL"
  elif [[ "$NAME" == *-goal-tl-r* ]]; then
    GOAL_SLUG="${NAME%-goal-tl-r*}"
  elif [[ "$NAME" == *-tk* ]]; then
    GOAL_SLUG="${NAME%-tk*}"
  else
    GOAL_SLUG="$NAME"
  fi
  GOAL_SLUG="$(printf '%s' "$GOAL_SLUG" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9_-' '-' | sed -e 's/^-*//' -e 's/-*$//')"
  [[ -n "$GOAL_SLUG" ]] || GOAL_SLUG="goal"
  GOAL_QUEUE_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/goal-queue.py"
  HANDOFF_ROOT="${HERDR_HANDOFF_ROOT:-$HOME/Development/handoff}"
  WORKTREES_ROOT="${HERDR_WORKTREES_ROOT:-$HOME/Development/worktrees}"
  ENSURE_JSON="$(python3 "$GOAL_QUEUE_SCRIPT" ensure-worktree \
    --handoff-dir "$HANDOFF_ROOT/$GOAL_SLUG" --repo "$CWD" --goal "$GOAL_SLUG" \
    --branch "feat/$GOAL_SLUG" --worktrees-root "$WORKTREES_ROOT")" || {
      echo "Error: unable to ensure singleton worktree for goal '$GOAL_SLUG'" >&2
      exit 1
    }
  CWD="$(printf '%s' "$ENSURE_JSON" | python3 -c 'import json, sys; print(json.load(sys.stdin)["worktree"])')"
  AUTO_PIVOTED_WORKTREE=1
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
  writer|explorer|pr_monitor|mechanical) LOGICAL_GROUP="group/coding" ;;
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

SKIPPED_CLAUDE=0
SKIPPED_CLAUDE_REASON=""
if [[ "$KIND" == "claude" ]]; then
  CLAUDE_PREFLIGHT="$(
    python3 - "$NAME" "$NEW_WORKSPACE" <<'PY' || printf 'ok\tdirect\n'
import json, os, pathlib, subprocess, sys, time, urllib.error, urllib.request

agent_name, new_ws = sys.argv[1], sys.argv[2]
override = os.environ.get("HERDR_CLAUDE_QUOTA_STATE", "").strip()
if override:
    if override in ("ok", "direct", "available"):
        print("ok\tdirect")
    elif override == "magpie":
        print("ok\tmagpie")
    else:
        print(f"skip\t{override}")
    sys.exit(0)

if os.environ.get("MOCK_HERDR_LOG"):
    print("ok\tdirect")
    sys.exit(0)

# If reusing an existing live Goal TL workspace, skip preflight probe.
if new_ws == "1":
    try:
        proc = subprocess.run(["herdr", "agent", "get", agent_name], capture_output=True, text=True, timeout=1.5)
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            def find_status(node):
                if isinstance(node, dict):
                    if "agent_status" in node and isinstance(node["agent_status"], str):
                        return node["agent_status"]
                    for v in node.values():
                        r = find_status(v)
                        if r:
                            return r
                elif isinstance(node, list):
                    for v in node:
                        r = find_status(v)
                        if r:
                            return r
                return ""
            if find_status(data) in ("working", "idle", "done"):
                print("ok\tdirect")
                sys.exit(0)
    except Exception:
        pass

home = pathlib.Path.home()
magpie_dir = home / ".config" / "magpie"
logins_path = magpie_dir / "logins.json"
quotas_path = magpie_dir / "quotas.json"
providers_path = magpie_dir / "providers.json"

try:
    logins = json.loads(logins_path.read_text(encoding="utf-8")) if logins_path.is_file() else []
except Exception:
    logins = []

claude_accounts = [
    x for x in logins
    if isinstance(x, dict) and x.get("agent") == "claude" and x.get("on") is not False and x.get("user")
]
if not claude_accounts:
    print("skip\tno_claude_account")
    sys.exit(0)

default_cap = float(os.environ.get("HERDR_CLAUDE_MAX_USED_PCT", "80"))
account_caps = {}
try:
    pdata = json.loads(providers_path.read_text(encoding="utf-8")) if providers_path.is_file() else {}
    for p in pdata.get("providers") or []:
        if isinstance(p, dict) and p.get("id") == "claude":
            for k, v in (p.get("accountCaps") or {}).items():
                account_caps[str(k)] = float(v)
except Exception:
    pass

try:
    quotas = json.loads(quotas_path.read_text(encoding="utf-8")) if quotas_path.is_file() else {}
except Exception:
    quotas = {}

cache_path = pathlib.Path("/tmp/herdr-claude-preflight.json")
cache_ttl = float(os.environ.get("HERDR_CLAUDE_PREFLIGHT_TTL_SEC", "60"))
now = time.time()
cache_data = {}
try:
    if cache_path.is_file():
        cache_data = json.loads(cache_path.read_text(encoding="utf-8"))
except Exception:
    cache_data = {}

last_reason = "quota_unavailable"
for acc in claude_accounts:
    email = str(acc.get("user"))
    cap = account_caps.get(email, default_cap)
    qentry = (quotas.get(f"claude/{email}") or {}).get("quota") or {}
    windows = qentry.get("windows") or []
    if not windows:
        last_reason = f"quota_unread:{email}"
        continue
    capped_win = None
    for w in windows:
        if not isinstance(w, dict):
            continue
        used = float(w.get("used", 100.0))
        if used >= cap:
            capped_win = f"quota_capped:{w.get('name', 'window')}={used:g}%>={cap:g}%"
            break
    if capped_win:
        last_reason = capped_win
        continue

    token = (((acc.get("auth") or {}).get("claudeAiOauth") or {}).get("accessToken") or "").strip()
    if not token:
        print("ok\tdirect")
        sys.exit(0)

    cache_key = f"{email}:{token[:16]}"
    cached = cache_data.get(cache_key)
    if isinstance(cached, dict) and (now - float(cached.get("ts", 0))) < cache_ttl:
        if cached.get("ok"):
            print("ok\tdirect")
            sys.exit(0)
        last_reason = str(cached.get("reason") or "cached_failure")
        continue

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages?beta=true",
        data=json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "1"}],
        }).encode("utf-8"),
        headers={
            "authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            if 200 <= resp.status < 300:
                cache_data[cache_key] = {"ok": True, "ts": now}
                try:
                    cache_path.write_text(json.dumps(cache_data), encoding="utf-8")
                except Exception:
                    pass
                print("ok\tdirect")
                sys.exit(0)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 429, 529):
            last_reason = f"http_{exc.code}"
            cache_data[cache_key] = {"ok": False, "reason": last_reason, "ts": now}
            try:
                cache_path.write_text(json.dumps(cache_data), encoding="utf-8")
            except Exception:
                pass
            continue
        print("ok\tdirect")
        sys.exit(0)
    except Exception:
        print("ok\tdirect")
        sys.exit(0)

print(f"skip\t{last_reason}")
PY
  )"
  IFS=$'\t' read -r CLAUDE_STATUS CLAUDE_DETAIL <<< "$CLAUDE_PREFLIGHT"
  if [[ "$CLAUDE_STATUS" == "skip" ]]; then
    SKIPPED_CLAUDE=1
    SKIPPED_CLAUDE_REASON="${CLAUDE_DETAIL:-quota_unavailable}"
    KIND="${HERDR_CLAUDE_FALLBACK_KIND:-copilot}"
    [[ -n "$LOGICAL_GROUP" ]] || LOGICAL_GROUP="group/steering"
    # Strip Claude-specific CLI flags (--allowedTools, --permission-mode, Claude model args) before switching to fallback CLI.
    CLEANED_ARGS=()
    skip_next=0
    in_tool_list=0
    for arg in "${AGENT_ARGS[@]+"${AGENT_ARGS[@]}"}"; do
      if [[ "$skip_next" -eq 1 ]]; then
        skip_next=0
        continue
      fi
      if [[ "$in_tool_list" -eq 1 ]]; then
        if [[ "$arg" == -* ]]; then
          in_tool_list=0
        else
          continue
        fi
      fi
      case "$arg" in
        -m|--model|--permission-mode)
          skip_next=1
          ;;
        --model=*|--permission-mode=*|--dangerously-skip-permissions)
          ;;
        --allowedTools|--disallowedTools)
          in_tool_list=1
          ;;
        --allowedTools=*|--disallowedTools=*)
          ;;
        *)
          CLEANED_ARGS+=("$arg")
          ;;
      esac
    done
    AGENT_ARGS=("${CLEANED_ARGS[@]+"${CLEANED_ARGS[@]}"}")
    REQUESTED_MODEL=""
    MODEL_INDEX=-1
    MODEL_PREFIX=""
  fi
fi

if [[ -n "$LOGICAL_GROUP" ]]; then
  ROLE_ROUTE=1
  if [[ "$KIND" == "claude" && "$GATEWAY_MODE" != "magpie" ]]; then
    # Claude runs direct against Anthropic so it never switches models mid-session and preserves native Subagent/Teammate support.
    GATEWAY_ONLINE=0
  elif [[ "$GATEWAY_MODE" == "magpie" || "${HERDR_MOCK_MAGPIE_ONLINE:-}" == "1" ]]; then
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
  SANITIZED_AGENT_ARGS=()
  prev_was_add_dir=0
  for arg in "${AGENT_ARGS[@]+"${AGENT_ARGS[@]}"}"; do
    if [[ "$prev_was_add_dir" -eq 1 ]]; then
      if [[ -n "${MOCK_HERDR_LOG:-}" || -d "$arg" ]]; then
        EXISTING_ADD_DIRS+=("$arg")
        SANITIZED_AGENT_ARGS+=("$arg")
      fi
      prev_was_add_dir=0
      continue
    fi
    case "$arg" in
      --allow-tool|--allow-tool=*|--deny-tool|--deny-tool=*)
        HAS_ALLOW_OR_DENY=1
        SANITIZED_AGENT_ARGS+=("$arg")
        ;;
      --add-dir)
        prev_was_add_dir=1
        ;;
      --add-dir=*)
        caller_dir="${arg#--add-dir=}"
        if [[ -n "${MOCK_HERDR_LOG:-}" || -d "$caller_dir" ]]; then
          EXISTING_ADD_DIRS+=("$caller_dir")
          SANITIZED_AGENT_ARGS+=("$arg")
        fi
        ;;
      --no-ask-user)
        HAS_NO_ASK=1
        SANITIZED_AGENT_ARGS+=("$arg")
        ;;
      *) SANITIZED_AGENT_ARGS+=("$arg") ;;
    esac
  done
  AGENT_ARGS=("${SANITIZED_AGENT_ARGS[@]+"${SANITIZED_AGENT_ARGS[@]}"}")
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

if [[ "$NEW_WORKSPACE" -eq 1 ]]; then
  # Idempotent live-TL check: never spawn a second TL if <goal>-goal-tl-r1 is already working/idle/done.
  EXISTING_INFO="$(
    herdr agent get "$NAME" 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.loads(sys.stdin.read())
except Exception:
    sys.exit(1)
def scalar(node, key):
    if isinstance(node, dict):
        if key in node and not isinstance(node[key], (dict, list)):
            return str(node[key])
        for v in node.values():
            r = scalar(v, key)
            if r is not None:
                return r
    elif isinstance(node, list):
        for v in node:
            r = scalar(v, key)
            if r is not None:
                return r
    return None
st = scalar(data, "agent_status") or ""
pane = scalar(data, "pane_id") or ""
ws = scalar(data, "workspace_id") or (pane.split(":")[0] if ":" in pane else "")
if st in ("working", "idle", "done") and pane:
    print(f"{st}\t{pane}\t{ws}")
else:
    sys.exit(1)
' || true
  )"
  if [[ -n "$EXISTING_INFO" ]]; then
    IFS=$'\t' read -r EXISTING_STATUS NEW_PANE_ID NEW_WORKSPACE_ID <<< "$EXISTING_INFO"
    if [[ "$EXISTING_STATUS" == "idle" || "$EXISTING_STATUS" == "done" ]]; then
      PROMPT_CMD=(herdr agent prompt "$NAME" "$PROMPT")
      [[ "$WAIT_FLAG" -eq 1 ]] && PROMPT_CMD+=(--wait)
      [[ -n "$TIMEOUT_MS" ]] && PROMPT_CMD+=(--timeout "$TIMEOUT_MS")
      if ! PROMPT_ERR="$("${PROMPT_CMD[@]}" 2>&1)"; then
        if [[ "$PROMPT_ERR" == *"agent_prompt_stalled"* ]]; then
          herdr agent send-keys "$NAME" enter >/dev/null 2>&1 || true
        else
          printf '%s\n' "$PROMPT_ERR" >&2
          exit 1
        fi
      fi
    fi
    WATCHDOG_GOAL="${WORKSPACE_LABEL:-${NAME%-goal-tl-r*}}"
    [[ -n "$WATCHDOG_GOAL" ]] || WATCHDOG_GOAL="<goal>"
    WATCHDOG_CMD="python3 ~/Development/user-skills/skills/team-lead/scripts/goal-supervisor.py watch --handoff-dir ~/Development/handoff/${WATCHDOG_GOAL} --interval 15 --max-wait 600 --json"
    python3 - "$NEW_PANE_ID" "$NAME" "$ROLE_ROUTE" "$LOGICAL_GROUP" "$RESOLVED_MODEL" "$GATEWAY_ONLINE" "$NEW_WORKSPACE_ID" "$EXISTING_STATUS" "$WATCHDOG_CMD" "$CWD" "$AUTO_PIVOTED_WORKTREE" <<'PY'
import json, sys
payload = {
    "ok": True,
    "pane_id": sys.argv[1],
    "workspace_id": sys.argv[7],
    "agent": sys.argv[2],
    "reused_existing": True,
    "agent_status": sys.argv[8],
    "watchdog_cmd": sys.argv[9],
    "cwd": sys.argv[10],
    "auto_pivoted_worktree": sys.argv[11] == "1",
}
if sys.argv[3] == "1":
    payload.update({
        "logical_group": sys.argv[4],
        "resolved_model": sys.argv[5],
        "gateway_mode": "magpie" if sys.argv[6] == "1" else "direct_fallback",
    })
print(json.dumps(payload))
PY
    exit 0
  fi

  # Normalize handoff/<goal> cwd to shared parent root so Herdr groups the workspace cleanly.
  WS_CWD="$CWD"
  if [[ "$(basename "$(dirname "$CWD")")" == "handoff" ]]; then
    if [[ "$CWD" == /Volumes/SSD/* ]]; then
      WS_CWD="$(dirname "$CWD")"
    else
      WS_CWD="$HOME/Development"
    fi
  fi
  WS_LABEL="${WORKSPACE_LABEL:-${NAME%-goal-tl-r*}}"
  WS_JSON="$(
    herdr workspace create \
      --cwd "$WS_CWD" \
      --label "$WS_LABEL" \
      --no-focus
  )"
  IFS=$'\t' read -r NEW_WORKSPACE_ID NEW_PANE_ID <<< "$(
    printf '%s' "$WS_JSON" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(1)
res = d.get("result") or {}
ws = (res.get("workspace") or {}).get("workspace_id") or res.get("workspace_id") or ""
pane = ((res.get("root_pane") or {}).get("pane_id")
        or (res.get("pane") or {}).get("pane_id")
        or "")
if not isinstance(pane, str) or not pane.strip():
    sys.exit(1)
ws = ws.strip() if isinstance(ws, str) and ws.strip() else (pane.split(":")[0] if ":" in pane else "")
print(f"{ws}\t{pane.strip()}")
'
  )"
else
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
fi

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

WATCHDOG_CMD=""
if [[ "$ROLE" == "team_lead" || "$NEW_WORKSPACE" -eq 1 ]]; then
  WATCHDOG_GOAL="${WORKSPACE_LABEL:-${NAME%-goal-tl-r*}}"
  [[ -n "$WATCHDOG_GOAL" ]] || WATCHDOG_GOAL="<goal>"
  WATCHDOG_CMD="python3 ~/Development/user-skills/skills/team-lead/scripts/goal-supervisor.py watch --handoff-dir ~/Development/handoff/${WATCHDOG_GOAL} --interval 15 --max-wait 600 --json"
fi

python3 - "$NEW_PANE_ID" "$NAME" "$ROLE_ROUTE" "$LOGICAL_GROUP" "$RESOLVED_MODEL" "$GATEWAY_ONLINE" "${HERDR_INCLUDE_ROUTE_META:-0}" "$NEW_WORKSPACE_ID" "$KIND" "$SKIPPED_CLAUDE" "$SKIPPED_CLAUDE_REASON" "$WATCHDOG_CMD" "$CWD" "$AUTO_PIVOTED_WORKTREE" <<'PY'
import json, sys
payload = {
    "ok": True,
    "pane_id": sys.argv[1],
    "agent": sys.argv[2],
    "cwd": sys.argv[13],
    "auto_pivoted_worktree": sys.argv[14] == "1",
}
if sys.argv[8]:
    payload["workspace_id"] = sys.argv[8]
if sys.argv[12]:
    payload["watchdog_cmd"] = sys.argv[12]
if sys.argv[3] == "1" or sys.argv[7] == "1":
    payload.update({
        "resolved_kind": sys.argv[9],
        "logical_group": sys.argv[4],
        "resolved_model": sys.argv[5],
        "gateway_mode": "magpie" if sys.argv[6] == "1" else "direct_fallback",
    })
if sys.argv[10] == "1":
    payload["resolved_kind"] = sys.argv[9]
    payload["skipped_claude"] = True
    payload["skipped_claude_reason"] = sys.argv[11]
print(json.dumps(payload))
PY
