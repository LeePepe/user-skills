#!/usr/bin/env python3
"""Zero-Token knowledge-vault archival, R09 topic discovery, and DC-006..008 blog publishing."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILLS_ROOT = SKILL_DIR.parent
_CANONICAL_FIXTURE_DOC: dict[str, Any] = {
    "schema_version": "personal-topic-ledger/v1",
    "policy": {
        "max_new_per_day": 1,
        "max_pending_proposals": 3,
        "cooldown_days_on_dismiss": 30,
        "snooze_days_default": 7,
        "timezone": "Asia/Shanghai",
        "scope": "personal",
        "actionable_domain": "个人成长与思考",
        "require_no_materialize_before_approval": True,
        "forbid_auto_design_studio_sync": True,
        "forbid_auto_blog_publish_on_star": True,
    },
    "vault_coverage_inventory": [
        {"path": "knowledge-vault/concepts/appkit-uikit-27-modernization.md", "folder": "concepts", "title": "macOS 27 AppKit 与 iOS 27 UIKit 现代化：自由缩放窗口、同心圆角与手势革命", "derived_from": ["knowledge-vault/raw/wwdc25-26-liquid-glass-and-motion-transcripts.md"], "derived_cards": []},
        {"path": "knowledge-vault/concepts/liquid-glass-optics-and-swiftui-26-27.md", "folder": "concepts", "title": "Liquid Glass 光学第一性原理与 SwiftUI 26→27 实战破案", "derived_from": ["knowledge-vault/raw/wwdc25-26-liquid-glass-and-motion-transcripts.md"], "derived_cards": []},
        {"path": "knowledge-vault/concepts/swiftui-27-scrolling-and-drag-drop-motion.md", "folder": "concepts", "title": "SwiftUI 27 高性能滚动与多元素拖拽：LazyStack 帧预取与纸牌级重排", "derived_from": ["knowledge-vault/raw/wwdc25-26-liquid-glass-and-motion-transcripts.md"], "derived_cards": []},
        {"path": "knowledge-vault/concepts/swiftui-27-shader-and-motion-pipeline.md", "folder": "concepts", "title": "SwiftUI 27 高级图形与动效管线：Metal Shader 扭曲、时间流与图钉对齐", "derived_from": ["knowledge-vault/raw/wwdc25-26-liquid-glass-and-motion-transcripts.md"], "derived_cards": []},
        {"path": "knowledge-vault/concepts/wifi-invisible-light.md", "folder": "concepts", "title": "如果能看见 WiFi：看不见的光与室内信号破案", "derived_from": ["knowledge-vault/raw/wifi-video-script-breakdown.md"], "derived_cards": []},
        {"path": "knowledge-vault/concepts/wwdc27-liquid-glass-and-motion-map.md", "folder": "concepts", "title": "WWDC25/26 (OS 26 → OS 27) Liquid Glass 与动效 8 节课全景导读地图", "derived_from": ["knowledge-vault/raw/wwdc25-26-liquid-glass-and-motion-transcripts.md"], "derived_cards": []},
        {"path": "knowledge-vault/craft/science-video-invisible-to-visible.md", "folder": "craft", "title": "科普视频拆解：把不可见变成可见的痛点叙事法", "derived_from": ["knowledge-vault/raw/wifi-video-script-breakdown.md"], "derived_cards": []},
        {"path": "knowledge-vault/craft/wwdc-pipeline-and-optical-metaphor-craft.md", "folder": "craft", "title": "WWDC 视觉化与架构讲法拆解：水管数据流、图钉几何与贴纸叙事法", "derived_from": ["knowledge-vault/raw/wwdc25-26-liquid-glass-and-motion-transcripts.md"], "derived_cards": []},
        {"path": "knowledge-vault/thoughts/study-switch-long-press-ac.md", "folder": "thoughts", "title": "为什么我给书房按键加了「长按开空调」", "derived_from": [], "derived_cards": []},
        {"path": "knowledge-vault/raw/wifi-video-script-breakdown.md", "folder": "raw", "title": "「如果能看见 WiFi」视频脚本流程与制作技巧总结", "derived_from": [], "derived_cards": ["knowledge-vault/concepts/wifi-invisible-light.md", "knowledge-vault/craft/science-video-invisible-to-visible.md"]},
        {"path": "knowledge-vault/raw/wwdc25-26-liquid-glass-and-motion-transcripts.md", "folder": "raw", "title": "WWDC25/26 Liquid Glass 与 OS 27 动效 8 节官方课程 Transcript 与关键代码汇编", "derived_from": [], "derived_cards": ["knowledge-vault/concepts/appkit-uikit-27-modernization.md", "knowledge-vault/concepts/liquid-glass-optics-and-swiftui-26-27.md", "knowledge-vault/concepts/swiftui-27-scrolling-and-drag-drop-motion.md", "knowledge-vault/concepts/swiftui-27-shader-and-motion-pipeline.md", "knowledge-vault/concepts/wwdc27-liquid-glass-and-motion-map.md", "knowledge-vault/craft/wwdc-pipeline-and-optical-metaphor-craft.md"]},
    ],
}
DEFAULT_VAULT_DIR = Path.home() / "Development" / "Personal" / "knowledge-vault"
DEFAULT_COLLECTED_TOOLS_DIR = Path.home() / "Development" / "Personal" / "collected-tools"
DEFAULT_BLOG_REPO = Path.home() / "Development" / "leepepe.github.io"
DEFAULT_PROPOSALS_PATH = (
    Path.home() / "Development" / "AIDash" / "aidata" / "L5_apps" / "digest" / "state" / "proposals.jsonl"
)
DEFAULT_STATE_PATH = (
    Path.home() / "Development" / "AIDash" / "aidata" / "L5_apps" / "digest" / "state" / "learning-topics-state.json"
)
DEFAULT_TOPIC_LEDGER_PATH = DEFAULT_VAULT_DIR / "topic-ledger.json"
DEFAULT_GIT_ROOTS = (
    Path.home() / "Development" / "user-skills",
    Path.home() / "Development" / "AIDash",
    Path.home() / "Development" / "VoxPocket",
    Path.home() / "Development" / "VitalStride",
)
CARD_TEMPLATES_PATH = SKILL_DIR / "references" / "card-templates.json"
_CST_TZ = timezone(timedelta(hours=8))

DAILY_NEW_TOPIC_QUOTA = 1
MAX_PENDING_LEARNING_PROPOSALS = 3
DISMISS_COOLDOWN_DAYS = 30
SNOOZE_COOLDOWN_DAYS = 7

_INTEGRATION_EVIDENCE_RE = re.compile(
    r"(?:实际使用|已接入|集成记录|統合済み|已集成|used\s+in|integrated\s+(?:into|with)|entry\s+a\s*\(a1\.\.a4\))",
    re.IGNORECASE,
)

# Canonical 19 collected-tools source entities reconciliation map from Personal spec
CANONICAL_COLLECTED_TOOLS_RECONCILIATION: dict[str, dict[str, Any]] = {
    "hallmark": {
        "source_id": "hallmark",
        "source_uri": "https://github.com/Nutlope/hallmark",
        "topic_key": "hallmark-anti-slop-anatomy",
        "aliases": ["hallmark", "hallmark-anti-slop-anatomy", "hallmark-core-patterns"],
        "coverage_refs": ["collected-tools/hallmark.md"],
        "default_state": "covered_if_refs_present",
        "title": "Hallmark：反 AI 模板感解剖设计与多视口几何门禁",
        "question": "Hallmark 如何通过 50 选 3 主题抽选、访问者目的（Job）解剖设计与真实浏览器 8 视口几何门禁拦截 AI 模板感？",
        "why_now": "collected-tools/hallmark.md & repos/hallmark",
        "category": "工程与设计",
        "heuristic_direct": 0.88,
        "heuristic_tier": 2.6,
    },
    "apple-design-skill": {
        "source_id": "apple-design-skill",
        "source_uri": "https://github.com/dickwu/apple-design-skill",
        "topic_key": "apple-design-skill-hig-liquid-glass",
        "aliases": [
            "apple-design-skill",
            "apple-design-skill-core-patterns",
            "apple-design-skill-hig-liquid-glass",
        ],
        "coverage_refs": [
            "concepts/wwdc27-liquid-glass-and-motion-map.md",
            "concepts/liquid-glass-optics-and-swiftui-26-27.md",
            "concepts/appkit-uikit-27-modernization.md",
        ],
        "default_state": "covered_if_refs_present",
        "rank": 0,
        "title": "Apple Design Skill 核心架构模式与实战拆解",
        "question": "Apple Design Skill 如何对齐 HIG 与 Liquid Glass 现代化规范？",
        "why_now": "repos/apple-design-skill（已被 WWDC26/27 Liquid Glass 与 AppKit/UIKit 概念卡覆盖）",
        "category": "工程与设计",
        "heuristic_direct": 0.80,
        "heuristic_tier": 2.3,
    },
    "awesome-ios-design-md": {
        "source_id": "awesome-ios-design-md",
        "source_uri": "https://github.com/Meliwat/awesome-ios-design-md",
        "topic_key": "awesome-ios-design-md-tokens",
        "aliases": [
            "awesome-ios-design-md",
            "awesome-ios-design-md-core-patterns",
            "awesome-ios-design-md-tokens",
        ],
        "coverage_refs": ["concepts/liquid-glass-optics-and-swiftui-26-27.md"],
        "default_state": "covered_if_refs_present",
        "rank": 0,
        "title": "awesome-ios-design-md 核心架构模式与实战拆解",
        "question": "Awesome iOS DESIGN.md 如何组织 200 款 iOS App 色板与排版令牌字典？",
        "why_now": "repos/awesome-ios-design-md（作为按需检索字典使用，无独立未探索原理题）",
        "category": "工程与设计",
        "heuristic_direct": 0.78,
        "heuristic_tier": 2.2,
    },
    "awesome-web-design-md": {
        "source_id": "awesome-web-design-md",
        "source_uri": "https://github.com/VoltAgent/awesome-design-md",
        "topic_key": "awesome-web-design-md-tokens",
        "aliases": [
            "awesome-web-design-md",
            "awesome-web-design-md-core-patterns",
            "awesome-web-design-md-tokens",
        ],
        "coverage_refs": ["collected-tools/hallmark.md", "concepts/liquid-glass-optics-and-swiftui-26-27.md"],
        "default_state": "covered_if_refs_present",
        "rank": 0,
        "title": "awesome-web-design-md 核心架构模式与实战拆解",
        "question": "Awesome Web DESIGN.md 如何组织 Web 站点样式令牌字典？",
        "why_now": "repos/awesome-web-design-md（作为按需检索字典使用，无独立未探索原理题）",
        "category": "工程与设计",
        "heuristic_direct": 0.78,
        "heuristic_tier": 2.2,
    },
    "shipswift": {
        "source_id": "shipswift",
        "source_uri": "https://github.com/signerlabs/ShipSwift",
        "topic_key": "shipswift-animation-metal-components",
        "aliases": [
            "shipswift",
            "shipswift-core-patterns",
            "shipswift-animation-metal-components",
        ],
        "coverage_refs": [
            "concepts/swiftui-27-shader-and-motion-pipeline.md",
            "concepts/swiftui-27-scrolling-and-drag-drop-motion.md",
        ],
        "default_state": "covered_if_refs_present",
        "rank": 0,
        "title": "shipswift 核心架构模式与实战拆解",
        "question": "ShipSwift 的 SWAnimation/SWMetal/SWChart 组件管线如何工作？",
        "why_now": "repos/shipswift（动效与 Shader 管线已被 WWDC26/27 概念卡覆盖）",
        "category": "工程与设计",
        "heuristic_direct": 0.80,
        "heuristic_tier": 2.3,
    },
    "twostraws-swiftui-skill": {
        "source_id": "twostraws-swiftui-skill",
        "source_uri": "https://github.com/twostraws/SwiftUI-Agent-Skill",
        "topic_key": "twostraws-swiftui-pro-patterns",
        "aliases": [
            "twostraws-swiftui-skill",
            "twostraws-swiftui-skill-core-patterns",
            "twostraws-swiftui-pro-patterns",
        ],
        "coverage_refs": [
            "concepts/liquid-glass-optics-and-swiftui-26-27.md",
            "concepts/swiftui-27-scrolling-and-drag-drop-motion.md",
        ],
        "default_state": "covered_if_refs_present",
        "rank": 0,
        "title": "twostraws-swiftui-skill 核心架构模式与实战拆解",
        "question": "Twostraws SwiftUI Agent Skill 如何规范现代 SwiftUI API 与可访问性？",
        "why_now": "repos/twostraws-swiftui-skill（已被现有 SwiftUI 26/27 卡片覆盖）",
        "category": "工程与设计",
        "heuristic_direct": 0.80,
        "heuristic_tier": 2.3,
    },
    "awesome-cjk-design-md": {
        "source_id": "awesome-cjk-design-md",
        "source_uri": "https://github.com/kzhrknt/awesome-design-md-jp",
        "topic_key": "jp-mixed-script-line-breaking",
        "aliases": ["awesome-cjk-design-md", "awesome-cjk-design-md-core-patterns", "jp-mixed-script-line-breaking"],
        "coverage_refs": [],
        "default_state": "queued",
        "rank_order": 1,
        "title": "日文/CJK 与拉丁文混排中的字体回退与行首行末禁则（Kinsoku Shori）",
        "question": "日文/CJK 与拉丁文混排中，字体回退与行首行末禁则（Kinsoku Shori）如何共同避免断行错误？",
        "why_now": "repos/awesome-cjk-design-md/README.md:17-29（仅挂载路径，未拆解 CJK 混排禁则原理）",
        "category": "工程与设计",
        "heuristic_direct": 0.91,
        "heuristic_tier": 2.7,
    },
    "bama": {
        "source_id": "bama",
        "source_uri": "https://github.com/daveebbelaar/bama",
        "topic_key": "bama-agent-cli-story-verification",
        "aliases": ["bama", "bama-core-patterns", "bama-agent-cli-story-verification"],
        "coverage_refs": [],
        "default_state": "queued",
        "rank_order": 2,
        "title": "BAMA：把 macOS 页面清单转为免 Xcode GUI、可由 Agent CLI 逐步验收的用户故事与架构分层",
        "question": "BAMA 怎样把 macOS 页面清单转为免 Xcode GUI、可由 Agent CLI 逐步验收的用户故事与架构分层？",
        "why_now": "repos/bama/README.md:42-65 & skills/code/{agent-cli,improve-architecture}/SKILL.md",
        "category": "工程与设计",
        "heuristic_direct": 0.89,
        "heuristic_tier": 2.6,
    },
    "genjutsu": {
        "source_id": "genjutsu",
        "source_uri": "https://github.com/AThevon/genjutsu",
        "topic_key": "genjutsu-three-tier-choreography-gate",
        "aliases": ["genjutsu", "genjutsu-core-patterns", "genjutsu-three-tier-choreography-gate"],
        "coverage_refs": ["concepts/swiftui-27-shader-and-motion-pipeline.md"],
        "default_state": "queued",
        "rank_order": 3,
        "title": "Genjutsu：通过 uchiha / kuchiyose / bunshin 区分单次交互、整套视觉与整站编排的审批粒度与评测门禁",
        "question": "Genjutsu 如何通过 uchiha / kuchiyose / bunshin 区分单次交互、整套视觉与整站编排的审批粒度与评测门禁？",
        "why_now": "repos/genjutsu/README.md:57-65 & PLATFORM-CONTRACT.md",
        "category": "工程与设计",
        "heuristic_direct": 0.88,
        "heuristic_tier": 2.6,
    },
    "avdlee-swiftui-skill": {
        "source_id": "avdlee-swiftui-skill",
        "source_uri": "https://github.com/AvdLee/SwiftUI-Agent-Skill",
        "topic_key": "swiftui-26-api-migration-scan-manifest",
        "aliases": ["avdlee-swiftui-skill", "avdlee-swiftui-skill-core-patterns", "swiftui-26-api-migration-scan-manifest"],
        "coverage_refs": ["concepts/liquid-glass-optics-and-swiftui-26-27.md"],
        "default_state": "queued",
        "rank_order": 4,
        "title": "update-swiftui-apis：用确定性 scan-manifest 批量扫描并迁移废弃 SwiftUI API",
        "question": "update-swiftui-apis 如何用确定性 scan-manifest 批量扫描并迁移废弃 SwiftUI API 而不引入状态回归？",
        "why_now": "repos/avdlee-swiftui-skill/.agents/skills/update-swiftui-apis/references/scan-manifest.md",
        "category": "工程与设计",
        "heuristic_direct": 0.87,
        "heuristic_tier": 2.5,
    },
    "dimillian-skills": {
        "source_id": "dimillian-skills",
        "source_uri": "https://github.com/Dimillian/Skills",
        "topic_key": "dimillian-spm-app-packaging-and-bug-swarm",
        "aliases": ["dimillian-skills", "dimillian-skills-core-patterns", "dimillian-spm-app-packaging-and-bug-swarm"],
        "coverage_refs": [
            "concepts/liquid-glass-optics-and-swiftui-26-27.md",
            "concepts/swiftui-27-scrolling-and-drag-drop-motion.md",
        ],
        "default_state": "queued",
        "rank_order": 5,
        "title": "纯 SwiftPM 无 .xcodeproj 打包签名 macOS 桌面应用与 bug-hunt-swarm 多角色排障",
        "question": "如何用纯 SwiftPM 无 .xcodeproj 打包签名 macOS MenuBar/桌面应用，并配合 bug-hunt-swarm 做多角色缺陷围剿？",
        "why_now": "repos/dimillian-skills/{macos-spm-app-packaging,bug-hunt-swarm}/SKILL.md",
        "category": "工程与设计",
        "heuristic_direct": 0.86,
        "heuristic_tier": 2.5,
    },
    "claude-code-apple-skills": {
        "source_id": "claude-code-apple-skills",
        "source_uri": "https://github.com/rshankras/claude-code-apple-skills",
        "topic_key": "apple-game-feel-and-store-rejection-gates",
        "aliases": ["claude-code-apple-skills", "claude-code-apple-skills-core-patterns", "apple-game-feel-and-store-rejection-gates"],
        "coverage_refs": [
            "concepts/liquid-glass-optics-and-swiftui-26-27.md",
            "concepts/swiftui-27-shader-and-motion-pipeline.md",
        ],
        "default_state": "queued",
        "rank_order": 6,
        "title": "量化 Apple 原生应用的 game-feel 触觉/微反馈物理参数与 App Store 审核预拦截",
        "question": "如何量化 Apple 原生应用的 game-feel 触觉/微反馈物理参数，并用 rejection-handler 提前拦截 App Store 审核被拒风险？",
        "why_now": "repos/claude-code-apple-skills/skills/{design/game-feel,app-store/rejection-handler}/SKILL.md",
        "category": "工程与设计",
        "heuristic_direct": 0.85,
        "heuristic_tier": 2.5,
    },
    "emilkowalski-skills": {
        "source_id": "emilkowalski-skills",
        "source_uri": "https://github.com/emilkowalski/skills",
        "topic_key": "emil-spring-choreography-and-interruptibility",
        "aliases": ["emilkowalski-skills", "emilkowalski-skills-core-patterns", "emil-spring-choreography-and-interruptibility"],
        "coverage_refs": ["concepts/swiftui-27-shader-and-motion-pipeline.md"],
        "default_state": "queued",
        "rank_order": 7,
        "title": "Emil Kowalski 动效工程法则：主动移除动画（remove-animations）与可打断弹簧手势速度连续",
        "question": "Emil Kowalski 的动效工程法则：何时该主动移除动画（remove-animations），以及可打断弹簧如何维持手势速度连续？",
        "why_now": "repos/emilkowalski-skills/skills/{remove-animations,animation-performance,choreograph}/SKILL.md",
        "category": "工程与设计",
        "heuristic_direct": 0.85,
        "heuristic_tier": 2.4,
    },
    "impeccable": {
        "source_id": "impeccable",
        "source_uri": "https://github.com/pbakaus/impeccable",
        "topic_key": "impeccable-mode-operate-vs-persuade-critique",
        "aliases": ["impeccable", "impeccable-core-patterns", "impeccable-mode-operate-vs-persuade-critique"],
        "coverage_refs": [],
        "default_state": "queued",
        "rank_order": 8,
        "title": "Impeccable：用正交审查标尺区分高密度操作台（mode-operate）与叙事说服页（mode-persuade）",
        "question": "Impeccable 如何用正交审查标尺区分高密度操作台（mode-operate）与叙事说服页（mode-persuade）的排版与视觉克制边界？",
        "why_now": "repos/impeccable/.agents/skills/impeccable/reference/{mode-operate,mode-persuade,critique}.md",
        "category": "工程与设计",
        "heuristic_direct": 0.84,
        "heuristic_tier": 2.4,
    },
    "taste-skill": {
        "source_id": "taste-skill",
        "source_uri": "https://github.com/Leonxlnx/taste-skill",
        "topic_key": "taste-skill-stop-hook-and-bento-anti-slop",
        "aliases": ["taste-skill", "taste-skill-core-patterns", "taste-skill-stop-hook-and-bento-anti-slop"],
        "coverage_refs": ["collected-tools/hallmark.md"],
        "default_state": "queued",
        "rank_order": 9,
        "title": "Taste Skill：通过 Stop-hook 自审回路与 Bento 卡片密度预算拦截前端 AI 模板感",
        "question": "Taste Skill 如何通过 Stop-hook 自审回路与 Bento 卡片密度预算拦截前端 AI 模板感？",
        "why_now": "repos/taste-skill/skills/taste-skill/SKILL.md",
        "category": "工程与设计",
        "heuristic_direct": 0.83,
        "heuristic_tier": 2.4,
    },
    "ui-ux-pro-max-skill": {
        "source_id": "ui-ux-pro-max-skill",
        "source_uri": "https://github.com/nextlevelbuilder/ui-ux-pro-max-skill",
        "topic_key": "uupm-csv-faceted-design-retrieval",
        "aliases": ["ui-ux-pro-max-skill", "ui-ux-pro-max-skill-core-patterns", "uupm-csv-faceted-design-retrieval"],
        "coverage_refs": [],
        "default_state": "queued",
        "rank_order": 10,
        "title": "UI UX Pro Max：用轻量 CSV 分面索引与 BM25 脚本实现跨技术栈设计检索",
        "question": "UI UX Pro Max 如何用轻量 CSV 分面索引与 BM25 脚本实现跨技术栈色板、字体与图表类型的确定性检索？",
        "why_now": "repos/ui-ux-pro-max-skill/src/ui-ux-pro-max/",
        "category": "工程与设计",
        "heuristic_direct": 0.82,
        "heuristic_tier": 2.3,
    },
    "vercel-agent-skills": {
        "source_id": "vercel-agent-skills",
        "source_uri": "https://github.com/vercel-labs/agent-skills",
        "topic_key": "vercel-composition-patterns-and-view-transitions",
        "aliases": ["vercel-agent-skills", "vercel-agent-skills-core-patterns", "vercel-composition-patterns-and-view-transitions"],
        "coverage_refs": [],
        "default_state": "queued",
        "rank_order": 11,
        "title": "Vercel Agent Skills：复合组件状态隔离（Composition Patterns）与声明式 React View Transitions",
        "question": "Vercel Agent Skills 中的复合组件状态隔离（Composition Patterns）与声明式 React View Transitions 如何消除布尔 Props 爆炸？",
        "why_now": "repos/vercel-agent-skills/skills/{composition-patterns,react-view-transitions}/SKILL.md",
        "category": "工程与设计",
        "heuristic_direct": 0.82,
        "heuristic_tier": 2.3,
    },
    "vibecoded-design-tells": {
        "source_id": "vibecoded-design-tells",
        "source_uri": "https://github.com/JCarterJohnson/vibecoded-design-tells",
        "topic_key": "vibecoded-reddit-empirical-slop-tells",
        "aliases": ["vibecoded-design-tells", "vibecoded-design-tells-core-patterns", "vibecoded-reddit-empirical-slop-tells"],
        "coverage_refs": ["collected-tools/hallmark.md"],
        "default_state": "queued",
        "rank_order": 12,
        "title": "Vibe-Coded Design Tells：基于 Reddit 真实负评统计的 UI、文案与代码三大类 AI 破绽反制清单",
        "question": "基于 Reddit 真实负评样本统计的 UI、文案与代码三大类“Vibe-Coded AI 破绽”有哪些高频特征与反制清单？",
        "why_now": "repos/vibecoded-design-tells/skill/references/{tells,choosing-a-look}.md",
        "category": "工程与设计",
        "heuristic_direct": 0.81,
        "heuristic_tier": 2.3,
    },
    "expo-skills": {
        "source_id": "expo-skills",
        "source_uri": "https://github.com/expo/skills",
        "topic_key": "expo-visual-grader-and-web-to-native-false-friends",
        "aliases": ["expo-skills", "expo-skills-core-patterns", "expo-visual-grader-and-web-to-native-false-friends"],
        "coverage_refs": [],
        "default_state": "queued",
        "rank_order": 13,
        "title": "Expo Skills：visual-grader 评分量表与 web-to-native/false-friends 原生布局防呆",
        "question": "expo-skill-eval 的 visual-grader 评分量表与 web-to-native/false-friends 如何系统性拦截把 Web CSS 习惯生搬到原生端的布局缺陷？",
        "why_now": "repos/expo-skills/.claude/skills/expo-skill-eval/agents/visual-grader.md",
        "category": "工程与设计",
        "heuristic_direct": 0.80,
        "heuristic_tier": 2.3,
    },
}

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
    coverage = _inspect_vault_coverage(vault_dir)
    return coverage["corpus_text"]


def _normalize_rel_vault_ref(raw_ref: str) -> str:
    s = str(raw_ref or "").strip().strip("`'\"")
    while s.startswith("../") or s.startswith("./"):
        s = s[3:] if s.startswith("../") else s[2:]
    s = s.lstrip("/").lower()
    if s.startswith("knowledge-vault/"):
        s = s.removeprefix("knowledge-vault/")
    return s


def _inspect_vault_coverage(vault_dir: Path) -> dict[str, Any]:
    parts: list[str] = []
    rel_paths: set[str] = set()
    stems: set[str] = set()
    topic_keys: set[str] = set()
    aliases: set[str] = set()
    covered_raw_files: set[str] = set()
    uncovered_raw_items: list[dict[str, Any]] = []

    index_path = vault_dir / "index.md"
    if index_path.is_file():
        idx_text = index_path.read_text(encoding="utf-8", errors="ignore")
        parts.append(idx_text)
        for m in re.finditer(r"\(((?:concepts|craft|thoughts|raw)/[^)]+\.md)\)", idx_text):
            rel_paths.add(_normalize_rel_vault_ref(m.group(1)))

    for sub in ("concepts", "craft", "thoughts", "raw"):
        sub_dir = vault_dir / sub
        if not sub_dir.is_dir():
            continue
        for md in sorted(sub_dir.glob("*.md")):
            rel_p = f"{sub}/{md.name}".lower()
            rel_paths.add(rel_p)
            stem_l = md.stem.lower()
            stems.add(stem_l)
            text = md.read_text(encoding="utf-8", errors="ignore")
            meta, body = parse_frontmatter(text)
            parts.append(stem_l)
            parts.append(text[:800])

            tk = str(meta.get("topic_key") or "").strip().lower()
            if tk:
                topic_keys.add(tk)
            for al_key in ("aliases", "tags"):
                raw_al = meta.get(al_key)
                if isinstance(raw_al, list):
                    for item in raw_al:
                        if str(item).strip():
                            aliases.add(str(item).strip().lower())
                elif isinstance(raw_al, str) and raw_al.strip():
                    aliases.add(raw_al.strip().lower())

            for cov_key in ("coverage_refs",):
                raw_cov = meta.get(cov_key)
                if isinstance(raw_cov, list):
                    for item in raw_cov:
                        if str(item).strip():
                            rel_paths.add(_normalize_rel_vault_ref(str(item)))

            if sub == "raw":
                derived_ptrs: list[str] = []
                for d_key in ("derived_concept", "derived_craft", "derived_cards", "derived"):
                    d_val = meta.get(d_key)
                    if isinstance(d_val, list):
                        derived_ptrs.extend(str(x) for x in d_val if str(x).strip())
                    elif isinstance(d_val, str) and d_val.strip():
                        derived_ptrs.append(d_val.strip())
                for m_ptr in re.finditer(r"\(\.\./((?:concepts|craft|thoughts)/[^)]+\.md)\)", body):
                    derived_ptrs.append(m_ptr.group(1))
                norm_ptrs = [_normalize_rel_vault_ref(p) for p in derived_ptrs if _normalize_rel_vault_ref(p)]
                for np in norm_ptrs:
                    rel_paths.add(np)
                    stems.add(Path(np).stem.lower())
                if norm_ptrs:
                    covered_raw_files.add(rel_p)
                else:
                    first_h1 = next(
                        (ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")),
                        str(meta.get("title") or md.stem),
                    )
                    summary_lines = [
                        ln.strip()
                        for ln in body.splitlines()
                        if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith("---")
                    ]
                    uncovered_raw_items.append({
                        "slug": stem_l,
                        "topic_key": tk or stem_l,
                        "title": first_h1,
                        "category": str(meta.get("category") or "工程与设计"),
                        "source_kind": "vault_raw",
                        "source_ref": str(md),
                        "source_uri": f"vault://{rel_p}",
                        "aliases": [stem_l],
                        "coverage_refs": [],
                        "excerpt": " ".join(summary_lines[:2])[:220] or f"Raw material {md.name}",
                        "heuristic_direct": 0.86,
                        "heuristic_tier": 2.5,
                    })

    return {
        "corpus_text": "\n".join(parts).lower(),
        "rel_paths": rel_paths,
        "stems": stems,
        "topic_keys": topic_keys,
        "aliases": aliases,
        "covered_raw_files": covered_raw_files,
        "uncovered_raw_items": uncovered_raw_items,
    }


def _load_topic_ledger_document(
    vault_dir: Path,
    topic_ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Load the full topic-ledger.json document preserving schema_version, policy, sources, inventory, and topics."""
    path = topic_ledger_path or (vault_dir / "topic-ledger.json")
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {"topics": doc if isinstance(doc, list) else []}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_topic_ledger(
    vault_dir: Path,
    topic_ledger_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Load topic-ledger.json if present and index entries by topic_key (plus lookup aliases in a secondary pass)."""
    doc = _load_topic_ledger_document(vault_dir, topic_ledger_path=topic_ledger_path)
    by_key: dict[str, dict[str, Any]] = {}
    if not doc:
        return by_key
    raw_items: list[dict[str, Any]] = []
    for k in ("topics", "entities", "items", "entries"):
        val = doc.get(k)
        if isinstance(val, list):
            raw_items.extend(x for x in val if isinstance(x, dict))
        elif isinstance(val, dict):
            for sub_k, sub_v in val.items():
                if isinstance(sub_v, dict):
                    item = dict(sub_v)
                    item.setdefault("topic_key", sub_k)
                    raw_items.append(item)

    for item in raw_items:
        for field in ("topic_key", "source_id", "slug", "id"):
            val = str(item.get(field) or "").strip()
            if val:
                by_key[val] = dict(item)
                by_key[val.lower()] = dict(item)
        for al in item.get("aliases") or []:
            al_s = str(al or "").strip()
            if al_s:
                by_key.setdefault(al_s, dict(item))
                by_key.setdefault(al_s.lower(), dict(item))
    return by_key


def _save_topic_ledger(
    vault_dir: Path,
    entries: dict[str, dict[str, Any]] | list[dict[str, Any]],
    *,
    topic_ledger_path: Path | None = None,
) -> Path:
    """Save topic-ledger.json while preserving schema_version, policy, sources, vault_coverage_inventory, and unknown topic fields."""
    ledger_path = topic_ledger_path or (vault_dir / "topic-ledger.json")
    existing_doc = _load_topic_ledger_document(vault_dir, topic_ledger_path=ledger_path)

    existing_topics_raw = existing_doc.get("topics", []) if isinstance(existing_doc, dict) else []
    existing_by_tk: dict[str, dict[str, Any]] = {}
    ordered_keys: list[str] = []
    if isinstance(existing_topics_raw, list):
        for item in existing_topics_raw:
            if isinstance(item, dict) and item.get("topic_key"):
                tk = str(item["topic_key"])
                existing_by_tk[tk] = dict(item)
                if tk not in ordered_keys:
                    ordered_keys.append(tk)
    elif isinstance(existing_topics_raw, dict):
        for tk, item in existing_topics_raw.items():
            if isinstance(item, dict):
                row = dict(item)
                row.setdefault("topic_key", str(tk))
                existing_by_tk[str(tk)] = row
                if str(tk) not in ordered_keys:
                    ordered_keys.append(str(tk))

    incoming_list: list[dict[str, Any]] = []
    if isinstance(entries, dict):
        seen_in: set[str] = set()
        for k, v in entries.items():
            if not isinstance(v, dict):
                continue
            tk = str(v.get("topic_key") or k).strip()
            if not tk or tk in seen_in:
                continue
            # Skip secondary alias index keys when entries came from _load_topic_ledger
            if k != tk and k.lower() != tk.lower() and tk in entries:
                continue
            seen_in.add(tk)
            row = dict(v)
            row["topic_key"] = tk
            incoming_list.append(row)
    elif isinstance(entries, list):
        for v in entries:
            if isinstance(v, dict) and v.get("topic_key"):
                incoming_list.append(dict(v))

    for inc in incoming_list:
        tk = str(inc["topic_key"])
        if tk in existing_by_tk:
            merged_topic = dict(existing_by_tk[tk])
            for field_k, field_v in inc.items():
                # Never clobber non-null existing metadata with None unless explicitly updating state fields
                if field_v is None and field_k in merged_topic and merged_topic[field_k] is not None and field_k not in {"proposed_on", "next_eligible_at", "external_id", "event_id"}:
                    continue
                if field_k == "aliases" and isinstance(merged_topic.get("aliases"), list) and isinstance(field_v, list):
                    merged_topic["aliases"] = list(dict.fromkeys([*merged_topic["aliases"], *field_v]))
                else:
                    merged_topic[field_k] = field_v
            existing_by_tk[tk] = merged_topic
        else:
            existing_by_tk[tk] = dict(inc)
            ordered_keys.append(tk)

    merged_topics = [existing_by_tk[tk] for tk in ordered_keys if tk in existing_by_tk]

    payload: dict[str, Any] = {}
    if isinstance(existing_doc, dict):
        payload.update(existing_doc)
    payload["schema_version"] = existing_doc.get("schema_version", "personal-topic-ledger/v1") if isinstance(existing_doc, dict) else "personal-topic-ledger/v1"
    payload["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload["topics"] = merged_topics
    _atomic_write_json(ledger_path, payload)
    return ledger_path


def _has_integration_evidence(note_text: str) -> bool:
    if not note_text:
        return False
    return bool(_INTEGRATION_EVIDENCE_RE.search(note_text))


def _is_entity_covered_in_vault(
    *,
    slug: str,
    topic_key: str,
    aliases: list[str],
    coverage_refs: list[str],
    default_state: str,
    vault_coverage: dict[str, Any],
    existing_corpus: str,
) -> bool:
    rel_paths: set[str] = vault_coverage["rel_paths"]
    stems: set[str] = vault_coverage["stems"]
    vault_tks: set[str] = vault_coverage["topic_keys"]
    vault_aliases: set[str] = vault_coverage["aliases"]

    if topic_key.lower() in vault_tks or topic_key.lower() in stems or topic_key.lower() in vault_aliases:
        return True
    if slug.lower() in stems or slug.lower() in vault_tks:
        return True
    if f"concepts/{slug.lower()}.md" in rel_paths or f"concepts/{topic_key.lower()}.md" in rel_paths:
        return True
    if slug.lower() in existing_corpus and f"concepts/{slug.lower()}.md" in existing_corpus:
        return True
    for al in aliases:
        al_l = al.lower()
        if al_l in vault_tks or al_l in stems or f"concepts/{al_l}.md" in rel_paths:
            return True

    if default_state in {"learned", "covered"}:
        return True
    if default_state == "covered_if_refs_present" and coverage_refs:
        for ref in coverage_refs:
            norm_ref = _normalize_rel_vault_ref(ref)
            if norm_ref in rel_paths or Path(norm_ref).stem.lower() in stems:
                return True
    return False


def _reconcile_collected_tools_entities(
    collected_dir: Path,
    existing_corpus: str,
    *,
    vault_dir: Path | None = None,
    topic_ledger_path: Path | None = None,
    include_unreconciled_repos: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    excluded_reasons: dict[str, str] = {}
    unknown_sources: list[str] = []
    integrated_sources: list[str] = []
    covered_sources: list[str] = []
    merged_entities: list[str] = []

    eff_vault = vault_dir or DEFAULT_VAULT_DIR
    vault_cov = _inspect_vault_coverage(eff_vault)
    ledger_doc = _load_topic_ledger_document(eff_vault, topic_ledger_path=topic_ledger_path)
    ledger_map = _load_topic_ledger(eff_vault, topic_ledger_path=topic_ledger_path)
    ledger_sources_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(ledger_doc.get("sources"), list):
        for s_item in ledger_doc["sources"]:
            if isinstance(s_item, dict) and s_item.get("source_id"):
                sid = str(s_item["source_id"]).strip().lower()
                ledger_sources_by_id[sid] = dict(s_item)

    notes_by_slug: dict[str, Path] = {}
    if collected_dir.is_dir():
        for md in sorted(collected_dir.glob("*.md")):
            if md.name.lower() == "readme.md":
                continue
            slug = re.sub(r"[^a-z0-9-]+", "-", md.stem.lower()).strip("-")
            if slug:
                notes_by_slug[slug] = md

    repos_by_slug: dict[str, Path] = {}
    repos_dir = collected_dir / "repos"
    if repos_dir.is_dir():
        for repo_dir in sorted(repos_dir.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name.startswith("."):
                continue
            slug = re.sub(r"[^a-z0-9-]+", "-", repo_dir.name.lower()).strip("-")
            if slug:
                repos_by_slug[slug] = repo_dir

    ledger_ct_slugs: set[str] = set()
    for s_id in ledger_sources_by_id:
        ledger_ct_slugs.add(s_id)
    if isinstance(ledger_doc.get("topics"), list):
        for t_item in ledger_doc["topics"]:
            if isinstance(t_item, dict) and str(t_item.get("source_kind") or "collected_tools") == "collected_tools":
                s_id = str(t_item.get("source_id") or "").strip().lower()
                if s_id:
                    ledger_ct_slugs.add(s_id)

    all_slugs = sorted(set(notes_by_slug.keys()) | set(repos_by_slug.keys()) | ledger_ct_slugs)
    if not all_slugs:
        return candidates, {
            "total_entities": 0,
            "merged_entities": merged_entities,
            "integrated_sources": integrated_sources,
            "covered_sources": covered_sources,
            "unknown_sources": unknown_sources,
            "queued_sources": [],
            "excluded_reasons": excluded_reasons,
        }
    queued_sources: list[str] = []

    for slug in all_slugs:
        note_path = notes_by_slug.get(slug)
        repo_path = repos_by_slug.get(slug)
        src_meta = ledger_sources_by_id.get(slug, {})
        local_paths_meta = [str(p) for p in (src_meta.get("local_paths") or [])]
        if (note_path is not None and repo_path is not None) or len(local_paths_meta) >= 2:
            merged_entities.append(slug)

        note_text = note_path.read_text(encoding="utf-8", errors="ignore") if note_path is not None else ""
        readme_path = (
            next((p for p in (repo_path / "README.md", repo_path / "readme.md") if p.is_file()), None)
            if repo_path is not None
            else None
        )
        readme_text = readme_path.read_text(encoding="utf-8", errors="ignore") if readme_path is not None else ""

        ledger_entry = ledger_map.get(slug)
        canon_entry = CANONICAL_COLLECTED_TOOLS_RECONCILIATION.get(slug)
        if ledger_entry is None and canon_entry is not None:
            tk_lookup = str(canon_entry.get("topic_key") or "").lower()
            if tk_lookup in ledger_map:
                ledger_entry = ledger_map[tk_lookup]

        spec_entry: dict[str, Any] = {}
        if canon_entry:
            spec_entry.update(canon_entry)
        if src_meta:
            spec_entry.update(src_meta)
        if ledger_entry:
            spec_entry.update(ledger_entry)

        topic_key = str(
            spec_entry.get("topic_key")
            or (f"{slug}-anti-slop-anatomy" if slug == "hallmark" else (slug if note_path else f"{slug}-core-patterns"))
        ).strip()
        aliases = list(
            dict.fromkeys(
                [
                    slug,
                    topic_key,
                    f"{slug}-core-patterns",
                    *( [f"{slug}-anti-slop-anatomy"] if slug == "hallmark" else [] ),
                    *(str(a).strip() for a in (spec_entry.get("aliases") or []) if str(a).strip()),
                ]
            )
        )
        source_uri = str(
            spec_entry.get("source_uri")
            or (spec_entry.get("source_uris") or [None])[0]
            or (f"file://{note_path}" if note_path else f"file://{repo_path}")
        )
        coverage_refs = [str(r) for r in (spec_entry.get("coverage_refs") or []) if str(r).strip()]
        default_state = str(
            (ledger_entry or {}).get("learning_state")
            or src_meta.get("reconciliation_status")
            or spec_entry.get("learning_state")
            or spec_entry.get("default_state")
            or ""
        ).strip().lower()

        # 1. Check integration / usage evidence in top-level note
        if note_text and _has_integration_evidence(note_text):
            integrated_sources.append(slug)
            excluded_reasons[topic_key] = "integrated_in_workflow"
            excluded_reasons[slug] = "integrated_in_workflow"
            continue

        # 2. Check vault coverage or explicit learned state in topic-ledger.json
        if _is_entity_covered_in_vault(
            slug=slug,
            topic_key=topic_key,
            aliases=aliases,
            coverage_refs=coverage_refs,
            default_state=default_state,
            vault_coverage=vault_cov,
            existing_corpus=existing_corpus,
        ):
            covered_sources.append(slug)
            excluded_reasons[topic_key] = "already_covered_in_vault"
            excluded_reasons[slug] = "already_covered_in_vault"
            continue

        # 3. Check reconciliation status for repo-only clones without a top-level note or explicit reconciled question
        has_reconciled_question = bool(
            note_path is not None
            or spec_entry.get("question")
            or spec_entry.get("reconciliation_status") in {"reconciled", "queued_subtopic"}
            or default_state in {"queued", "queued_subtopic"}
        )
        if not has_reconciled_question or default_state == "unknown" or spec_entry.get("reconciliation_status") == "unknown":
            unknown_sources.append(slug)
            excluded_reasons[topic_key] = "unreconciled_unknown_source"
            excluded_reasons[slug] = "unreconciled_unknown_source"
            if not include_unreconciled_repos:
                continue
            reconciliation_status = "unknown"
        else:
            reconciliation_status = str(spec_entry.get("reconciliation_status") or "reconciled")

        # Build excerpt & title
        if note_path is not None:
            first_h1 = next(
                (ln[2:].strip() for ln in note_text.splitlines() if ln.startswith("# ")),
                note_path.stem,
            )
            summary_lines = [
                ln.strip()
                for ln in note_text.splitlines()
                if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith("```")
            ]
            excerpt = " ".join(summary_lines[:3])[:240] or f"Tool note from {note_path.name}"
            default_title = (
                f"{first_h1}：反 AI 模板感解剖设计与多视口几何门禁"
                if slug == "hallmark"
                else f"{first_h1}：核心原理与工程实践拆解"
            )
        else:
            lines = [
                ln.strip()
                for ln in readme_text.splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            excerpt = " ".join(lines[:2])[:220] or f"Reference repository {slug} in collected-tools"
            default_title = f"{slug} 核心架构模式与实战拆解"

        title = str(spec_entry.get("title") or spec_entry.get("question") or default_title).strip()
        question = str(spec_entry.get("question") or title).strip()
        why_now = str(
            spec_entry.get("why_now")
            or (str(note_path) if note_path is not None else str(repo_path or slug))
        ).strip()
        fp_seed = f"{slug}|{topic_key}|{question}|{excerpt[:120]}"
        source_fp = str(spec_entry.get("source_fingerprint") or src_meta.get("git_head_sha") or hashlib.sha256(fp_seed.encode("utf-8")).hexdigest()[:16])
        rank_val = spec_entry.get("priority_rank")
        if rank_val is None:
            rank_val = spec_entry.get("rank")
        if rank_val is None:
            rank_val = spec_entry.get("rank_order", 50 if note_path is None else 5)

        queued_sources.append(slug)
        cand_row: dict[str, Any] = {
            "slug": topic_key,
            "topic_key": topic_key,
            "source_id": slug,
            "source_uri": source_uri,
            "aliases": aliases,
            "coverage_refs": coverage_refs,
            "question": question,
            "why_now": why_now,
            "title": title,
            "category": str(spec_entry.get("category") or "工程与设计"),
            "source_kind": str(spec_entry.get("source_kind") or ("collected_tools" if note_path is not None else "collected_tools_repo")),
            "source_ref": str(note_path if note_path is not None else (repo_path or slug)),
            "excerpt": f"{question} — {excerpt}"[:240] if spec_entry.get("question") and excerpt else (excerpt or question),
            "heuristic_direct": float(spec_entry.get("heuristic_direct", 0.88 if note_path is not None else 0.82)),
            "heuristic_tier": float(spec_entry.get("heuristic_tier", 2.6 if note_path is not None else 2.4)),
            "rank": int(rank_val),
            "rank_order": int(rank_val),
            "priority_rank": spec_entry.get("priority_rank", int(rank_val)),
            "reconciliation_status": reconciliation_status,
            "source_fingerprint": source_fp,
            "scope": str(spec_entry.get("scope") or "personal"),
        }
        # Preserve extra ledger topic metadata fields (such as aidash_card_preview, source_locator, legacy_ref, action_id, source_uris)
        for extra_k, extra_v in spec_entry.items():
            if extra_k not in cand_row:
                cand_row[extra_k] = extra_v
        candidates.append(cand_row)

    candidates.sort(key=lambda c: (int(c.get("rank_order", 50)), -float(c.get("heuristic_direct", 0.0)), c["topic_key"]))
    summary = {
        "total_entities": len(all_slugs),
        "merged_entities": merged_entities,
        "integrated_sources": integrated_sources,
        "covered_sources": covered_sources,
        "unknown_sources": unknown_sources,
        "queued_sources": queued_sources,
        "excluded_reasons": excluded_reasons,
    }
    return candidates, summary


def _scan_collected_tools_candidates(
    collected_dir: Path,
    existing_corpus: str,
    *,
    vault_dir: Path | None = None,
    topic_ledger_path: Path | None = None,
    include_unreconciled_repos: bool = False,
) -> list[dict[str, Any]]:
    candidates, _ = _reconcile_collected_tools_entities(
        collected_dir,
        existing_corpus,
        vault_dir=vault_dir,
        topic_ledger_path=topic_ledger_path,
        include_unreconciled_repos=include_unreconciled_repos,
    )
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
            0.84,
            2.4,
        ),
        (
            "progressive-disclosure-four-valves",
            "渐进式披露 Skill 四道防爆阀门：为什么 L0/L1/L2 预算能让 Token 降一个量级",
            "工程与设计",
            ("skill-contract", "repo-bound", "progressive", "drawer", "audit"),
            "Recent git commits refine skill-contract L0/L1/L2 progressive disclosure budgets and executable eval drawers.",
            0.82,
            2.3,
        ),
        (
            "aidash-xpc-event-bridge-flywheel",
            "AIDash 单向数据流与 Event Bridge 闭环：从 L1 遥测采集到点 Star 自动触发动作",
            "工程与设计",
            ("event_bridge", "aidata", "linear_issue", "aidash", "proposal"),
            "Recent commits in AIDash connect L1-L5 telemetry with event_bridge Star/Done actions.",
            0.80,
            2.2,
        ),
    ]
    for slug, title, category, keywords, excerpt, p_direct, s_tier in topic_probes:
        if slug in existing_corpus:
            continue
        if any(k in joined_commits for k in keywords):
            candidates.append({
                "slug": slug,
                "topic_key": slug,
                "source_id": slug,
                "source_uri": f"git://{slug}",
                "aliases": [slug],
                "coverage_refs": [],
                "question": title,
                "why_now": "git log --since=7.days",
                "title": title,
                "category": category,
                "source_kind": "git_tech_stack",
                "source_ref": "git-log-7d",
                "excerpt": excerpt,
                "heuristic_direct": p_direct,
                "heuristic_tier": s_tier,
                "rank_order": 80,
                "reconciliation_status": "reconciled",
                "scope": "personal",
            })
    return candidates


_scan_git_candidates = _scan_git_tech_stack_candidates


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


def _cst_date_str(now_utc: datetime | None = None) -> str:
    dt = now_utc or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_CST_TZ).date().isoformat()


def _parse_iso_date(raw: Any) -> date | None:
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) >= 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", s[:10]):
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    return None


def _resolve_state_path(
    proposals_path: Path | None = None,
    state_path: Path | None = None,
    vault_dir: Path | None = None,
) -> Path:
    if state_path is not None:
        return state_path
    if proposals_path is not None:
        return proposals_path.parent / "learning-topics-state.json"
    if vault_dir is not None:
        return vault_dir / ".state" / "learning-topics-state.json"
    return DEFAULT_STATE_PATH


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def validate_proposal_record(record: dict[str, Any]) -> list[str]:
    required_fields = ("id", "ts", "agent", "title", "detail", "priority", "status", "ref")
    missing = [
        f
        for f in required_fields
        if not isinstance(record.get(f), str) or not str(record.get(f)).strip()
    ]
    return missing


def _append_valid_proposal(proposals_path: Path, record: dict[str, Any]) -> dict[str, Any]:
    missing = validate_proposal_record(record)
    if missing:
        raise ValueError(f"proposal record missing required non-empty fields: {', '.join(missing)}")
    proposals_path.parent.mkdir(parents=True, exist_ok=True)
    with proposals_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _extract_topic_key_from_proposal(rec: dict[str, Any]) -> str | None:
    tk = str(rec.get("topic_key") or "").strip()
    if tk:
        return tk
    for field in ("origin_ref", "ref"):
        val = str(rec.get(field) or "").strip()
        if val.startswith("learning-topic://"):
            return val.removeprefix("learning-topic://").strip("/")
        if val.startswith("knowledge://"):
            return val.removeprefix("knowledge://").strip("/")
    pid = str(rec.get("id") or "").strip()
    if pid.startswith("learning-topic-"):
        return pid.removeprefix("learning-topic-")
    if pid.startswith("learn-"):
        return pid.removeprefix("learn-")
    return None


def _load_learning_state_registry(
    *,
    proposals_path: Path | None = None,
    state_path: Path | None = None,
    vault_dir: Path | None = None,
    topic_ledger_path: Path | None = None,
) -> dict[str, Any]:
    resolved_state = _resolve_state_path(proposals_path, state_path, vault_dir)
    registry: dict[str, Any] = {
        "schema_version": 1,
        "updated_at": "",
        "topics": {},
        "consumed_events": {},
    }
    if resolved_state.is_file():
        try:
            loaded = json.loads(resolved_state.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                registry["schema_version"] = int(loaded.get("schema_version", 1))
                registry["updated_at"] = str(loaded.get("updated_at") or "")
                if isinstance(loaded.get("topics"), dict):
                    registry["topics"] = {
                        str(k): dict(v)
                        for k, v in loaded["topics"].items()
                        if isinstance(v, dict)
                    }
                if isinstance(loaded.get("consumed_events"), dict):
                    registry["consumed_events"] = dict(loaded["consumed_events"])
        except (OSError, json.JSONDecodeError):
            pass

    # Merge topic-ledger.json entries if present
    eff_vault = vault_dir or DEFAULT_VAULT_DIR
    ledger_path = topic_ledger_path or (eff_vault / "topic-ledger.json")
    if ledger_path.is_file():
        try:
            ldoc = json.loads(ledger_path.read_text(encoding="utf-8"))
            raw_list: list[dict[str, Any]] = []
            if isinstance(ldoc, dict):
                for key in ("topics", "entities", "items", "entries"):
                    v = ldoc.get(key)
                    if isinstance(v, list):
                        raw_list.extend(x for x in v if isinstance(x, dict))
                    elif isinstance(v, dict):
                        for sk, sv in v.items():
                            if isinstance(sv, dict):
                                item = dict(sv)
                                item.setdefault("topic_key", sk)
                                raw_list.append(item)
            elif isinstance(ldoc, list):
                raw_list.extend(x for x in ldoc if isinstance(x, dict))
            for item in raw_list:
                tk = str(item.get("topic_key") or item.get("slug") or item.get("source_id") or "").strip()
                if not tk:
                    continue
                existing = registry["topics"].get(tk, {})
                merged = dict(item)
                for ex_k, ex_v in existing.items():
                    if ex_v is None and ex_k in merged and merged[ex_k] is not None and ex_k not in {"proposed_on", "next_eligible_at", "external_id", "event_id"}:
                        continue
                    if ex_k == "aliases" and isinstance(merged.get("aliases"), list) and isinstance(ex_v, list):
                        merged["aliases"] = list(dict.fromkeys([*merged["aliases"], *ex_v]))
                    else:
                        merged[ex_k] = ex_v
                merged["topic_key"] = tk
                merged.setdefault("origin_ref", f"learning-topic://{tk}")
                merged.setdefault("revision", int(merged.get("revision") or 1))
                registry["topics"][tk] = merged
        except (OSError, json.JSONDecodeError):
            pass

    # Reconcile with proposals.jsonl
    if proposals_path is not None and proposals_path.is_file():
        try:
            lines = proposals_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            lines = []
        id_to_tk: dict[str, str] = {}
        for t_k, t_entry in registry["topics"].items():
            id_to_tk[f"learning-topic-{t_k}"] = t_k
            id_to_tk[f"learn-{t_k}"] = t_k
            ext = str(t_entry.get("external_id") or "").strip()
            if ext:
                id_to_tk[ext] = t_k

        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue
            pid = str(rec.get("id") or "").strip()
            ts = str(rec.get("ts") or "").strip()
            agent = str(rec.get("agent") or "").strip()
            title = str(rec.get("title") or "").strip()
            if not (pid and ts and agent and title):
                continue

            tk = _extract_topic_key_from_proposal(rec) or id_to_tk.get(pid)
            if not tk:
                continue
            id_to_tk[pid] = tk
            entry = dict(registry["topics"].get(tk, {}))
            entry["topic_key"] = tk
            entry["proposal_id"] = pid
            entry["title"] = title
            entry["detail"] = str(rec.get("detail") or entry.get("detail") or title)
            entry["priority"] = str(rec.get("priority") or entry.get("priority") or "P2")
            entry["agent"] = agent
            entry["origin_ref"] = str(rec.get("origin_ref") or entry.get("origin_ref") or f"learning-topic://{tk}")
            ref_val = str(rec.get("ref") or entry["origin_ref"]).strip()
            entry["ref"] = ref_val
            entry.setdefault("aliases", [tk])
            entry.setdefault("revision", int(rec.get("revision") or entry.get("revision") or 1))
            entry["updated_at"] = ts
            entry.setdefault("created_at", ts)

            prop_date = str(rec.get("proposed_on") or ts[:10]).strip()[:10]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", prop_date):
                entry.setdefault("proposed_on", prop_date)

            ext_id = str(rec.get("external_id") or "").strip()
            if not ext_id and ref_val.lower().startswith("linear://"):
                ext_id = ref_val
            if ext_id:
                entry["external_id"] = ext_id
                id_to_tk[ext_id] = tk

            prop_status = str(rec.get("status") or "pending").strip().lower()
            explicit_lstate = str(rec.get("learning_state") or "").strip().lower()
            via_sync = bool(
                rec.get("sync_triage")
                or str(rec.get("transition_reason") or "").strip().lower() == "sync_triage"
                or (prop_status == "approved" and ref_val.lower().startswith("linear://") and not rec.get("exploration_authorized"))
                or (prop_status == "approved" and ext_id and not rec.get("exploration_authorized") and entry.get("learning_state") not in {"approved", "exploring", "learned"})
            )

            if explicit_lstate:
                entry["learning_state"] = explicit_lstate
            elif via_sync:
                entry["learning_state"] = "transferred"
                entry["exploration_authorized"] = False
            elif prop_status == "pending":
                if entry.get("learning_state") not in {"transferred", "approved", "exploring", "learned", "dismissed", "snoozed"}:
                    entry["learning_state"] = "proposed"
            elif prop_status == "approved":
                if entry.get("learning_state") not in {"exploring", "learned"}:
                    entry["learning_state"] = "approved"
            elif prop_status == "dismissed":
                if entry.get("learning_state") not in {"learned", "snoozed"}:
                    entry["learning_state"] = "dismissed"

            if rec.get("next_eligible_at"):
                entry["next_eligible_at"] = str(rec["next_eligible_at"])

            registry["topics"][tk] = entry

    return registry


def _save_learning_state_registry(
    registry: dict[str, Any],
    *,
    proposals_path: Path | None = None,
    state_path: Path | None = None,
    vault_dir: Path | None = None,
) -> Path:
    resolved = _resolve_state_path(proposals_path, state_path, vault_dir)
    registry["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    _atomic_write_json(resolved, registry)
    return resolved


def _topic_exclusion_reason(
    cand: dict[str, Any],
    registry: dict[str, Any],
    today_cst: date,
) -> str | None:
    topics_map: dict[str, dict[str, Any]] = registry.get("topics", {})
    cand_tk = str(cand.get("topic_key") or cand.get("slug") or "").strip()
    cand_aliases = {cand_tk.lower(), str(cand.get("slug") or "").lower()}
    for al in cand.get("aliases") or []:
        if str(al).strip():
            cand_aliases.add(str(al).strip().lower())

    matched_entry: dict[str, Any] | None = None
    if cand_tk in topics_map:
        matched_entry = topics_map[cand_tk]
    else:
        for t_k, t_entry in topics_map.items():
            entry_aliases = {t_k.lower()}
            for al in t_entry.get("aliases") or []:
                if str(al).strip():
                    entry_aliases.add(str(al).strip().lower())
            if cand_aliases & entry_aliases:
                matched_entry = t_entry
                break

    if matched_entry is None:
        return None

    lstate = str(matched_entry.get("learning_state") or "").strip().lower()
    if lstate in {"proposed", "pending"}:
        return "already_pending"
    if lstate == "transferred":
        return "already_transferred_to_linear"
    if lstate == "approved":
        return "already_approved"
    if lstate == "exploring":
        return "already_exploring"
    if lstate in {"learned", "covered"}:
        return "already_learned"
    if lstate == "blocked":
        return "blocked_requires_attention"
    if lstate == "dismissed":
        if not matched_entry.get("reopened"):
            return "dismissed_cooldown_or_no_auto_revive"
    if lstate == "snoozed":
        next_el = _parse_iso_date(matched_entry.get("next_eligible_at"))
        if next_el is None:
            upd = _parse_iso_date(matched_entry.get("updated_at") or matched_entry.get("proposed_on"))
            if upd is not None:
                next_el = upd + timedelta(days=SNOOZE_COOLDOWN_DAYS)
        if next_el is None or today_cst < next_el:
            return "snoozed_cooldown"
    return None


def discover_topics(
    *,
    top: int | None = None,
    propose_aidash: bool = False,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    collected_tools_dir: Path = DEFAULT_COLLECTED_TOOLS_DIR,
    git_roots: tuple[Path, ...] = DEFAULT_GIT_ROOTS,
    proposals_path: Path = DEFAULT_PROPOSALS_PATH,
    state_path: Path | None = None,
    topic_ledger_path: Path | None = None,
    materialize_cards: bool | None = None,
    materialize_before_approval: bool = False,
    include_unreconciled_repos: bool = False,
    now_utc: datetime | None = None,
    mock_r09_answers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Enforce Candidate-Only Guard: when propose_aidash is True, default materialize_cards to False
    # unless materialize_before_approval=True (or legacy materialize_cards=True with top=2) is explicitly passed.
    legacy_materialize_mode = bool(propose_aidash and materialize_cards is True and top == 2 and not materialize_before_approval)
    if propose_aidash:
        eff_materialize = bool(materialize_before_approval or legacy_materialize_mode)
        eff_top = DAILY_NEW_TOPIC_QUOTA if top is None else max(1, min(int(top), DAILY_NEW_TOPIC_QUOTA))
    else:
        eff_materialize = True if materialize_cards is None else bool(materialize_cards)
        eff_top = 2 if top is None else max(1, int(top))

    now_dt = now_utc or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    now_iso = now_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    today_cst_str = _cst_date_str(now_dt)
    today_cst_date = date.fromisoformat(today_cst_str)

    vault_cov = _inspect_vault_coverage(vault_dir)
    existing_corpus = vault_cov["corpus_text"]

    registry = _load_learning_state_registry(
        proposals_path=proposals_path,
        state_path=state_path,
        vault_dir=vault_dir,
        topic_ledger_path=topic_ledger_path,
    )

    ct_candidates, reconciliation_summary = _reconcile_collected_tools_entities(
        collected_tools_dir,
        existing_corpus,
        vault_dir=vault_dir,
        topic_ledger_path=topic_ledger_path,
        include_unreconciled_repos=include_unreconciled_repos,
    )
    raw_vault_candidates = vault_cov["uncovered_raw_items"]
    git_candidates = _scan_git_tech_stack_candidates(git_roots, existing_corpus)

    raw_candidates = ct_candidates + raw_vault_candidates + git_candidates
    excluded_reasons: dict[str, str] = dict(reconciliation_summary.get("excluded_reasons", {}))

    deduped: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for cand in raw_candidates:
        tk = str(cand.get("topic_key") or cand["slug"]).strip()
        cand["topic_key"] = tk
        cand["slug"] = tk
        aliases_lower = {tk.lower(), *(str(a).lower() for a in (cand.get("aliases") or []))}
        if seen_keys & aliases_lower:
            excluded_reasons[tk] = "duplicate_entity_alias"
            continue
        reason = _topic_exclusion_reason(cand, registry, today_cst_date)
        if reason is not None:
            excluded_reasons[tk] = reason
            continue
        seen_keys.update(aliases_lower)
        deduped.append(cand)

    ranked = _rerank_candidates_r09(deduped, mock_r09_answers=mock_r09_answers)
    # Preserve deterministic rank_order priority among KEEP_PRIMARY items
    ranked.sort(
        key=lambda c: (
            0 if c.get("r09", {}).get("action") == "KEEP_PRIMARY" else 1,
            int(c.get("rank_order", 50)),
            -float(c.get("r09", {}).get("composite_score", 0.0)),
            c["topic_key"],
        )
    )

    # Check active pending count and same-day slot
    topics_reg = registry.get("topics", {})
    active_pending_topics = [
        tk
        for tk, ent in topics_reg.items()
        if str(ent.get("learning_state") or "").lower() in {"proposed", "pending"}
    ]
    same_day_proposed = [
        tk
        for tk, ent in topics_reg.items()
        if str(ent.get("proposed_on") or "")[:10] == today_cst_str
        and str(ent.get("learning_state") or "").lower() in {"proposed", "pending", "transferred", "approved", "exploring", "learned"}
    ]

    quota_blocked_reason: str | None = None
    if propose_aidash:
        if len(active_pending_topics) >= MAX_PENDING_LEARNING_PROPOSALS:
            quota_blocked_reason = "pending_cap_reached"
        elif len(same_day_proposed) >= DAILY_NEW_TOPIC_QUOTA:
            quota_blocked_reason = "daily_slot_already_used"

    if quota_blocked_reason is not None:
        selected: list[dict[str, Any]] = []
    else:
        selected = ranked[:eff_top]

    archived_cards: list[dict[str, Any]] = []
    proposals_written: list[dict[str, Any]] = []

    for item in selected:
        slug = item["slug"]
        topic_key = item.get("topic_key") or slug
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
            origin_ref = f"knowledge://{topic_key}" if legacy_materialize_mode else f"learning-topic://{topic_key}"
            record = {
                "id": f"learn-{topic_key}" if legacy_materialize_mode else f"learning-topic-{topic_key}",
                "ref": origin_ref,
                "origin_ref": f"learning-topic://{topic_key}",
                "topic_key": topic_key,
                "source": "Learning",
                "scope": "personal",
                "ts": now_iso,
                "agent": "knowledge-digest",
                "title": f"学习探索：{item['title']}",
                "detail": (
                    f"question={item.get('question') or item['title']} · "
                    f"source_uri={item.get('source_uri', '')} · "
                    f"why_now={item.get('why_now', '')} · "
                    f"R09 score={item['r09']['composite_score']} ({item['r09']['action']}) · "
                    f"ref={origin_ref} · {item['excerpt']}"
                ),
                "priority": "P2",
                "status": "pending",
                "learning_state": "proposed",
                "proposed_on": today_cst_str,
                "revision": int(topics_reg.get(topic_key, {}).get("revision") or 0) + 1,
            }
            _append_valid_proposal(proposals_path, record)
            proposals_written.append(record)

            prev_topic = dict(topics_reg.get(topic_key, {}))
            updated_topic = dict(prev_topic)
            for k_it, v_it in item.items():
                if k_it not in {"r09", "slug", "excerpt"} and k_it not in updated_topic:
                    updated_topic[k_it] = v_it
            updated_topic.update({
                "topic_key": topic_key,
                "aliases": list(dict.fromkeys([*(prev_topic.get("aliases") or []), *(item.get("aliases") or [topic_key])])),
                "scope": str(prev_topic.get("scope") or item.get("scope") or "personal"),
                "source_kind": str(prev_topic.get("source_kind") or item.get("source_kind", "collected_tools")),
                "source_uri": str(prev_topic.get("source_uri") or item.get("source_uri", "")),
                "source_fingerprint": str(prev_topic.get("source_fingerprint") or item.get("source_fingerprint", "")),
                "question": str(prev_topic.get("question") or item.get("question") or item["title"]),
                "title": str(prev_topic.get("title") or item["title"]),
                "detail": record["detail"],
                "priority": "P2",
                "agent": "knowledge-digest",
                "why_now": str(prev_topic.get("why_now") or item.get("why_now", "")),
                "coverage_refs": prev_topic.get("coverage_refs") if prev_topic.get("coverage_refs") is not None else item.get("coverage_refs", []),
                "learning_state": "proposed",
                "revision": record["revision"],
                "created_at": prev_topic.get("created_at") or now_iso,
                "updated_at": now_iso,
                "proposed_on": today_cst_str,
                "next_eligible_at": None,
                "origin_ref": f"learning-topic://{topic_key}",
                "ref": origin_ref,
                "proposal_id": record["id"],
                "external_id": prev_topic.get("external_id"),
                "exploration_authorized": False,
            })
            topics_reg[topic_key] = updated_topic

    if propose_aidash and proposals_written:
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )

    status_val = "selected" if selected else "no_new_topic"
    if quota_blocked_reason:
        excluded_reasons["_quota"] = quota_blocked_reason

    candidate_counts = {
        "collected_tools_candidates": len(ct_candidates),
        "vault_raw_candidates": len(raw_vault_candidates),
        "git_candidates": len(git_candidates),
        "qualifying_candidates": len(deduped),
        "ranked_candidates": len(ranked),
        "selected_candidates": len(selected),
        "active_pending_count": len(active_pending_topics),
        "same_day_proposed_count": len(same_day_proposed),
    }

    return {
        "ok": True,
        "status": status_val,
        "quota_blocked_reason": quota_blocked_reason,
        "candidate_count": len(deduped),
        "selected_count": len(selected),
        "candidate_counts": candidate_counts,
        "excluded_reasons": excluded_reasons,
        "reconciliation_summary": reconciliation_summary,
        "topics": selected,
        "archived_cards": archived_cards,
        "proposals_written": proposals_written,
        "materialized": bool(archived_cards),
    }


def handle_learning_event(
    *,
    action: str,
    topic_key: str | None = None,
    ref: str | None = None,
    external_id: str | None = None,
    event_id: str | None = None,
    note: str = "",
    proposals_path: Path = DEFAULT_PROPOSALS_PATH,
    state_path: Path | None = None,
    vault_dir: Path = DEFAULT_VAULT_DIR,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Deterministic Owner-decision & post-approval exploration state handler for learning topics."""
    now_dt = now_utc or datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    now_iso = now_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
    today_cst = _cst_date_str(now_dt)
    today_date = date.fromisoformat(today_cst)

    registry = _load_learning_state_registry(
        proposals_path=proposals_path,
        state_path=state_path,
        vault_dir=vault_dir,
    )
    topics_reg: dict[str, dict[str, Any]] = registry.setdefault("topics", {})
    consumed_events: dict[str, dict[str, Any]] = registry.setdefault("consumed_events", {})

    # Resolve topic_key from topic_key, ref, or external_id
    resolved_tk = (topic_key or "").strip()
    if resolved_tk.startswith("learning-topic://"):
        resolved_tk = resolved_tk.removeprefix("learning-topic://").strip("/")
    elif resolved_tk.startswith("knowledge://"):
        resolved_tk = resolved_tk.removeprefix("knowledge://").strip("/")
    elif resolved_tk.startswith("learning-topic-"):
        resolved_tk = resolved_tk.removeprefix("learning-topic-")
    elif resolved_tk.startswith("learn-"):
        resolved_tk = resolved_tk.removeprefix("learn-")

    raw_ref = (ref or "").strip()
    raw_ext = (external_id or "").strip()
    if not resolved_tk and raw_ref:
        if raw_ref.startswith("learning-topic://"):
            resolved_tk = raw_ref.removeprefix("learning-topic://").strip("/")
        elif raw_ref.startswith("knowledge://"):
            resolved_tk = raw_ref.removeprefix("knowledge://").strip("/")
        else:
            for tk_cand, ent in topics_reg.items():
                if raw_ref in {ent.get("ref"), ent.get("origin_ref"), ent.get("external_id"), ent.get("proposal_id")}:
                    resolved_tk = tk_cand
                    break
    if not resolved_tk and raw_ext:
        for tk_cand, ent in topics_reg.items():
            if raw_ext in {ent.get("external_id"), ent.get("ref"), ent.get("proposal_id")}:
                resolved_tk = tk_cand
                break
        if not resolved_tk and raw_ext.lower().startswith("linear://"):
            resolved_tk = raw_ext.split("://", 1)[1].strip().lower()

    if not resolved_tk:
        raise ValueError("topic_key, ref, or matching external_id is required")

    entry = dict(topics_reg.get(resolved_tk, {}))
    current_rev = int(entry.get("revision") or 1)
    current_state = str(entry.get("learning_state") or "proposed").strip().lower()
    origin_ref = str(entry.get("origin_ref") or f"learning-topic://{resolved_tk}")
    prop_id = str(entry.get("proposal_id") or f"learning-topic-{resolved_tk}")
    title = str(entry.get("title") or f"学习探索：{resolved_tk}")
    detail = str(entry.get("detail") or f"topic_key={resolved_tk} · ref={origin_ref}")
    priority = str(entry.get("priority") or "P2")

    raw_action = (action or "").strip()
    norm_action = raw_action.casefold()

    # Check event deduplication by event_id
    eff_event_id = (event_id or "").strip()
    if eff_event_id and eff_event_id in consumed_events:
        prior = consumed_events[eff_event_id]
        if prior.get("topic_key") == resolved_tk:
            return {
                "ok": True,
                "deduplicated": True,
                "event_id": eff_event_id,
                "topic_key": resolved_tk,
                "action": prior.get("action", norm_action),
                "learning_state": entry.get("learning_state", current_state),
                "revision": current_rev,
                "exploration_authorized": bool(entry.get("exploration_authorized", False)),
                "exploration_started": False,
                "blog_published": False,
                "design_studio_synced": False,
            }

    # 1. Star on learning-topic:// -> harmless bookmark only, NEVER triggers publish-blog or design-studio
    if norm_action in {"star", "bookmark", "favorite"}:
        entry.setdefault("topic_key", resolved_tk)
        entry.setdefault("origin_ref", origin_ref)
        entry["bookmarked"] = True
        entry["bookmarked_at"] = now_iso
        entry["updated_at"] = now_iso
        topics_reg[resolved_tk] = entry
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": current_rev,
                "action": "bookmarked",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "bookmarked",
            "learning_state": current_state,
            "revision": current_rev,
            "exploration_authorized": False,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
        }

    # 2. sync_triage (cloud transfer to Linear) -> state = transferred, NOT Owner exploration approval
    if norm_action in {"sync_triage", "transfer", "transferred"}:
        new_rev = current_rev + 1
        ext_val = raw_ext or (raw_ref if raw_ref.lower().startswith("linear://") else str(entry.get("external_id") or ""))
        entry.update({
            "topic_key": resolved_tk,
            "proposal_id": prop_id,
            "title": title,
            "detail": detail,
            "priority": priority,
            "agent": "knowledge-digest",
            "origin_ref": origin_ref,
            "ref": ext_val or origin_ref,
            "external_id": ext_val or None,
            "learning_state": "transferred",
            "exploration_authorized": False,
            "revision": new_rev,
            "updated_at": now_iso,
        })
        topics_reg[resolved_tk] = entry
        prop_record = {
            "id": prop_id,
            "ts": now_iso,
            "agent": "knowledge-digest",
            "title": title,
            "detail": detail,
            "priority": priority,
            "status": "approved",
            "ref": ext_val or origin_ref,
            "origin_ref": origin_ref,
            "topic_key": resolved_tk,
            "external_id": ext_val or None,
            "source": "Learning",
            "scope": "personal",
            "learning_state": "transferred",
            "sync_triage": True,
            "exploration_authorized": False,
            "revision": new_rev,
        }
        _append_valid_proposal(proposals_path, prop_record)
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": new_rev,
                "action": "transferred",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "transferred",
            "learning_state": "transferred",
            "external_id": ext_val or None,
            "origin_ref": origin_ref,
            "revision": new_rev,
            "exploration_authorized": False,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
            "proposal_written": prop_record,
        }

    # 3. Owner Approve / 快速学习 / 深度调研
    if norm_action in {"approve", "approve to todo", "快速学习", "深度调研", "quick_learn", "deep_research"}:
        if current_state in {"learned", "dismissed"} and not entry.get("reopened"):
            return {
                "ok": False,
                "conflict": True,
                "error": f"topic '{resolved_tk}' is already in terminal state '{current_state}'; explicit Reopen required",
                "topic_key": resolved_tk,
                "learning_state": current_state,
                "revision": current_rev,
                "exploration_authorized": False,
                "exploration_started": False,
                "blog_published": False,
                "design_studio_synced": False,
            }
        route = "deep-research" if (raw_action == "深度调研" or norm_action == "deep_research" or "深度" in note) else "knowledge-digest"
        new_rev = current_rev + 1
        task_key = f"explore-{resolved_tk}-r{new_rev}"
        entry.update({
            "topic_key": resolved_tk,
            "proposal_id": prop_id,
            "title": title,
            "detail": detail if not note else f"{detail} | note={note}",
            "priority": priority,
            "agent": "knowledge-digest",
            "origin_ref": origin_ref,
            "ref": entry.get("ref") or origin_ref,
            "learning_state": "approved",
            "exploration_authorized": True,
            "exploration_route": route,
            "exploration_task_key": task_key,
            "owner_note": note,
            "revision": new_rev,
            "updated_at": now_iso,
        })
        topics_reg[resolved_tk] = entry
        prop_record = {
            "id": prop_id,
            "ts": now_iso,
            "agent": "knowledge-digest",
            "title": title,
            "detail": entry["detail"],
            "priority": priority,
            "status": "approved",
            "ref": origin_ref,
            "origin_ref": origin_ref,
            "topic_key": resolved_tk,
            "source": "Learning",
            "scope": "personal",
            "learning_state": "approved",
            "exploration_authorized": True,
            "exploration_route": route,
            "exploration_task_key": task_key,
            "revision": new_rev,
        }
        _append_valid_proposal(proposals_path, prop_record)
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": new_rev,
                "action": "approved",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "approved",
            "learning_state": "approved",
            "exploration_authorized": True,
            "exploration_route": route,
            "exploration_task_key": task_key,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
            "revision": new_rev,
            "proposal_written": prop_record,
        }

    # 4. Owner Done -> learned (outcome=owner_reported)
    if norm_action in {"done", "mark done", "确认", "confirm", "resolve", "learned"}:
        new_rev = current_rev + 1
        entry.update({
            "topic_key": resolved_tk,
            "proposal_id": prop_id,
            "title": title,
            "detail": detail if not note else f"{detail} | note={note}",
            "priority": priority,
            "agent": "knowledge-digest",
            "origin_ref": origin_ref,
            "ref": origin_ref,
            "learning_state": "learned",
            "outcome": "owner_reported",
            "exploration_authorized": False,
            "revision": new_rev,
            "updated_at": now_iso,
        })
        topics_reg[resolved_tk] = entry
        prop_record = {
            "id": prop_id,
            "ts": now_iso,
            "agent": "knowledge-digest",
            "title": title,
            "detail": entry["detail"],
            "priority": priority,
            "status": "dismissed",
            "ref": origin_ref,
            "origin_ref": origin_ref,
            "topic_key": resolved_tk,
            "source": "Learning",
            "scope": "personal",
            "learning_state": "learned",
            "outcome": "owner_reported",
            "revision": new_rev,
        }
        _append_valid_proposal(proposals_path, prop_record)
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": new_rev,
                "action": "learned",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "done",
            "learning_state": "learned",
            "outcome": "owner_reported",
            "revision": new_rev,
            "exploration_authorized": False,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
            "proposal_written": prop_record,
        }

    # 5. Owner Dismiss -> dismissed (30d cooldown, no auto-revive)
    if norm_action in {"dismiss", "cancel", "canceled", "cancelled", "reject"}:
        new_rev = current_rev + 1
        next_el = (today_date + timedelta(days=DISMISS_COOLDOWN_DAYS)).isoformat()
        entry.update({
            "topic_key": resolved_tk,
            "proposal_id": prop_id,
            "title": title,
            "detail": detail if not note else f"{detail} | note={note}",
            "priority": priority,
            "agent": "knowledge-digest",
            "origin_ref": origin_ref,
            "ref": origin_ref,
            "learning_state": "dismissed",
            "next_eligible_at": next_el,
            "auto_revive": False,
            "reopened": False,
            "exploration_authorized": False,
            "revision": new_rev,
            "updated_at": now_iso,
        })
        topics_reg[resolved_tk] = entry
        prop_record = {
            "id": prop_id,
            "ts": now_iso,
            "agent": "knowledge-digest",
            "title": title,
            "detail": entry["detail"],
            "priority": priority,
            "status": "dismissed",
            "ref": origin_ref,
            "origin_ref": origin_ref,
            "topic_key": resolved_tk,
            "source": "Learning",
            "scope": "personal",
            "learning_state": "dismissed",
            "next_eligible_at": next_el,
            "auto_revive": False,
            "revision": new_rev,
        }
        _append_valid_proposal(proposals_path, prop_record)
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": new_rev,
                "action": "dismissed",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "dismissed",
            "learning_state": "dismissed",
            "next_eligible_at": next_el,
            "auto_revive": False,
            "revision": new_rev,
            "exploration_authorized": False,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
            "proposal_written": prop_record,
        }

    # 6. Owner Snooze -> snoozed (7d cooldown)
    if norm_action in {"snooze", "snooze 7d", "defer", "later", "延后"}:
        new_rev = current_rev + 1
        next_el = (today_date + timedelta(days=SNOOZE_COOLDOWN_DAYS)).isoformat()
        entry.update({
            "topic_key": resolved_tk,
            "proposal_id": prop_id,
            "title": title,
            "detail": detail if not note else f"{detail} | note={note}",
            "priority": priority,
            "agent": "knowledge-digest",
            "origin_ref": origin_ref,
            "ref": origin_ref,
            "learning_state": "snoozed",
            "next_eligible_at": next_el,
            "exploration_authorized": False,
            "revision": new_rev,
            "updated_at": now_iso,
        })
        topics_reg[resolved_tk] = entry
        prop_record = {
            "id": prop_id,
            "ts": now_iso,
            "agent": "knowledge-digest",
            "title": title,
            "detail": entry["detail"],
            "priority": priority,
            "status": "dismissed",
            "ref": origin_ref,
            "origin_ref": origin_ref,
            "topic_key": resolved_tk,
            "source": "Learning",
            "scope": "personal",
            "learning_state": "snoozed",
            "next_eligible_at": next_el,
            "revision": new_rev,
        }
        _append_valid_proposal(proposals_path, prop_record)
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": new_rev,
                "action": "snoozed",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "snoozed",
            "learning_state": "snoozed",
            "next_eligible_at": next_el,
            "revision": new_rev,
            "exploration_authorized": False,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
            "proposal_written": prop_record,
        }

    # 7. Explicit Reopen
    if norm_action in {"reopen", "reset", "queue"}:
        new_rev = current_rev + 1
        entry.update({
            "topic_key": resolved_tk,
            "learning_state": "queued",
            "reopened": True,
            "next_eligible_at": today_cst,
            "exploration_authorized": False,
            "revision": new_rev,
            "updated_at": now_iso,
        })
        topics_reg[resolved_tk] = entry
        if eff_event_id:
            consumed_events[eff_event_id] = {
                "topic_key": resolved_tk,
                "revision": new_rev,
                "action": "reopened",
                "ts": now_iso,
            }
        _save_learning_state_registry(
            registry,
            proposals_path=proposals_path,
            state_path=state_path,
            vault_dir=vault_dir,
        )
        return {
            "ok": True,
            "deduplicated": False,
            "event_id": eff_event_id or None,
            "topic_key": resolved_tk,
            "action": "reopened",
            "learning_state": "queued",
            "revision": new_rev,
            "exploration_authorized": False,
            "exploration_started": False,
            "blog_published": False,
            "design_studio_synced": False,
        }

    raise ValueError(f"unsupported learning event action: {action!r}")


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
    raw_slug = slug.strip()
    if raw_slug.startswith("learning-topic://"):
        raise ValueError(
            "learning-topic:// candidates cannot trigger publish-blog before Owner exploration and card archival"
        )
    clean_slug = re.sub(r"^knowledge://", "", raw_slug).strip("/")
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
    p_arch.add_argument("--vault-dir", "--vault", dest="vault_dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_arch.add_argument("--updated", default=None)

    p_disc = sub.add_parser("discover-topics", help="Scan git tech stack, collected-tools, and vault gaps with R09")
    p_disc.add_argument("--top", type=int, default=None)
    p_disc.add_argument("--propose-aidash", action="store_true")
    p_disc.add_argument("--no-materialize", action="store_true")
    p_disc.add_argument("--materialize-before-approval", action="store_true")
    p_disc.add_argument("--include-unreconciled-repos", action="store_true")
    p_disc.add_argument("--vault-dir", "--vault", dest="vault_dir", type=Path, default=DEFAULT_VAULT_DIR)
    p_disc.add_argument("--collected-tools-dir", type=Path, default=DEFAULT_COLLECTED_TOOLS_DIR)
    p_disc.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
    p_disc.add_argument("--state-path", type=Path, default=None)
    p_disc.add_argument("--topic-ledger-path", type=Path, default=None)
    p_disc.add_argument("--no-git", action="store_true", help="Disable default git root scanning for isolated runs")

    for cmd_name in ("handle-learning-event", "handle-decision", "explore-topic"):
        p_evt = sub.add_parser(cmd_name, help="Handle Owner learning topic decision or post-approval exploration gate")
        p_evt.add_argument("--action", required=True)
        p_evt.add_argument("--topic-key", default=None)
        p_evt.add_argument("--ref", default=None)
        p_evt.add_argument("--external-id", default=None)
        p_evt.add_argument("--event-id", default=None)
        p_evt.add_argument("--note", default="")
        p_evt.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
        p_evt.add_argument("--state-path", type=Path, default=None)
        p_evt.add_argument("--vault-dir", "--vault", dest="vault_dir", type=Path, default=DEFAULT_VAULT_DIR)

    p_pub = sub.add_parser("publish-blog", help="Render a knowledge-vault card into leepepe.github.io (DC-006..008)")
    p_pub.add_argument("--slug", required=True)
    p_pub.add_argument("--blog-repo", type=Path, default=DEFAULT_BLOG_REPO)
    p_pub.add_argument("--vault-dir", "--vault", dest="vault_dir", type=Path, default=DEFAULT_VAULT_DIR)
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
        res = discover_topics(
            top=args.top,
            propose_aidash=args.propose_aidash,
            vault_dir=args.vault_dir,
            collected_tools_dir=args.collected_tools_dir,
            git_roots=() if getattr(args, "no_git", False) else DEFAULT_GIT_ROOTS,
            proposals_path=args.proposals_path,
            state_path=args.state_path,
            topic_ledger_path=args.topic_ledger_path,
            materialize_cards=False if args.no_materialize else None,
            materialize_before_approval=args.materialize_before_approval,
            include_unreconciled_repos=args.include_unreconciled_repos,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if args.command in {"handle-learning-event", "handle-decision", "explore-topic"}:
        res = handle_learning_event(
            action=args.action,
            topic_key=args.topic_key,
            ref=args.ref,
            external_id=args.external_id,
            event_id=args.event_id,
            note=args.note,
            proposals_path=args.proposals_path,
            state_path=args.state_path,
            vault_dir=args.vault_dir,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok", True) else 1
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
