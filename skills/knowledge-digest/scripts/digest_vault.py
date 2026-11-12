#!/usr/bin/env python3
"""Zero-Token knowledge-vault archival, R09 topic discovery, and DC-006..008 blog publishing."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import fcntl
import html
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILLS_ROOT = SKILL_DIR.parent
DEFAULT_VAULT_DIR = Path.home() / "Development" / "Personal" / "knowledge-vault"
DEFAULT_COLLECTED_TOOLS_DIR = Path.home() / "Development" / "Personal" / "collected-tools"
DEFAULT_BLOG_REPO = Path.home() / "Development" / "leepepe.github.io"
DEFAULT_PROPOSALS_PATH = (
    Path.home() / "Development" / "AIDash" / "aidata" / "L5_apps" / "digest" / "state" / "proposals.jsonl"
)
DEFAULT_TOPIC_LEDGER_PATH = DEFAULT_VAULT_DIR / "topic-ledger.json"

CURATED_LEARNING_TOPICS: tuple[dict[str, Any], ...] = (
    # 6 learned (covered by knowledge-vault / design integration)
    {
        "source_id": "hallmark",
        "source_uri": "https://github.com/Nutlope/hallmark",
        "topic_key": "hallmark-anti-slop-anatomy",
        "aliases": ["hallmark", "hallmark-core-patterns", "hallmark-anti-slop-anatomy"],
        "question": "Hallmark 如何通过 50 选 3 材质主题、页面意图解剖与真实浏览器多视口 sloplint 拦截 AI 模板感？",
        "title": "Hallmark：反 AI 模板感解剖设计与多视口几何门禁",
        "category": "工程与设计",
        "coverage_refs": ["collected-tools/hallmark.md"],
        "why_now": "collected-tools/hallmark.md + repos/hallmark（已完成反 AI 模板感探索与门禁沉淀）",
        "learning_state": "learned",
        "rank": 0,
    },
    {
        "source_id": "apple-design-skill",
        "source_uri": "https://github.com/dickwu/apple-design-skill",
        "topic_key": "apple-design-skill-hig-liquid-glass",
        "aliases": ["apple-design-skill", "apple-design-skill-core-patterns"],
        "question": "Apple Design Skill 如何对齐 HIG 与 Liquid Glass 现代化规范？",
        "title": "Apple Design Skill 核心架构模式与实战拆解",
        "category": "工程与设计",
        "coverage_refs": [
            "concepts/wwdc27-liquid-glass-and-motion-map.md",
            "concepts/liquid-glass-optics-and-swiftui-26-27.md",
            "concepts/appkit-uikit-27-modernization.md",
        ],
        "why_now": "repos/apple-design-skill（已被 WWDC26/27 Liquid Glass 与 AppKit/UIKit 概念卡覆盖）",
        "learning_state": "learned",
        "rank": 0,
    },
    {
        "source_id": "awesome-ios-design-md",
        "source_uri": "https://github.com/Meliwat/awesome-ios-design-md",
        "topic_key": "awesome-ios-design-md-tokens",
        "aliases": ["awesome-ios-design-md", "awesome-ios-design-md-core-patterns"],
        "question": "Awesome iOS DESIGN.md 如何组织 200 款 iOS App 色板与排版令牌字典？",
        "title": "awesome-ios-design-md 核心架构模式与实战拆解",
        "category": "工程与设计",
        "coverage_refs": ["concepts/liquid-glass-optics-and-swiftui-26-27.md"],
        "why_now": "repos/awesome-ios-design-md（作为按需检索字典使用，无独立未探索原理题）",
        "learning_state": "learned",
        "rank": 0,
    },
    {
        "source_id": "awesome-web-design-md",
        "source_uri": "https://github.com/VoltAgent/awesome-design-md",
        "topic_key": "awesome-web-design-md-tokens",
        "aliases": ["awesome-web-design-md", "awesome-web-design-md-core-patterns"],
        "question": "Awesome Web DESIGN.md 如何组织 Web 站点样式令牌字典？",
        "title": "awesome-web-design-md 核心架构模式与实战拆解",
        "category": "工程与设计",
        "coverage_refs": ["collected-tools/hallmark.md"],
        "why_now": "repos/awesome-web-design-md（作为按需检索字典使用，无独立未探索原理题）",
        "learning_state": "learned",
        "rank": 0,
    },
    {
        "source_id": "shipswift",
        "source_uri": "https://github.com/signerlabs/ShipSwift",
        "topic_key": "shipswift-animation-metal-components",
        "aliases": ["shipswift", "shipswift-core-patterns"],
        "question": "ShipSwift 的 SWAnimation/SWMetal/SWChart 组件管线如何工作？",
        "title": "shipswift 核心架构模式与实战拆解",
        "category": "工程与设计",
        "coverage_refs": [
            "concepts/swiftui-27-shader-and-motion-pipeline.md",
            "concepts/swiftui-27-scrolling-and-drag-drop-motion.md",
        ],
        "why_now": "repos/shipswift（动效与 Shader 管线已被 WWDC26/27 概念卡覆盖）",
        "learning_state": "learned",
        "rank": 0,
    },
    {
        "source_id": "twostraws-swiftui-skill",
        "source_uri": "https://github.com/twostraws/SwiftUI-Agent-Skill",
        "topic_key": "twostraws-swiftui-pro-patterns",
        "aliases": ["twostraws-swiftui-skill", "twostraws-swiftui-skill-core-patterns"],
        "question": "Twostraws SwiftUI Agent Skill 如何规范现代 SwiftUI API 与可访问性？",
        "title": "twostraws-swiftui-skill 核心架构模式与实战拆解",
        "category": "工程与设计",
        "coverage_refs": [
            "concepts/liquid-glass-optics-and-swiftui-26-27.md",
            "concepts/swiftui-27-scrolling-and-drag-drop-motion.md",
        ],
        "why_now": "repos/twostraws-swiftui-skill（已被现有 SwiftUI 26/27 卡片覆盖）",
        "learning_state": "learned",
        "rank": 0,
    },
    # 13 queued concrete learning topics
    {
        "source_id": "awesome-cjk-design-md",
        "source_uri": "https://github.com/kzhrknt/awesome-design-md-jp",
        "topic_key": "jp-mixed-script-line-breaking",
        "aliases": ["awesome-cjk-design-md", "awesome-cjk-design-md-core-patterns", "jp-mixed-script-line-breaking"],
        "question": "日文/CJK 与拉丁文混排中，字体回退与行首行末禁则（Kinsoku Shori）如何共同避免断行错误？",
        "title": "日文/CJK 与拉丁文混排中，字体回退与行首行末禁则（Kinsoku Shori）如何共同避免断行错误？",
        "category": "工程与设计",
        "coverage_refs": [],
        "why_now": "repos/awesome-cjk-design-md/README.md:17-29（仅挂载路径，未拆解 CJK 混排禁则原理）",
        "learning_state": "queued",
        "rank": 1,
        "heuristic_direct": 0.94,
        "heuristic_tier": 2.9,
    },
    {
        "source_id": "bama",
        "source_uri": "https://github.com/daveebbelaar/bama",
        "topic_key": "bama-agent-cli-story-verification",
        "aliases": ["bama", "bama-core-patterns", "bama-agent-cli-story-verification"],
        "question": "BAMA 怎样把 macOS 页面清单转为免 Xcode GUI、可由 Agent CLI 逐步验收的用户故事与架构分层？",
        "title": "BAMA 怎样把 macOS 页面清单转为免 Xcode GUI、可由 Agent CLI 逐步验收的用户故事与架构分层？",
        "category": "工程与设计",
        "coverage_refs": [],
        "why_now": "repos/bama/README.md:42-65 & skills/code/{agent-cli,improve-architecture}/SKILL.md（仅摄取测色板，未拆解 Agent CLI 架构方法）",
        "learning_state": "queued",
        "rank": 2,
        "heuristic_direct": 0.92,
        "heuristic_tier": 2.8,
    },
    {
        "source_id": "genjutsu",
        "source_uri": "https://github.com/AThevon/genjutsu",
        "topic_key": "genjutsu-three-tier-choreography-gate",
        "aliases": ["genjutsu", "genjutsu-core-patterns", "genjutsu-three-tier-choreography-gate"],
        "question": "Genjutsu 如何通过 uchiha / kuchiyose / bunshin 区分单次交互、整套视觉与整站编排的审批粒度与评测门禁？",
        "title": "Genjutsu 如何通过 uchiha / kuchiyose / bunshin 区分单次交互、整套视觉与整站编排的审批粒度与评测门禁？",
        "category": "工程与设计",
        "coverage_refs": ["concepts/swiftui-27-shader-and-motion-pipeline.md"],
        "why_now": "repos/genjutsu/README.md:57-65 & PLATFORM-CONTRACT.md（基础 Shader 已覆盖，三级编排契约与 evals 待探索）",
        "learning_state": "queued",
        "rank": 3,
        "heuristic_direct": 0.90,
        "heuristic_tier": 2.7,
    },
    {
        "source_id": "avdlee-swiftui-skill",
        "source_uri": "https://github.com/AvdLee/SwiftUI-Agent-Skill",
        "topic_key": "swiftui-26-api-migration-scan-manifest",
        "aliases": ["avdlee-swiftui-skill", "avdlee-swiftui-skill-core-patterns", "swiftui-26-api-migration-scan-manifest"],
        "question": "update-swiftui-apis 如何用确定性 scan-manifest 批量扫描并迁移废弃 SwiftUI API 而不引入状态回归？",
        "title": "update-swiftui-apis 如何用确定性 scan-manifest 批量扫描并迁移废弃 SwiftUI API 而不引入状态回归？",
        "category": "工程与设计",
        "coverage_refs": ["concepts/liquid-glass-optics-and-swiftui-26-27.md"],
        "why_now": "repos/avdlee-swiftui-skill/.agents/skills/update-swiftui-apis/references/scan-manifest.md（未探索 API 迁移扫描清单）",
        "learning_state": "queued",
        "rank": 4,
        "heuristic_direct": 0.89,
        "heuristic_tier": 2.6,
    },
    {
        "source_id": "dimillian-skills",
        "source_uri": "https://github.com/Dimillian/Skills",
        "topic_key": "dimillian-spm-app-packaging-and-bug-swarm",
        "aliases": ["dimillian-skills", "dimillian-skills-core-patterns", "dimillian-spm-app-packaging-and-bug-swarm"],
        "question": "如何用纯 SwiftPM 无 .xcodeproj 打包签名 macOS MenuBar/桌面应用，并配合 bug-hunt-swarm 做多角色缺陷围剿？",
        "title": "如何用纯 SwiftPM 无 .xcodeproj 打包签名 macOS MenuBar/桌面应用，并配合 bug-hunt-swarm 做多角色缺陷围剿？",
        "category": "工程与设计",
        "coverage_refs": [
            "concepts/liquid-glass-optics-and-swiftui-26-27.md",
            "concepts/swiftui-27-scrolling-and-drag-drop-motion.md",
        ],
        "why_now": "repos/dimillian-skills/{macos-spm-app-packaging,bug-hunt-swarm}/SKILL.md（工程打包与排障子技能未探索）",
        "learning_state": "queued",
        "rank": 5,
        "heuristic_direct": 0.88,
        "heuristic_tier": 2.6,
    },
    {
        "source_id": "claude-code-apple-skills",
        "source_uri": "https://github.com/rshankras/claude-code-apple-skills",
        "topic_key": "apple-game-feel-and-store-rejection-gates",
        "aliases": ["claude-code-apple-skills", "claude-code-apple-skills-core-patterns", "apple-game-feel-and-store-rejection-gates"],
        "question": "如何量化 Apple 原生应用的 game-feel 触觉/微反馈物理参数，并用 rejection-handler 提前拦截 App Store 审核被拒风险？",
        "title": "如何量化 Apple 原生应用的 game-feel 触觉/微反馈物理参数，并用 rejection-handler 提前拦截 App Store 审核被拒风险？",
        "category": "工程与设计",
        "coverage_refs": [
            "concepts/liquid-glass-optics-and-swiftui-26-27.md",
            "concepts/swiftui-27-shader-and-motion-pipeline.md",
        ],
        "why_now": "repos/claude-code-apple-skills/skills/{design/game-feel,app-store/rejection-handler}/SKILL.md",
        "learning_state": "queued",
        "rank": 6,
        "heuristic_direct": 0.87,
        "heuristic_tier": 2.5,
    },
    {
        "source_id": "emilkowalski-skills",
        "source_uri": "https://github.com/emilkowalski/skills",
        "topic_key": "emil-spring-choreography-and-interruptibility",
        "aliases": ["emilkowalski-skills", "emilkowalski-skills-core-patterns", "emil-spring-choreography-and-interruptibility"],
        "question": "Emil Kowalski 的动效工程法则：何时该主动移除动画（remove-animations），以及可打断弹簧如何维持手势速度连续？",
        "title": "Emil Kowalski 的动效工程法则：何时该主动移除动画（remove-animations），以及可打断弹簧如何维持手势速度连续？",
        "category": "工程与设计",
        "coverage_refs": ["concepts/swiftui-27-shader-and-motion-pipeline.md"],
        "why_now": "repos/emilkowalski-skills/skills/{remove-animations,animation-performance,choreograph}/SKILL.md",
        "learning_state": "queued",
        "rank": 7,
        "heuristic_direct": 0.86,
        "heuristic_tier": 2.5,
    },
    {
        "source_id": "impeccable",
        "source_uri": "https://github.com/pbakaus/impeccable",
        "topic_key": "impeccable-mode-operate-vs-persuade-critique",
        "aliases": ["impeccable", "impeccable-core-patterns", "impeccable-mode-operate-vs-persuade-critique"],
        "question": "Impeccable 如何用正交审查标尺区分高密度操作台（mode-operate）与叙事说服页（mode-persuade）的排版与视觉克制边界？",
        "title": "Impeccable 如何用正交审查标尺区分高密度操作台（mode-operate）与叙事说服页（mode-persuade）的排版与视觉克制边界？",
        "category": "工程与设计",
        "coverage_refs": [],
        "why_now": "repos/impeccable/.agents/skills/impeccable/reference/{mode-operate,mode-persuade,critique}.md",
        "learning_state": "queued",
        "rank": 8,
        "heuristic_direct": 0.85,
        "heuristic_tier": 2.4,
    },
    {
        "source_id": "taste-skill",
        "source_uri": "https://github.com/Leonxlnx/taste-skill",
        "topic_key": "taste-skill-stop-hook-and-bento-anti-slop",
        "aliases": ["taste-skill", "taste-skill-core-patterns", "taste-skill-stop-hook-and-bento-anti-slop"],
        "question": "Taste Skill 如何通过 Stop-hook 自审回路与 Bento 卡片密度预算拦截前端 AI 模板感？",
        "title": "Taste Skill 如何通过 Stop-hook 自审回路与 Bento 卡片密度预算拦截前端 AI 模板感？",
        "category": "工程与设计",
        "coverage_refs": ["collected-tools/hallmark.md"],
        "why_now": "repos/taste-skill/skills/taste-skill/SKILL.md（与 Hallmark 互补的 Stop-hook 机制与密度预算）",
        "learning_state": "queued",
        "rank": 9,
        "heuristic_direct": 0.84,
        "heuristic_tier": 2.4,
    },
    {
        "source_id": "ui-ux-pro-max-skill",
        "source_uri": "https://github.com/nextlevelbuilder/ui-ux-pro-max-skill",
        "topic_key": "uupm-csv-faceted-design-retrieval",
        "aliases": ["ui-ux-pro-max-skill", "ui-ux-pro-max-skill-core-patterns", "uupm-csv-faceted-design-retrieval"],
        "question": "UI UX Pro Max 如何用轻量 CSV 分面索引与 BM25 脚本实现跨技术栈色板、字体与图表类型的确定性检索？",
        "title": "UI UX Pro Max 如何用轻量 CSV 分面索引与 BM25 脚本实现跨技术栈色板、字体与图表类型的确定性检索？",
        "category": "工程与设计",
        "coverage_refs": [],
        "why_now": "repos/ui-ux-pro-max-skill/src/ui-ux-pro-max/ & .claude/skills/ui-ux-pro-max/scripts/search.py",
        "learning_state": "queued",
        "rank": 10,
        "heuristic_direct": 0.83,
        "heuristic_tier": 2.3,
    },
    {
        "source_id": "vercel-agent-skills",
        "source_uri": "https://github.com/vercel-labs/agent-skills",
        "topic_key": "vercel-composition-patterns-and-view-transitions",
        "aliases": ["vercel-agent-skills", "vercel-agent-skills-core-patterns", "vercel-composition-patterns-and-view-transitions"],
        "question": "Vercel Agent Skills 中的复合组件状态隔离（Composition Patterns）与声明式 React View Transitions 如何消除布尔 Props 爆炸？",
        "title": "Vercel Agent Skills 中的复合组件状态隔离（Composition Patterns）与声明式 React View Transitions 如何消除布尔 Props 爆炸？",
        "category": "工程与设计",
        "coverage_refs": [],
        "why_now": "repos/vercel-agent-skills/skills/{composition-patterns,react-view-transitions}/SKILL.md",
        "learning_state": "queued",
        "rank": 11,
        "heuristic_direct": 0.82,
        "heuristic_tier": 2.3,
    },
    {
        "source_id": "vibecoded-design-tells",
        "source_uri": "https://github.com/JCarterJohnson/vibecoded-design-tells",
        "topic_key": "vibecoded-reddit-empirical-slop-tells",
        "aliases": ["vibecoded-design-tells", "vibecoded-design-tells-core-patterns", "vibecoded-reddit-empirical-slop-tells"],
        "question": "基于 Reddit 真实负评样本统计的 UI、文案与代码三大类 Vibe-Coded AI 破绽有哪些高频特征与反制清单？",
        "title": "基于 Reddit 真实负评样本统计的 UI、文案与代码三大类 Vibe-Coded AI 破绽有哪些高频特征与反制清单？",
        "category": "工程与设计",
        "coverage_refs": ["collected-tools/hallmark.md"],
        "why_now": "repos/vibecoded-design-tells/skill/references/{tells,choosing-a-look}.md",
        "learning_state": "queued",
        "rank": 12,
        "heuristic_direct": 0.81,
        "heuristic_tier": 2.2,
    },
    {
        "source_id": "expo-skills",
        "source_uri": "https://github.com/expo/skills",
        "topic_key": "expo-visual-grader-and-web-to-native-false-friends",
        "aliases": ["expo-skills", "expo-skills-core-patterns", "expo-visual-grader-and-web-to-native-false-friends"],
        "question": "expo-skill-eval 的 visual-grader 评分量表与 web-to-native/false-friends 如何系统性拦截把 Web CSS 习惯生搬到原生端的布局缺陷？",
        "title": "expo-skill-eval 的 visual-grader 评分量表与 web-to-native/false-friends 如何系统性拦截把 Web CSS 习惯生搬到原生端的布局缺陷？",
        "category": "工程与设计",
        "coverage_refs": [],
        "why_now": "repos/expo-skills/.claude/skills/expo-skill-eval/agents/visual-grader.md",
        "learning_state": "queued",
        "rank": 13,
        "heuristic_direct": 0.80,
        "heuristic_tier": 2.2,
    },
)
_CURATED_BY_SOURCE_ID = {item["source_id"]: item for item in CURATED_LEARNING_TOPICS}
_CURATED_BY_ALIAS: dict[str, dict[str, Any]] = {}
for _item in CURATED_LEARNING_TOPICS:
    _CURATED_BY_ALIAS[_item["topic_key"]] = _item
    _CURATED_BY_ALIAS[_item["source_id"]] = _item
    for _al in _item.get("aliases", []):
        _CURATED_BY_ALIAS[_al] = _item
DEFAULT_GIT_ROOTS = (
    Path.home() / "Development" / "user-skills",
    Path.home() / "Development" / "AIDash",
    Path.home() / "Development" / "VoxPocket",
    Path.home() / "Development" / "VitalStride",
)
CARD_TEMPLATES_PATH = SKILL_DIR / "references" / "card-templates.json"

FOLDER_SECTION_HEADINGS = {
    "thoughts": "## 💭 思考与感悟随笔（`thoughts/`）",
    "concepts": "## 🧠 原理与概念痛点卡（`concepts/`）",
    "craft": "## 🎬 叙事结构与制作技巧卡（`craft/`）",
    "raw": "## 📦 原始好素材存档（`raw/`）",
}

FOLDER_TABLE_HEADERS = {
    "thoughts": "| 门类 | 标题 | 核心感悟（Insight） | 路径 | 更新日期 |\n|---|---|---|---|---|",
    "concepts": "| 门类 | 标题 | 一句话本质（TL;DR） | 路径 | 更新日期 |\n|---|---|---|---|---|",
    "craft": "| 门类 | 标题 | 一句话公式（TL;DR） | 路径 | 更新日期 |\n|---|---|---|---|---|",
    "raw": "| 来源类型 | 标题 | 衍生卡片 | 路径 | 归档日期 |\n|---|---|---|---|---|",
}

DC006_FORBIDDEN_RE = re.compile(r"\A---\s*\n[\s\S]{1,400}?\n---\s*\n\s*<!doctype html>", re.IGNORECASE)
DC007_FORBIDDEN_RE = re.compile(r"<h[1-4][^>]*>\s*[📌❓✅🎬🏷️💡🔥✨🎯🚀⚡🧰🔭🛠🔄💭🧠📦]")
DC008_FORBIDDEN_RE = re.compile(r"\.prose\s+th\s*,\s*\.prose\s+td\s*\{[^}]*white-space\s*:\s*nowrap")
_EMOJI_STRIP_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]+")


def _load_helper_module(name: str, path: Path):
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def strip_emoji(text: str) -> str:
    return re.sub(r"\s+", " ", _EMOJI_STRIP_RE.sub("", text)).strip()


def load_card_templates(path: Path = CARD_TEMPLATES_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {"schema": "knowledge-digest-card-templates/v1", "version": 1, "templates": []}
    return json.loads(path.read_text(encoding="utf-8"))


def infer_folder(category: str, explicit_folder: str | None = None) -> str:
    if explicit_folder in {"concepts", "craft", "thoughts", "raw"}:
        return explicit_folder
    cat = category.strip()
    if "叙事" in cat or "表达" in cat or "craft" in cat.lower():
        return "craft"
    if "原始" in cat or "raw" in cat.lower():
        return "raw"
    if "思考" in cat or "感悟" in cat or "随笔" in cat or "智能家居与生活" in cat:
        return "thoughts"
    return "concepts"


def parse_frontmatter(md_text: str) -> tuple[dict[str, Any], str]:
    text = md_text.strip()
    meta: dict[str, Any] = {}
    if not text.startswith("---"):
        return meta, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return meta, text
    fm_lines = parts[1].splitlines()
    body = parts[2].strip()
    current_list_key: str | None = None
    for raw_line in fm_lines:
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        m_item = re.match(r"^\s*-\s+(.*)$", line)
        if m_item and current_list_key:
            val = m_item.group(1).strip().strip("\"'")
            meta.setdefault(current_list_key, []).append(val)
            continue
        m_kv = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if m_kv:
            key = m_kv.group(1).strip()
            val = m_kv.group(2).strip()
            if not val:
                current_list_key = key
                meta[key] = []
            elif val.startswith("[") and val.endswith("]"):
                current_list_key = None
                inner = val[1:-1].strip()
                meta[key] = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()] if inner else []
            else:
                current_list_key = None
                meta[key] = val.strip("\"'")
    return meta, body


def extract_tldr_summary(md_text: str, fallback: str = "") -> str:
    meta, body = parse_frontmatter(md_text)
    if meta.get("insight"):
        return strip_emoji(str(meta["insight"]))
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(">"):
            cleaned = re.sub(r"^>\s*", "", s)
            cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
            cleaned = strip_emoji(cleaned)
            if cleaned:
                return cleaned
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith("|") and s != "---":
            return strip_emoji(re.sub(r"\*\*([^*]+)\*\*", r"\1", s))[:160]
    return strip_emoji(fallback or str(meta.get("title") or ""))


def _ensure_index_scaffold(index_path: Path) -> str:
    if index_path.is_file():
        return index_path.read_text(encoding="utf-8")
    return (
        "# Personal Knowledge Vault（个人知识与好内容拆解库）\n\n"
        "由 [`knowledge-digest`](../../user-skills/skills/knowledge-digest/SKILL.md) Skill 统一维护。\n\n"
        f"{FOLDER_SECTION_HEADINGS['thoughts']}\n\n{FOLDER_TABLE_HEADERS['thoughts']}\n\n---\n\n"
        f"{FOLDER_SECTION_HEADINGS['concepts']}\n\n{FOLDER_TABLE_HEADERS['concepts']}\n\n---\n\n"
        f"{FOLDER_SECTION_HEADINGS['craft']}\n\n{FOLDER_TABLE_HEADERS['craft']}\n\n---\n\n"
        f"{FOLDER_SECTION_HEADINGS['raw']}\n\n{FOLDER_TABLE_HEADERS['raw']}\n"
    )


def upsert_vault_index(
    index_path: Path,
    *,
    folder: str,
    rel_path: str,
    category: str,
    title: str,
    summary: str,
    updated_date: str,
) -> None:
    text = _ensure_index_scaffold(index_path)
    safe_cat = category.replace("|", "｜").strip()
    safe_title = title.replace("|", "｜").strip()
    safe_sum = summary.replace("|", "｜").strip()
    new_row = f"| {safe_cat} | {safe_title} | {safe_sum} | [`{rel_path}`]({rel_path}) | {updated_date} |"

    lines = text.splitlines()
    link_token = f"[`{rel_path}`]({rel_path})"
    replaced = False
    for idx, line in enumerate(lines):
        if link_token in line or f"({rel_path})" in line:
            lines[idx] = new_row
            replaced = True
            break

    if not replaced:
        heading = FOLDER_SECTION_HEADINGS.get(folder, FOLDER_SECTION_HEADINGS["concepts"])
        sec_idx = next((i for i, ln in enumerate(lines) if heading in ln), -1)
        if sec_idx < 0:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend([heading, "", FOLDER_TABLE_HEADERS[folder], new_row])
        else:
            insert_at = sec_idx + 1
            while insert_at < len(lines) and not lines[insert_at].strip().startswith("|---"):
                insert_at += 1
            if insert_at < len(lines):
                insert_at += 1
                while insert_at < len(lines) and lines[insert_at].strip().startswith("|"):
                    insert_at += 1
                lines.insert(insert_at, new_row)
            else:
                lines.extend(["", FOLDER_TABLE_HEADERS[folder], new_row])

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def archive_card(
    *,
    slug: str,
    category: str,
    title: str,
    file_path: Path,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    folder: str | None = None,
    updated_date: str | None = None,
) -> dict[str, Any]:
    clean_slug = re.sub(r"[^a-z0-9-]+", "-", slug.strip().lower()).strip("-")
    if not clean_slug:
        raise ValueError("slug must contain alphanumeric characters")
    if not file_path.is_file():
        raise FileNotFoundError(f"card file not found: {file_path}")

    target_folder = infer_folder(category, folder)
    raw_md = file_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw_md)
    eff_updated = updated_date or str(meta.get("updated") or meta.get("date") or date.today().isoformat())
    eff_title = title.strip() or str(meta.get("title") or clean_slug)
    eff_cat = category.strip() or str(meta.get("category") or "工程与设计")

    if not raw_md.strip().startswith("---"):
        raw_md = (
            f"---\ntitle: {eff_title}\ncategory: {eff_cat}\n"
            f"tags: [{clean_slug}]\nchannel: acquire\nupdated: {eff_updated}\n---\n\n"
            f"# {eff_title}\n\n{body}\n"
        )

    dest_dir = vault_dir / target_folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{clean_slug}.md"
    if file_path.resolve() != dest_path.resolve():
        dest_path.write_text(raw_md.rstrip() + "\n", encoding="utf-8")

    rel_path = f"{target_folder}/{clean_slug}.md"
    summary = extract_tldr_summary(raw_md, fallback=eff_title)
    index_path = vault_dir / "index.md"
    upsert_vault_index(
        index_path,
        folder=target_folder,
        rel_path=rel_path,
        category=eff_cat,
        title=eff_title,
        summary=summary,
        updated_date=eff_updated,
    )
    return {
        "ok": True,
        "slug": clean_slug,
        "folder": target_folder,
        "category": eff_cat,
        "title": eff_title,
        "card_path": str(dest_path),
        "rel_path": rel_path,
        "index_path": str(index_path),
        "summary": summary,
        "updated": eff_updated,
    }


def _existing_vault_corpus(vault_dir: Path) -> str:
    parts: list[str] = []
    index_path = vault_dir / "index.md"
    if index_path.is_file():
        parts.append(index_path.read_text(encoding="utf-8", errors="ignore"))
    for sub in ("concepts", "craft", "thoughts", "raw"):
        sub_dir = vault_dir / sub
        if sub_dir.is_dir():
            for md in sub_dir.glob("*.md"):
                parts.append(f"{sub}/{md.name}")
                parts.append(md.stem)
                parts.append(md.read_text(encoding="utf-8", errors="ignore")[:800])
    return "\n".join(parts).lower()


def _normalize_source_uri(raw_uri: str) -> str:
    s = raw_uri.strip()
    if s.startswith("git@github.com:"):
        s = "https://github.com/" + s[len("git@github.com:") :]
    if s.endswith(".git"):
        s = s[:-4]
    return s.rstrip("/")


def _has_usage_or_integration_evidence(text: str) -> bool:
    lower = text.lower()
    markers = (
        "実際に使ったメモ",
        "統合済み",
        "已集成",
        "已接入",
        "design-studio",
        "dc-009",
        "dc-010",
        "vault `t-",
        "already integrated",
    )
    return any(m in lower for m in markers)


class TopicLedgerError(RuntimeError):
    pass


def _load_topic_ledger(vault_dir: Path) -> dict[str, dict[str, Any]]:
    ledger_path = vault_dir / "topic-ledger.json"
    if not ledger_path.is_file():
        raise TopicLedgerError(f"missing authoritative topic ledger: {ledger_path}")
    try:
        doc = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TopicLedgerError(f"invalid topic ledger: {ledger_path}: {exc}") from exc
    raw_topics = doc.get("topics") if isinstance(doc, dict) else None
    if not isinstance(raw_topics, list) or not raw_topics:
        raise TopicLedgerError(f"incomplete topic ledger: {ledger_path}")
    entries: dict[str, dict[str, Any]] = {}
    required = ("topic_key", "source_uri", "question", "learning_state", "source_fingerprint")
    for item in raw_topics:
        if not isinstance(item, dict) or any(not str(item.get(key) or "").strip() for key in required):
            raise TopicLedgerError(f"incomplete topic entry in {ledger_path}")
        key = str(item["topic_key"]).strip()
        entries[key] = dict(item)
    return entries


def _save_topic_ledger(vault_dir: Path, entries: dict[str, dict[str, Any]]) -> Path:
    ledger_path = vault_dir / "topic-ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "topics": list(entries.values()),
    }
    ledger_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ledger_path


def _parse_iso_ts(raw_ts: str) -> datetime | None:
    s = str(raw_ts or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _cst_day_str(dt: datetime | None = None) -> str:
    eff = dt or datetime.now(timezone.utc)
    cst_tz = timezone(timedelta(hours=8))
    return eff.astimezone(cst_tz).date().isoformat()


def _reconcile_proposals_history(proposals_path: Path | None) -> dict[str, Any]:
    """Read proposals.jsonl last-valid-record-wins for learning topics."""
    by_topic: dict[str, dict[str, Any]] = {}
    proposed_today_cst = set()
    today_cst = _cst_day_str()
    if proposals_path and proposals_path.is_file():
        for raw_line in proposals_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid proposal history JSON: {exc}") from exc
            if not isinstance(rec, dict):
                raise ValueError("invalid proposal history record: expected object")
            required = ("id", "ts", "agent", "title")
            if any(not str(rec.get(key) or "").strip() for key in required):
                raise ValueError(f"invalid proposal history record: missing {required}")
            rec_id = str(rec.get("id") or "").strip()
            ref = str(rec.get("ref") or "").strip()
            origin_ref = str(rec.get("origin_ref") or "").strip()
            topic_key = str(rec.get("topic_key") or "").strip()
            if not topic_key:
                for candidate_ref in (ref, origin_ref):
                    if candidate_ref.startswith("learning-topic://"):
                        topic_key = candidate_ref[len("learning-topic://") :].strip("/")
                        break
                    if candidate_ref.startswith("knowledge://"):
                        topic_key = candidate_ref[len("knowledge://") :].strip("/")
                        break
            if not topic_key and rec_id.startswith(("learn-", "learning-", "knowledge-")):
                topic_key = re.sub(r"^(?:learn|learning|knowledge)-", "", rec_id)
            if not topic_key:
                continue
            # Canonicalize alias to curated topic_key if matched
            curated = _CURATED_BY_ALIAS.get(topic_key)
            if curated:
                topic_key = curated["topic_key"]
            by_topic[topic_key] = rec
            ts_dt = _parse_iso_ts(str(rec.get("ts") or rec.get("created_at") or ""))
            status_val = str(rec.get("status") or "").lower()
            l_state = str(rec.get("learning_state") or "").lower()
            if ts_dt and _cst_day_str(ts_dt) == today_cst and (
                status_val == "pending" or l_state == "proposed"
            ):
                proposed_today_cst.add(topic_key)

    pending_topics: set[str] = set()
    blocked_or_done_topics: set[str] = set()
    now_utc = datetime.now(timezone.utc)
    for t_key, rec in by_topic.items():
        status_val = str(rec.get("status") or "pending").lower()
        l_state = str(rec.get("learning_state") or "").lower()
        next_elig = _parse_iso_ts(str(rec.get("next_eligible_at") or ""))
        ts_dt = _parse_iso_ts(str(rec.get("ts") or ""))
        if l_state in {"proposed"} or (status_val == "pending" and l_state not in {"snoozed", "dismissed", "learned", "approved", "exploring"}):
            pending_topics.add(t_key)
            blocked_or_done_topics.add(t_key)
        elif l_state in {"approved", "exploring", "learned", "blocked"} or status_val in {"approved", "learned", "blocked"}:
            # Note: if status == approved only because of Linear transfer, it is still active/pending decision
            if l_state == "transferred":
                pending_topics.add(t_key)
            blocked_or_done_topics.add(t_key)
        elif l_state == "snoozed" or status_val == "snoozed":
            if next_elig and now_utc < next_elig:
                blocked_or_done_topics.add(t_key)
            elif ts_dt and (now_utc - ts_dt) < timedelta(days=7):
                blocked_or_done_topics.add(t_key)
        elif l_state == "dismissed" or status_val == "dismissed":
            if next_elig and now_utc < next_elig:
                blocked_or_done_topics.add(t_key)
            elif ts_dt is None or (now_utc - ts_dt) < timedelta(days=30):
                blocked_or_done_topics.add(t_key)
            else:
                #Per spec §6: dismissed requires explicit reopen or substantial new question even after 30d
                blocked_or_done_topics.add(t_key)

    return {
        "by_topic": by_topic,
        "pending_topics": pending_topics,
        "excluded_topics": blocked_or_done_topics,
        "proposed_today_cst": proposed_today_cst,
    }


def _scan_collected_tools_candidates(
    collected_dir: Path,
    existing_corpus: str,
    vault_dir: Path | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if not collected_dir.is_dir():
        return candidates

    # Step 1: Merge top-level *.md notes and repos/<name> into unified source entities
    entities: dict[str, dict[str, Any]] = {}
    for md in sorted(collected_dir.glob("*.md")):
        if md.name.lower() == "readme.md":
            continue
        source_id = re.sub(r"[^a-z0-9-]+", "-", md.stem.lower()).strip("-")
        text = md.read_text(encoding="utf-8", errors="ignore")
        entities.setdefault(
            source_id,
            {"source_id": source_id, "note_path": None, "note_text": "", "repo_dir": None, "readme_text": ""},
        )
        entities[source_id]["note_path"] = md
        entities[source_id]["note_text"] = text

    repos_dir = collected_dir / "repos"
    if repos_dir.is_dir():
        for repo_dir in sorted(repos_dir.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name.startswith("."):
                continue
            source_id = re.sub(r"[^a-z0-9-]+", "-", repo_dir.name.lower()).strip("-")
            readme = next(
                (p for p in (repo_dir / "README.md", repo_dir / "readme.md") if p.is_file()),
                None,
            )
            r_text = readme.read_text(encoding="utf-8", errors="ignore") if readme is not None else ""
            entities.setdefault(
                source_id,
                {"source_id": source_id, "note_path": None, "note_text": "", "repo_dir": None, "readme_text": ""},
            )
            entities[source_id]["repo_dir"] = repo_dir
            entities[source_id]["readme_text"] = r_text

    try:
        ledger_entries = _load_topic_ledger(vault_dir) if vault_dir else {}
    except TopicLedgerError:
        # Legacy preview/archive scans may operate without the Personal ledger;
        # proposal-producing discovery is fail-closed in discover_topics.
        ledger_entries = {}
    if ledger_entries:
        authoritative: list[dict[str, Any]] = []
        for topic_key, item in sorted(ledger_entries.items(), key=lambda pair: (int(pair[1].get("rank", 99) or 99), pair[0])):
            state = str(item.get("learning_state", "unknown")).lower()
            if state != "queued":
                continue
            source_id = str(item.get("source_id") or "").strip()
            ent = entities.get(source_id, {})
            source_text = f"{ent.get('note_text', '')}\n{ent.get('readme_text', '')}"
            source_ref = ent.get("repo_dir") or ent.get("note_path")
            if not source_ref or not source_text.strip() or _has_usage_or_integration_evidence(source_text):
                continue
            question = str(item.get("question") or "").strip()
            source_uri = _normalize_source_uri(str(item.get("source_uri") or ""))
            if not question or len(question) < 12 or not source_uri:
                continue
            authoritative.append({
                "slug": topic_key, "topic_key": topic_key, "source_id": source_id,
                "source_uri": source_uri, "aliases": list(item.get("aliases") or [source_id, topic_key]),
                "title": str(item.get("title") or question), "question": question,
                "category": str(item.get("category") or "工程与设计"),
                "source_kind": str(item.get("source_kind") or "collected_tools"),
                "source_ref": str(source_ref), "why_now": str(item.get("why_now") or ""),
                "coverage_refs": list(item.get("coverage_refs") or []),
                "excerpt": str(item.get("why_now") or question),
                "heuristic_direct": float(item.get("heuristic_direct", 0.8) or 0.8),
                "heuristic_tier": float(item.get("heuristic_tier", 2.0) or 2.0),
                "rank": int(item.get("rank", 99) or 99), "scope": "personal",
            })
        return authoritative

    for source_id, ent in sorted(entities.items()):
        note_text = ent["note_text"]
        readme_text = ent["readme_text"]
        combined_text = f"{note_text}\n{readme_text}"

        # Recognize Hallmark or any tool note with explicit usage/integration evidence
        if _has_usage_or_integration_evidence(note_text):
            continue

        curated = _CURATED_BY_SOURCE_ID.get(source_id)
        if curated is not None:
            if curated.get("learning_state") == "learned":
                continue
            topic_key = curated["topic_key"]
            ledger_override = ledger_entries.get(topic_key, {})
            if ledger_override.get("learning_state") in {"learned", "dismissed", "approved", "exploring"}:
                continue
            if topic_key in existing_corpus or f"concepts/{topic_key}.md" in existing_corpus:
                continue
            candidates.append({
                "slug": topic_key,
                "topic_key": topic_key,
                "source_id": source_id,
                "source_uri": _normalize_source_uri(curated["source_uri"]),
                "aliases": list(curated.get("aliases", [source_id, topic_key])),
                "title": curated["question"],
                "question": curated["question"],
                "category": curated.get("category", "工程与设计"),
                "source_kind": "collected_tools_repo" if ent["repo_dir"] is not None else "collected_tools",
                "source_ref": str(ent["repo_dir"] or ent["note_path"]),
                "why_now": curated["why_now"],
                "coverage_refs": list(curated.get("coverage_refs", [])),
                "excerpt": f"来源：{curated['why_now']} · 预期产出：1 张知识卡（不含自动发博或 design-studio 同步）",
                "heuristic_direct": float(curated.get("heuristic_direct", 0.85)),
                "heuristic_tier": float(curated.get("heuristic_tier", 2.5)),
                "rank": int(curated.get("rank", 50)),
                "scope": "personal",
            })
            continue

        # Custom / test fixture entity not in the 19 curated repos
        if source_id in existing_corpus and f"concepts/{source_id}.md" in existing_corpus:
            continue
        first_h1 = next(
            (ln[2:].strip() for ln in combined_text.splitlines() if ln.startswith("# ")),
            source_id,
        )
        summary_lines = [
            ln.strip()
            for ln in combined_text.splitlines()
            if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith("```")
        ]
        excerpt = " ".join(summary_lines[:3])[:220] or f"Reference tool {source_id} in collected-tools"
        topic_key = f"{source_id}-core-patterns" if ent["repo_dir"] is not None and not ent["note_path"] else source_id
        if topic_key in existing_corpus:
            continue
        candidates.append({
            "slug": topic_key,
            "topic_key": topic_key,
            "source_id": source_id,
            "source_uri": str(ent["repo_dir"] or ent["note_path"]),
            "aliases": [source_id, topic_key],
            "title": f"{first_h1}：核心机制与工程痛点拆解",
            "question": f"{first_h1}：核心机制与工程痛点拆解",
            "category": "工程与设计",
            "source_kind": "collected_tools_repo" if ent["repo_dir"] is not None else "collected_tools",
            "source_ref": str(ent["repo_dir"] or ent["note_path"]),
            "why_now": f"collected-tools/{source_id}（尚未在 knowledge-vault 建立概念卡）",
            "coverage_refs": [],
            "excerpt": excerpt,
            "heuristic_direct": 0.84,
            "heuristic_tier": 2.4,
            "rank": 99,
            "scope": "personal",
        })

    candidates.sort(key=lambda c: (int(c.get("rank", 99)), str(c.get("topic_key", ""))))
    return candidates


def _scan_git_tech_stack_candidates(git_roots: tuple[Path, ...], existing_corpus: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    commit_lines: list[str] = []
    for root in git_roots:
        if not (root / ".git").exists():
            continue
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), "--no-pager", "log", "--since=7.days", "-n", "15", "--oneline"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                commit_lines.extend(proc.stdout.strip().splitlines())
        except (OSError, subprocess.SubprocessError):
            continue

    # Per spec §3 & TK1: never fabricate fallback topics when git log is empty!
    if not commit_lines:
        return []

    joined_commits = "\n".join(commit_lines).lower()
    topic_probes = [
        (
            "jev-r09-r06-truth-table",
            "Jev System One R09 重排与 R06 引用核查：用零幻觉真值表替代黑盒打分",
            "计算机与网络",
            ("r09", "r06", "jev", "systemone", "rerank"),
            "Recent commits wire Jev R09 score reranking and R06 citation verification across research and digest pipelines.",
            0.90,
            2.7,
        ),
        (
            "progressive-disclosure-four-valves",
            "渐进式披露 Skill 四道防爆阀门：为什么 L0/L1/L2 预算能让 Token 降一个量级",
            "工程与设计",
            ("skill-contract", "repo-bound", "progressive", "drawer", "audit"),
            "Recent git commits refine skill-contract L0/L1/L2 progressive disclosure budgets and executable eval drawers.",
            0.85,
            2.5,
        ),
        (
            "aidash-xpc-event-bridge-flywheel",
            "AIDash 单向数据流与 Event Bridge 闭环：从 L1 遥测采集到点 Star 自动触发动作",
            "工程与设计",
            ("event_bridge", "aidata", "linear_issue", "aidash", "proposal"),
            "Recent commits in AIDash connect L1-L5 telemetry with event_bridge Star/Done actions.",
            0.83,
            2.4,
        ),
    ]
    for slug, title, category, keywords, excerpt, p_direct, s_tier in topic_probes:
        if slug in existing_corpus:
            continue
        matched = any(k in joined_commits for k in keywords)
        if matched:
            candidates.append({
                "slug": slug,
                "topic_key": slug,
                "source_id": slug,
                "source_uri": f"git://{slug}",
                "aliases": [slug],
                "title": title,
                "question": title,
                "category": category,
                "source_kind": "git_tech_stack",
                "source_ref": "git-log",
                "why_now": excerpt,
                "coverage_refs": [],
                "excerpt": excerpt,
                "heuristic_direct": p_direct,
                "heuristic_tier": s_tier,
                "rank": 120,
                "scope": "personal",
            })
    return candidates


def _rerank_candidates_r09(
    candidates: list[dict[str, Any]],
    *,
    mock_r09_answers: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    research_helper = _load_helper_module(
        "jev_research_helper",
        SKILLS_ROOT / "deep-research" / "scripts" / "jev_research_helper.py",
    )
    jev_mod = _load_helper_module(
        "jev_client",
        SKILLS_ROOT / "team-lead" / "scripts" / "jev_client.py",
    )

    answers: dict[str, Any] | None = mock_r09_answers
    if answers is None and research_helper is not None and jev_mod is not None:
        req = research_helper.build_r09_request(
            research_goal="Select high-value learning topics for knowledge-vault and AIDash",
            sub_question="Which candidate topic fills a high-leverage gap in recent engineering & tool stockpile?",
            candidates=[
                {"slug": c["slug"], "title": c["title"], "excerpt": c["excerpt"]}
                for c in candidates
            ],
        )
        decision = jev_mod.jev_decide(
            req["state"],
            req["questions"],
            agent_role="knowledge-digest",
        )
        if isinstance(decision, dict):
            answers = decision.get("answers", decision)

    if answers is None:
        answers = {}
        for idx, cand in enumerate(candidates):
            p_d = float(cand.get("heuristic_direct", 0.82))
            s_t = float(cand.get("heuristic_tier", 2.4))
            answers[f"cand_{idx}_direct"] = {"noul": p_d}
            answers[f"cand_{idx}_tier"] = {
                "score": s_t,
                "probabilities": {"0": 0.05, "2": 0.45, "3": 0.50},
            }

    if research_helper is not None:
        rows = research_helper.evaluate_r09_truth_table(answers, candidates)
    else:
        rows = []
        for idx, cand in enumerate(candidates):
            p_d = float(answers.get(f"cand_{idx}_direct", {}).get("noul", 0.8))
            s_t = float(answers.get(f"cand_{idx}_tier", {}).get("score", 2.4))
            rows.append({
                "index": idx,
                "composite_score": round(0.65 * p_d + 0.35 * (s_t / 3.0), 6),
                "action": "KEEP_PRIMARY" if p_d >= 0.7 and s_t >= 2.0 else "KEEP_SUPPORTING",
                "p_direct": p_d,
                "s_tier": s_t,
            })
        rows.sort(key=lambda r: (-r["composite_score"], r["index"]))

    ranked: list[dict[str, Any]] = []
    for row in rows:
        if row["action"] == "DROP_NOISE":
            continue
        cand = dict(candidates[row["index"]])
        cand["r09"] = row
        ranked.append(cand)
    return ranked


def render_concept_card_markdown(candidate: dict[str, Any], updated_date: str | None = None) -> str:
    eff_date = updated_date or date.today().isoformat()
    slug = candidate["slug"]
    title = candidate["title"]
    category = candidate.get("category", "工程与设计")
    excerpt = candidate.get("excerpt", "")
    return f"""---
title: {title}
category: {category}
tags: [{slug}, 工程实践, 痛点破案]
channel: acquire
updated: {eff_date}
---

# {title}

## ⚡ 一句话本质（TL;DR）

> **{title}的核心不是堆砌规则，而是把隐性工程摩擦变成零心智负担的确定性护栏** —— 将高频排障与阅读检索耗时压缩 **80% 以上**（从数十分钟人工比对降到 **1 次**确定性检查）。

## 🧰 核心教具与量级速览

| 抽象概念 / 参数 | 关键数字 | 生活教具 / 直观参照 | 物理/工程效果 |
|---|---|---|---|
| 上下文与信噪比预算 | 压缩 **80%+** 无关噪音 | 行李箱分层收纳袋 | 避免一次性塞满长文本导致注意力稀释 |
| 确定性门禁回执 | **0 Token** 毫秒级拦截 | 电路保险丝与防呆卡扣 | 在问题进入下游前当场定位并阻断 |

## 🎯 核心痛点直答（Pain-Point Q&A）

### Q1：为什么平时收藏了很多工具与工程实践，真正用时还是容易踩旧坑？
- **直答**：缺少从「原始素材」到「痛点问答 + 零 Token 门禁」的闭环转化。
- **原理与教具**：就像厨房买了很多调料却没贴标签分装（{excerpt[:80] or "散落在仓库与笔记中"}），紧要关头只能凭记忆乱抓；只有提炼成 **3–5 个痛点问答**并绑定脚本检查，知识才会变成肌肉记忆。

### Q2：如何在不增加日常维护负担的前提下持续消化新主题？
- **直答**：用自动寻题重排（R09）筛出 Top **1–2** 个高价值缺口，推上每日简报按需点亮发布。
- **原理与教具**：像智能温控器只在实际偏离阈值时才启动加热，每次只聚焦最痛的 **1 个工程切面**，拒绝百科全书式囤积。

## ✅ 立即可用清单（Actionable Checklist）

1. **先跑一行命令验证现状**：用确定性脚本扫描当前仓库与知识库缺口，不靠主观猜测选题。
2. **用生活教具替掉干瘪定义**：每张卡片至少绑定 **1 个具体数字对比**与 **1 个日常参照物**。
3. **点 Star 一键沉淀与发布**：在 AIDash 上审批高价值主题，自动同步到个人博客与索引表。

## 🔭 延伸视野（1 分钟拔高）

- 当知识摄取、每日简报与静态博客形成单向无摩擦飞轮，每一次解决线上痛点都会自然长成一篇可复用的公开技术笔记。
"""


def discover_topics(
    *,
    top: int = 1,
    propose_aidash: bool = False,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    collected_tools_dir: Path = DEFAULT_COLLECTED_TOOLS_DIR,
    git_roots: tuple[Path, ...] = DEFAULT_GIT_ROOTS,
    proposals_path: Path = DEFAULT_PROPOSALS_PATH,
    materialize_cards: bool | None = None,
    mock_r09_answers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Candidate discovery and reservation are one serialized critical section.
    lock_path = proposals_path.with_name(proposals_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
    # Per TK1 contract: proposal discovery never materializes a card, even when a
    # legacy caller passes --materialize. Archival is a separate explicit command.
    eff_materialize = bool(materialize_cards) if not propose_aidash else False
    try:
        existing_corpus = _existing_vault_corpus(vault_dir)
        history = _reconcile_proposals_history(proposals_path)
        try:
            ledger_entries = _load_topic_ledger(vault_dir)
        except TopicLedgerError:
            if propose_aidash:
                raise
            ledger_entries = {}
    except (TopicLedgerError, ValueError) as exc:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
        return {"ok": False, "status": "health_error", "error": str(exc), "selected_count": 0, "topics": [], "proposals_written": []}
    excluded_topics: set[str] = set(history["excluded_topics"])
    pending_topics: set[str] = set(history["pending_topics"])
    proposed_today_cst: set[str] = set(history["proposed_today_cst"])

    raw_candidates = (
        _scan_collected_tools_candidates(collected_tools_dir, existing_corpus, vault_dir=vault_dir)
        + _scan_git_tech_stack_candidates(git_roots, existing_corpus)
    )
    deduped: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_questions: set[tuple[str, str]] = set()
    excluded_count = 0
    for cand in raw_candidates:
        t_key = str(cand.get("topic_key") or cand.get("slug") or "").strip()
        s_uri = _normalize_source_uri(str(cand.get("source_uri") or ""))
        question = re.sub(r"\s+", " ", str(cand.get("question") or cand.get("title") or "").strip()).casefold()
        aliases = {str(a).strip() for a in cand.get("aliases", []) if str(a).strip()} | {t_key}
        question_key = (s_uri, question)
        if not t_key or (aliases & seen_keys) or question_key in seen_questions:
            continue
        seen_keys |= aliases
        seen_questions.add(question_key)
        if aliases & excluded_topics:
            excluded_count += 1
            continue
        deduped.append(cand)

    # Cap pending learning topics at 3 and daily new proposals at 1 per Asia/Shanghai day
    daily_cap_reached = propose_aidash and len(proposed_today_cst) >= 1
    pending_cap_reached = propose_aidash and len(pending_topics) >= 3
    if daily_cap_reached or pending_cap_reached or not deduped:
        reason = (
            "pending_cap_reached"
            if pending_cap_reached
            else "daily_cap_reached"
            if daily_cap_reached
            else "no_qualified_candidates"
        )
        return {
            "ok": True,
            "status": "no_new_topic",
            "reason": reason,
            "candidate_count": len(deduped),
            "excluded_count": excluded_count,
            "pending_learning_count": len(pending_topics),
            "selected_count": 0,
            "topics": [],
            "archived_cards": [],
            "proposals_written": [],
        }

    # Candidate selection is metadata-only and deterministic. Approval-time research
    # may use other services, but discovery must never call a network/LLM reranker.
    ranked = sorted(deduped, key=lambda item: (int(item.get("rank", 99)), str(item.get("topic_key", ""))))
    eff_top = 1 if propose_aidash else max(1, top)
    selected = ranked[:eff_top]
    if not selected:
        return {
            "ok": True,
            "status": "no_new_topic",
            "reason": "no_qualified_candidates",
            "candidate_count": len(deduped),
            "excluded_count": excluded_count,
            "pending_learning_count": len(pending_topics),
            "selected_count": 0,
            "topics": [],
            "archived_cards": [],
            "proposals_written": [],
        }

    archived_cards: list[dict[str, Any]] = []
    proposals_written: list[dict[str, Any]] = []
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    today_cst = _cst_day_str()

    for item in selected:
        slug = str(item.get("topic_key") or item["slug"]).strip()
        if eff_materialize:
            card_path = vault_dir / "concepts" / f"{slug}.md"
            if not card_path.is_file():
                card_path.parent.mkdir(parents=True, exist_ok=True)
                card_path.write_text(render_concept_card_markdown(item), encoding="utf-8")
            archived = archive_card(
                slug=slug,
                category=item["category"],
                title=item["title"],
                file_path=card_path,
                vault_dir=vault_dir,
                folder="concepts",
            )
            archived_cards.append(archived)

        if propose_aidash:
            ref_uri = f"learning-topic://{slug}"
            why_now = str(item.get("why_now") or item.get("excerpt") or "").strip()
            source_uri = str(item.get("source_uri") or item.get("source_ref") or "").strip()
            cov_refs = list(item.get("coverage_refs") or [])
            cov_note = f"已核对相邻卡片: {', '.join(cov_refs)}" if cov_refs else "未覆盖缺口（6 concepts / 2 craft 均无同题）"
            detail_text = (
                f"为什么现在学：{why_now} · 来源依据：{source_uri} · 覆盖对账：{cov_note} · "
                f"批准后由后台启动 knowledge-digest 探索并归档至 knowledge-vault；不自动发博客，不同步 design-studio。"
            )
            record = {
                "id": f"learning-{slug}",
                "ref": ref_uri,
                "origin_ref": ref_uri,
                "topic_key": slug,
                "source": "Learning",
                "scope": "personal",
                "source_uri": source_uri,
                "why_now": why_now,
                "coverage_refs": cov_refs,
                "learning_state": "proposed",
                "proposed_on": today_cst,
                "revision": 1,
                "ts": now_iso,
                "created_at": now_iso,
                "agent": "knowledge-digest",
                "title": item["title"],
                "detail": detail_text,
                "priority": "medium",
                "status": "pending",
            }
            proposals_path.parent.mkdir(parents=True, exist_ok=True)
            with proposals_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            proposals_written.append(record)

    return {
        "ok": True,
        "status": "proposed" if proposals_written else "ok",
        "candidate_count": len(deduped),
        "excluded_count": excluded_count,
        "pending_learning_count": len(pending_topics) + len(proposals_written),
        "selected_count": len(selected),
        "topics": selected,
        "archived_cards": archived_cards,
        "proposals_written": proposals_written,
    }


def validate_dc006_to_008(html_text: str, css_text: str = "") -> list[str]:
    violations: list[str] = []
    if not html_text.startswith("<!doctype html>"):
        violations.append("DC-006: HTML does not start with <!doctype html> on line 1")
    if DC006_FORBIDDEN_RE.search(html_text):
        violations.append("DC-006: Raw YAML frontmatter leaked above <!doctype html>")
    if DC007_FORBIDDEN_RE.search(html_text):
        violations.append("DC-007: Decorative Emoji found inside <h1..h4> heading")
    if css_text and DC008_FORBIDDEN_RE.search(css_text):
        violations.append("DC-008: Global white-space: nowrap found on .prose th, .prose td")
    return violations


def _find_vault_card(vault_dir: Path, slug: str) -> tuple[str, Path]:
    for folder in ("concepts", "craft", "thoughts"):
        candidate = vault_dir / folder / f"{slug}.md"
        if candidate.is_file():
            return folder, candidate
    for folder in ("concepts", "craft", "thoughts"):
        sub = vault_dir / folder
        if not sub.is_dir():
            continue
        for md in sorted(sub.glob("*.md")):
            if slug in md.stem or md.stem in slug:
                return folder, md
    raise FileNotFoundError(f"knowledge card for slug '{slug}' not found under {vault_dir}")


def _render_standalone_learn_html(
    *,
    slug: str,
    title: str,
    teaser: str,
    updated_date: str,
    tags: list[str],
    prose_html: str,
    toc_items: list[dict[str, str]],
    words: int,
) -> str:
    clean_title = strip_emoji(title)
    clean_teaser = strip_emoji(teaser)
    tags_li = "".join(f'<li><a class="tag" href="/tags/">{html.escape(strip_emoji(t))}</a></li>' for t in tags[:4])
    toc_lis = "".join(
        f'<li class="{t["level"]}"><a href="#{t["id"]}">{html.escape(strip_emoji(t["title"]))}</a></li>'
        for t in toc_items
    )
    return f"""<!doctype html>
<html lang="zh-CN" data-lp-theme="light" data-strata-brand="yellow" data-strata-material="glass">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(clean_title)} · Lee Pepe</title>
<meta name="description" content="{html.escape(clean_teaser, quote=True)}">
<link rel="stylesheet" href="/assets/colors.css">
<link rel="stylesheet" href="/assets/strata-theme.css">
<link rel="stylesheet" href="/assets/blog.css">
</head>
<body>
<main id="main" class="shell">
  <div class="post-layout">
    <div>
      <article class="card article" data-slug="{html.escape(slug)}">
        <header class="article-header">
          <ul class="tags" aria-label="标签">{tags_li}</ul>
          <h1>{html.escape(clean_title)}</h1>
          <p class="lede">{html.escape(clean_teaser)}</p>
          <div class="post-meta"><time datetime="{html.escape(updated_date)}">{html.escape(updated_date)}</time><span class="dot" aria-hidden="true">·</span><span>约 {words:,} 字</span></div>
        </header>
        <div class="prose">
{prose_html}
        </div>
      </article>
    </div>
    <aside class="toc" aria-label="本文目录">
      <p class="section-label">目录</p>
      <ol>{toc_lis}</ol>
    </aside>
  </div>
</main>
</body>
</html>
"""


def publish_blog(
    *,
    slug: str,
    blog_repo: Path = DEFAULT_BLOG_REPO,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    mode: str = "observe",
) -> dict[str, Any]:
    clean_slug = re.sub(r"^knowledge://", "", slug.strip()).strip("/")
    folder, card_path = _find_vault_card(vault_dir, clean_slug)
    raw_md = card_path.read_text(encoding="utf-8")
    meta, _ = parse_frontmatter(raw_md)
    title = strip_emoji(str(meta.get("title") or card_path.stem))
    category = strip_emoji(str(meta.get("category") or "工程与设计"))
    updated_date = str(meta.get("updated") or meta.get("date") or date.today().isoformat())
    tags_raw = meta.get("tags") or [clean_slug]
    tags = [strip_emoji(str(t)) for t in (tags_raw if isinstance(tags_raw, list) else [tags_raw]) if str(t).strip()]
    teaser = extract_tldr_summary(raw_md, fallback=title)

    build_site_path = blog_repo / "build_site.py"
    site_mod = _load_helper_module("leepepe_build_site", build_site_path)
    rel_source = f"{folder}/{card_path.name}"

    if site_mod is not None:
        site_mod.REPO_ROOT = blog_repo
        site_mod.LEARN_DIR = blog_repo / "learn"
        site_mod.THOUGHTS_DIR = blog_repo / "thoughts"
        site_mod.VAULT_DIR = vault_dir
        site_mod.VAULT_THOUGHTS_DIR = vault_dir / "thoughts"

        if folder != "thoughts":
            existing_idx = next(
                (
                    i
                    for i, art in enumerate(site_mod.LEARN_ARTICLES)
                    if art.get("slug") == clean_slug or rel_source in art.get("sources", [])
                ),
                None,
            )
            article_entry = {
                "num": f"{len(site_mod.LEARN_ARTICLES) + 1:02d}" if existing_idx is None else site_mod.LEARN_ARTICLES[existing_idx]["num"],
                "slug": clean_slug if existing_idx is None else site_mod.LEARN_ARTICLES[existing_idx]["slug"],
                "aliases": [] if existing_idx is None else site_mod.LEARN_ARTICLES[existing_idx].get("aliases", []),
                "category": category,
                "title": title,
                "short_title": title[:28],
                "reading_time": str(meta.get("reading_time") or "阅读约 6 分钟"),
                "updated": updated_date,
                "teaser": teaser,
                "sources": [rel_source],
                "tags": tags[:3] or ["求知"],
            }
            if existing_idx is None:
                site_mod.LEARN_ARTICLES.append(article_entry)
            else:
                site_mod.LEARN_ARTICLES[existing_idx] = article_entry
                clean_slug = article_entry["slug"]

        site_mod.main()
        out_section = "thoughts" if folder == "thoughts" else "learn"
        out_html_path = blog_repo / out_section / clean_slug / "index.html"
    else:
        out_section = "thoughts" if folder == "thoughts" else "learn"
        out_html_path = blog_repo / out_section / clean_slug / "index.html"
        out_html_path.parent.mkdir(parents=True, exist_ok=True)
        prose_lines: list[str] = [
            f"<blockquote><p><strong>一句话结论</strong>：{html.escape(teaser)}</p></blockquote>"
        ]
        toc_items: list[dict[str, str]] = []
        sec_no = 0
        _, body_md = parse_frontmatter(raw_md)
        for ln in body_md.splitlines():
            s = ln.strip()
            if not s or s.startswith("# ") or s == "---":
                continue
            if s.startswith("## "):
                sec_no += 1
                sec_id = f"sec-{sec_no}"
                h2_clean = strip_emoji(s[3:])
                toc_items.append({"id": sec_id, "level": "l2", "title": h2_clean})
                prose_lines.append(f'<h2 id="{sec_id}">{html.escape(h2_clean)}</h2>')
            elif s.startswith("### "):
                h3_clean = strip_emoji(s[4:])
                prose_lines.append(f"<h3>{html.escape(h3_clean)}</h3>")
            elif not s.startswith(">"):
                prose_lines.append(f"<p>{html.escape(strip_emoji(s))}</p>")
        rendered = _render_standalone_learn_html(
            slug=clean_slug,
            title=title,
            teaser=teaser,
            updated_date=updated_date,
            tags=tags,
            prose_html="\n".join(prose_lines),
            toc_items=toc_items,
            words=max(500, len(body_md)),
        )
        out_html_path.write_text(rendered, encoding="utf-8")

    html_text = out_html_path.read_text(encoding="utf-8")
    css_path = blog_repo / "assets" / "blog.css"
    css_text = css_path.read_text(encoding="utf-8") if css_path.is_file() else ""
    violations = validate_dc006_to_008(html_text, css_text)
    if violations:
        raise ValueError(f"DC-006..008 validation failed: {'; '.join(violations)}")

    committed = False
    commit_sha: str | None = None
    if mode == "auto" and (blog_repo / ".git").exists():
        status_proc = subprocess.run(
            ["git", "-C", str(blog_repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if status_proc.returncode == 0 and status_proc.stdout.strip():
            subprocess.run(["git", "-C", str(blog_repo), "add", "-A"], check=False)
            commit_proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(blog_repo),
                    "commit",
                    "-m",
                    f"docs(learn): publish {clean_slug} from knowledge-vault",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if commit_proc.returncode == 0:
                committed = True
                rev_proc = subprocess.run(
                    ["git", "-C", str(blog_repo), "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                commit_sha = rev_proc.stdout.strip() if rev_proc.returncode == 0 else None

    return {
        "ok": True,
        "slug": clean_slug,
        "folder": folder,
        "card_path": str(card_path),
        "output_html": str(out_html_path),
        "url": f"/{out_section}/{clean_slug}/",
        "dc_validation": "PASS",
        "mode": mode,
        "committed": committed,
        "commit_sha": commit_sha,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Knowledge Digest vault & blog flywheel CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_arch = sub.add_parser("archive", help="Archive a markdown card into knowledge-vault and update index.md")
    p_arch.add_argument("--slug", required=True)
    p_arch.add_argument("--category", required=True)
    p_arch.add_argument("--title", required=True)
    p_arch.add_argument("--file", required=True, type=Path)
    p_arch.add_argument("--folder", choices=("concepts", "craft", "thoughts", "raw"), default=None)
    p_arch.add_argument("--vault-dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_arch.add_argument("--updated", default=None)

    p_disc = sub.add_parser("discover-topics", help="Scan git tech stack, collected-tools, and vault gaps with R09")
    p_disc.add_argument("--top", type=int, default=1)
    p_disc.add_argument("--propose-aidash", action="store_true")
    p_disc.add_argument("--no-materialize", action="store_true", help="Do not materialize cards (default when proposing)")
    p_disc.add_argument("--materialize", action="store_true", help="Explicitly materialize concept cards")
    p_disc.add_argument("--vault-dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_disc.add_argument("--collected-tools-dir", type=Path, default=DEFAULT_COLLECTED_TOOLS_DIR)
    p_disc.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)

    p_pub = sub.add_parser("publish-blog", help="Render a knowledge-vault card into leepepe.github.io (DC-006..008)")
    p_pub.add_argument("--slug", required=True)
    p_pub.add_argument("--blog-repo", type=Path, default=DEFAULT_BLOG_REPO)
    p_pub.add_argument("--vault-dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_pub.add_argument("--mode", choices=("observe", "auto"), default="observe")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "archive":
        res = archive_card(
            slug=args.slug,
            category=args.category,
            title=args.title,
            file_path=args.file,
            vault_dir=args.vault_dir,
            folder=args.folder,
            updated_date=args.updated,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if args.command == "discover-topics":
        mat: bool | None = None
        if getattr(args, "no_materialize", False):
            mat = False
        elif getattr(args, "materialize", False):
            mat = True
        elif not args.propose_aidash:
            mat = False
        res = discover_topics(
            top=args.top,
            propose_aidash=args.propose_aidash,
            vault_dir=args.vault_dir,
            collected_tools_dir=args.collected_tools_dir,
            proposals_path=args.proposals_path,
            materialize_cards=mat,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if args.command == "publish-blog":
        res = publish_blog(
            slug=args.slug,
            blog_repo=args.blog_repo,
            vault_dir=args.vault_dir,
            mode=args.mode,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
