# Playbook Leaf · §3.1–3.3 4-Element Extraction Sheet, Narrative Templates & Desensitization

本文件为 [`playbook.md`](./playbook.md) §3.1–3.3 的按需展开切片（`L1` leaf `<= 145` 行），定义四元素叙事抽取单、三大体裁模板与脱敏发布流水线。详见 [`story-craft`](../../story-craft/SKILL.md)。

## 3.1 四元素《Story & Tech Extraction Sheet》

每次从项目攻关或工程演进中抽取一张内部素材单，发布稿只消费脱敏副本：

```markdown
# Story & Tech Extraction Sheet
## 0. Provenance
- Source Goal / Task, Evidence window, Accurate head / release, Internal pointers, Audience & format:
## 1. The Hook Scene (具象时间锚点与切入现场，可复现细节，禁空泛口号)
## 2. Quantitative Delta (硬核量化前后对比)
| Metric | Before | After | Baseline / target | Evidence |
|---|---:|---:|---:|---|
| PLT / Crash-Free / coverage / rounds / LOC | | | | |
## 3. The Counter-Intuitive Pivot (原本以为 A → 证据排除 A → 真凶/机制 B + H1–H5 排除项)
## 4. The Takeaway Mechanism (架构图 / 最小代码配置 / 极简规则 + 泛化边界 + 下一步 Task/锁)
## 5. Desensitization (替换内部标识、保留公开术语、私有证据隔离、双人审查记录)
```

## 3.2 三种 Narrative Templates

| 模板 | 适用场景与结构 | 核心验收标准 |
|---|---|---|
| **模板一：Story Series**<br>（工程故事集连载） | 1. 具体时刻异常切入 → 2. 旧直觉碰壁 → 3. 翻日志/回执破案 → 4. 换上极简新机制 → 5. 真实跑通与读回余味 | 一篇一现场一机制；共享术语与脱敏映射；未知项如实保留；篇末附可复制检查表或最小实验 |
| **模板二：Tech Deep-Dive Blog**<br>（技术深潜长文） | 1. 线上现象与指标水位 → 2. H1–H5 时间线与假象排除 → 3. 底层原理与根因解剖 → 4. RED→GREEN 与压测/遥测读回 → 5. 三锁防回归护栏 | 每个结论有内部证据指针；数字含单位/分位数/样本量/环境；仅保留理解机制的最小代码片段；可按文复现诊断 |
| **模板三：Sharing / Deck**<br>（体系化专题分享） | 1. 痛点矩阵（用户/交付/质量/维护）→ 2. 架构演进代际图 → 3. 关键设计取舍（含放弃选项）→ 4. 实测收益与边界 | 每页一结论一可读证据；架构图箭头标明输入/产出/回流；区分相关性与因果性；讲者备注保留证据入口 |

## 3.3 去口号化、脱敏硬红线与发布流水线

### 叙事打磨五原则

1. **具象代替抽象**（用命令、曲线、回执替代“高效/智能”）；
2. **展示过程而非自夸结论**（写清假设、失败与排除）；
3. **保留真实曲折与限制**（说明未解决边界）；
4. **克制收尾**（以读回事实或下一步实验收尾）；
5. **一文一核心母题**（其余发现入选题池）。

### 隐私脱敏硬红线

| 信息类别 | 内部稿可保留 | 公开稿处理 |
|---|---|---|
| 个人姓名、账号、机器名 | 受控证据索引 | 映射为“作者 / 协调者 / Reviewer / 执行环境” |
| 公司、组织、产品与项目代号 | 发布审批范围内 | 批准的泛化名称或公开名称 |
| 私有平台、内部域名、绝对路径、内部 ID | 用于事实定位 | 删除、泛化或替换为公开等价接口 |
| API Key、令牌、Cookie、密钥、客户数据 | 严禁入正文 | **永不发布；发现即阻塞并轮换** |
| 公开技术栈、指标与日志结构 | 可保留 | 保留公开语义与必要数字，去除用户标识与敏感时间精度 |

### Desensitization & Publish Pipeline Checklist

`Extraction Sheet → 选定母题与模板 → 内部初稿 → 脱敏映射 → 双人隐私检查 → Markdown 构建 (ai-workflow/lab/_build.py) → Archify 配图与静态哈希校验 → 线上页面/版本/摘要读回 → 回流选题池与 Task 规则`

- [ ] 单一核心母题，Hook / Quantitative Delta / Pivot / Takeaway 四项齐全且可回指 R10/Git/CI/Telemetry。
- [ ] 已彻底移除个人、账号、机器名、内部域名、绝对路径、密钥与客户数据。
- [ ] `ai-workflow/lab/_build.py` 构建通过，线上页面、静态资源哈希、版本与摘要已读回记录。
