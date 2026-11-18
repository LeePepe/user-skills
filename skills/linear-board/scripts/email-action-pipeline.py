#!/usr/bin/env python3
"""Extract actionable engineering emails from WorkIQ / DSH Email, pre-stage M2 plans, and sync Linear issues.

This script bridges Cindy's daily WorkIQ email briefing / DSH email sync with
personal-m2-assistant and Linear Team TIA:
1. Parses Outlook OWA deep links (`https://outlook.office.com/mail/.../applink/read/<EWS_ItemId>`)
   and `<EWS_ItemId>` deduplication fingerprints from WorkIQ JSON `artifacts[].parts[].data[].targetLink`
   (supporting both dict-by-citationId and list-of-objects shapes) as well as markdown response text.
2. Identifies actionable engineering emails (e.g., App Store Connect `ITMS-*` build warnings,
   `Info.plist` / entitlement / manifest / config / code changes).
3. Invokes `m2-router.py --plan` to pre-stage `<slug>.plan.json` under
   `~/Development/handoff/email-action-plans/` (mode 0600, never auto-applying).
4. Idempotently creates or updates a Linear Actionable Issue in Team TIA with
   `Needs Decision` label, `Assignee = Owner`, clickable `[📧 打开原邮件]` link,
   and `<!-- GTT-EMAIL-ACTION:v1 {...} -->` contract metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit

OWNER_USER_ID = "0680d5e4-720a-4724-a1d4-b9660c0d1495"
DEFAULT_PROJECT = "user-skills"
DEFAULT_MILESTONE = "Skills Workflow"
DEFAULT_LABEL = "Needs Decision"
DEFAULT_PLANS_DIR = Path("~/Development/handoff/email-action-plans").expanduser()
ROOT = Path(__file__).resolve().parents[3]
SYNC_PATH = ROOT / "skills" / "team-lead" / "scripts" / "linear-sync.py"
ROUTER_PATH = ROOT / "skills" / "personal-m2-assistant" / "scripts" / "m2-router.py"

APPLINK_RE = re.compile(
    r"https://outlook\.office(?:365)?\.com/mail/[^\s)>\"']+/applink/read/([^/?#\s)>\"']+)[^\s)>\"']*",
    re.I,
)
OWA_ITEM_LINK_RE = re.compile(
    r"https://outlook\.office(?:365)?\.com/owa/\?[^\s)>\"']*ItemID=([^&#\s)>\"']+)[^\s)>\"']*",
    re.I,
)
EMAIL_ACTION_MARKER_RE = re.compile(
    r"(?m)^[ \t]*<!--\s*GTT-EMAIL-ACTION:v1\s+(\{[^\n]*?\})\s*-->[ \t]*$"
)
ITMS_RE = re.compile(r"\bITMS-(\d{4,6})\b", re.I)
PLIST_KEY_RE = re.compile(r"\b(NS[A-Za-z0-9]+UsageDescription|ITSAppUsesNonExemptEncryption|WKAppBoundDomains|CFBundle[A-Za-z0-9]+)\b")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def extract_outlook_item_id(value: Any) -> str:
    """Extract canonical EWS ItemId from an OWA applink/read URL, OWA ItemID URL, or raw ID."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"/applink/read/([^/?#\s)>\"']+)", text, re.I)
    if match:
        return unquote(match.group(1)).strip().rstrip("/")
    parsed = urlsplit(text)
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        for key in ("ItemID", "itemId", "itemid"):
            if params.get(key) and params[key][0].strip():
                return unquote(params[key][0]).strip()
    if re.fullmatch(r"[A-Za-z0-9+_=-]{20,}", text):
        return text
    return ""


def normalize_outlook_target_link(url: Any) -> str:
    """Normalize an Outlook deep link, stripping citation fragments and transient search params."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    if parts.username or parts.password:
        return ""
    query_pairs = []
    if parts.query:
        for chunk in parts.query.split("&"):
            if not chunk:
                continue
            key = chunk.split("=", 1)[0]
            if key == "EntityRepresentationId":
                continue
            query_pairs.append(chunk)
    clean_query = "&".join(query_pairs)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, clean_query, ""))


def extract_workiq_citations(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Extract citationId -> {citation_id, target_link, email_item_id, ...} from WorkIQ JSON artifacts."""
    citations: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return citations
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        return citations
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        parts = artifact.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            data = part.get("data")
            entries: list[tuple[str, dict[str, Any]]] = []
            if isinstance(data, dict):
                if "targetLink" in data or "webLink" in data:
                    cid = str(data.get("citationId") or data.get("id") or "0")
                    entries.append((cid, data))
                else:
                    for key, item in data.items():
                        if isinstance(item, dict):
                            entries.append((str(key), item))
            elif isinstance(data, list):
                for index, item in enumerate(data):
                    if isinstance(item, dict):
                        cid = str(item.get("citationId") or item.get("id") or index)
                        entries.append((cid, item))
            for cid, item in entries:
                raw_link = item.get("targetLink") or item.get("webLink") or item.get("url") or ""
                clean_link = normalize_outlook_target_link(raw_link)
                item_id = extract_outlook_item_id(clean_link or item.get("email_item_id") or item.get("itemId") or item.get("id"))
                if not clean_link and not item_id:
                    continue
                record = {
                    **item,
                    "citation_id": cid,
                    "target_link": clean_link,
                    "email_item_id": item_id,
                    "is_cited": bool(item.get("isCitedInResponse", True)),
                }
                citations[cid] = record
    return citations


def _clean_markdown_inline(text: str) -> str:
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", str(text or ""))
    cleaned = re.sub(r"[*`]+", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_response_sections(text: str, citations: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse numbered or top-level bulleted email sections from WorkIQ markdown response."""
    if not text.strip():
        return []
    by_item_id = {c["email_item_id"]: c for c in citations.values() if c.get("email_item_id")}
    field_label = r"(?:Sender|From|Received|Time|What(?:\s+they\s+want|\s+to\s+do)?|Action(?:\s+needed)?|Urgency|Deadline|Context|Summary|Outlook(?:\s+deep)?\s+link)\s*:"
    pattern = re.compile(
        rf"(?m)^(?:(\d+)\.\s+\*\*(.+?)\*\*|[-*]\s+\*\*(?!{field_label})(.+?)\*\*(?:[ \t]*[｜|—–-][ \t]*(.+))?)[ \t]*$",
        re.I,
    )
    matches = list(pattern.finditer(text))
    sections: list[dict[str, Any]] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end]
        raw_header = match.group(2) or match.group(3) or ""
        subject = _clean_markdown_inline(raw_header)
        sender_match = re.search(r"(?mi)^\s*[-*]\s*\*\*Sender:\*\*\s*(.+)$", block)
        received_match = re.search(r"(?mi)^\s*[-*]\s*\*\*Received:\*\*\s*(.+)$", block)
        what_match = re.search(r"(?mi)^\s*[-*]\s*\*\*What(?:\s+they\s+want|\s+to\s+do):\*\*\s*(.+)$", block)
        urgency_match = re.search(r"(?mi)^\s*[-*]\s*\*\*(?:Urgency|Deadline):\*\*\s*(.+)$", block)
        context_match = re.search(r"(?mi)^\s*[-*]\s*\*\*Context:\*\*\s*(.+)$", block)

        sender = _clean_markdown_inline(sender_match.group(1)) if sender_match else ""
        received = _clean_markdown_inline(received_match.group(1)) if received_match else ""
        what_to_do = _clean_markdown_inline(what_match.group(1)) if what_match else ""
        deadline = _clean_markdown_inline(urgency_match.group(1)) if urgency_match else ""
        context = _clean_markdown_inline(context_match.group(1)) if context_match else ""
        if not what_to_do and match.group(4):
            what_to_do = _clean_markdown_inline(match.group(4))

        target_link = ""
        email_item_id = ""
        citation_id = ""
        frag_matches = re.findall(r"https://outlook\.office(?:365)?\.com/mail/[^\s)>\"']+#([A-Za-z0-9_-]+)", block, re.I)
        for frag in frag_matches:
            if frag in citations:
                citation_id = frag
                target_link = citations[frag]["target_link"]
                email_item_id = citations[frag]["email_item_id"]
                break
        if not target_link:
            ref_matches = re.findall(r"\[(?:citation|ref)?\s*#?([A-Za-z0-9]{4,16})\]", block, re.I)
            for ref in ref_matches:
                if ref in citations:
                    citation_id = ref
                    target_link = citations[ref]["target_link"]
                    email_item_id = citations[ref]["email_item_id"]
                    break
        if not target_link:
            link_match = APPLINK_RE.search(block) or OWA_ITEM_LINK_RE.search(block)
            if link_match:
                raw_url = link_match.group(0)
                email_item_id = extract_outlook_item_id(raw_url)
                if email_item_id in by_item_id:
                    citation_id = by_item_id[email_item_id]["citation_id"]
                    target_link = by_item_id[email_item_id]["target_link"]
                else:
                    target_link = normalize_outlook_target_link(raw_url)
        if not email_item_id and not target_link:
            continue
        sections.append({
            "subject": subject,
            "sender": sender,
            "received_at": received,
            "what_to_do": what_to_do,
            "deadline": deadline,
            "context": context or _clean_markdown_inline(block),
            "target_link": target_link,
            "email_item_id": email_item_id,
            "citation_id": citation_id,
            "raw_block": block.strip(),
        })
    return sections


def extract_emails_from_workiq(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract deduplicated email records with verified Outlook deep links & EWS ItemIds."""
    if not isinstance(payload, dict):
        return []
    citations = extract_workiq_citations(payload)
    by_item_id = {c["email_item_id"]: c for c in citations.values() if c.get("email_item_id")}
    records: list[dict[str, Any]] = []

    for list_key in ("emails", "items", "messages", "cards"):
        raw_list = payload.get(list_key)
        if isinstance(raw_list, list):
            for entry in raw_list:
                if not isinstance(entry, dict):
                    continue
                cid = str(entry.get("citationId") or entry.get("citation_id") or "")
                cit = citations.get(cid, {})
                raw_link = (
                    entry.get("targetLink")
                    or entry.get("target_link")
                    or entry.get("webLink")
                    or cit.get("target_link")
                    or ""
                )
                target_link = normalize_outlook_target_link(raw_link)
                item_id = (
                    str(entry.get("email_item_id") or "").strip()
                    or extract_outlook_item_id(target_link)
                    or extract_outlook_item_id(entry.get("id") or entry.get("messageId") or "")
                    or str(cit.get("email_item_id") or "").strip()
                )
                if not target_link and item_id in by_item_id:
                    target_link = by_item_id[item_id]["target_link"]
                if not item_id and not target_link:
                    continue
                records.append({
                    "subject": str(entry.get("subject") or entry.get("title") or cit.get("subject") or "").strip(),
                    "sender": str(entry.get("sender") or entry.get("from") or cit.get("sender") or "").strip(),
                    "received_at": str(entry.get("received_at") or entry.get("receivedAt") or entry.get("receivedDateTime") or entry.get("latestTimestamp") or cit.get("received_at") or "").strip(),
                    "what_to_do": str(entry.get("what_to_do") or entry.get("whatToDo") or entry.get("action") or cit.get("what_to_do") or "").strip(),
                    "deadline": str(entry.get("deadline") or cit.get("deadline") or "").strip(),
                    "context": str(entry.get("context") or entry.get("bodyPreview") or entry.get("preview") or cit.get("context") or "").strip(),
                    "target_link": target_link,
                    "email_item_id": item_id,
                    "citation_id": cid or str(cit.get("citation_id") or ""),
                    "target_topic": str(entry.get("target_topic") or cit.get("target_topic") or "").strip(),
                    "target_repo": str(entry.get("target_repo") or cit.get("target_repo") or "").strip(),
                    "target_files": list(entry.get("target_files") or cit.get("target_files") or []),
                    "slug": str(entry.get("slug") or cit.get("slug") or "").strip(),
                    "actionable_engineering": entry.get("actionable_engineering"),
                })

    texts: list[str] = []
    if isinstance(payload.get("response"), str) and payload["response"].strip():
        texts.append(payload["response"])
    for artifact in payload.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        for part in artifact.get("parts") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str) and part["text"].strip():
                if part["text"] not in texts:
                    texts.append(part["text"])
    for text in texts:
        records.extend(_parse_response_sections(text, citations))

    for cid, cit in citations.items():
        if any(str(cit.get(k) or "").strip() for k in ("subject", "what_to_do", "whatToDo", "context", "summary")):
            records.append({
                "subject": str(cit.get("subject") or cit.get("title") or "").strip(),
                "sender": str(cit.get("sender") or cit.get("from") or "").strip(),
                "received_at": str(cit.get("received_at") or cit.get("receivedAt") or "").strip(),
                "what_to_do": str(cit.get("what_to_do") or cit.get("whatToDo") or "").strip(),
                "deadline": str(cit.get("deadline") or "").strip(),
                "context": str(cit.get("context") or cit.get("summary") or "").strip(),
                "target_link": cit["target_link"],
                "email_item_id": cit["email_item_id"],
                "citation_id": cid,
                "target_topic": str(cit.get("target_topic") or "").strip(),
                "target_repo": str(cit.get("target_repo") or "").strip(),
                "target_files": list(cit.get("target_files") or []),
                "slug": str(cit.get("slug") or "").strip(),
                "actionable_engineering": cit.get("actionable_engineering"),
            })

    deduped: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in records:
        key = item.get("email_item_id") or item.get("target_link") or ""
        if not key or key in seen_ids:
            continue
        seen_ids.add(key)
        deduped.append(item)
    return deduped


def classify_actionable_email(email: dict[str, Any]) -> dict[str, Any] | None:
    """Determine if an email requires an engineering code/config PR and derive its M2 routing spec."""
    if not isinstance(email, dict):
        return None
    item_id = str(email.get("email_item_id") or "").strip()
    target_link = normalize_outlook_target_link(email.get("target_link"))
    if not item_id or not target_link:
        return None

    subject = str(email.get("subject") or "").strip()
    sender = str(email.get("sender") or "").strip()
    what_to_do = str(email.get("what_to_do") or "").strip()
    context = str(email.get("context") or "").strip()
    corpus = f"{subject}\n{sender}\n{what_to_do}\n{context}"

    if email.get("actionable_engineering") is False:
        return None
    if re.search(r"(?i)\b(?:no action|fyi only)\b|无需操作|无需直接操作", what_to_do):
        return None
    if re.search(r"(?i)(?:github\.com|msft\.ghe\.com).*?(?:pull request|review|comment)", corpus) and not email.get("actionable_engineering"):
        return None

    itms_match = ITMS_RE.search(corpus)
    plist_match = PLIST_KEY_RE.search(corpus)
    has_code_signal = bool(
        email.get("actionable_engineering") is True
        or itms_match
        or plist_match
        or re.search(r"(?i)\b(?:Info\.plist|AndroidManifest\.xml|build\.gradle|Package\.swift|Podfile|entitlements)\b", corpus)
        or (
            re.search(r"(?i)\b(?:App Store Connect|TestFlight|build|binary|compile|deprecation|CVE-\d{4}-\d+)\b", corpus)
            and re.search(r"(?i)\b(?:add|fix|update|remove|patch|upgrade|补齐|修复|添加|升级)\b", corpus)
        )
    )
    if not has_code_signal:
        return None

    target_topic = str(email.get("target_topic") or "").strip()
    target_repo = str(email.get("target_repo") or "").strip()
    target_files = [str(f).strip() for f in (email.get("target_files") or []) if str(f).strip()]
    slug = str(email.get("slug") or "").strip()

    is_bing_china = bool(re.search(r"(?i)BingChina|微软必应", corpus))
    if not target_topic:
        if is_bing_china or itms_match:
            target_topic = "sapphire-china-bugs"
        elif re.search(r"(?i)Smart\s*Banner|Adjust|Universal\s*Link", corpus):
            target_topic = "sapphire-smartbanner"
        elif re.search(r"(?i)Notification|Push|Weather", corpus):
            target_topic = "sapphire-notification"
        elif re.search(r"(?i)WebIQ|Aquamarine", corpus):
            target_topic = "webiq-search"
        else:
            target_topic = "skills-workflow"

    if not target_repo:
        if target_topic.startswith("sapphire-"):
            target_repo = "san/bing-growth-sapphire-app"
        elif target_topic == "webiq-search":
            target_repo = "WebIQSearch"
        else:
            target_repo = "LeePepe/user-skills"

    if not target_files:
        if is_bing_china and (plist_match or "Info.plist" in corpus):
            target_files = ["iOS/Sapphire/BingChina/Info.plist"]
        else:
            found_files = re.findall(r"\b(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:plist|swift|kt|java|xml|json|yaml|yml|py|ts|js)\b", corpus)
            target_files = list(dict.fromkeys(found_files))

    version_match = re.search(r"\b(\d+\.\d+\.\d{6,10})\b", corpus)
    build_version = version_match.group(1) if version_match else ""
    itms_code = f"ITMS-{itms_match.group(1)}" if itms_match else ""
    plist_key = plist_match.group(1) if plist_match else ""

    if not slug:
        if is_bing_china and itms_code:
            slug = f"bingchina-{itms_code.lower()}"
        elif itms_code:
            slug = itms_code.lower()
        else:
            words = re.findall(r"[a-z0-9]+", subject.casefold())[:4]
            digest = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:6]
            slug = "-".join([*words, digest]) if words else f"email-action-{digest}"
    slug = re.sub(r"[^a-z0-9-]+", "-", slug.casefold()).strip("-")

    if is_bing_china and itms_code and plist_key:
        ver_label = f" {build_version}" if build_version else ""
        issue_title = f"[Email Action] 修复微软必应{ver_label} {itms_code}（BingChina Info.plist 补齐 {plist_key}）"
        brief_summary = f"修复必应 {itms_code} 定位权限键"
        one_line_summary = (
            f"针对 App Store Connect `{itms_code}` 告警，在 `iOS/Sapphire/BingChina/Info.plist` "
            f"补齐 `{plist_key}` 用途说明（对齐 `Bing/Info.plist` 与 `.lproj` 本地化）并提交 GHE PR。"
        )
        m2_prompt = (
            f"处理 App Store Connect 邮件告警 {itms_code}（微软必应{ver_label}）："
            f"请在 `{target_repo}` 的独立 git worktree 中（严禁直接修改主目录 main），"
            f"对齐 `iOS/Sapphire/Bing/Info.plist` 与各 `.lproj/Localizable.strings` 多语言翻译，"
            f"为 `iOS/Sapphire/BingChina/Info.plist` 补齐 `<key>{plist_key}</key>`，"
            f"运行 `plutil -lint iOS/Sapphire/BingChina/Info.plist` 验证通过后提交 GHE PR，并回写 PR 链接。"
            f"\n原邮件链接：{target_link}"
        )
    else:
        short_subj = _clean_markdown_inline(subject)[:48] or slug
        issue_title = f"[Email Action] {short_subj}"
        brief_summary = _clean_markdown_inline(what_to_do or subject)[:22] or "处理邮件工程变更"
        one_line_summary = what_to_do or context or subject
        files_hint = f"（目标文件：{', '.join(target_files)}）" if target_files else ""
        m2_prompt = (
            f"处理可执行工程邮件（{subject}）：请在 `{target_repo}` 的独立 git worktree 中完成修复{files_hint}，"
            f"要求：{one_line_summary}。完成后运行验证并提交 PR。\n原邮件链接：{target_link}"
        )

    return {
        "email_item_id": item_id,
        "target_link": target_link,
        "subject": subject,
        "sender": sender,
        "received_at": str(email.get("received_at") or "").strip(),
        "deadline": str(email.get("deadline") or "Next build / Before ship").strip(),
        "what_to_do": what_to_do or one_line_summary,
        "context": context,
        "slug": slug,
        "target_topic": target_topic,
        "target_repo": target_repo,
        "target_files": target_files,
        "issue_title": issue_title,
        "brief_summary": brief_summary[:25],
        "one_line_summary": one_line_summary,
        "m2_prompt": m2_prompt,
    }


def stage_email_action_plan(
    action: dict[str, Any],
    *,
    plans_dir: Path = DEFAULT_PLANS_DIR,
    router_path: Path = ROUTER_PATH,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Run `m2-router.py --plan` (never `--apply`) and persist `<slug>.plan.json`."""
    slug = re.sub(r"[^a-z0-9-]+", "-", str(action["slug"]).casefold()).strip("-")
    if not slug:
        raise ValueError("action slug is required")
    plans_root = plans_dir.expanduser().resolve()
    plan_file = plans_root / f"{slug}.plan.json"
    mock_jev = json.dumps({
        "route_mode": "ROUTE_EXISTING_M2",
        "primary_topic": action["target_topic"],
        "has_unmapped_domain": False,
    })
    argv = [
        sys.executable,
        str(router_path),
        "--plan",
        "--message",
        str(action["m2_prompt"]),
        "--mock-jev-json",
        mock_jev,
        "--no-dsh",
        "--transport",
        "herdr",
        "--json",
    ]
    exec_fn = runner or (lambda cmd, timeout: subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False))
    completed = exec_fn(argv, 30)
    try:
        plan = json.loads(completed.stdout) if completed.stdout else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"m2-router --plan returned invalid JSON: {exc}") from exc
    if completed.returncode != 0 or not isinstance(plan, dict) or not plan.get("plan_id") or not plan.get("targets"):
        raise RuntimeError("m2-router --plan did not produce a valid dispatch plan")

    for target in plan.get("targets") or []:
        if isinstance(target, dict):
            target["topic_id"] = action["target_topic"]
            target["domain_status"] = "MAPPED_DOMAIN"
            target.pop("proposed_domain", None)
    plan["new_domain_candidates"] = []
    plan["email_action"] = {
        "version": 1,
        "email_item_id": action["email_item_id"],
        "target_link": action["target_link"],
        "subject": action["subject"],
        "sender": action["sender"],
        "received_at": action["received_at"],
        "slug": slug,
        "target_topic": action["target_topic"],
        "target_repo": action["target_repo"],
        "target_files": action["target_files"],
        "plan_file": str(plan_file),
    }
    _atomic_write_json(plan_file, plan)
    return plan_file, plan


def parse_email_action_marker(description: str) -> dict[str, Any] | None:
    """Parse standalone `<!-- GTT-EMAIL-ACTION:v1 {...} -->` metadata from an issue description."""
    text = str(description or "")
    if "GTT-EMAIL-ACTION:v1" not in text:
        return None
    in_fence = False
    for line in text.splitlines():
        if re.match(r"^\s*(?:```|~~~)", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = EMAIL_ACTION_MARKER_RE.match(line)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        item_id = str(payload.get("email_item_id") or "").strip()
        plan_file = str(payload.get("plan_file") or "").strip()
        if not item_id or not plan_file or item_id == "..." or plan_file == "...":
            continue
        return payload
    return None


def render_email_action_issue_description(action: dict[str, Any], plan_file: Path, plan: dict[str, Any]) -> str:
    """Render the 3-layer Linear issue description with top clickable email deep link and bottom contract marker."""
    files_display = ", ".join(f"`{f}`" for f in (action.get("target_files") or [])) or "`(auto-detect in worktree)`"
    marker_payload = {
        "email_item_id": action["email_item_id"],
        "plan_file": str(plan_file),
        "target_topic": action["target_topic"],
        "target_repo": action["target_repo"],
        "email_url": action["target_link"],
        "slug": action["slug"],
        "brief_summary": action.get("brief_summary") or action["issue_title"],
    }
    marker_line = f"<!-- GTT-EMAIL-ACTION:v1 {json.dumps(marker_payload, ensure_ascii=False, sort_keys=True)} -->"
    return "\n".join([
        "## ⚡ TL;DR & 待拍板动作（Next Action）",
        "",
        f"- **原邮件直链**：[📧 打开原邮件（{action['slug']}）]({action['target_link']})",
        f"- **改动摘要**：{action['one_line_summary']}",
        f"- **目标仓 / 文件**：`{action['target_repo']}` · {files_display}",
        f"- **预落盘计划**：`{plan_file}`（`plan_id: {plan.get('plan_id')}` · `topic: {action['target_topic']}`，未自动 apply）",
        "- [ ] **Approve & 自动执行**：勾选此项 `[x]`、在 05:00 Daily Brief 勾选对应编号，或直接评论 `Approve` / `执行`，即可由 `linear-bot-bridge` 调用 `m2-router.py --apply --approve` 在独立 worktree 自动改代码并提 PR。",
        "",
        "---",
        "",
        "## 📋 邮件上下文与验收条件（Human Overview）",
        "",
        f"- **发件人**：`{action.get('sender') or 'Unknown'}`",
        f"- **主题**：`{action.get('subject') or '(No subject)'}`",
        f"- **接收时间**：`{action.get('received_at') or 'Unknown'}`",
        f"- **截止 / 紧急度**：{action.get('deadline') or 'Next delivery'}",
        f"- **核心背景**：{action.get('context') or action['one_line_summary']}",
        "- **验收条件**：",
        f"  1. 在 `{action['target_repo']}` 独立 git worktree 中完成代码/配置修复并通过静态检查与测试；",
        "  2. 提交 PR 并将 PR 链接回写至本 Issue。",
        "",
        "---",
        "",
        "<details>",
        "<summary>🤖 AI / Machine Context (Pre-staged M2 Plan)</summary>",
        "",
        f"- **Email ItemId**: `{action['email_item_id']}`",
        f"- **Target Topic**: `{action['target_topic']}`",
        f"- **Plan ID**: `{plan.get('plan_id')}`",
        f"- **Plan File**: `{plan_file}`",
        "",
        "</details>",
        "",
        marker_line,
    ])


def _canceled(issue: dict[str, Any]) -> bool:
    state = issue.get("state") or {}
    return any(str(state.get(k) or "").casefold() in {"canceled", "cancelled"} for k in ("type", "name"))


def ensure_email_action_issue(
    client: Any,
    action: dict[str, Any],
    plan_file: Path,
    plan: dict[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create or deduplicate the Linear Actionable Issue for `action['email_item_id']`."""
    description = render_email_action_issue_description(action, plan_file, plan)
    title = str(action["issue_title"])
    if dry_run or client is None:
        return {
            "dry_run": True,
            "deduplicated": False,
            "title": title,
            "description": description,
            "email_item_id": action["email_item_id"],
            "plan_file": str(plan_file),
        }

    team = client.team_metadata("TIA")
    issues = client.list_issues("TIA", limit=None)
    matches = []
    for candidate in issues:
        if not isinstance(candidate, dict):
            continue
        meta = parse_email_action_marker(str(candidate.get("description") or ""))
        if meta and str(meta.get("email_item_id")) == str(action["email_item_id"]):
            matches.append(candidate)

    states = (team.get("states") or {}).get("nodes") or []
    todo_state = next((s for s in states if str(s.get("name") or "").casefold() == "todo" and s.get("id")), None)
    labels = (team.get("labels") or {}).get("nodes") or []
    needs_label = next((l for l in labels if str(l.get("name") or "").casefold() == DEFAULT_LABEL.casefold() and l.get("id")), None)
    projects = (team.get("projects") or {}).get("nodes") or []
    project = next((p for p in projects if str(p.get("name") or "").casefold() == DEFAULT_PROJECT.casefold() and p.get("id")), None)
    milestone_id = None
    if project and hasattr(client, "project_milestones"):
        milestones = client.project_milestones(str(project["id"])) or []
        ms = next((m for m in milestones if str(m.get("name") or "").casefold() == DEFAULT_MILESTONE.casefold() and m.get("id")), None)
        if ms:
            milestone_id = str(ms["id"])

    if matches:
        existing = min(matches, key=_canceled)
        update_kwargs: dict[str, Any] = {
            "title": title,
            "description": description,
        }
        if _canceled(existing) and todo_state:
            update_kwargs["state_id"] = str(todo_state["id"])
            update_kwargs["assignee_id"] = OWNER_USER_ID
            if needs_label:
                update_kwargs["label_ids"] = [str(needs_label["id"])]
        updated = client.update_issue(str(existing["id"]), **update_kwargs)
        return {
            "dry_run": False,
            "deduplicated": True,
            "issue": updated,
            "email_item_id": action["email_item_id"],
            "plan_file": str(plan_file),
        }

    if todo_state is None:
        raise RuntimeError("TIA Todo state unavailable")
    create_kwargs: dict[str, Any] = {
        "team_id": str(team["id"]),
        "title": title,
        "description": description,
        "state_id": str(todo_state["id"]),
        "assignee_id": OWNER_USER_ID,
    }
    if needs_label:
        create_kwargs["label_ids"] = [str(needs_label["id"])]
    if project:
        create_kwargs["project_id"] = str(project["id"])
    if milestone_id:
        create_kwargs["project_milestone_id"] = milestone_id
    created = client.create_issue(**create_kwargs)
    return {
        "dry_run": False,
        "deduplicated": False,
        "issue": created,
        "email_item_id": action["email_item_id"],
        "plan_file": str(plan_file),
    }


def ingest_workiq_payload(
    payload: dict[str, Any],
    *,
    plans_dir: Path = DEFAULT_PLANS_DIR,
    router_path: Path = ROUTER_PATH,
    client: Any = None,
    propose_linear: bool = False,
    dry_run: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    """End-to-end ingestion: extract emails -> filter actionable -> stage plan.json -> create Linear issue."""
    emails = extract_emails_from_workiq(payload)
    actionable_results: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for email in emails:
        spec = classify_actionable_email(email)
        if spec is None:
            skipped.append({
                "email_item_id": email.get("email_item_id"),
                "subject": email.get("subject"),
                "target_link": email.get("target_link"),
                "reason": "non_engineering_or_fyi",
            })
            continue
        plan_file, plan = stage_email_action_plan(
            spec,
            plans_dir=plans_dir,
            router_path=router_path,
            runner=runner,
        )
        issue_result = ensure_email_action_issue(
            client if propose_linear else None,
            spec,
            plan_file,
            plan,
            dry_run=dry_run or not propose_linear,
        )
        actionable_results.append({
            "slug": spec["slug"],
            "email_item_id": spec["email_item_id"],
            "target_link": spec["target_link"],
            "target_topic": spec["target_topic"],
            "target_repo": spec["target_repo"],
            "target_files": spec["target_files"],
            "plan_id": plan.get("plan_id"),
            "plan_file": str(plan_file),
            "linear": issue_result,
        })
    return {
        "ok": True,
        "extracted_count": len(emails),
        "actionable_count": len(actionable_results),
        "skipped_count": len(skipped),
        "actionable": actionable_results,
        "skipped": skipped,
    }


def _load_linear_client(*, dry_run: bool = False) -> Any:
    spec = importlib.util.spec_from_file_location("email_action_linear_sync", SYNC_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("canonical Linear client unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = module.load_api_key()
    if not token:
        raise RuntimeError("Linear credentials unavailable")
    return module.GraphQLClient(token, allow_refresh=not dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract actionable emails, pre-stage M2 plans, and create Linear issues.")
    sub = parser.add_subparsers(dest="command")

    ingest = sub.add_parser("ingest-workiq", help="Ingest a WorkIQ JSON response file or stdin")
    ingest.add_argument("--input-json", required=True, help="Path to WorkIQ JSON output or '-' for stdin")
    ingest.add_argument("--plans-dir", type=Path, default=DEFAULT_PLANS_DIR)
    ingest.add_argument("--propose-linear", action="store_true")
    ingest.add_argument("--dry-run", action="store_true")
    ingest.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    if args.command != "ingest-workiq":
        parser.print_help()
        return 0

    raw = sys.stdin.read() if args.input_json == "-" else Path(args.input_json).expanduser().read_text(encoding="utf-8")
    payload = json.loads(raw)
    client = None
    if args.propose_linear and not args.dry_run:
        client = _load_linear_client(dry_run=args.dry_run)
    result = ingest_workiq_payload(
        payload,
        plans_dir=args.plans_dir,
        client=client,
        propose_linear=args.propose_linear,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2 if not args.json else None))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
