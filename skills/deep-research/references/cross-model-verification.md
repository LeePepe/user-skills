# Cross-Model Verification

让**另一个模型**当独立核查者——发现者与核查者不同模型时,能抓到同模型自查抓不到的错(同模型倾向于犯同类错、认同自己的输出)。这是本 skill 的对抗性核心。

三个 CLI 都能非交互调用,互为核查者:

| 模型 | 非交互调用 | 备注 |
|------|-----------|------|
| Codex | `codex exec --sandbox read-only --skip-git-repo-check "<prompt>"` | 不在 git 目录需 `--skip-git-repo-check`;prompt 也可从 stdin 读 |
| Kimi | `kimi -p "<prompt>"` | `--auto` 不能和 `-p` 同用;`--output-format text|stream-json` |
| Claude | `claude -p "<prompt>"` | Claude Code 非交互模式 |

`--sandbox read-only`(codex)保证核查者只读不改,契合"核查"语义。

**核查者只读语义(各端不同,务必注意):**
- **codex**:`--sandbox read-only` 强制只读,最干净。
- **kimi**:`kimi -p` 默认可写、可跑 Bash,**没有强制只读开关**——核查任务纯读推理,prompt 里明确"只做分析和证伪,不要修改任何文件",并在无关目录(如 `/tmp`)下调用,降低误改风险。
- **claude**:`claude -p` 非交互;核查场景不要授予写权限。

核查者的产物只有一个:`verdict_*.md`(由主 agent 用重定向落盘),核查者本身不应动仓库文件。

## 标准跨模型核查流程

1. 主 agent 把待核查发现落盘成 `finding.md`(schema 见 verification-protocol.md)。
2. 挑一个**与发现者不同**的模型当核查者,读文件、证伪、输出 verdict。
3. 收集 verdict,进圆桌。

命令模式(核查者读 finding、输出 verdict):

```bash
# 核查者 = codex
codex exec --sandbox read-only --skip-git-repo-check \
  "尝试证伪以下研究发现:找反证、核对来源、检查数字自洽与时效。
   按 verdict schema 输出(verdict: CONFIRMED/REFUTED/UNCERTAIN + 反证 + 来源核对 + 修正)。
   发现内容:
   $(cat finding.md)" > verdict_codex.md

# 核查者 = kimi
kimi -p "尝试证伪以下研究发现:找反证、核对来源、检查数字自洽与时效。
   按 verdict schema 输出(verdict: CONFIRMED/REFUTED/UNCERTAIN + 反证 + 来源核对 + 修正)。
   发现内容:
   $(cat finding.md)" > verdict_kimi.md

# 核查者 = claude(在 kimi/codex 端时反过来调它)
claude -p "尝试证伪以下研究发现……(同上)
   $(cat finding.md)" > verdict_claude.md
```

## 双核查者(deep 档,更强对抗)

同一个 finding 交给**两个**不同模型核查,两份 verdict 有分歧 → 直接标 `CONTESTED`,不要主观选边:

```bash
codex exec --sandbox read-only --skip-git-repo-check "证伪:$(cat finding.md)" > verdict_codex.md &
kimi -p "证伪:$(cat finding.md)" > verdict_kimi.md &
wait
# 主 agent 读两份 verdict:一致 CONFIRMED→HIGH;任一 REFUTED 且有实证→按反证修正;分歧未解→CONTESTED
```

## 三端如何选核查者(谁在跑,就调别人)

| 当前主 agent | 首选核查者 | 次选 | 机制 |
|-------------|-----------|------|------|
| Claude Code | 另起 `Task` 子 agent(不同实例) | `Bash` 调 `codex exec` / `kimi -p` | 优先 Task 子 agent;跨模型作更强对抗时用 Bash 调 CLI |
| Kimi | `codex exec`(不同模型) | `claude -p` | shell 调兄弟 CLI |
| Codex | `kimi -p`(不同模型) | `claude -p` | shell 调兄弟 CLI |

## 注意

- 核查者子进程**无当前对话记忆**——prompt 必须自包含,把 finding 全文喂进去(上面用 `$(cat finding.md)`)。
- 核查者可能没联网/被墙:让它至少做**逻辑自洽 + 数字校验 + 内在矛盾**核查,把"无法联网验证 URL"如实写进 verdict.notes,不要当作 REFUTED。
- 调用失败(CLI 不存在 / 超时 / 报错)→ 降级为单模型自查,并在报告局限性标注"未经跨模型核查"。永远给出可用结果。
- 首次接入某端时先探活:`command -v codex && command -v kimi && command -v claude`。
