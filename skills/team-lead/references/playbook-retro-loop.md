# Playbook Leaf · §2.2–2.5 Project Loop A, Engineering Loop B & 3 Physical Locks

本文件为 [`playbook.md`](./playbook.md) §2.2–2.5 的按需展开切片（`L1` leaf `<= 145` 行），定义双路复盘的诊断链路、H1–H5 假设检验、三道物理防回归锁与记录模板。详见 [`retro-loop`](../../retro-loop/SKILL.md)。

## 2.2 回路 A：Project / Product Feedback Loop

| 子回路 | 链路与核心信号 | 产出 Task 必填要素 |
|---|---|---|
| **A1. Telemetry 性能与体验退化** | 埋点 → `shared-telemetry` (`aidata`) → 基线对比（PLT P50/P90/P99、首屏/交互成功率、FPS/卡顿、DSAT 聚类突增） | 退化版本区间、可疑模块、可重复基准测试与夹具、目标阈值、上线后读回时间窗与回滚条件 |
| **A2. Crash 与线上异常诊断** | 符号化聚簇 → Top 崩溃版本区间 → `postmortem` 考古 → 必现回归测试（RED）→ 最小修复（GREEN）→ 读回 | 符号化栈、Git 近期变更比对、最小复现与竞态窗口、H1–H5 排除伪根因、Crash-Free 读回阈值 |
| **A3. 周期产品与设计一致性巡检** | 对比 Design Token、Figma、Basalt Web、SwiftUI DesignKit、实际页面与 `AppleUITesting` | 截图/快照、期望与实际差异、四态与无障碍覆盖、归属层（产品层 vs 共享底座） |

## 2.3 回路 B：Workflow / PR / Infra Feedback Loop

### B1. 五步证据链与 H1–H5 假设检验

1. **锁定证据边界**：固定 `ledger.md`、brief、R10、Git SHA、PR、CI 与时间窗（附 SHA-256）。
2. **还原时间线并找 Earliest Load-Bearing Fork**：定位第一个承载后续失败的分岔点。
3. **H1–H5 可反驳假设检验**：
   - `H1` 定义或验收不可实现（首次执行即缺真实接口/R7 证据）
   - `H2` 依赖或基线漂移（上游变更后契约/命令失效）
   - `H3` 调度或权限边界错误（Worktree/pane/D1 失配）
   - `H4` 实现/测试缺陷（有提交但验收或 V1 反复失败）
   - `H5` 门禁/发布读回缺失（本地绿但 6DQ/版本/线上不一致）
4. **反事实门槛核对**：移除该因素后最早分岔点是否仍出现？区分根因与放大器。
5. **转化为规则补丁**：写明触发条件、自动检查、失败输出与回滚方式。

### B2. PR 生命周期阻塞分类

| 阻塞类 | 核心证据 | 回馈开发动作 |
|---|---|---|
| 定义不完整 / 实现未收敛 | brief、R7、diff、R10 | 补齐真实接口与停止线；强制 RED→GREEN 最小切片 |
| 审查反复打回 / CI 契约漂移 | V1/V5 findings、6DQ、`sync_contract.py` | 沉淀静态检查路由、共享门禁或契约生成器 |
| 发布／读回失败 | `release_sync.py`、线上响应 | 增加发布前阻断与版本/摘要/TestFlight 读回 Task |

### B3. 三道物理防回归锁（3 Physical Locks）

| 锁 | 作用位置 | 适合固化的结论与载体 |
|---|---|---|
| **锁 1：认知与工作流层** | `user-skills` | 任务拆分、状态机、R10 模板、双轴清单（只靠记忆会再犯则落锁 1） |
| **锁 2：本地执行层** | `PreToolUse` 守卫、`herdr-dispatch.sh` | 路径隔离、显式 ID、Socket 唤醒、危险拦截（本地顺序错会再犯则落锁 2） |
| **锁 3：跨仓 CI 与组件层** | `shared-ci` 6DQ、`sync_contract.py`、`release_sync.py` | 契约、版本、质量、组件与遥测不可回归（多仓/发布仍会犯则落锁 3） |

*升级铁律*：同一故障两次出现，必须增加至少一条更高层级的物理锁；仅加文字提醒不算闭环。

## 2.4–2.5 双路复盘执行模板与结果分级

### 项目回路 A 记录模板

```markdown
# Project Feedback Record
Trigger / Product / version / environment:
Telemetry query, evidence window & control group:
Baseline, target & observed regression:
Candidate module & reproduction command:
Performance / Crash / UX Task (Owned Paths, pre/post-release acceptance, rollback):
```

### 工程回路 B 记录模板

```markdown
# Engineering Feedback Record
Trigger & Goal / Task / PR / release:
Evidence boundary (hashes) & Observable timeline:
Earliest Load-Bearing Fork:
H1..H5 verdict & Counterfactual gate:
Responsibility, stop boundary & Lock 1 / Lock 2 / Lock 3:
Executable Task & Verification command:
```

**结果分级**：`confirmed`（生成 Task/锁）· `probable`（生成诊断 Task）· `unknown`（扩充证据，维持 `blocked`）· `not-reproducible`（扩样本保留监控）· `rejected`（记录排除原因）。
