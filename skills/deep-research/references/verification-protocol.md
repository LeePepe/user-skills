# Verification Protocol

对抗式核查的数据契约与判据。Phase 1 产出 finding,Phase 2 产出 verdict,Phase 3 按判据分级。

## finding schema(Phase 1 每个发现落盘成一个 finding)

发现者产出。核查者只读它、不读发现者的其余上下文——所以 finding 必须自包含。

```markdown
## Finding: <一句话论断>
- id: <短 slug,如 anthropic-valuation-2026>
- claim: <可证伪的具体陈述,含数字/日期/主体>
- sources:
  - <URL> — <发布日期> — <来源类型: 官方/新闻/论坛/百科/论文>
  - <URL> — ...
- type: 事实 / 数据 / 观点 / 推测
- date: <信息时效,即内容说的是哪个时间点>
- confidence_self: HIGH / MEDIUM / LOW   # 发现者初判,核查前
```

要点:
- **claim 必须可证伪**——"X 很流行"不可证伪;"X 在 2026-06 有 12 万 star"可证伪。
- 单一发现只承载**一个**论断。多个论断拆成多个 finding。
- 观点/推测也要收,但 `type` 如实标,不要伪装成事实。

## verdict schema(Phase 2 核查者读 finding 后产出)

核查者产出。**目标是证伪,不是确认。**

```markdown
## Verdict for <finding id>
- verifier: <谁核查的,如 codex / kimi / claude-subagent-2>
- verdict: CONFIRMED / REFUTED / UNCERTAIN
- disconfirming_evidence: <主动去找的反证;找不到也要写"已尝试证伪,未发现反证">
- source_check: <逐条看 finding 的 sources:可访问吗?内容匹配 claim 吗?有利益关联/偏见吗?过期吗?>
- correction: <若 claim 有误,给出修正后的表述>
- notes: <付费墙/地区限制/无法访问等,标注而非当作失败>
```

## 对抗式证伪 checklist(核查者逐项过)

1. **来源真实性**:URL 真能打开吗?内容真支持 claim 吗(不是标题党)?
2. **交叉印证**:换一个独立来源能否复现同一事实?只有单一来源就不能给 HIGH。
3. **利益关联**:来源是不是被评价对象自己/其竞品/其投资方?有则降权。
4. **时效**:数据是不是过期了?claim 说的时间点和来源发布时间对得上吗?
5. **数字自洽**:比例/总量/单位对得上吗?(如"某 bug 影响 8% 用户"却被解释成"序列化 bug"——序列化应是 100%,比例对不上说明解释错了。)
6. **反证搜索**:主动搜"X 被证伪 / X 争议 / X 撤稿 / X 下降",而不是只搜支持性证据。
7. **观点 vs 事实**:把被包装成事实的观点/推测拎出来。

## 置信度分级判据(Phase 3/4)

| 级别 | 判据 |
|------|------|
| `HIGH` | ≥2 个**独立**来源印证,且核查 verdict = CONFIRMED,无未解反证 |
| `MEDIUM` | 单一较可信来源,或多源但均较弱;verdict 非 REFUTED |
| `LOW` | 单一弱来源 / 仅领域常识无 URL / verdict = UNCERTAIN |
| `CONTESTED` | 跨渠道矛盾,或核查者之间分歧未解;**必须在报告里如实标注争议双方** |

"独立"= 不是互相转载、不同利益方。三家媒体转同一通稿算**一个**来源。

**硬规则:未经 Phase 2 核查的发现一律封顶 `MEDIUM`。** 没有 verdict 就没有 CONFIRMED,不能给 HIGH——哪怕它看起来有多个来源。跳过核查(见降级规则)是省时间的合理选择,但代价就是这些发现最高只能 MEDIUM,并在报告局限性里注明"未经独立核查"。

## subagent / 子 CLI 输出纪律(硬约束)

派生出去的调研/核查子任务:

- **只把完整结果作为响应正文返回,不要写磁盘文件。** 文件名、路径、交叉引用由主 agent 统一决定;子任务各自落盘会散成一堆不一致文件。
- 子任务无当前对话记忆,context 必须自包含。
- 正文可以长(上万字),没有硬长度限制。

给子任务 prompt 里附上这段:

```
输出规范:完整结果作为响应正文返回,不要写入磁盘文件;父 agent 统一收集落盘。
不要调用 write_file / heredoc / tee 等落盘命令。内容可长,正文无硬性长度上限。
```
