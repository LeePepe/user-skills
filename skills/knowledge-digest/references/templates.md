# `knowledge-digest` 标准模板与标杆范例

本文件定义 `~/Development/Personal/knowledge-vault/` 下三类档案（`concepts/`、`craft/`、`raw/`）及总索引 `index.md` 的标准结构。

---

## 1. 概念痛点卡模板（`concepts/<slug>.md`）

```markdown
---
title: <概念中文名：副标题>
category: <物理与硬件 | 计算机与网络 | 工程与设计 | 科学健康与生活>
tags: [<tag1>, <tag2>, <tag3>]
channel: <acquire | digest>
updated: YYYY-MM-DD
related_craft: ../craft/<craft-slug>.md   # 可选
raw_source: ../raw/<raw-slug>.md         # 可选
---

# <概念中文名：副标题>

## ⚡ 一句话本质（TL;DR）

> **<反常识底层结论>** —— <1 个直观尺度/时间/倍率数字对比>。

## 🧰 核心教具与量级速览

| 抽象概念 / 参数 | 关键数字 | 生活教具 / 直观参照 | 物理/工程效果 |
|---|---|---|---|
| <概念 1> | <数字 1> | <教具 1> | <效果 1> |
| <概念 2> | <数字 2> | <教具 2> | <效果 2> |

## 🎯 核心痛点直答（Pain-Point Q&A）

### Q1：<最常见的生活/工程痛点问题 1>？
- **直答**：<一句话结论>。
- **原理与教具**：<用生活物品打比方 + 1 个核心数字讲透机制>。

### Q2：<高频干扰或反直觉现象问题 2>？
- **直答**：<一句话结论>。
- **原理与教具**：<用生活物品打比方 + 1 个核心数字讲透机制>。

### Q3：<常见误区或安全焦虑问题 3>？
- **直答**：<一句话结论>。
- **原理与教具**：<用生活物品打比方 + 1 个核心数字讲透机制>。

### Q4：<选型对比或进阶边界问题 4>？
- **直答**：<一句话结论>。
- **原理与教具**：<用生活物品打比方 + 1 个核心数字讲透机制>。

## ✅ 立即可用清单（Actionable Checklist）

1. **<动作 1>**：<一句话讲清怎么做 + 为什么有效>。
2. **<动作 2>**：<一句话讲清怎么做 + 为什么有效>。
3. **<动作 3>**：<一句话讲清怎么做 + 为什么有效>。

## 🔭 延伸视野（1 分钟拔高）

- <从该原理延伸出的前沿应用或破界思考，控制在 2–3 句话内>。
```

---

## 2. 叙事与制作技巧卡模板（`craft/<slug>.md`）

```markdown
---
title: <技法名称>
category: 叙事与表达技法
tags: [<科普脚本>, <视觉化>, <教具隐喻>]
updated: YYYY-MM-DD
exemplar_concept: ../concepts/<concept-slug>.md
raw_source: ../raw/<raw-slug>.md
---

# <技法名称>

## ⚡ 一句话公式（TL;DR）

> **<核心叙事骨架公式>**（如：先制造视觉悬念 -> 用生活物件解释原理 -> 落到实用动作与前沿拔高）。

## 🎬 标准脚本结构流（Step-by-Step Spine）

| 阶段 | 目标作用 | 示范动作 / 句式 | 避坑指南 |
|---|---|---|---|
| 1. 开场钩子 | 打破习以为常，建立反常识悬念 | “如果能看见 XX……它其实是一种 YY” | 忌教科书式定义开场 |
| 2. 建立概念 | 放进熟悉家族，做极端尺度缩放 | “把波长放大 N 倍，相当于……” | 忌孤立报出枯燥频段/单位 |
| 3. 制造速度/规模感 | 用生活空间衡量极端物理量 | “穿过整个客厅只需 30 纳秒” | 忌只写公式光速 $3\times 10^8\text{ m/s}$ |
| 4. 可视化 + 教具演示 | 把暗箱机制变成肉眼可见的交互 | 冰箱=反射、鱼缸=吸收、砖墙=衰减 | 忌抽象术语堆叠 |
| 5. 常见痛点破案 | 解答观众最挠头的切身体验 | 手机挪几厘米卡顿、微波炉断网 | 忌脱离日常体感谈理论 |
| 6. 回应焦虑与对比 | 拆解安全迷思与参数选型 | 光子能量 vs. DNA；2.4G vs. 5G | 忌只说“安全”不给量级证据 |
| 7. 行动建议与拔高 | 给出可落地清单，结尾升华格局 | 摆位三原则 -> 无线感知新世界 | 忌虎头蛇尾无可执行收获 |

## 🛠 四大可复用表达技巧（Reusable Craft Patterns）

1. **把不可见变成可见（Invisible -> Visible）**：<具体手法与适用场景>
2. **用生活物品当教具（Household Props as Mental Models）**：<具体手法与适用场景>
3. **夸张时间/空间尺度（Extreme Scale Zoom）**：<具体手法与适用场景>
4. **单数字配单画面（One Number, One Scene）**：<具体手法与适用场景>

## 🔄 迁移套用模板（怎么用到下一个主题）

- 当你要讲 **<新主题 A>** 时：把不可见的 `<X>` 变成 `<视觉元素>`，用 `<日常物件>` 当教具，只抓 `<1 个核心倍率>`。
```

---

## 3. 原始素材与拆解存档模板（`raw/<slug>.md`）

```markdown
---
title: <原始素材标题>
category: 原始素材存档
source_type: <video_script_summary | article | transcript | note>
captured_at: YYYY-MM-DD
derived_concept: ../concepts/<concept-slug>.md
derived_craft: ../craft/<craft-slug>.md
---

# <原始素材标题>

## 原始摘录 / 总结全文

<完整保留用户提供的原始总结、脚本或关键段落，不做有损删改>

## 提炼产物指针

- **概念痛点卡**：[`../concepts/<concept-slug>.md`](../concepts/<concept-slug>.md)
- **叙事制作技巧卡**：[`../craft/<craft-slug>.md`](../craft/<craft-slug>.md)
```

---

## 4. 总目录索引模板（`index.md`）

```markdown
# Personal Knowledge Vault（个人知识与好内容拆解库）

由 `knowledge-digest` Skill 维护。所有概念卡采用「⚡ 一句话本质 + 🎯 核心痛点 Q&A + ✅ 立即可用清单」结构；好内容同步拆解沉淀到 `craft/` 与 `raw/`。

## 🧠 原理与概念痛点卡（`concepts/`）

| 门类 | 标题 | 一句话本质（TL;DR） | 路径 | 更新日期 |
|---|---|---|---|---|
| 物理与硬件 | 如果能看见 WiFi：看不见的光与室内信号破案 | WiFi 是波长放大 25 万倍的「看不见的光」，穿家仅需 30 多纳秒；卡顿与断网本质是金属反射、水体吸收与同频高功率干扰。 | [`concepts/wifi-invisible-light.md`](concepts/wifi-invisible-light.md) | 2026-11-09 |

## 🎬 叙事结构与制作技巧卡（`craft/`）

| 门类 | 标题 | 一句话公式（TL;DR） | 路径 | 更新日期 |
|---|---|---|---|---|
| 叙事与表达技法 | 科普视频拆解：把不可见变成可见的痛点叙事法 | 先制造视觉悬念（不可见变可见），再用生活教具（冰箱/鱼缸/墙）和单数字配画面解释原理，最后落到行动建议与前沿拔高。 | [`craft/science-video-invisible-to-visible.md`](craft/science-video-invisible-to-visible.md) | 2026-11-09 |

## 📦 原始好素材存档（`raw/`）

| 来源类型 | 标题 | 衍生卡片 | 路径 | 归档日期 |
|---|---|---|---|---|
| 视频脚本总结 | 「如果能看见 WiFi」视频脚本流程与制作技巧总结 | `concepts/wifi-invisible-light.md` / `craft/science-video-invisible-to-visible.md` | [`raw/wifi-video-script-breakdown.md`](raw/wifi-video-script-breakdown.md) | 2026-11-09 |
```
