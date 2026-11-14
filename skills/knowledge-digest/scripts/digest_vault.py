#!/usr/bin/env python3
"""Zero-Token knowledge-vault archival, R09 topic discovery, and DC-006..008 blog publishing."""

from __future__ import annotations

import argparse
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
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILLS_ROOT = SKILL_DIR.parent
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
                parts.append(md.read_text(encoding="utf-8"))
    return "\n".join(parts).lower()


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
        query = urlencode([(key, value) for key, value in parse_qsl(parsed.query)
                           if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}])
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
            parts.append(text)

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


def _vault_coverage(vault_dir: Path) -> tuple[set[str], set[str]]:
    identities: set[str] = set()
    questions: set[str] = set()
    for folder in ("concepts", "craft", "thoughts", "raw"):
        for path in sorted((vault_dir / folder).glob("*.md")):
            meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            derived = [value for key, value in meta.items() if key.startswith("derived_")]
            # Raw material without a derived artifact is not a learned result.
            if folder == "raw" and not derived:
                continue
            identities.add(path.stem)
            for key in ("topic_key", "aliases", "topic_aliases"):
                values = meta.get(key, [])
                identities.update(str(v).strip() for v in (values if isinstance(values, list) else [values]) if v)
            if meta.get("question"):
                questions.add(_question_identity(str(meta["question"])))
            for value in derived:
                for ref in (value if isinstance(value, list) else [value]):
                    identities.add(Path(str(ref)).stem)
    return identities, questions


def _question_identity(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip()).casefold()


def _topic_alias_map(entries: dict[str, dict[str, Any]]) -> dict[str, str]:
    owners: dict[str, set[str]] = {}
    for key, item in entries.items():
        for alias in [key, *item.get("aliases", [])]:
            owners.setdefault(str(alias), set()).add(key)
    return {alias: next(iter(keys)) for alias, keys in owners.items() if len(keys) == 1}


class TopicLedgerError(RuntimeError):
    pass


def _load_topic_ledger_document(vault_dir: Path, topic_ledger_path: Path | None = None) -> dict[str, Any]:
    path = topic_ledger_path or vault_dir / "topic-ledger.json"
    if not path.is_file():
        raise TopicLedgerError(f"missing authoritative topic ledger: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TopicLedgerError(f"invalid topic ledger: {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise TopicLedgerError(f"invalid topic ledger document: {path}")
    return doc


def _load_topic_ledger(vault_dir: Path, topic_ledger_path: Path | None = None) -> dict[str, dict[str, Any]]:
    ledger_path = topic_ledger_path or vault_dir / "topic-ledger.json"
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
        if key in entries or str(item["learning_state"]).lower() not in {
            "unknown", "queued", "proposed", "approved", "exploring", "learned",
            "dismissed", "snoozed", "blocked", "transferred",
        }:
            raise TopicLedgerError(f"invalid or duplicate topic state: {key}")
        if any(not isinstance(item.get(field, []), list) for field in ("aliases", "coverage_refs")):
            raise TopicLedgerError(f"invalid topic metadata lists: {key}")
        entries[key] = dict(item)
    return entries


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


def _reconcile_proposals_history(
    proposals_path: Path | None, entries: dict[str, dict[str, Any]] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    by_topic: dict[str, dict[str, Any]] = {}
    creation_days: dict[str, str] = {}
    aliases = _topic_alias_map(entries or {})
    question_owners: dict[str, set[str]] = {}
    for key, item in (entries or {}).items():
        if item.get("question"):
            question_owners.setdefault(_question_identity(str(item["question"])), set()).add(key)
    if proposals_path and proposals_path.is_file():
        for line_no, line in enumerate(proposals_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid proposal history JSON at line {line_no}: {exc}") from exc
            required = ("id", "ts", "agent", "title")
            if not isinstance(rec, dict) or any(not str(rec.get(k) or "").strip() for k in required):
                raise ValueError(f"invalid proposal history record at line {line_no}: missing {required}")
            if not _parse_iso_ts(rec["ts"]):
                raise ValueError(f"invalid proposal timestamp at line {line_no}")
            key = str(rec.get("topic_key") or "").strip()
            if not key:
                for ref in (rec.get("origin_ref", ""), rec.get("ref", "")):
                    if str(ref).startswith(("learning-topic://", "knowledge://")):
                        key = str(ref).split("://", 1)[1].strip("/")
                        break
            if not key and str(rec["id"]).startswith(("learn-", "learning-", "knowledge-")):
                key = re.sub(r"^(?:learn|learning|knowledge)-", "", str(rec["id"]))
            if not key:
                continue
            key = aliases.get(key, key)
            owners = question_owners.get(_question_identity(str(rec.get("question") or rec["title"])), set())
            if key not in (entries or {}) and len(owners) == 1:
                key = next(iter(owners))
            # Immutable fields win; legacy history falls back to the FIRST ts, not status updates.
            created = rec.get("proposed_on") or rec.get("created_at")
            created_at = _parse_iso_ts(str(created or rec["ts"]))
            if created_at is None:
                raise ValueError(f"invalid immutable proposal time at line {line_no}")
            day = _cst_day_str(created_at)
            if created or key not in creation_days:
                creation_days[key] = min(creation_days.get(key, day), day)
            previous = by_topic.get(key)
            if previous is None or int(rec.get("revision", 0)) >= int(previous.get("revision", 0)):
                by_topic[key] = rec

    pending: set[str] = set()
    excluded: set[str] = set()
    now = now_utc or datetime.now(timezone.utc)
    # The ledger is authoritative over generic source text; newer proposal lifecycle
    # records can advance it, but a stale record cannot reopen a terminal ledger state.
    for key in set(entries or {}) | set(by_topic):
        ledger = (entries or {}).get(key, {})
        rec = by_topic.get(key, ledger)
        if int(ledger.get("revision") or 0) > int(rec.get("revision") or 0) or (ledger.get("learning_state") in {"learned", "dismissed", "blocked", "approved", "exploring", "transferred"} and int(rec.get("revision") or 0) <= int(ledger.get("revision") or 1)):
            rec = ledger
        state = str(rec.get("learning_state") or "").lower()
        status = str(rec.get("status") or "").lower()
        if state and state not in {"unknown", "queued", "proposed", "approved", "exploring", "learned",
                                   "dismissed", "snoozed", "blocked", "transferred"}:
            raise ValueError(f"invalid learning state for {key}: {state}")
        if state == "snoozed" or status == "snoozed":
            eligible = _parse_iso_ts(str(rec.get("next_eligible_at") or ""))
            changed = _parse_iso_ts(str(rec.get("updated_at") or rec.get("ts") or ""))
            eligible = eligible or (changed + timedelta(days=7) if changed else None)
            if eligible is None or now < eligible:
                excluded.add(key)
        elif state in {"proposed", "transferred"} or status == "transferred" or (not state and status == "pending"):
            pending.add(key)
            excluded.add(key)
        elif state not in {"queued"} or status in {"approved", "dismissed", "blocked", "transferred"}:
            excluded.add(key)
    return {"by_topic": by_topic, "pending_topics": pending, "excluded_topics": excluded,
            "creation_days": creation_days, "proposed_today_cst": {key for key, day in creation_days.items() if day == _cst_day_str(now)}}


def _scan_collected_tools_candidates(
    collected_dir: Path, existing_corpus: str, vault_dir: Path | None = None,
    ledger_entries: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not vault_dir or not collected_dir.is_dir():
        return []
    entries = ledger_entries if ledger_entries is not None else _load_topic_ledger(vault_dir)
    covered, covered_questions = _vault_coverage(vault_dir)
    source_paths: dict[str, Path] = {}
    uri_paths: dict[str, Path] = {}
    for note in sorted(collected_dir.glob("*.md")):
        if note.name.lower() == "readme.md":
            continue
        text = note.read_text(encoding="utf-8")
        if not text.strip():
            continue
        source_paths[note.stem] = note
        meta, _ = parse_frontmatter(text)
        uri = str(meta.get("source_uri") or "")
        if not uri:
            match = re.search(r"https?://[^\s<>\])]+|git@github\.com:[^\s<>\])]+", text)
            uri = match.group(0) if match else ""
        if uri:
            uri_paths[_normalize_source_uri(uri)] = note
    for repo in sorted((collected_dir / "repos").glob("*")):
        readme = next((p for p in (repo / "README.md", repo / "readme.md") if p.is_file()), None)
        if not readme or not readme.read_text(encoding="utf-8").strip():
            continue
        source_paths[repo.name] = readme
        config = repo / ".git" / "config"
        if config.is_file():
            match = re.search(r"(?m)^\s*url\s*=\s*(\S+)", config.read_text(encoding="utf-8"))
            if match:
                uri_paths[_normalize_source_uri(match.group(1))] = readme
    candidates: list[dict[str, Any]] = []
    topic_aliases = _topic_alias_map(entries)
    for key, item in entries.items():
        state = str(item["learning_state"]).lower()
        if state not in {"queued", "snoozed"} or item.get("scope", "personal") != "personal":
            continue
        aliases = {key} | {alias for alias in item.get("aliases", []) if topic_aliases.get(alias) == key}
        question = str(item["question"]).strip()
        if aliases & covered or _question_identity(question) in covered_questions:
            continue
        if len(question) < 12 or not re.search(r"[?？]|如何|怎样|为什么|\b(how|why|what|when)\b", question, re.I):
            continue
        source_id = str(item.get("source_id") or "")
        names = {source_id, *item.get("aliases", [])}
        source_ref = next((source_paths[name] for name in sorted(names) if name in source_paths), None)
        source_ref = source_ref or uri_paths.get(_normalize_source_uri(str(item["source_uri"])))
        if source_ref is None:
            continue
        candidates.append({
            "slug": key, "topic_key": key, "source_id": source_id,
            "source_uri": _normalize_source_uri(str(item["source_uri"])),
            "source_fingerprint": item["source_fingerprint"],
            "aliases": list(item.get("aliases", [])), "title": str(item.get("title") or question),
            "question": question, "category": str(item.get("category") or "工程与设计"),
            "source_kind": str(item.get("source_kind") or "collected_tools"),
            "source_ref": str(source_ref), "why_now": str(item.get("why_now") or question),
            "coverage_refs": list(item.get("coverage_refs", [])), "excerpt": question,
            "rank": int(item.get("rank") or item.get("priority_rank") or 99), "scope": "personal",
        })
    return sorted(candidates, key=lambda item: (item["rank"], item["topic_key"]))


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
    record.setdefault("created_at", record["ts"])
    record.setdefault("proposed_on", _cst_day_str(_parse_iso_ts(record["created_at"])))
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
    *, proposals_path: Path | None = None, state_path: Path | None = None,
    vault_dir: Path | None = None, topic_ledger_path: Path | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    resolved = _resolve_state_path(proposals_path, state_path, vault_dir)
    registry: dict[str, Any] = {"schema_version": 1, "updated_at": "", "topics": {}, "consumed_events": {}}
    if resolved.is_file():
        loaded = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or not isinstance(loaded.get("topics"), dict) or not isinstance(loaded.get("consumed_events", {}), dict):
            raise ValueError(f"invalid learning state registry: {resolved}")
        registry.update(loaded)
    ledger_path = topic_ledger_path or (vault_dir or DEFAULT_VAULT_DIR) / "topic-ledger.json"
    ledger = _load_topic_ledger(vault_dir or DEFAULT_VAULT_DIR, ledger_path) if ledger_path.is_file() else {}
    entries = {key: dict(item) for key, item in ledger.items()}
    for key, item in registry["topics"].items():
        if not isinstance(item, dict):
            raise ValueError(f"invalid learning state entry: {key}")
        if int(item.get("revision") or 0) >= int(entries.get(key, {}).get("revision") or 0):
            entries[key] = {**entries.get(key, {}), **item}
    history = _reconcile_proposals_history(proposals_path, entries, now_utc)
    for key, item in list(entries.items()):
        # A proposed-state mirror cannot outlive its authoritative proposal record.
        if item.get("learning_state") == "proposed" and key not in history["by_topic"] and key in ledger:
            entries[key] = dict(ledger[key])
    for key, rec in history["by_topic"].items():
        prior = entries.get(key, {})
        if int(rec.get("revision") or 0) < int(prior.get("revision") or 0) or (prior.get("learning_state") in {"learned", "dismissed", "blocked", "approved", "exploring", "transferred"} and int(rec.get("revision") or 0) <= int(prior.get("revision") or 1)):
            continue
        state = str(rec.get("learning_state") or "")
        if not state:
            status = rec.get("status")
            state = "transferred" if status == "approved" and str(rec.get("ref", "")).startswith("linear://") and not rec.get("exploration_authorized") else {"pending": "proposed", "approved": "approved", "dismissed": "dismissed"}.get(status, "unknown")
        entries[key] = {**prior, **rec, "topic_key": key, "proposal_id": rec["id"], "learning_state": state}
        if str(rec.get("ref", "")).startswith("linear://"):
            entries[key]["external_id"] = rec["ref"]
        if key in history["creation_days"]:
            entries[key]["proposed_on"] = history["creation_days"][key]
    registry["topics"] = entries
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


def _learning_brief(item: dict[str, Any]) -> dict[str, Any]:
    question = str(item.get("question") or item.get("title") or "").strip()
    title = str(item.get("title") or question).strip()
    why_now = str(item.get("why_now") or item.get("excerpt") or "").strip()
    source = str(item.get("source_id") or item.get("source_uri") or "").strip()
    return {
        "why_now": f"{why_now} 来源/缺口：{source}。",
        "preview_questions": [
            f"底层机制是什么，为什么会出现“{question}”这一现象？",
            f"最容易误判的边界条件是什么，怎样用一个工程例子验证“{title}”？",
            f"落地时最小可行的检查或实验是什么，结果应如何解释？",
        ],
        "recommended_command": f"/deep-research {title}" if item.get("source_kind") == "git_tech_stack" else f"/learn {title}",
    }


def _daily_digest_markdown(day: str, topics: list[dict[str, Any]]) -> str:
    lines = [f"<!-- GTT-LINEAR-DEDUP:daily-learn-{day} -->", "", "候选学习仅在 Owner Gate 批准后展开；本 issue 不自动归档或发布。", ""]
    for item in topics:
        brief = item["learning_brief"]
        lines.extend([
            f"- [ ] [{item['topic_key']}](learning-topic://{item['topic_key']}) {item['title']}",
            f"  - 为什么现在值得学：{brief['why_now']}",
            "  - 3 个核心痛点追问预览：",
            *[f"    1. {q}" for q in brief["preview_questions"]],
            f"  - 推荐展开命令：`{brief['recommended_command']}`",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _linear_transport_from_repo() -> Any:
    path = SKILLS_ROOT / "team-lead" / "scripts" / "linear-sync.py"
    mod = _load_helper_module("knowledge_digest_linear_sync", path)
    if mod is None or not getattr(mod, "load_api_key", None):
        raise RuntimeError("Linear transport is unavailable")
    token = mod.load_api_key()
    if not token:
        raise RuntimeError("Linear credentials are not configured; use the existing team-lead credential setup")
    return mod.LinearClient(token)


def _upsert_linear_daily_issue(client: Any, day: str, topics: list[dict[str, Any]]) -> dict[str, Any]:
    title = f"[Daily Learn {day}] 今日 3 个推荐学习主题"
    marker = f"GTT-LINEAR-DEDUP:daily-learn-{day}"
    issues = client.list_issues("TIA", query=title, limit=50)
    issue = next((x for x in issues if marker in str(x.get("description") or "") or x.get("title") == title), None)
    description = _daily_digest_markdown(day, topics)
    if issue:
        return client.update_issue(issue.get("id") or issue.get("identifier"), title=title, description=description)
    team = client.team_metadata("TIA")
    state_id = next((s.get("id") for s in team.get("states", {}).get("nodes", []) if s.get("type") == "backlog"), None)
    return client.create_issue(team_id=team["id"], title=title, description=description, state_id=state_id)


def discover_topics(
    *,
    top: int | None = None,
    propose_aidash: bool = False,
    propose_linear: bool = False,
    linear_transport: Any | None = None,
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
    propose_aidash = propose_aidash or propose_linear
    with _proposal_lock(proposals_path):
        now_dt = now_utc or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)
        # Per TK1 contract: proposal discovery never materializes a card, even when a
        # legacy caller passes --materialize. Archival is a separate explicit command.
        eff_materialize = bool(materialize_cards) if not propose_aidash else False
        try:
            existing_corpus = _existing_vault_corpus(vault_dir)
            try:
                ledger_entries = _load_topic_ledger(vault_dir, topic_ledger_path)
            except TopicLedgerError:
                if propose_aidash:
                    raise
                ledger_entries = {}
            registry = _load_learning_state_registry(proposals_path=proposals_path, state_path=state_path, vault_dir=vault_dir, topic_ledger_path=topic_ledger_path, now_utc=now_dt)
            effective_entries = registry["topics"]
            history = _reconcile_proposals_history(proposals_path, effective_entries, now_dt)
        except (TopicLedgerError, ValueError) as exc:
            return {"ok": False, "status": "health_error", "error": str(exc), "selected_count": 0, "topics": [], "proposals_written": []}
        excluded_topics: set[str] = set(history["excluded_topics"])
        pending_topics: set[str] = set(history["pending_topics"])
        proposed_today_cst: set[str] = set(history["proposed_today_cst"])

        raw_candidates = (
            _scan_collected_tools_candidates(collected_tools_dir, existing_corpus, vault_dir=vault_dir, ledger_entries=ledger_entries)
            + ([] if propose_aidash else _scan_git_tech_stack_candidates(git_roots, existing_corpus))
        )
        daily_topics = registry.get("daily_linear_issues", {}).get(_cst_day_str(now_dt), {})
        daily_topic_keys = set(daily_topics.get("topic_keys", [])) if isinstance(daily_topics, dict) else set()
        linear_client = linear_transport
        if propose_linear:
            try:
                linear_client = linear_client or _linear_transport_from_repo()
                day_title = f"[Daily Learn {_cst_day_str(now_dt)}] 今日 3 个推荐学习主题"
                remote_issues = linear_client.list_issues("TIA", query=day_title, limit=50)
                remote = next((x for x in remote_issues if f"GTT-LINEAR-DEDUP:daily-learn-{_cst_day_str(now_dt)}" in str(x.get("description") or "") or x.get("title") == day_title), None)
                if remote and not daily_topic_keys:
                    daily_topic_keys = set(re.findall(r"learning-topic://([a-z0-9-]+)", str(remote.get("description") or "")))
                    registry.setdefault("daily_linear_issues", {})[_cst_day_str(now_dt)] = {"topic_keys": sorted(daily_topic_keys), "external_id": f"linear://{remote.get('identifier') or remote.get('id')}", "url": remote.get("url")}
            except Exception as exc:
                return {"ok": False, "status": "linear_error", "error": f"Linear daily digest lookup failed: {exc}", "selected_count": 0, "topics": [], "proposals_written": []}
        deduped: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        seen_questions: set[str] = set()
        excluded_count = 0
        excluded_reasons = {key: reason for key, entry in effective_entries.items() if (reason := _topic_exclusion_reason({"topic_key": key}, registry, now_dt.astimezone(_CST_TZ).date()))}
        topic_aliases = _topic_alias_map(ledger_entries)
        for cand in raw_candidates:
            t_key = str(cand.get("topic_key") or cand.get("slug") or "").strip()
            question = re.sub(r"\s+", " ", str(cand.get("question") or cand.get("title") or "").strip()).casefold()
            aliases = {str(a).strip() for a in cand.get("aliases", []) if topic_aliases.get(str(a)) == t_key} | {t_key}
            question_key = question
            if not t_key or (aliases & seen_keys) or question_key in seen_questions:
                continue
            seen_keys |= aliases
            seen_questions.add(question_key)
            if aliases & excluded_topics and not (propose_linear and t_key in daily_topic_keys):
                excluded_count += 1
                continue
            deduped.append(cand)

        # Explicit multi-topic/Linear mode opts into a larger daily quota; legacy AIDash
        # mode remains one candidate per CST day and pending cap 3.
        requested_quota = max(1, top or (3 if propose_linear else 1))
        daily_cap_reached = propose_aidash and not propose_linear and len(proposed_today_cst) >= 1
        pending_cap_reached = propose_aidash and not propose_linear and len(pending_topics) >= 3
        if propose_linear and daily_topic_keys:
            deduped = [c for c in deduped if c.get("topic_key") in daily_topic_keys] or deduped
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
                "quota_blocked_reason": "pending_cap_reached" if pending_cap_reached else "daily_slot_already_used" if daily_cap_reached else None,
                "excluded_reasons": excluded_reasons,
                "materialized": False,
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
        eff_top = requested_quota if (propose_linear or top is not None) else (1 if propose_aidash else 1)
        selected = ranked[:eff_top]
        for item in selected:
            item["learning_brief"] = _learning_brief(item)
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
        now_iso = now_dt.isoformat(timespec="seconds").replace("+00:00", "Z")
        today_cst = _cst_day_str(now_dt)

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
                prior = effective_entries.get(slug, {})
                record = {
                    "id": str(prior.get("proposal_id") or prior.get("id") or f"learning-topic-{slug}"),
                    "ref": ref_uri,
                    "origin_ref": ref_uri,
                    "topic_key": slug,
                    "source": "Learning",
                    "scope": "personal",
                    "source_uri": source_uri,
                    "source_fingerprint": item.get("source_fingerprint"),
                    "why_now": why_now,
                    "coverage_refs": cov_refs,
                    "learning_state": "proposed",
                    "proposed_on": history["creation_days"].get(slug, str(prior.get("proposed_on") or today_cst)),
                    "revision": int(prior.get("revision") or 0) + 1,
                    "ts": now_iso,
                    "created_at": prior.get("created_at") or now_iso,
                    "question": item["question"],
                    "agent": "knowledge-digest",
                    "title": item["title"],
                    "detail": detail_text,
                    "priority": "P2",
                    "status": "pending",
                }
                if not prior.get("proposal_id") or slug not in history["by_topic"]:
                    _append_valid_proposal(proposals_path, record)
                    proposals_written.append(record)
                else:
                    record = {**prior, **record, "revision": int(prior.get("revision") or 1)}
                registry["topics"][slug] = {**prior, **item, **record, "proposal_id": record["id"]}

        linear_issue = None
        if propose_linear:
            try:
                client = linear_client or linear_transport or _linear_transport_from_repo()
                day = _cst_day_str(now_dt)
                linear_issue = _upsert_linear_daily_issue(client, day, selected)
                daily_registry = registry.setdefault("daily_linear_issues", {})
                daily_registry[day] = {
                    "external_id": f"linear://{linear_issue.get('identifier') or linear_issue.get('id')}",
                    "url": linear_issue.get("url"),
                    "topic_keys": [str(x.get("topic_key")) for x in selected],
                    "updated_at": now_iso,
                }
                for item in selected:
                    entry = registry["topics"].get(item["topic_key"], {})
                    entry["daily_linear_issue"] = daily_registry[day]["external_id"]
                    registry["topics"][item["topic_key"]] = entry
            except Exception as exc:
                return {"ok": False, "status": "linear_error", "error": f"Linear daily digest failed: {exc}", "selected_count": 0, "topics": [], "proposals_written": proposals_written}

        if proposals_written or linear_issue:
            _save_learning_state_registry(registry, proposals_path=proposals_path, state_path=state_path, vault_dir=vault_dir)

        return {
            "ok": True,
            "status": "selected",
            "materialized": bool(archived_cards),
            "quota_blocked_reason": None,
            "excluded_reasons": excluded_reasons,
            "candidate_count": len(deduped),
            "excluded_count": excluded_count,
            "pending_learning_count": len(pending_topics) + len(proposals_written),
            "selected_count": len(selected),
            "topics": selected,
            "archived_cards": archived_cards,
            "proposals_written": proposals_written,
            "linear_issue": linear_issue,
        }


def _handle_learning_event_locked(
    *,
    action: str,
    topic_key: str | None = None,
    ref: str | None = None,
    external_id: str | None = None,
    event_id: str | None = None,
    note: str = "",
    revision: int | None = None,
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
        now_utc=now_dt,
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

    resolved_tk = _topic_alias_map(topics_reg).get(resolved_tk, resolved_tk)
    entry = dict(topics_reg.get(resolved_tk, {}))
    entry.setdefault("created_at", now_iso)
    entry.setdefault("proposed_on", _cst_day_str(_parse_iso_ts(entry["created_at"])))
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

    if revision is not None and revision != current_rev:
        return {"ok": False, "conflict": True, "error": "stale topic revision", "topic_key": resolved_tk, "revision": current_rev, "learning_state": current_state}

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
        prop_record.update({key: entry[key] for key in ("created_at", "proposed_on", "external_id") if key in entry})
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
        if current_state in {"learned", "dismissed", "blocked"} and not entry.get("reopened"):
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
        if current_state in {"approved", "exploring"}:
            return {"ok": True, "deduplicated": True, "topic_key": resolved_tk, "learning_state": current_state, "revision": current_rev, "exploration_authorized": True, "exploration_started": False, "exploration_task_key": entry.get("exploration_task_key"), "blog_published": False, "design_studio_synced": False}
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
        prop_record.update({key: entry[key] for key in ("created_at", "proposed_on", "external_id") if key in entry})
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
        prop_record.update({key: entry[key] for key in ("created_at", "proposed_on", "external_id") if key in entry})
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
        prop_record.update({key: entry[key] for key in ("created_at", "proposed_on", "external_id") if key in entry})
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
        prop_record.update({key: entry[key] for key in ("created_at", "proposed_on", "external_id") if key in entry})
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


def handle_learning_event(*, proposals_path: Path = DEFAULT_PROPOSALS_PATH, **kwargs: Any) -> dict[str, Any]:
    with _proposal_lock(proposals_path):
        return _handle_learning_event_locked(proposals_path=proposals_path, **kwargs)


def _reconcile_collected_tools_entities(collected_dir: Path, existing_corpus: str, *, vault_dir: Path, topic_ledger_path: Path | None = None, include_unreconciled_repos: bool = False):
    entries = _load_topic_ledger(vault_dir, topic_ledger_path)
    candidates = _scan_collected_tools_candidates(collected_dir, existing_corpus, vault_dir, entries)
    sources = {str(item.get("source_id") or "") for item in entries.values()}
    local_sources = {p.name for p in (collected_dir / "repos").glob("*") if p.is_dir()}
    learned = sorted({str(item.get("source_id")) for item in entries.values() if item["learning_state"] == "learned"})
    return candidates, {"merged_entities": sorted({p.stem for p in collected_dir.glob("*.md") if (collected_dir / "repos" / p.stem).is_dir()}), "covered_sources": learned, "integrated_sources": learned, "unknown_sources": sorted(local_sources - sources)}


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
    p_disc.add_argument("--propose-linear", action="store_true", help="Explicitly create/update the daily TIA digest issue")
    p_disc.add_argument("--no-materialize", action="store_true")
    p_disc.add_argument("--materialize-before-approval", action="store_true", help="Deprecated; cannot override candidate-only gate")
    p_disc.add_argument("--materialize", action="store_true", help="Explicit standalone archival only")
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
        p_evt.add_argument("--revision", type=int, default=None)
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
            propose_linear=args.propose_linear,
            vault_dir=args.vault_dir,
            collected_tools_dir=args.collected_tools_dir,
            git_roots=() if getattr(args, "no_git", False) else DEFAULT_GIT_ROOTS,
            proposals_path=args.proposals_path,
            state_path=args.state_path,
            topic_ledger_path=args.topic_ledger_path,
            materialize_cards=False if args.no_materialize else args.materialize,
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
            revision=args.revision,
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
