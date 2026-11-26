---
name: repo-kit
description: 用 shared-ci 模板与检查器治理 repo 合同；当任务涉及 tech-context.md、.github/repo-contract.json、scripts/verify、.githooks/pre-push、core.hooksPath、RG-001..RG-006 或 repo_gate_auditor.py 时触发。支持 init、adopt、split。
---

# repo-kit

**规则不在本 skill 里。** 合同、模板、检查器都在 `<owner>/shared-ci`，按固定 SHA 使用；随仓业务技能（Repo-Bound Skills）由 `skill-contract` 审计；跨仓门禁、慢脚本与 Hook 漂移由 `scripts/repo_gate_auditor.py` 确定性探测：

| 需要 | 读取（`shared-ci@<SHA>` 或 `user-skills`） |
|---|---|
| repo 最少要有什么 | `ai/repo-contract.md`（机器可读：`schemas/repo-contract-v1.json`） |
| 如何定义本仓 layer 与 PR | 同版本 `ai/repo-contract.md` 的 Repository development contract；格式见 `docs/context-cli-contract.md` |
| 执行 agent 在 repo 内怎么工作 | `ai/agent-protocol.md` |
| 模板 | `templates/`（目录入口、root/leaf `tech-context`、`design-contract.md`、`development.md`、`ci.yml`、`release-{testflight,sdk}.yml`、`scripts/verify`） |
| 架构与 UI 门禁检查 | `shared-ci/scripts/context/audit`、`shared-ci/scripts/context/resolve <path>`、`shared-ci/scripts/context/design_contract.py` |
| 发布与线上读回（Ring 6） | `shared-ci/scripts/quality/release_sync.py` + `docs/automated-release.md` + `templates/release-{testflight,sdk}.yml` |
| 跨仓门禁与慢脚本检测 | `python3 skills/repo-kit/scripts/repo_gate_auditor.py scan --dev-root ~/Development --json` |
| 随仓业务技能（Repo-Bound Skills） | `python3 skills/skill-contract/scripts/skill_contract.py audit --repo . --strict`（细则见 [repo-bound-skills-and-gates.md](references/repo-bound-skills-and-gates.md)） |

先取当前 `shared-ci` 发布 SHA（其 release notes / README 顶部），整个任务只用这一个 SHA。

`pr-facts`（别名 `pr-status`）只读输出 PR 与检查事实摘要；失败检查默认是 `soft_advisory_exit_0`，`--strict` 才返回非零。

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| init / adopt / split repo contract | `references/repo-bound-skills-and-gates.md` | `python3 skills/repo-kit/scripts/repo_gate_auditor.py scan --repo . --json` |
| cross-repo gate drift (`RG-001..006`) or slow `verify` | `references/repo-bound-skills-and-gates.md` | `python3 skills/repo-kit/scripts/repo_gate_auditor.py bottlenecks --dev-root ~/Development --json` |
| `skill-retro` self-driven loop check | `references/repo-bound-skills-and-gates.md` | `python3 skills/repo-kit/scripts/repo_gate_auditor.py loop-check --dev-root ~/Development --json` (supports `--summary`, `--full`, `--no-cache`) |
| Repo-Bound Skills & UI `design_contract.py` gate | `references/repo-bound-skills-and-gates.md` | `python3 skills/skill-contract/scripts/skill_contract.py audit --repo . --strict` |
| verification | `references/eval-cases.json` (Script-Only Regression Drawer: run via `skill_contract.py eval`, do not view) | `python3 skills/skill-contract/scripts/skill_contract.py eval --skill repo-kit --strict` |

## Role-Based Routing

| Role | Single mandatory L1 drawer | Single mandatory L2 CLI |
|---|---|---|
| Role: Explorer / Mechanical | `references/repo-bound-skills-and-gates.md` | `python3 skills/repo-kit/scripts/repo_gate_auditor.py scan --dev-root ~/Development --json` |
| Role: M1-TL | `references/repo-bound-skills-and-gates.md` | `python3 skills/repo-kit/scripts/repo_gate_auditor.py loop-check --dev-root ~/Development --json` |
| Role: Writer | `references/repo-bound-skills-and-gates.md` | `python3 skills/repo-kit/scripts/repo_gate_auditor.py scan --repo . --strict --json` |
| Role: Reviewer | `references/repo-bound-skills-and-gates.md` | `python3 skills/skill-contract/scripts/skill_contract.py eval --skill repo-kit --strict` |

## 共同前置与 Worktree 根

- 仓库先定义 layer/PR/验证规范，agent 从 `AGENTS.md` 目录读取它；Dev Team 的 Steering/Task 流程只是消费者。
- 在目标 repo 建专用分支/worktree（`~/Development/worktrees/<task>/` 或 `~/orca/workspaces/<repo>/<task>/`）；Owner 主 checkout 不作为 agent 写入位置。
- 读目标 repo 现有 `AGENTS.md`、`CLAUDE.md`、`constitution.md`、`docs/` 与仓内 `skills/`，**保留业务事实和红线**，只改结构。
- 账号、profile、本机绝对路径、repo 数字 ID 不写进 repo（audit 会拒绝）。首次提交前用 `git config user.email` 确认；若 repo 自带 `.git/config` 覆盖绑定，报告给 Owner。

## Repo-Bound Skills 与跨仓门禁（`RG-001..RG-006`）

- **`tech-context.md` vs. Repo-Bound Skills**：`tech-context.md` 声明代码层 `owns`、`depends_on`、`red_lines` 与 `gate`；仓内 `skills/<app>-*/SKILL.md`（或 `.agents/skills/<app>-*/SKILL.md`）沉淀业务领域规则、数据手册与专用脚手架，并在仓根 `AGENTS.md` 注册，接入 `skill_contract.py audit --repo . --strict`。
- **`repo_gate_auditor.py` 六类漂移与四维提速**：自动检测 `RG-001`（缺 `scripts/verify`）、`RG-002`（缺增量路径选择）、`RG-003`（串行 `for layer in $layers; do context run "$layer"` 未批量并发）、`RG-004`（`pre-push` 强制 `--all` 或绕过 `scripts/verify`）、`RG-005`（`core.hooksPath` 或 CI 漂移）、`RG-006`（`shared-ci` SHA 落后或未用 `.github/repo-contract.json`），输出 `gate_health_score`、四维提速建议（`angle_a..d`）与 `actionable_proposals` 供 `skill-retro` 直接消费。

## init：新建或补齐 repo 合同

1. **探测**：运行 `repo_gate_auditor.py scan --repo . --json` 识别栈（SPM / npm / Python / Xcode）、package/target、测试根、仓内 `skills/`、现有 hooks/CI 与 ruleset required checks。
2. **layer 合同与 UI 门禁**：按 root/leaf 模板生成总表与各层 `tech-context.md`。**UI 层必须挂载 `design_contract.py`**（`--platform <desktop|mobile|web> --audit-code <paths> --audit-l10n . --locales en,zh-Hans --check-edge-clipping --audit-geometry`）。
3. **Repo-Bound Skills 梳理**：整理至 `skills/<app>-*/SKILL.md` 或 `.agents/skills/<app>-*/SKILL.md`，在 `AGENTS.md` 注册并用 `skill_contract.py audit --repo . --strict` 校验。
4. **PR 与开发规范**：从 `templates/development.md` 生成 `docs/development.md`，引用 layer 路径权威，不复制第二套 globs。
5. **AGENTS.md**：只作目录，`CLAUDE.md` 只写“先读 `AGENTS.md`”。
6. **同入口增量 `verify` 与 `pre-push`**：`scripts/verify [--changed|--all|--selected]` 默认按 `git diff --name-only` 跑变更层，多 layer 一次性传给 `context run $layers --mode "$mode"` 触发拓扑波次并发；`.githooks/pre-push` 默认调 `scripts/verify`（仅在 `VERIFY_ALL=1` 时跑 `--all`）。
7. **CI、Runner 与发布读回（Ring 6）**：`.github/workflows/ci.yml` 调 `<owner>/shared-ci/.github/workflows/quality.yml@<SHA>`；发布流接入 `release-{testflight,sdk}.yml` + `release_sync.py`；self-hosted runner 独立注册（禁 `--replace`），细则见 [repo-bound-skills-and-gates.md](references/repo-bound-skills-and-gates.md)。
8. **PR 模板 + CODEOWNERS + 验收**：归属/依赖 audit、`repo_gate_auditor.py` 与 `skill_contract.py --repo .` 零发现。

## adopt：接入或升级共享库

1. 读目标版本 `ai/INTEGRATION.md` 或 `ai/MIGRATION.md` 及 `ai/COMPATIBILITY.md`。
2. 统一更新 `.github/repo-contract.json`、workflow caller 与 manifest/lockfile 中的完整 SHA pin，必要适配代码放消费方 adapter layer。
3. `scripts/verify --all` 与 `repo_gate_auditor.py scan --repo .` 通过。

## split：从现有 repo 拆出共享库

1. **切接缝**：在原 repo 新建暂存包（如 `Packages/<Kit>`），宿主依赖改为协议注入。
2. **搬迁与补齐**：代码移入暂存包，保留并跑通全部回归测试，完成新增能力。
3. **拆 repo**：隐私预检无凭据泄漏后，用 `git filter-repo --path Packages/<Kit>/ --path-rename Packages/<Kit>/:` 导出；新 repo 执行 **init**（含 `release-sdk.yml` 与 `release_sync.py`），原 repo 执行 **adopt** 切换远程 exact 版本。

See [repo-bound-skills-and-gates.md](references/repo-bound-skills-and-gates.md) and [eval-cases.json](references/eval-cases.json).
