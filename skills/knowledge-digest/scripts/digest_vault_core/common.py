"""Constants, track classification, and shared runtime helpers for digest_vault."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import fcntl
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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from skill_runtime import (
    ACI_MAX_BYTES,
    RagDoc,
    SelfDisclosingArgumentParser,
    SkillVersionInfo,
    VaultIsolationError,
    atomic_write_json as runtime_atomic_write_json,
    compact_json_dumps,
    hybrid_search,
    is_safe_local_dir,
    is_safe_local_file,
    is_valid_skill_slug,
    load_markdown_vault_corpus,
    redact_payload,
    redact_text,
    resolve_skill_version,
    safe_read_text,
)
from skill_runtime.fs_guard import _SF_DATALESS as RUNTIME_SF_DATALESS

SKILL_DIR = Path(__file__).resolve().parents[2]
SKILLS_ROOT = SKILL_DIR.parent
SF_DATALESS = RUNTIME_SF_DATALESS
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

VALID_TRACKS = ("workflow", "personal")
TRACK_LABELS = {
    "workflow": "板块一 · Workflow 相关学习",
    "personal": "板块二 · 个人成长与领域新能力",
}
TRACK_SECTION_HEADERS = {
    "workflow": "**【板块一 · Workflow 相关学习】**",
    "personal": "**【板块二 · 个人成长与领域新能力】**",
}
TRACK_STANDARD_HEADINGS = {
    "workflow": "### 🛠️ 板块一 · Workflow 相关学习（Workflow & Agent 编排）",
    "personal": "### 🌱 板块二 · 个人成长与领域新能力（理财 / 健身 / 原生设计）",
}

_WORKFLOW_EXACT_KEYS = frozenset({
    "bama",
    "bama-agent-cli-story-verification",
    "bama-budget-aware-multi-agent",
    "genjutsu",
    "genjutsu-three-tier-choreography-gate",
    "genjutsu-styled-image-generation",
    "taste-skill",
    "taste-skill-stop-hook-and-bento-anti-slop",
    "ui-ux-pro-max-skill",
    "uupm-csv-faceted-design-retrieval",
    "vercel-agent-skills",
    "vercel-composition-patterns-and-view-transitions",
    "vibecoded-design-tells",
    "vibecoded-reddit-empirical-slop-tells",
    "expo-skills",
    "expo-visual-grader-and-web-to-native-false-friends",
    "impeccable",
    "impeccable-mode-operate-vs-persuade-critique",
    "refactor-review-and-skill-audit",
    "test-contracts-and-regression-gates",
    "ios-debugger-review-swarm",
    "dimillian-spm-app-packaging-and-bug-swarm",
    "jev-r09-r06-truth-table",
    "progressive-disclosure-four-valves",
    "aidash-xpc-event-bridge-flywheel",
})

_PERSONAL_EXACT_KEYS = frozenset({
    "jp-mixed-script-line-breaking",
    "emil-spring-choreography-and-interruptibility",
    "apple-game-feel-and-store-rejection-gates",
    "apple-design-and-game-feel",
    "apple-design-skill-hig-liquid-glass",
    "awesome-ios-design-md-tokens",
    "awesome-web-design-md-tokens",
    "shipswift-animation-metal-components",
    "twostraws-swiftui-pro-patterns",
    "swiftui-26-api-migration-scan-manifest",
    "hallmark-anti-slop-anatomy",
    "appkit-uikit-27-modernization",
    "liquid-glass-optics-and-swiftui-26-27",
    "swiftui-27-scrolling-and-drag-drop-motion",
    "swiftui-27-shader-and-motion-pipeline",
    "wifi-invisible-light",
    "wwdc27-liquid-glass-and-motion-map",
    "study-switch-long-press-ac",
    "swift-concurrency-expert-coding",
    "swiftui-architecture-and-performance",
    "macos-swiftpm-menubar-packaging",
    "ios-architecture-navigation-migration",
    "swift-concurrency-memory-safety",
    "swiftui-state-layout-and-view-structure",
    "swiftdata-persistence-and-migration",
    "instruments-performance-and-swiftui-debugging",
    "app-store-release-security-rejection-gates",
    "feature-generators-and-apple-platform-integration",
    "apple-intelligence-and-special-platforms",
    "investment-frameworks-and-agent-skills",
    "investment-all-weather-and-fifo-rebalancing",
})

_PERSONAL_SIGNAL_RE = re.compile(
    r"(?:investment|asset-screener|portfolio-advisor|fifo|理财|投资|基金|红利|全天候|风险平价|调仓|"
    r"workout|fitness|vitalstride|运动|健身|有氧|心率|科学健康|智能家居|物理与硬件|"
    r"study-switch|wifi-invisible-light|kinsoku|jp-mixed-script|emil-spring|game-feel|"
    r"swiftui|liquid-glass|appkit|uikit|swift-concurrency|swiftdata|instruments|app-store|"
    r"shipswift|twostraws|avdlee|apple-design|apple-intelligence|core\s*ml|hallmark)",
    re.IGNORECASE,
)

_WORKFLOW_SIGNAL_RE = re.compile(
    r"(?:workflow|skill-contract|multi-agent|agent-cli|agent-skills|stop-hook|sloplint|"
    r"visual-grader|bm25|composition-patterns|bug-hunt-swarm|event-bridge|xpc|"
    r"bama|genjutsu|taste-skill|uupm|ui-ux-pro-max|vibecoded|impeccable|"
    r"编排|工作流|自审回路|评测门禁|回归门禁|分面索引|检索)",
    re.IGNORECASE,
)

_INTEGRATION_EVIDENCE_RE = re.compile(
    r"(?:实际使用|已接入|集成记录|統合済み|已集成|used\s+in|integrated\s+(?:into|with)|entry\s+a\s*\(a1\.\.a4\))",
    re.IGNORECASE,
)

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


class TopicLedgerError(RuntimeError):
    pass


def infer_topic_track(item: Mapping[str, Any]) -> str:
    """Deterministically classify a learning topic into `'workflow'` or `'personal'`."""
    explicit = str(item.get("track") or "").strip().lower()
    if explicit in VALID_TRACKS:
        return explicit

    key_candidates = {
        str(item.get("topic_key") or "").strip().lower(),
        str(item.get("slug") or "").strip().lower(),
        str(item.get("source_id") or "").strip().lower(),
    }
    raw_aliases = item.get("aliases")
    if isinstance(raw_aliases, list):
        key_candidates.update(str(a).strip().lower() for a in raw_aliases if str(a).strip())

    if key_candidates & _WORKFLOW_EXACT_KEYS:
        return "workflow"
    if key_candidates & _PERSONAL_EXACT_KEYS:
        return "personal"

    haystack = " ".join(
        str(item.get(field) or "")
        for field in ("topic_key", "slug", "source_id", "category", "title", "question", "why_now")
    )
    if _PERSONAL_SIGNAL_RE.search(haystack) and not re.search(
        r"(?:bama|genjutsu|taste-skill|ui-ux-pro-max|uupm|vercel-agent-skills|vibecoded|expo-skills|impeccable|bug-hunt-swarm|skill-contract)",
        haystack,
        re.IGNORECASE,
    ):
        return "personal"
    if _WORKFLOW_SIGNAL_RE.search(haystack):
        return "workflow"
    if _PERSONAL_SIGNAL_RE.search(haystack):
        return "personal"
    return "workflow"


def _select_balanced_by_track(ranked: list[dict[str, Any]], eff_top: int) -> list[dict[str, Any]]:
    """Balance multi-slot selection across `workflow` and `personal` tracks, grouped by track."""
    if eff_top <= 0 or not ranked:
        return []
    if eff_top == 1:
        return list(ranked[:1])

    workflow_pool = [c for c in ranked if infer_topic_track(c) == "workflow"]
    personal_pool = [c for c in ranked if infer_topic_track(c) == "personal"]
    if not workflow_pool or not personal_pool:
        return list(ranked[:eff_top])

    per_track_quota = eff_top // 2
    picked: list[dict[str, Any]] = []
    picked_keys: set[str] = set()

    for pool in (workflow_pool, personal_pool):
        for cand in pool[:per_track_quota]:
            tk = str(cand.get("topic_key") or cand.get("slug") or "")
            if tk not in picked_keys:
                picked.append(cand)
                picked_keys.add(tk)

    if len(picked) < eff_top:
        for cand in ranked:
            if len(picked) >= eff_top:
                break
            tk = str(cand.get("topic_key") or cand.get("slug") or "")
            if tk not in picked_keys:
                picked.append(cand)
                picked_keys.add(tk)

    track_order = {"workflow": 0, "personal": 1}
    return sorted(
        picked,
        key=lambda item: (
            track_order.get(infer_topic_track(item), 9),
            int(item.get("rank", 99)),
            str(item.get("topic_key", "")),
        ),
    )


def _tracks_summary(selected: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "workflow": [str(x.get("topic_key") or x.get("slug")) for x in selected if infer_topic_track(x) == "workflow"],
        "personal": [str(x.get("topic_key") or x.get("slug")) for x in selected if infer_topic_track(x) == "personal"],
    }


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


@contextmanager
def _proposal_lock(proposals_path: Path):
    lock_path = proposals_path.with_name(proposals_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _normalize_source_uri(raw_uri: str) -> str:
    uri = raw_uri.strip()
    if uri.startswith("git@github.com:"):
        uri = "https://github.com/" + uri[len("git@github.com:"):]
    parsed = urlsplit(uri)
    if parsed.hostname and parsed.hostname.lower() == "github.com":
        path = parsed.path.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return "https://github.com" + path.casefold()
    if parsed.scheme in {"http", "https"}:
        query = urlencode([
            (key, value)
            for key, value in parse_qsl(parsed.query)
            if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
        ])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), query, parsed.fragment))
    return uri.rstrip("/")


def _normalize_rel_vault_ref(raw_ref: str) -> str:
    s = str(raw_ref or "").strip().strip("`'\"")
    while s.startswith("../") or s.startswith("./"):
        s = s[3:] if s.startswith("../") else s[2:]
    s = s.lstrip("/").lower()
    if s.startswith("knowledge-vault/"):
        s = s.removeprefix("knowledge-vault/")
    return s


def _question_identity(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip()).casefold()


def _topic_alias_map(entries: dict[str, dict[str, Any]]) -> dict[str, str]:
    owners: dict[str, set[str]] = {}
    for key, item in entries.items():
        for alias in [key, *item.get("aliases", [])]:
            owners.setdefault(str(alias), set()).add(key)
    return {alias: next(iter(keys)) for alias, keys in owners.items() if len(keys) == 1}


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
