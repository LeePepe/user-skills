---
name: knowledge-digest
description: 面向日常学习、知识获取与好内容拆解沉淀的通用技能。当用户想快速搞懂一个知识/概念/原理、拆解一段优质科普/技术视频脚本或文章、或说「帮我讲懂 / 了解一下 / 拆解这篇内容 / 沉淀这个知识点 / knowledge digest」时使用。内置双通道自适应信息获取（通道 A 主动求知检索四要素 vs. 通道 B 素材拆解与补全），统一采用「极简 TL;DR + 核心痛点问答（FAQ）+ 立即可用清单 + 可选叙事制作技巧」展示框架，并将卡片与索引归档到 ~/Development/Personal/knowledge-vault/。
user-invocable: true
---

# Knowledge Digest（双通道求知、痛点直答与个人知识库沉淀）

把「搞懂一个新概念」和「拆解一段好内容」统一成一条高信噪比流水线：**先精准获取信息 → 用反常识尺度 + 生活教具直击痛点 → 分类沉淀到个人知识库**，拒绝百科式概念堆砌。

---

## 1. 双通道自适应信息获取（`acquisition_mode = auto_dual_channel`，最高优先级）

收到用户输入后，先判断属于哪条通道，按对应策略获取高密度原料：

| 通道 | 触发信号 | 核心信息获取动作 | 串联与升级规则 |
|---|---|---|---|
| **通道 A · 主动求知**<br>（`Topic / Question -> Acquire`） | 用户直接提问一个想了解的知识、概念或现象（如「为什么微波炉一开 WiFi 就卡？」「什么是零知识证明？」「热泵烘干机原理是什么？」） | 发起快速多角度检索（Web / 权威科普 / 工程实践），强制抓取**四要素**：<br>1. **第一性原理**：最底层的物理/数学/机制本质是什么？<br>2. **量级数字对比**：相差多少倍？多快？多小？（找 1–3 个可感知的具象数字）<br>3. **高频生活/工程痛点与误区**：日常最容易踩什么坑？最常见的反直觉现象是什么？<br>4. **实操建议**：普通人或工程师立刻能做的 1–3 个动作是什么？ | 若主题属于**高争议、多学派分歧、或需要严肃多源交叉证伪**的深水区议题，按需串联 [`deep-research`](../deep-research/SKILL.md) 执行多渠道 fan-out 与证伪核查，再将结论浓缩回本技能的痛点问答模板。 |
| **通道 B · 素材拆解**<br>（`Content / URL / Script -> Digest`） | 用户贴入视频总结、字幕、脚本、文章正文或链接，希望提炼知识或学习其讲法 | 1. 若输入为视频链接且缺正文，可配合 `youtube-content` 或 `web_fetch` 提取字幕/全文；<br>2. 抽取**核心知识卡**（概念 + 数字 + 痛点 + 行动建议）与**叙事/制作技巧卡**（脚本结构 + 视觉化/教具手法）；<br>3. **轻量背景补全**：对原文一笔带过但影响理解的关键机制或数字（如频段、量级、边界条件）做 1 次快速检索补全。 | 若原文含未证实的夸张断言，快速核实一手常数/标准并在卡片中标注准确量级。 |

### 信息获取铁律（Anti-Encyclopedia Rules）

1. **禁止定义背诵开场**：绝不用“XX（英文：YY）是指一种……”开头，直接给反常识结论 + 尺度对比。
2. **一数字配一画面**：拒绝连续堆砌 5 个以上干瘪参数；每个核心数字必须绑定一个肉眼可见或生活可感的参照物。
3. **生活物品当教具**：遇到抽象机制（电磁衰减、缓存淘汰、阻抗匹配等），必须映射为具体生活物件（如：冰箱=金属反射、鱼缸=水体吸收、砖墙=衰减物）。

---

## 2. 极简展示与讲解框架（`display_template = minimal_tldr_qa`）

向用户输出讲解时，默认遵循以下 **3+1 极简结构**（标准 Markdown 模板见 [templates.md](references/templates.md)）：

### ① ⚡ 一句话本质（TL;DR）
- **公式**：`反常识底层结论` + `1 个直观尺度/时间/倍率数字对比`。
- *示例*：「WiFi 其实就是一种肉眼看不见的『慢速低能光』——把可见光波长放大 **25 万倍**，穿过整个客厅只需 **30 多纳秒**。」

### ② 🎯 核心痛点直答（Pain-Point Q&A，3–5 问）
- 围绕该主题最抓人的 **3–5 个真实痛点/反直觉现象**展开问答。
- 每问遵循 **`现象痛点 → 生活教具类比 + 单个关键数字 → 一句话破案`**：
  - *问 1（现象破案）*：为什么手机只挪了几厘米，视频就突然卡住？
  - *问 2（冲突干扰）*：为什么厨房微波炉一开，客厅就断网？
  - *问 3（常见焦虑）*：路由器辐射全屋，到底伤不伤身体 / 破坏 DNA？
  - *问 4（选型纠结）*：5G 频段和 2.4G 频段，穿墙差在哪？

### ③ ✅ 立即可用清单（Actionable Checklist，≤3 条）
- 给出看完马上能照着做的动作建议（每条 `动作 + 一句话原因`，不超过 3 条）。

### ④ 🎬 脚本结构与制作技巧拆解（条件触发）
- **何时输出**：当输入来自**通道 B（视频脚本/文章素材）**，或用户明确要求「拆解讲法 / 怎么做科普 / 怎么写脚本」时附带。
- 包含两部分：
  1. **完整脚本叙事流**：`开场钩子` → `建立概念` → `速度/尺度感` → `可视化演示` → `日常现象破案` → `安全/误区回应` → `对比选型` → `行动建议` → `结尾拔高`。
  2. **可复用表达与视觉化技巧**：如「把不可见变可见」「用生活物品当教具」「夸张时间/空间尺度」「单数字配单画面」。

---

## 3. 个人知识库分类与沉淀（`storage_location = personal_knowledge_vault`）

所有沉淀内容统一归档至个人知识库：

```text
~/Development/Personal/knowledge-vault/
├── index.md              # 总目录索引表（门类 | 标题 | 一句话本质 | 路径 | 更新日期）
├── concepts/             # 原理与概念痛点卡（TL;DR + 痛点 Q&A + 立即可用清单）
├── craft/                # 内容拆解、脚本结构与讲法技巧库（叙事流 + 视觉化/教具手法）
└── raw/                  # 原始好素材与脚本拆解存档（保留原始摘录与溯源信息）
```

### 门类划分（Category Taxonomy）

在 `index.md` 与卡片 frontmatter 中使用清晰的一级门类标识：
- `物理与硬件`（电磁波、声学、芯片、传感器、光学等）
- `计算机与网络`（协议、系统架构、AI/LLM、算法、安全等）
- `工程与设计`（架构模式、交互机制、数据工程、音视频等）
- `科学健康与生活`（生理机制、家电原理、日常理化现象等）
- `叙事与表达技法`（科普脚本结构、视觉化拆解、隐喻教具库等，对应 `craft/`）

### 沉淀工作流（Ingest & Dedupe Steps）

1. **先读索引去重**：读取 `~/Development/Personal/knowledge-vault/index.md` 与权威 `topic-ledger.json`。`discover-topics --propose-aidash --no-materialize` 只生成 `learning-topic://<topic-key>` 待拍板候选（每日最多 1 条、pending 最多 3 条），不调用网络/LLM 重排，不写 `concepts/` 或 `index.md`。若同一具体问题已有覆盖，**增量更新（append/refine）** 现有卡片，不重复建题。
2. **按层落盘文件与 0-Token 归档**（文件名采用 kebab-case，骨架见 [templates.md](references/templates.md)，完整 L2 抽屉见 [card-templates.json](references/card-templates.json)）：
   - **纯概念学习（通道 A）**：写入 `concepts/<topic-slug>.md`；
   - **好内容/脚本拆解（通道 B）**：原始素材存 `raw/<source-slug>.md`，概念卡存 `concepts/<concept-slug>.md`，制作技巧存 `craft/<craft-slug>.md`；
   - **一键归档与索引同步**：调用 `python3 skills/knowledge-digest/scripts/digest_vault.py archive --slug <slug> --category <cat> --title "..." --file <md>` 自动归档并幂等更新 `index.md`。
3. **审批与发布分离**：`learning-topic://<topic-key>` 的 Approve 只授权后台探索；探索完成后仍需显式归档/发布授权。Star 不会自动写卡、发 Blog 或同步 `design-studio`。已存在且明确授权的 `knowledge://<slug>` learned card 仍可按既有发布流程调用 `publish-blog`。
4. **工程与设计双入口联动钩子（Entry A1 -> Tier 1..3 Promotion Hook）**：当沉淀门类为 `工程与设计` 且涉及 **UI/UX、SwiftUI/AppKit/UIKit、Liquid Glass、Metal Shader、动效状态机或 Apple HIG**（如 WWDC 设计/UI 课程拆解）时，归档完个人知识库卡片后，**必须继续读取 [`../my-designer/references/design-flow-architecture.md`](../my-designer/references/design-flow-architecture.md) 的「Change Impact Matrix」**，按双入口防爆架构将可复用原语、原子灵感卡、紧凑规则与 0-Token 静态红线同步提升至 Tier 1–3（`design-studio` 与 `my-designer`），并运行 `python3 skills/my-designer/scripts/verify_design_cases.py flow-map` 校验预算健康度。
5. **简短回报**：向用户展示讲解内容，并在末尾用 1–2 行告知已沉淀的卡片路径（及若触发工程与设计钩子时同步更新的 Tier 1–3 资产 ID）。

---

## References

- [templates.md](references/templates.md) & [card-templates.json](references/card-templates.json) — `concepts/`、`craft/`、`thoughts/`、`raw/` 与 `index.md` 的标准骨架及 L2 模板抽屉。
- [digest_vault.py](scripts/digest_vault.py) — `archive`、`discover-topics`（含 Jev `R09` 重排与 `AIDash` 提案生成）与 `publish-blog`（`DC-006..008` 校验）CLI。
- [../my-designer/references/design-flow-architecture.md](../my-designer/references/design-flow-architecture.md) — `Entry A (Ingest) ⇄ Tier 0..3 ⇄ Entry B (Design)` 双入口知识流转与 4 道防信息爆炸物理阀门真源。
