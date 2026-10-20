---
name: deep-research
description: 对任意主题做深度、多来源、经交叉核查的调研,产出带引用、按置信度分级的报告。五阶段流水线:把问题分解成 4-8 个子问题 → 跨渠道(web/github/news/academic)并行 fan-out 调研 → 对每个发现做对抗式核查(证伪而非确认,发现者≠核查者)→ 交叉验证+圆桌综合、标注分歧 → 输出 HIGH/MEDIUM/LOW/CONTESTED 分级的引用报告。Provider-agnostic:同一份 skill 在 Claude Code、Kimi(kimi -p)、Codex(codex exec)三端都成立;fan-out 与核查阶段可跨模型互查(claude 的发现让 codex/kimi 证伪,反之亦然),对方模型作独立第二视角。当用户想「深度调研 / deep research / 全面调研 / 多角度调研 / research roundtable / 帮我把某主题查透并给带引用的报告」时使用。调用前:若问题欠具体(如「买什么车」却无预算/用途/地区),先问 2-3 个澄清问题收窄范围,再把澄清结果织进 args。
user-invocable: true
---

# Deep Research

一条 **provider-agnostic** 的深度调研流水线:把一个问题查透,产出带引用、按置信度分级、经过对抗式核查的报告。

设计目标:同一份 SKILL.md 在三种 agent CLI 下都能驱动——
- **Claude Code**(有 `Task` 子 agent、`Workflow` 编排、`WebSearch`/`WebFetch`)
- **Kimi**(`kimi -p "..."` 非交互;`kimi --auto` 全自动会话)
- **Codex**(`codex exec "..."` 非交互)

核心纪律贯穿始终:**发现者 ≠ 核查者**,**核查以证伪为目标**,**每个论断都要有可回溯的来源**。

---

## 调用前:够不够具体?

调研跑起来很贵(多轮搜索 + 多模型核查)。开跑前先判断问题是否可直接研究:

- **够具体** → 直接进 Phase 0。
- **欠具体**(缺预算/地区/用途/时间范围/对比基准等关键约束)→ 先问 **2-3 个**澄清问题,把答案织进研究问题再开跑。别用宽泛问题烧一整轮 fan-out。

例:「买什么车」→ 先问预算区间、主要用途(通勤/家庭/越野)、地区(影响可选车型与补贴)。

---

## Pipeline 总览

```
用户问题(已澄清)
   │
   ▼
[Phase 0] 分解:生成 4-8 个聚焦子问题,各标注适合渠道
   │
   ▼
[Phase 1] fan-out 调研  ← 能力抽象:并行派生      ┐
   │   web / github / news / academic 四渠道并行   │ 见「能力映射表」
   │   每个发现附来源 URL + 日期 + 类型 + 初判置信度 │
   ▼                                               │
[Phase 2] 对抗式核查  ← 能力抽象:独立 reviewer + 跨 CLI 调用
   │   每个发现落盘成 finding → 独立核查者尝试证伪   │
   │   核查者≠发现者;可跨模型(codex/kimi 互查)   ┘
   ▼
[Phase 3] 交叉验证 + 圆桌:找跨渠道一致/矛盾,仲裁分歧
   │
   ▼
[Phase 4] 报告:按置信度分级(HIGH/MEDIUM/LOW/CONTESTED),带完整引用
```

阶段可自适应裁剪,见文末「降级与裁剪规则」。

---

## Phase 0 — 分解

主 agent 自己做,不派生。

1. 明确调研范围与深度(quick / standard / deep)。
2. 生成聚焦子问题(**数量随档位**:quick 2-3 个,standard 4-6 个,deep 6-8 个),覆盖:背景/现状、技术细节、对比/替代方案、趋势/前景。
3. 给每个子问题标注适合的渠道:`web` / `github` / `news` / `academic`。
4. 确定报告语言(跟随用户偏好)。

分解 prompt 模板:

```
分析以下问题,生成 4-8 个聚焦、彼此独立可查的子问题。
每个子问题标注最适合的调研渠道(web/github/news/academic),
覆盖:背景现状、技术细节、对比替代、趋势前景。
问题:{research_question}
```

---

## Phase 1 — fan-out 调研

**这是能力抽象点①:并行派生。** 各端如何并行见「能力映射表」。逻辑一致:每个渠道一个调研任务,尽量并行。

- 渠道搜索手册(每个渠道搜什么、怎么搜、避坑):见 `references/channel-playbooks.md`。GitHub landscape 用结构化搜索(`stars:>100 sort:stars` + 关键词变体),搜索引擎被 bot 拦时切 Wikipedia curl 兜底——这两条是反复验证的高价值套路,务必读。
- 每个发现的记录格式(claim / 来源 / 日期 / 类型 / 初判置信度):见 `references/verification-protocol.md` 的 finding schema。
- **派生出去的子任务必须自包含**(子 agent / 子 CLI 无当前对话记忆),且**只返回正文、不写文件**——文件布局由主 agent 统一决定。

---

## Phase 2 — 对抗式核查

**这是能力抽象点②(独立 reviewer)+ ③(跨 CLI 调用)。**

原则:**核查者的任务是证伪,不是确认。** 且核查者应与发现者**不同**——不同子 agent,理想情况下**不同模型**。

流程:
1. 把每个待核查发现落盘成 `finding.md`(claim + sources + 初判 confidence)。
2. 指派**独立核查者**读该文件,尝试证伪,输出 `verdict.md`(`CONFIRMED` / `REFUTED` / `UNCERTAIN` + 反证 + 修正)。
3. 主 agent 汇总所有 verdict 进 Phase 3。

**跨模型核查**(用户要点):让另一个模型当独立第二视角。具体命令模式与三端映射见 `references/cross-model-verification.md`。核查提问清单(证伪 checklist)与 verdict schema 见 `references/verification-protocol.md`。

**何时可跳过 Phase 2:** 来源以官方文档 / GitHub 一手页 / arXiv 原文为主时,数据已一手验证,核查边际价值低,可直接进 Phase 3(见降级规则)。

---

## Phase 3 — 交叉验证 + 圆桌

所有核查完成后,主 agent 综合:

1. **交叉验证**
   - 跨渠道一致 → 升 `HIGH`
   - 跨渠道矛盾、或核查者间分歧未解 → `CONTESTED`
   - 仅单一来源 → `MEDIUM` / `LOW`
2. **圆桌综合**:从不同视角审视**已有的 finding/verdict**(web 视角关注应用/市场、技术视角关注实现可行性、新闻视角关注趋势/商业、学术视角关注理论/实验)。视角差异用来**暴露被忽略的角度**,但结论必须落在证据上——**不要为了"显得有分歧"而臆造观点**,没有实质分歧就直说一致。
3. 输出:一致结论、争议点及原因、最可信综合结论、信息缺口。

---

## Phase 4 — 报告

主 agent 整合成最终报告。模板(执行摘要 / 分级发现 / 圆桌纪要 / 置信度表 / 引用来源 / 局限性)见 `references/report-template.md`。

置信度判据:`HIGH` = ≥2 个独立来源印证**且经核查 verdict=CONFIRMED**;`CONTESTED` = 核查者间或跨渠道分歧未解;`MEDIUM/LOW` = 单源或弱证据。**跳过 Phase 2 核查的发现一律封顶 `MEDIUM`**——没有 verdict 就不能给 HIGH。

---

## 能力映射表(唯一需按端适配的地方)

三处机制各端不同,其余逻辑全通用。执行时按当前所在端选对应实现:

| 能力 | Claude Code | Kimi | Codex | 都不可用时(降级) |
|------|-------------|------|-------|-------------------|
| **①并行 fan-out** | `Task` 起多个子 agent,或 `Workflow` 编排 `parallel()`/`pipeline()` | 单会话内顺序做各渠道;或 shell 起多个 `kimi -p` 后台进程 | 单会话内顺序;或 shell 起多个 `codex exec` 后台进程 | 主 agent 单会话**串行**跑各渠道 |
| **②独立 reviewer** | 另起一个 `Task` 子 agent 做核查(与发现者不同实例) | shell 调**另一个模型** CLI(codex/claude)读 finding 证伪 | shell 调**另一个模型** CLI(kimi/claude)读 finding 证伪 | 主 agent 换视角自查并**标注为"未经独立核查"** |
| **③跨 CLI 调用** | `Bash` 调 `codex exec` / `kimi -p` | `Bash`/shell 调 `codex exec` / `claude -p` | shell 调 `kimi -p` / `claude -p` | 跳过跨模型,单模型核查并标注降级 |

> 判断当前所在端:看可用工具。有 `Task`/`Workflow` 工具 → Claude Code;否则看进程/环境。不确定时按"降级"列执行,永远给出可用结果,只是把降级如实标注在报告局限性里。

---

## 降级与裁剪规则

- **深度档位**:`quick`(2-3 子问题,2 渠道 web+github,跳过 Phase 2)/ `standard`(4-6 子问题,四渠道,核查)/ `deep`(6-8 子问题 + 递归深挖 + 补充搜索)。默认 `standard`;用户说"快速"用 quick,"深度"用 deep。
- **渠道裁剪**:工具/运维类主题跳过 academic(无有意义论文);稳定成熟主题跳过 news;最少保留 web+github。
- **Phase 2 跳过**:来源以一手权威页为主时跳过核查,直接圆桌。
- **无子 agent / 无跨模型 CLI**:按能力映射表"降级"列串行执行,并在报告「局限性」里注明哪些发现未经独立/跨模型核查。
- **搜索被拦**:所有搜索引擎(Google/Bing/DuckDuckGo)可能同时 bot 拦截——不要反复换引擎重试,直接切 Wikipedia curl 兜底(见 channel-playbooks)。

---

## References

- `references/channel-playbooks.md` — 四渠道搜索手册:GitHub 结构化 landscape 搜索、Wikipedia curl 兜底、来源质量与时效规则。
- `references/verification-protocol.md` — finding / verdict 的 markdown schema、对抗式证伪 checklist、置信度分级判据、subagent 输出纪律(只返正文不写文件)。
- `references/cross-model-verification.md` — claude/kimi/codex 互为独立核查者的命令模式与三端映射,含可直接抄的调用片段。
- `references/report-template.md` — 最终报告模板(执行摘要、分级发现、圆桌纪要、置信度表、引用来源、局限性)。
