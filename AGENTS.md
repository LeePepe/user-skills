# AGENTS.md — user-skills 目录索引

本仓库是面向各类 CLI 与智能体客户端的通用技能包（skills）集合。开始任务前按目标技能或模块按需下钻阅读对应文件，采用渐进式披露（Progressive Disclosure），不要一次性预读全部技能文档。

## 目录结构与渐进式披露层次

| 路径 | 层次与职责 | 读取时机 |
|---|---|---|
| `README.md` | 仓库概览、技能拓扑、共享组件与安装说明 | 了解仓库全貌或增删技能入口时 |
| `.claude-plugin/marketplace.json` | 仓库级插件注册表 | 新增、重命名或归档技能时 |
| `skills/<skill>/SKILL.md` | **L0 入口文档**：技能核心心智模型、触发边界与路由索引 | 修改或调用对应技能时首先读取 |
| `skills/<skill>/references/` | **L1/L2 按需知识抽屉**：细分协议、模板、案例卡与配置表 | 仅在 `SKILL.md` 路由命中对应主题时下钻读取 |
| `skills/<skill>/scripts/` | **确定性脚本与 CLI**：零 Token 检查器、同步器、目录与状态工具 | 修改脚本逻辑、参数契约或单测时读取 |
| `skills/<skill>/.claude-plugin/plugin.json` | 单技能插件元数据（`name`、`description`、`category`） | 修改技能元数据或分类时读取 |
| `scripts/` | 仓库级全量验证门禁（`./scripts/verify`）与回归测试套件（`test_*.py`） | 运行验证或增补回归测试时读取 |

## 核心技能速览

| 类别 | 技能目录 | 内容职责 |
|---|---|---|
| `workflow` | `workflow-hub`、`linear-board`、`m2-dispatcher`、`team-lead`、`retro-loop`、`story-craft` | 开放能力图谱、Linear 看板同步、M2↔M1 生命周期编排、Goal→Task 执行、双路复盘与叙事产出 |
| `design` | `my-designer`、`design-studio` | 双入口设计流（产品设计/现代化改版/视觉回溯 vs. 灵感摄取/色板测量/视频流水线） |
| `repo` | `code-review`、`xcode-cli`、`repo-kit`、`codebase-design` | 双轴代码审查、Apple 构建测试路由、仓库合同初始化、深模块设计与领域建模（含 `CONTEXT.md` 与 ADR） |
| `research` | `deep-research`、`knowledge-digest`、`layered-data-warehouse` | 多源交叉核查调研、知识拆解沉淀与分层数仓蓝图设计 |
| `meta` | `skill-contract`、`save-tool` | 渐进式披露技能包四阀门审计与外部工具归档 |

## 验证门禁

本地开发或提交前可启用仓库 Git hooks 并运行唯一验证入口：

```bash
git config core.hooksPath .githooks
./scripts/verify
```

技能包结构与四道防爆阀门审计命令：

```bash
python3 skills/skill-contract/scripts/skill_contract.py audit --strict
```
