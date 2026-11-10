# `knowledge-digest` 标准模板与标杆范例

本文件定义 `~/Development/Personal/knowledge-vault/` 下四类档案（`concepts/`、`craft/`、`thoughts/`、`raw/`）及总索引 `index.md` 的骨架结构。完整结构化字段与长段落模板已抽入 L2 抽屉 [`card-templates.json`](card-templates.json)，归档与自动寻题/发布脚本见 [`../scripts/digest_vault.py`](../scripts/digest_vault.py)。

---

## 1. 概念痛点卡骨架（`concepts/<slug>.md` → `TPL-CONCEPT-001`）

```markdown
---
title: <概念中文名：副标题>
category: <物理与硬件 | 计算机与网络 | 工程与设计 | 科学健康与生活>
tags: [<tag1>, <tag2>, <tag3>]
channel: <acquire | digest>
updated: YYYY-MM-DD
---

# <概念中文名：副标题>

## ⚡ 一句话本质（TL;DR）
> **<反常识底层结论>** —— <1 个直观尺度/时间/倍率数字对比>。

## 🧰 核心教具与量级速览
| 抽象概念 / 参数 | 关键数字 | 生活教具 / 直观参照 | 物理/工程效果 |
|---|---|---|---|

## 🎯 核心痛点直答（Pain-Point Q&A，3–5 问）
### Q1：<最常见的生活/工程痛点问题>？
- **直答**：<一句话结论>。 / **原理与教具**：<生活物品类比 + 1 个核心数字>。

## ✅ 立即可用清单（Actionable Checklist，≤3 条）
1. **<动作 1>**：<一句话讲清怎么做 + 为什么有效>。
```

---

## 2. 叙事与制作技巧卡骨架（`craft/<slug>.md` → `TPL-CRAFT-001`）

```markdown
---
title: <技法名称>
category: 叙事与表达技法
tags: [科普脚本, 视觉化, 教具隐喻]
updated: YYYY-MM-DD
exemplar_concept: ../concepts/<concept-slug>.md
---

# <技法名称>

## ⚡ 一句话公式（TL;DR）
> **<核心叙事骨架公式>**（视觉悬念 -> 生活物件解释原理 -> 实用动作与拔高）。

## 🎬 标准脚本结构流（Step-by-Step Spine）
| 阶段 | 目标作用 | 示范动作 / 句式 | 避坑指南 |
|---|---|---|---|

## 🛠 四大可复用表达技巧 & 🔄 迁移套用模板
1. **把不可见变成可见** · 2. **用生活物品当教具** · 3. **夸张尺度** · 4. **单数字配单画面**
```

---

## 3. 原始素材存档与随笔骨架（`raw/<slug>.md` & `thoughts/<slug>.md`）

```markdown
---
title: <原始素材或随笔标题>
category: <原始素材存档 | 智能家居与生活 | 思考与感悟>
updated: YYYY-MM-DD
insight: <随笔一句话感悟（thoughts 专用）>
---

# <标题>

## 原始摘录 / 核心感悟正文
<保留原始总结或随笔三段式，详见 card-templates.json 中 TPL-RAW-001 / TPL-THOUGHTS-001>
```

---

## 4. 总目录索引模板（`index.md`）

```markdown
# Personal Knowledge Vault（个人知识与好内容拆解库）

## 💭 思考与感悟随笔（`thoughts/`）
| 门类 | 标题 | 核心感悟（Insight） | 路径 | 更新日期 |
|---|---|---|---|---|

## 🧠 原理与概念痛点卡（`concepts/`）
| 门类 | 标题 | 一句话本质（TL;DR） | 路径 | 更新日期 |
|---|---|---|---|---|

## 🎬 叙事结构与制作技巧卡（`craft/`）
| 门类 | 标题 | 一句话公式（TL;DR） | 路径 | 更新日期 |
|---|---|---|---|---|

## 📦 原始好素材存档（`raw/`）
| 来源类型 | 标题 | 衍生卡片 | 路径 | 归档日期 |
|---|---|---|---|---|
```
