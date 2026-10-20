# Channel Playbooks

四个调研渠道各自搜什么、怎么搜、怎么避坑。都是反复验证过的通用套路,与具体项目无关。

## Web

- 一个事实至少换 **2 个独立来源**交叉印证再给 HIGH。
- 每个发现附**来源 URL + 发布日期**;区分事实/观点/数据。
- 注意时效:优先近期来源;老数据标注时间点。
- 付费墙/地区限制:标注"无法完整访问",不要当作发现失败。

## GitHub Landscape 搜索(关键 — 覆盖度比深度更重要)

"某领域有哪些项目 / 有什么类似 X 的工具"这类问题是 **landscape survey**,最容易漏掉大项目。子 agent 常搜自然语言短语,结果找到 5 star 小库却漏掉 10 万 star 的头部项目。

**强制套路:**

1. **先按 star 排序**:`stars:>100 sort:stars`,或 API `sort=stars&order=desc`。先把最大的项目捞出来。
   ```bash
   curl -s "https://api.github.com/search/repositories?q=QUERY+stars:>100&sort=stars&per_page=20" \
     | grep -E '"full_name"|"stargazers_count"|"html_url"'
   ```
2. **搜多个关键词变体**:单一词必漏。如"docs-driven development"要同时搜"spec-driven development"、"documentation-first"、"DESIGN.md"、"README-driven"、"AGENTS.md"。变体要**显式列进子任务 context**,别指望子 agent 自己想到。
3. **查特定 org**:大厂 repo 名很短、关键词搜不到(如 `github/spec-kit` 不含 "docs-driven")。补 `org:github`、`org:google` 等定向搜索。
4. **API 优于浏览器搜索**:GitHub Search API 从终端拿排序完整列表,比浏览器搜索可靠;注意 rate limit,别短时间大量请求。

**曾经的失败**:4 个子 agent 集体漏掉多个 5 万-11 万 star 的头部项目,因为都把领域名当短语搜、没按 star 排序。教训:landscape 必须"结构化搜索 + star 排序 + 关键词变体"。

## News / 市场 / 融资

- 关注近 6-12 个月的报道、行业报告、融资动态、公司公告。
- 中英文来源都覆盖。
- 每条附来源媒体 + 发布日期。

**Wikipedia curl 兜底(搜索被拦时的主力):**

当所有搜索引擎(Google/Bing/DuckDuckGo)都 bot 拦截时——**不要反复换引擎重试**,直接切 Wikipedia:

```bash
curl -sL "https://en.wikipedia.org/wiki/<Topic>" \
  | sed -n 's/<[^>]*>//gp' \
  | grep -iE "2025|2026|funding|revenue|billion|million|<keyword>" \
  | head -30
```

为什么有效:
- 大公司/产品的 Wikipedia 条目含大量**有引用**的融资、用户数、估值、时间线数据,引用源就是 TechCrunch/Reuters/Bloomberg/WSJ——等于绕过 bot 检测拿到同样的权威来源。
- `curl -sL` 是普通 HTTP GET,不触发 bot 检测。
- 一个页面常覆盖 12-18 个月的动态,一次 curl 抵多次搜索。

局限:只覆盖有 Wikipedia 条目的主体(大公司/产品);数据可能滞后 1-2 周;冷门主体/突发新闻覆盖不到——那时 curl 具体新闻站首页或 RSS。

## Academic / 论文

- 搜 arXiv、Google Scholar、Semantic Scholar;关注高引用 + 最新发表。
- 每篇附标题、作者、年份、链接;提取核心方法与结论。
- arXiv API 有时不稳,备 Google Scholar 兜底。
- 核查阶段注意:论文是否真实存在、是否被撤稿、方法论是否合理。

## 渠道裁剪(省 token)

- **工具 / 运维 / 产品**类主题:跳过 academic(无有意义论文),GitHub 渠道最关键。
- **稳定成熟**主题:跳过 news(无近期动态)。
- 最少保留 **web + github**(quick 档)。
- landscape 类:产出**分类矩阵**(类别 × 工具:star 数、活跃/归档、融资、与用户现有栈的关系),而非线性发现列表。
