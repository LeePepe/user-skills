# Jev (System One) & Falcon 使用指南

> L1 reference：对应 `how-to-nodes.md` 的 **`I3 Classify`** 节点与三层漏斗 Layer 1（70–500ms 快思考判断）。

## §1 网关链路与 Source Key 鉴权

1. **标准调用链路**：
   - `Agent / Script` → `Falcon (127.0.0.1:19823/v1/systemone)` → `Local Raven (127.0.0.1:7024/v1/systemone, microsoft-decision-1)`。
   - **禁止直接绕过 `:19823` 裸调 `:7024`**：所有本机 Jev 请求必须经过 Falcon，以便留存 7 天概率分布并在 Falcon UI 中审阅加星（Star 永久保留为回归样本）。
2. **Source Key（`FALCON_SOURCE_KEY`）机制**：
   - Falcon 为每个本地来源签发独立令牌（形如 `falcon_...`），数据库中存 SHA-256 摘要与 4 位尾号，明文保存在 `~/.config/falcon/source-keys.json`（权限 `0600`）。
   - `skills/team-lead/scripts/jev_client.py` 按 `agent_role` 自动查找 `source-keys.json` 并内置角色别名回退：

| 调用方 `agent_role` | `source-keys.json` 主键 | 自动回退链（兼容旧键） |
|---|---|---|
| `team-lead` / `personal-m2-assistant` / `m2-dispatcher` | 同名键 | `team-lead` → `goal-task` |
| `writer` / `reviewer` / `explorer` | 同名键 | `team-lead` → `goal-task` |
| `deep-research` / `knowledge-digest` | 同名键 | `deep-research` → `team-lead` |
| `my-designer` / `design-studio` | 同名键 | `my-designer` → `team-lead` |
| `shared-ci` / `retro-loop` | 同名键 | `shared-ci` → `team-lead` |

## §2 四种题型与写题黄金法则

核心原则：**“Judgments from Jev, decisions in code”**——Jev 只回答可观察的客观事实判断，最终分支动作写在 Python 代码真值表（Truth Table）里。

| 题型 (`type`) | 适用场景 | 必填字段与返回结构 | 默认真值表阈值 |
|---|---|---|---|
| `choice` | 多选一互斥分类（路由、评论意图、引用支持度） | `instructions` + `criteria: {key: desc}`；返回 `choice`, `confidence`, `probabilities` | 必配伴生 `noul` 拦截“都不匹配（Null Case）” |
| `noul` | 二元真假概率（是否噪音、是否死循环、是否保留上下文） | `instructions`；返回 `noul` (`0.0 ~ 1.0`) | `> 0.70` Yes；`< 0.30` No；`0.30 ~ 0.70` 灰区升维 |
| `score` | 2–10 级量表打分（R09 Top-K 检索相关性重排） | `instructions` + 量表范围；返回加权期望分与各级概率 | 按得分降序取 Top-K |
| `box` | 视觉 UI 缺陷定位（R05 四态双外观截图溢出/碰撞框选） | 图像输入 + 检测目标描述；返回归一化坐标框 | 检出框且置信度达标则阻断或标 `IMPL_BUG` |

**五条写题铁律**：
1. **单题单义**：一题只问一件事，题面严禁出现 `and` / `or` 复合条件。
2. **并发扇出（Speculative Fan-Out）**：针对同一份 `state` 的多个正交问题，必须打包在同一个 `questions` 字典里单次 HTTP 请求并发问完。
3. **问事实不问策略**：问 `"Does this comment point out a bug or logic flaw?"`，不要问 `"Should the agent reply or edit code?"`。
4. **`choice` 必配伴生 `noul`**：防止模型在无关输入上强行选一个最高概率项。
5. **Fail-Open / 确定性兜底**：网关超时（默认 `1.5s`）或离线时，调用方必须回退到确定性规则或交由上层模型处理。

## §3 标准调用方式（复用 `jev_client.py`）

Python 脚本内调用：

```python
from jev_client import jev_decide

res = jev_decide(
    state={"comment": "LGTM, ship it!"},
    questions={
        "intent": {
            "type": "choice",
            "instructions": "Classify the PR review comment.",
            "criteria": {
                "requires_code_change": "Requests a code or test change",
                "question_only": "Asks a question without requesting edits",
                "ack_or_noise": "Approval, praise, or bot status noise",
            },
        },
        "is_actionable": {
            "type": "noul",
            "instructions": "Does this comment require the author to take action?",
        },
    },
    agent_role="shared-ci",
    goal_id="pr-128",
)
```

命令行快速探针测试：

```bash
python3 ~/Development/user-skills/skills/team-lead/scripts/jev_client.py \
  --state '{"msg":"先检查通知开关"}' \
  --questions '{"is_multi_repo":{"type":"noul","instructions":"Does this span multiple repos?"}}'
```

## §4 已落地场景与常见排障

| 场景编号 | 技能与脚本位置 | 题型组合 |
|---|---|---|
| `I7` M2 选路 | `personal-m2-assistant/scripts/m2-router.py` | `choice(route_mode)` + `choice(primary_topic)` + `noul(has_unmapped_domain)` |
| `R02` Skill 预筛 | `team-lead/scripts/jev-skill-router.py` | 两阶段 `choice` + 伴生 `noul` |
| `R03` 无损压缩 | `team-lead/scripts/jev-compact.py` | 逐轮 `noul`（后续步骤是否仍需该段工具输出） |
| `R06/R09` 调研核查 | `deep-research/scripts/jev_research_helper.py` | `choice(supports\|contradicts\|unrelated)` + `noul` + `score` |
| `R08` 死循环看门狗 | `team-lead/scripts/goal-supervisor.py` | 3 个并行 `noul`（重复失败 / 向前推进 / 越界） |

**常见故障速查**：
- **Falcon 记录不增长（被绕过）**：检查 `~/.config/falcon/source-keys.json` 是否包含当前 `agent_role`；缺失时旧逻辑会跳过 `:19823` 直连 `:7024`。
- **`Falcon storage is full.`**：并非 `falcon.sqlite` 满了，而是 macOS 磁盘可用空间耗尽触发了 Falcon 的磁盘余量保护；清理 `~/Library/Developer/Xcode/DerivedData` 等缓存即可恢复。
