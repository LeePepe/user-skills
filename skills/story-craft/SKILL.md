---
name: story-craft
description: 从已验收的项目与工程证据抽取四元素叙事素材（含跨 Handoff Hybrid RAG 叙事链检索），完成脱敏、Story Series/Tech Deep-Dive Blog/Deck 和发布读回。Not for unverified raw telemetry, secret-bearing dumps, or direct code implementation.
---
# Story Craft

`story-craft` 是 workflow-hub 的叙事产出节点（Learning & Narrative）。它只消费已确认的 §1/§2 证据，
不把内部事实、秘密或未经验证的假设直接发布。

## Cross-Handoff Hybrid RAG (`story_rag.py`)

通过 S2 相对软链 `scripts/skill_runtime` 复用共享 `skill_runtime.hybrid_rag`（`load_handoff_corpus` + `hybrid_search`，`corpus_scope="retro-story"`），检索历史 `~/Development/handoff/*/{spec,ledger}.md` 与 `receipts/*.md`，自动组装脱敏后的四元素叙事链（`Provenance -> Pivot -> Delta -> Takeaway`，输出严格 `<=12KB`）：

```bash
python3 skills/story-craft/scripts/story_rag.py --topic "<query>" --top-k 5 --json
python3 skills/story-craft/scripts/story_rag.py search --query "<query>" --use-jev --handoff-root ~/Development/handoff --json
```

## 4-Element Extraction Sheet

每份素材单至少包含：

1. **Provenance**：Source Goal/Task、Evidence window、accurate head/release、
   internal evidence pointers、audience and candidate format。
2. **The Hook Scene**：具体日期/时间窗、可见失败现场和一个可复现细节。
3. **Quantitative Delta**：Before/After、baseline/target、单位、样本、环境和
   证据指针。
4. **The Counter-Intuitive Pivot**：初始判断 A、挑战 A 的证据、机制 B 和被排除
   的 H1–H5 假设。
5. **The Takeaway Mechanism**：架构图、最小规则/代码片段、适用边界和下一 Task
   或物理锁。
6. **Desensitization**：身份、项目、路径、ID、密钥、用户数据的替换映射和发布
   审查者。

## Desensitization pipeline

```text
内部证据与 Extraction Sheet
  -> 内部初稿与量化表
  -> 身份/项目/路径/密钥/用户数据映射
  -> 双人内容与隐私检查
  -> 构建、静态资源哈希与发布读回
```

API key、token、cookie、客户数据和未批准的内部标识永不进入公开稿。任何数字、
时间精度或术语泛化都要在内部稿记录理由，不能制造错误因果。

## 3 Narrative Templates

- **Story Series**：具体异常切入 → 直觉碰壁 → 日志/回执破案 → 极简机制 → 量化
  读回与限制。
- **Tech Deep-Dive Blog**：线上指标 → H1–H5 时间线 → 原理与根因 → RED/GREEN
  修复与压测 → 三锁、6DQ 和下一次失败入口。
- **Deck**：痛点矩阵 → 架构演进代际图 → 关键取舍 → 实测收益与适用边界。

每篇只保留一个核心母题，公开稿删除内部身份但保留可验证机制。发布完成还要
读回构建产物、摘要、版本和页面/平台状态，并将读者反馈回流 `linear-board`。

提示槽配置：[prompt-slots.json](references/prompt-slots.json)；回归抽屉：[eval-cases.json](references/eval-cases.json)（Script-Only Regression Drawer，仅经 `skill_contract.py eval` 运行）。

## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| cross-handoff narrative retrieval | `references/prompt-slots.json` | `skills/story-craft/scripts/story_rag.py --topic "<query>" --json` |
| primary task | `references/prompt-slots.json` | `skill_contract.py eval --skill story-craft --strict` |
| verification | `references/prompt-slots.json` | `skill_contract.py eval --skill story-craft --strict` |
