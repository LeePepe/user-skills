# Repo-Bound Skills、跨仓门禁审计（`RG-001..006`）、UI 门禁、发布读回与 Runner 规范

> 本文件是 `repo-kit` 的按需 L1 参考：提供 `repo_gate_auditor.py` 六类跨仓门禁漂移与四维提速对照表、`tech-context.md` 与 Repo-Bound Skills 边界模板、UI 层 `design_contract.py` 门禁挂载、Ring 6 `release_sync.py` 发布读回，以及 self-hosted runner 注册 SOP。

## 1. `repo_gate_auditor.py` 六类跨仓门禁漂移与四维提速

```bash
python3 skills/repo-kit/scripts/repo_gate_auditor.py scan --dev-root ~/Development --json
python3 skills/repo-kit/scripts/repo_gate_auditor.py check --root ~/Development --summary --json
python3 skills/repo-kit/scripts/repo_gate_auditor.py bottlenecks --dir ~/Development --json
python3 skills/repo-kit/scripts/repo_gate_auditor.py loop-check --dev-root ~/Development --json --no-cache
# `audit` aliases `scan`; `--target-dir` aliases `--repo`; `--full` disables compaction.
```

| 规则 ID | 类别 (`category`) | 触发条件 | 标准修复动作与四维提速映射 |
|---|---|---|---|
| `RG-001` | `missing_verify_entrypoint` | 含 `.github/workflows/ci.yml`、`.githooks` 或 `scripts/hooks`，但缺少可执行 `scripts/verify` | 接入同入口 `scripts/verify`（`chmod +x`），统一本地 `pre-push` 与 CI 调用（`angle_d_tiered_fast_fail`） |
| `RG-002` | `missing_incremental_verify` | `scripts/verify` 存在但缺少增量路径筛选（`--changed`、`VERIFY_BASE`、`git diff --name-only`、`detect_changed_files`） | 默认按 `git merge-base` + `git diff --name-only` 仅跑受影响 layer/skill（`angle_a_path_scoped`） |
| `RG-003` | `serial_layer_or_suite_runner` | `scripts/verify` 串行循环调 `for layer in $layers; do context run "$layer"` 或串行跑多个测试套件 | 改为一次性传入 `context run $layers --mode "$mode"` 或 `ThreadPoolExecutor` 并发池（`angle_c_parallel_dag`） |
| `RG-004` | `prepush_forces_full_or_bypasses_verify` | 生效 `pre-push` 硬编码 `scripts/verify --all`，或存在 `scripts/verify` 却未调用 | `pre-push` 默认 `exec "$REPO_ROOT/scripts/verify"`，仅在 `VERIFY_ALL=1` 时传 `--all`（`angle_a_path_scoped`） |
| `RG-005` | `hook_path_or_ci_drift` | `core.hooksPath` 未配置/指向缺失或不可执行的 `pre-push`，或 `ci.yml` 未调 `scripts/verify` / `shared-ci/quality.yml` | 执行 `git config core.hooksPath .githooks`（或 `scripts/hooks`）并确保 `ci.yml` 同入口调用（`angle_d_tiered_fast_fail`） |
| `RG-006` | `shared_ci_pin_drift` | 仓库固定 `shared-ci` SHA 落后于本机 `shared-ci` HEAD，或仍用旧版 `AGENTS.md` 行内 pin 而缺 `.github/repo-contract.json` | 升级 `.github/repo-contract.json` 与 workflow caller 至最新 `shared-ci` SHA（`angle_b_watermark_cache`） |

`scan`/`check`/`audit`/`bottlenecks`/`loop-check` 会在缩进 JSON 超过 15,000 字节（`COMPACT_JSON_BYTES`）时自动保留契约顶层统计键与完整 `actionable_proposals` 归属字段（`skill`、`layer`、`ref`、`metric_id`、有界 `title`/`plain_reason`/`action`），按需裁剪 `repos`、`scan_errors`、`healthy_repo_names`、`bottleneck_repos` 并附 `*_truncated` 省略计数；`--summary` 或 `--compact` 强制摘要，`--full` 保留完整输出。默认审计使用临时目录中的 60 秒文件 stat/SHA watermark，`--no-cache` 可绕过。

`pr-facts`（别名 `pr-status`）只读汇总 PR 与检查事实；默认软提示，`--strict` 才因失败检查返回非零。默认审计使用临时目录中的 60 秒文件 stat/SHA watermark，`--no-cache` 可绕过。
---

## 2. `tech-context.md` vs. Repo-Bound Skills 职责边界

| 维度 | `tech-context.md`（架构层契约） | Repo-Bound Skill（`skills/<repo>-*/` 或 `.agents/skills/<repo>-*/`） |
|---|---|---|
| **定位** | 声明某一代码层（Layer）的路径归属、依赖方向、红线与确定性验证门禁 | 沉淀本仓库专属的领域规则、业务数据/API 手册、专用 CLI 剧本与截图/测试脚手架 |
| **机器消费方** | `shared-ci/scripts/context/{audit,resolve,run}`、`scripts/verify` | 智能体（Claude Code / Codex / Copilot）在触发对应领域任务时按需加载 |
| **典型内容** | `layer`、`owns`、`depends_on`、`gate`（build/test/design）、`red_lines` | 业务计算口径（如财务 Anchor+Delta）、内部 API 参数表、专用 fixture 生成器 |
| **禁止混放** | 禁止塞入长篇业务操作手册、API 枚举表或自然语言问答套路 | 禁止替代 `tech-context.md` 的 `owns` / `depends_on` 架构边界或跨仓通用规范 |
| **审计门禁** | `shared-ci/scripts/context/audit` | `skill_contract.py audit --repo . --strict`（及 `eval --repo . --strict`） |

### 2.1 Repo-Bound Skill 目录与注册规范

1. **目录位置**：统一放在 `<repo>/skills/<skill-name>/SKILL.md`（或 `<repo>/.agents/skills/<skill-name>/SKILL.md`），命名建议带业务前缀（如 `financial-advisor`、`financial-data-toolkit`）。
2. **根 `AGENTS.md` 注册**：在仓根 `AGENTS.md` 的读取契约或技能索引表中显式列出每个随仓技能的触发场景与路径，保证智能体从入口即可按需下钻。
3. **四道防爆阀门（`--repo` 模式）**：豁免跨项目私有符号拦截（`repo_bound=True`），严格执行 **Valve 2**（案例抽屉坐标必填、`<= 15` 条上限）、**Valve 3**（`L0 <= 180L / 16KB`、`L1 <= 160L / 16KB`、代码块 `<= 25L`）与 **Valve 4**（零孤儿引用）。

```bash
# 在业务仓库根目录或 scripts/verify 中接入随仓技能门禁
python3 ~/Development/user-skills/skills/skill-contract/scripts/skill_contract.py audit --repo . --strict
python3 ~/Development/user-skills/skills/skill-contract/scripts/skill_contract.py eval --repo . --strict
```

---

## 3. UI 层 `tech-context.md` 门禁模板（`design_contract.py`）

凡包含 Apple（macOS / iOS）或 Web 界面的 UI 层，其 `tech-context.md` 的 `gate:` 必须挂载 `shared-ci/scripts/context/design_contract.py`：

```yaml
---
layer: UI
owns: [Sources/AppUI/**, Tests/AppUITests/**]
depends_on: [Core, DesignKit]
gate:
  build: swift build --target AppUI
  test: swift test --filter AppUITests
  design: >-
    python3 "$SHARED_CI_ROOT/scripts/context/design_contract.py"
    --platform desktop
    --audit-code Sources/AppUI
    --audit-l10n . --locales en,zh-Hans
    --check-edge-clipping Sources/AppUI
    --audit-geometry Sources/AppUI
red_lines:
  - 禁止硬编码色值、字号或绕过 DesignKit 令牌；禁止出现未本地化裸 key 或数值截断省略号。
---
```

---

## 4. 发布与线上读回（Ring 6 `release_sync.py`）

- **模板接入**：Apple 应用使用 `shared-ci/templates/release-testflight.yml`；共享库 / SDK 使用 `shared-ci/templates/release-sdk.yml`。
- **状态读回与对账**：发布脚本与流水线统一调用 `shared-ci/scripts/quality/release_sync.py` 校验 `PUBLISHED_SHA == CANDIDATE`、构建号、处理状态（`complete`）及分发渠道可用性，严禁只触发上传而不读回终态。

---

## 5. Self-Hosted Runner 独立注册规范

个人账号的 GitHub runner 按 repository 独立注册，已有 runner 不跨仓复用、不搬迁，**严禁使用 `config.sh --replace` 抢占其他仓库的 runner**。

| 项目 | 每 repo 规范 |
|---|---|
| 安装目录 | `$HOME/actions-runner-<repo>` |
| runner name | `<repo>-mac` |
| LaunchAgent | `actions.runner.<owner>-<repo>.<name>`（由 `svc.sh` 管理） |
| labels | `self-hosted,macOS,ARM64`（可追加仓库专属 label） |

1. **下载与 SHA-256 校验**：固定官方 `actions/runner v2.335.1`（`actions-runner-osx-arm64-2.335.1.tar.gz`），先用 `shasum -a 256` 核对官方 SHA-256，再用 `tar tzf` 确认归档内无绝对路径或 `..` 段后解压。
2. **获取短时注册 Token**：经 Owner 批准调用 `POST repos/<owner>/<repo>/actions/runners/registration-token`，仅在内存变量传递，不打印、不落盘。
3. **注册（不带 `--replace`）**：
   ```bash
   ./config.sh --url "https://github.com/<owner>/<repo>" --token "$RUNNER_TOKEN" --name "<repo>-mac" --labels "self-hosted,macOS,ARM64" --unattended
   ```
4. **服务托管与环境变量**：执行 `./svc.sh install && ./svc.sh start`；`launchd` 不读取交互式 shell profile，模型网关与凭据须通过服务包装脚本或环境文件注入。
5. **正向/负向验收与 Ruleset 升级**：确认 `gh api repos/<owner>/<repo>/actions/runners` 显示 `online` → 设置 `SHARED_CI_REVIEW_RUNNER=true` → 跑通正向放行与负向拦截 → 列出 required checks `old -> new` 报 Owner 批准后更新 ruleset。
