# Go-Live Checklist（copy-paste，带验证）

`<slug>` = `OWNER/REPO`。`<label>` = runner 标签(如 `acme-mac`)。**顺序不可颠倒。**

## 0. 前提确认
```bash
gh auth status                                   # 对 repo 有 admin
gh api repos/<slug> --jq '{private, visibility, default_branch}'
gh api repos/<slug>/commits/<default_branch>/check-runs --jq '.check_runs[].name' | sort -u  # 现有 CI check 名
```
- 私有 + 免费方案 → rulesets/branch-protection 返回 403。必须 public 或付费方案。

## 1. (仅 private+free)转 public —— 先扫密钥
```bash
git -C <repo> ls-files | grep -iE '\.(pem|key|p12|env)$|secret|credential|serviceAccount' || echo "(none by name)"
git -C <repo> grep -nIE '(AIza[0-9A-Za-z_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[0-9A-Za-z]{30,})' || echo "(none)"
# 确认干净后:
gh repo edit <slug> --visibility public --accept-visibility-change-consequences
```
> 转 public 会公开全部代码 + 完整 git 历史。有敏感历史就先清理或改用付费私有方案。

## 2. 打开仓库级 auto-merge
```bash
gh api -X PATCH repos/<slug> -f allow_auto_merge=true -f delete_branch_on_merge=true \
    --jq '{allow_auto_merge, delete_branch_on_merge}'
```

## 3. 合并 workflow 文件到默认分支
```bash
cd <repo>
git checkout -b chore/auto-review-merge
git add .github/workflows scripts/ci scripts/rulesets docs/ci-gates.md
git commit -m "ci: auto-review + auto-merge via self-hosted runner"
git push -u origin chore/auto-review-merge
gh pr create --fill
# 此刻 required 只有现有 CI;正常合并(不要 --admin 绕过):
gh pr merge <n> --squash
```
> 首个 ruleset 也可先 apply(见步骤 6 的 JSON,此时还没有 claude-review 那项)。

## 4. 注册并启动 self-hosted runner
```bash
cd <repo> && git checkout <default_branch> && git pull
./scripts/ci/setup-runner.sh                      # 下载 + 注册
RD="$HOME/actions-runner-<label>"
(cd "$RD" && ./svc.sh install && ./svc.sh start)  # user-launchd,无需 sudo
gh api repos/<slug>/actions/runners --jq '.runners[] | "\(.name): \(.status)"'   # 期望 online
```

## 5. 证明 review 能上报绿
```bash
# 开一个 trivial 测试 PR(改一行 README 即可),然后:
gh run list --workflow=claude-review.yml --limit 3 --json databaseId,status,conclusion
# 确认:runner 接活 → claude-review 变绿 → PR 上出现 sticky 评论
```
- 若 job 红:读 `gh run view <id> --log`;常见是 runner 上 `claude` 未登录 / 无 `jq`。

## 6. 把 claude-review 变成 required 硬门（LAST）
编辑 `scripts/rulesets/main-protection.json`,在 `required_status_checks` 里加:
```json
{ "context": "claude-review" }
```
然后:
```bash
cd <repo> && ./scripts/rulesets/apply
gh api repos/<slug>/rules/branches/<default_branch> --jq '[.[].type]'   # 含 required_status_checks
```
> 顺序关键:runner 未就绪就 apply → claude-review 永不上报 → 所有 PR 卡死(仅 admin 能合)。

## 7. 加固 fork 执行(public repo)
```bash
gh api -X PUT repos/<slug>/actions/permissions/fork-pr-contributor-approval \
    -f approval_policy=all_external_contributors
```

## 验证闭环(end-to-end)
1. 开 PR 改一个源文件并**故意留一个 blocker**(如已知的越界 import)。
2. 观察:auto-merge 挂上 → claude-review 发 request-changes 评论 → check 红 → PR BLOCKED。
3. 修掉 → push → 两门转绿 → 自动 squash 合并 + 删分支。
4. 反例:docs-only PR → review pass → 仅 CI 门 → 自动合并。
