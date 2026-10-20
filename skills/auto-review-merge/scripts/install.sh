#!/usr/bin/env bash
# auto-review-merge skill installer.
#
# 把 assets/ 里的模板复制进目标 repo,替换 per-repo 占位符,chmod 脚本,
# 打印 go-live 步骤。**只写文件**——不 push、不 merge、不改 GitHub 服务端设置。
# git + gh 由调用者(你/agent)按 references/go-live-checklist.md 驱动。
#
# 用法:
#   scripts/install.sh --repo-dir <path> --runner-label <slug> \
#       --ci-check "<existing CI check name>" \
#       [--project-name <name>] \
#       [--review-dims-file <path>] \
#       [--runner-dir <path>]
#
# 参数:
#   --repo-dir       目标 git repo 根目录(必填)
#   --runner-label   self-hosted runner 的标签,唯一标识本机,如 acme-mac(必填)
#   --ci-check       repo 现有的、要保留为 required 的 CI check 名(必填);
#                    没有现成 CI 就传 ""(则 ruleset 初始只有 PR 门,见 checklist)
#   --project-name   review prompt 里的项目名,默认取 repo 目录名
#   --review-dims-file  一个文本文件,内容是本仓库的判 blocker 维度(会原样嵌进
#                    prompt 的编号列表处)。不传则用 references/review-dimensions.default.md
#   --runner-dir     runner 安装目录,默认 $HOME/actions-runner-<runner-label>

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$SKILL_DIR/assets"

REPO_DIR=""; RUNNER_LABEL=""; CI_CHECK=""; PROJECT_NAME=""; REVIEW_DIMS_FILE=""; RUNNER_DIR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --repo-dir) REPO_DIR="$2"; shift 2 ;;
        --runner-label) RUNNER_LABEL="$2"; shift 2 ;;
        --ci-check) CI_CHECK="$2"; shift 2 ;;
        --project-name) PROJECT_NAME="$2"; shift 2 ;;
        --review-dims-file) REVIEW_DIMS_FILE="$2"; shift 2 ;;
        --runner-dir) RUNNER_DIR="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

[ -n "$REPO_DIR" ] || { echo "ERROR: --repo-dir required" >&2; exit 2; }
[ -n "$RUNNER_LABEL" ] || { echo "ERROR: --runner-label required" >&2; exit 2; }
[ -d "$REPO_DIR/.git" ] || { echo "ERROR: $REPO_DIR is not a git repo" >&2; exit 2; }

PROJECT_NAME="${PROJECT_NAME:-$(basename "$(cd "$REPO_DIR" && pwd)")}"
RUNNER_DIR="${RUNNER_DIR:-\$HOME/actions-runner-$RUNNER_LABEL}"
REVIEW_DIMS_FILE="${REVIEW_DIMS_FILE:-$SKILL_DIR/references/review-dimensions.default.md}"
REPO_SLUG="$(cd "$REPO_DIR" && gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null || echo 'OWNER/REPO')"

[ -f "$REVIEW_DIMS_FILE" ] || { echo "ERROR: review dims file not found: $REVIEW_DIMS_FILE" >&2; exit 2; }
REVIEW_DIMENSIONS="$(cat "$REVIEW_DIMS_FILE")"

echo "== auto-review-merge installer =="
echo "  repo-dir:     $REPO_DIR"
echo "  repo-slug:    $REPO_SLUG"
echo "  runner-label: $RUNNER_LABEL"
echo "  runner-dir:   $RUNNER_DIR"
echo "  ci-check:     ${CI_CHECK:-<none>}"
echo "  project-name: $PROJECT_NAME"
echo "  review-dims:  $REVIEW_DIMS_FILE"
echo ""

# 占位符替换:用 perl 以避免 sed 的转义地狱,且支持多行 REVIEW_DIMENSIONS。
subst() {
    # $1=src template  $2=dst path
    local src="$1" dst="$2"
    mkdir -p "$(dirname "$dst")"
    RUNNER_LABEL="$RUNNER_LABEL" CI_CHECK="$CI_CHECK" PROJECT_NAME="$PROJECT_NAME" \
    RUNNER_DIR="$RUNNER_DIR" REPO_SLUG="$REPO_SLUG" REVIEW_DIMENSIONS="$REVIEW_DIMENSIONS" \
    perl -pe '
        s/\{\{RUNNER_LABEL\}\}/$ENV{RUNNER_LABEL}/g;
        s/\{\{CI_CHECK\}\}/$ENV{CI_CHECK}/g;
        s/\{\{PROJECT_NAME\}\}/$ENV{PROJECT_NAME}/g;
        s/\{\{RUNNER_DIR\}\}/$ENV{RUNNER_DIR}/g;
        s/\{\{REPO_SLUG\}\}/$ENV{REPO_SLUG}/g;
        s/\{\{REVIEW_DIMENSIONS\}\}/$ENV{REVIEW_DIMENSIONS}/g;
    ' "$src" > "$dst"
    echo "  wrote $dst"
}

cd "$REPO_DIR"
subst "$ASSETS/workflows/auto-merge.yml"        ".github/workflows/auto-merge.yml"
subst "$ASSETS/workflows/claude-review.yml"     ".github/workflows/claude-review.yml"
subst "$ASSETS/ci/claude-review.sh"             "scripts/ci/claude-review.sh"
subst "$ASSETS/ci/setup-runner.sh"              "scripts/ci/setup-runner.sh"
subst "$ASSETS/rulesets/main-protection.json"   "scripts/rulesets/main-protection.json"
subst "$ASSETS/rulesets/apply"                  "scripts/rulesets/apply"
subst "$ASSETS/docs/ci-gates.md"                "docs/ci-gates.md"

chmod +x scripts/ci/claude-review.sh scripts/ci/setup-runner.sh scripts/rulesets/apply

# 若没传 CI check,ruleset 初始就没有 required_status_checks 的第一项;提醒。
if [ -z "$CI_CHECK" ]; then
    echo ""
    echo "⚠️  未提供 --ci-check:main-protection.json 里保留了 {{CI_CHECK}} 占位。"
    echo "    请手动编辑该文件,删掉那一项或换成真实 check 名,再 apply。"
fi

cat <<EOF

✅ 文件已写入 ${REPO_DIR} 。接下来按 GO-LIVE ORDER(见 skill references/go-live-checklist.md):

  1. (private+free 才需)先扫密钥,再 gh repo edit ${REPO_SLUG} --visibility public
  2. gh api -X PATCH repos/${REPO_SLUG} -f allow_auto_merge=true -f delete_branch_on_merge=true
  3. 建分支 → 提交这些文件 → 开 PR → 合并(此刻 required 只有现有 CI)
  4. ./scripts/ci/setup-runner.sh && (cd ${RUNNER_DIR} && ./svc.sh install && ./svc.sh start)
     确认 gh api repos/${REPO_SLUG}/actions/runners 显示 online
  5. 开测试 PR,确认 claude-review 在本机跑、发评论、变绿
  6. 往 scripts/rulesets/main-protection.json 的 required_status_checks 加 {"context":"claude-review"}
     然后 ./scripts/rulesets/apply
  7. gh api -X PUT repos/${REPO_SLUG}/actions/permissions/fork-pr-contributor-approval \\
        -f approval_policy=all_external_contributors

顺序不可颠倒:runner 就绪前别把 claude-review 设成 required,否则所有 PR 会卡死。
EOF
