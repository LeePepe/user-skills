# Bot 与 CLI agent：分工与生命周期

本文件是本环境（Owner tianpei）的分工表与 CLI agent 生命周期规则；合同角色、权限与审查以 [SKILL.md](../SKILL.md) 为准，模型与启动参数只看 [runtime-profiles.yaml](runtime-profiles.yaml)，台账与事件见 [ledger.md](ledger.md)。

## 两层

| 层 | 是什么 | 谁开 | 活多久 |
|---|---|---|---|
| bot 层 | 有独立聊天的常驻 bot，各自固定一台机器 | Owner | 长期 |
| CLI agent 层 | bot 或 TL 在 herdr 里启动的 CLI 会话（TL、Planner、Reviewer、写者、Explorer） | 协调者 bot / TL | 用完即关 |

## bot 分工（唯一一处）

| bot | 机器 | 在 GTT 中的身份 | 不做 |
|---|---|---|---|
| Hotty | Mac mini | 总管：整体 plan 与跨 Goal 台账汇总，看卡点、协调各 bot 分工；Owner 问时从各 Goal 台账查超时/卡住事件 | 不接管具体 Goal 的协调，不做 Task 写者 |
| Joy | MacBook | MacBook 上 Goal 的协调者 | 不操作 Mac mini |
| Atlas | Mac mini | retro 相关工作；AIDash（数据与展示平台）及 soe + AIDash 合并线的协调者 | |
| 艺术家 | Mac mini | Strata 设计定义（设计方面的定义方/Owner 代理） | 不放行发布、合并 |
| fonder | Mac mini | 基础维护（环境、工具、CLI、herdr 等） | |
| flower | Mac mini | workflow 相关工作：01 需求流程、03 PR 交付的整体流程、06 周期维护 | 不做 PR 状态的具体盯梢（另由监督者负责，方案调研中） |
| pigger | Mac mini | Owner 的个人咨询顾问，不参与 GTT | 不接 GTT 任务 |

- 每个 bot 只操作自己的机器；跨机器只通过 handoff 交接（[ledger.md](ledger.md) 的 inputs 规则）。
- Owner（用户）是唯一能放行发布、合并和危险操作的人；bot 转述的授权不算放行，需在台账记 Owner 的原话或来源。

## 每个 Goal 的责任线

每个 Goal 在台账文件头写明：**Owner、协调者 bot、TL（herdr 名 + 模型）、机器、合同 SHA**。信号（事件）只按这条线走：

```
写者/Reviewer/Explorer → TL → 协调者 bot → Owner
```

- 协调者 bot 每个 Goal 只有一个：开 TL、收 TL 事件、需要 Owner 决定时去问。
- TL 拆 Target/Task，在 herdr 里开 Planner、Reviewer、写者；不兼任写者或独立验收（SKILL.md「角色绑定与交接」）。
- Hotty 不在线上，只读台账汇总；线上没人接的事件由 Owner 问 Hotty 时查出。

## CLI agent 角色与档位

| 角色 | 档位（见 profile） | 谁开 | 生命周期 |
|---|---|---|---|
| Team lead | strong | 协调者 bot | 整个 Goal |
| 裁决 | strong | TL / 协调者 | 一次裁决 |
| Planner | mid | TL | 一轮计划 |
| Planner 的 Reviewer、代码 Reviewer | mid，**与被审者不同家** | TL | 一次审查（每轮新会话） |
| 写者 | worker | TL | 一个 Task |
| Explorer、机械活 | worker | 任何 bot / TL | 一次调研或一次活 |
| 调研主 + 旁听 | research，旁听与主不同家，强档裁决 | 协调者 / TL | 一次调研 |

## 生命周期（herdr）

1. **全部进 herdr**：所有 CLI agent（含 Reviewer、Explorer、一次性 `codex exec`）都在 herdr 中启动，有可检索的名字；不留 herdr 外的临时进程。
2. **命名**：`<goal>-<task>-<role>-r<round>`，例如 `merge-tk1a-writer-r1`、`merge-tk1a-reviewer-r2`；Goal 级角色用 `<goal>-goal-tl-r1`。
3. **启动**：显式带 profile 里的模型和参数；`herdr agent start --timeout` 单位是毫秒（3000–300000）；首次进入目录时处理 folder-trust；不接受 bypass-permissions 提示，改用 profile 中的受限参数。
4. **发 prompt**：等 agent `idle` 再发，发完读回确认 `working`；没变 `working` 视为未派发。
5. **结束**：
   - TL 在下属还有 `working` 时不得结束；先收齐下属回执（R10）。
   - 每个 agent 完成后写回执、在台账记 `DONE`，然后关闭其 herdr 工作区。
   - worktree 不自动删：列清单（路径、分支、是否已合并）交 Owner 确认后再清理。
6. **不碰**：Owner 手开的会话与工作区；herdr `w1`（shared-design-system）。
