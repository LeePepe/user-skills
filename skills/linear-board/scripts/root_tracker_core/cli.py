from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .shared import *

from skill_runtime import SelfDisclosingArgumentParser

CLI_DESCRIPTION = "Project Root Tracker: offline-first collection, one-way Linear sync, and HTML war-room."

def render_html(snapshot: Mapping[str, Any], output: Path = DEFAULT_DASHBOARD) -> Path:
    data = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    root_cards = []
    timeline_cards = []
    for root in snapshot.get("roots", []):
        total = sum(value for value in root.get("summary", {}).values() if isinstance(value, int))
        root_cards.append(f'<button class="root-card" data-root="{_escape(str(root.get("root_id")))}"><strong>{_escape(str(root.get("name")))}</strong><span>{total} signals</span></button>')
        timeline = root.get("teams_timeline") or {}
        if timeline:
            links = " · ".join(f'<a target="_blank" rel="noreferrer" href="{_escape(str(link.get("url")))}">{_escape(str(link.get("title") or "Teams"))}</a>' for link in timeline.get("deep_links", []) if isinstance(link, Mapping) and link.get("url"))
            timeline_cards.append(f'<article class="column"><h2>Release Timeline &amp; Teams Context</h2><p><strong>{_escape(str(timeline.get("version") or "China Release"))}</strong> · target { _escape(str(timeline.get("target_date") or "TBD")) }</p><p>{_escape(_timeline_milestone_summary(timeline))}</p><p>{_escape(_timeline_context_summary(timeline))}</p><p>{links or "No Teams deep links"}</p><span class="badge">{"Confirmed" if timeline.get("confirmed") else "Preview only"}</span></article>')
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Project Root Tracker</title>
<style>
:root{{--bg:#f4f7fb;--card:#fff;--ink:#172033;--muted:#60708a;--accent:#0b67d1;--danger:#b42318;--line:#dbe3ef}}[data-theme=dark]{{--bg:#111827;--card:#1f2937;--ink:#eef2ff;--muted:#a8b3c7;--accent:#80b8ff;--danger:#ff8b82;--line:#374151}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif}}main{{max-width:1500px;margin:auto;padding:24px}}header{{display:flex;justify-content:space-between;align-items:center;gap:12px}}h1{{margin:.2em 0}}button,.pill{{border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);padding:10px 12px;cursor:pointer}}.root-picker,.filters{{display:flex;gap:10px;overflow:auto;padding:12px 0}}.root-card{{min-width:190px;text-align:left;display:grid;gap:5px}}.root-card.active{{outline:2px solid var(--accent)}}.root-card span,.muted{{color:var(--muted)}}.filters button.active{{background:var(--accent);color:white}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}}.column{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;min-height:180px}}.column h2{{font-size:15px;margin-top:0}}.item{{border-top:1px solid var(--line);padding:11px 0;cursor:pointer}}.item:first-of-type{{border-top:0}}.badge{{font-size:11px;border-radius:20px;padding:3px 7px;background:#e6eefb;color:#124c93;display:inline-block;margin:2px 3px 2px 0}}.danger{{color:var(--danger)}}#details{{margin-top:18px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;min-height:80px}}a{{color:var(--accent)}}pre{{white-space:pre-wrap;background:var(--bg);padding:10px;border-radius:8px}}@media(max-width:1000px){{.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:650px){{main{{padding:14px}}.grid{{grid-template-columns:1fr}}}}
</style></head><body><main><header><div><div class="muted">Project Root Tracker · offline-first</div><h1>China Release War-Room</h1></div><button id="theme">Toggle theme</button></header>
<section class="root-picker">{''.join(root_cards)}</section><section class="grid" aria-label="Release Timeline">{''.join(timeline_cards)}</section><section class="filters"><button data-filter="all" class="active">All signals</button>{''.join(f'<button data-filter="{_escape(signal)}">{_escape(signal)}</button>' for signal in SIGNALS)}</section>
<section id="tree" class="grid"></section><section id="details"><span class="muted">Select a signal to inspect details, jump to its PR/Linear issue, or copy its action command.</span></section><section class="column" style="margin-top:18px"><h2>Operator command palette</h2><p class="muted">Safe, owner-mediated commands: <code>record-feedback</code> · <code>my_pr_supervisor</code> · <code>devsync revert</code></p></section></main>
<script>const SNAPSHOT={data};const state={{root:SNAPSHOT.roots[0]?.root_id||'',filter:'all'}};const tree=document.querySelector('#tree'),details=document.querySelector('#details');
function esc(v){{return String(v??'').replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[c]))}}
function matches(item){{return state.filter==='all'||item.signal===state.filter||(state.filter==='Auto-Healing S1/S2/S3'&&item.classification==='AUTO_HEAL_S1_S2_S3')||(state.filter==='Bugs'&&item.type==='bug')||(state.filter==='P0 Blocking Decision'&&item.signal==='P0 Blocking Decision')}}
function render(){{const root=SNAPSHOT.roots.find(r=>r.root_id===state.root)||SNAPSHOT.roots[0];state.root=root?.root_id||'';document.querySelectorAll('.root-card').forEach(b=>b.classList.toggle('active',b.dataset.root===state.root));tree.innerHTML='';for(const name of {json.dumps(WORKSTREAMS)}){{const stream=root?.workstreams?.[name]||{{items:[]}};const col=document.createElement('article');col.className='column';col.innerHTML=`<h2>${{esc(name)}} <span class="muted">(${{stream.items.length}})</span></h2>`;for(const item of stream.items.filter(matches)){{const el=document.createElement('div');el.className='item';el.innerHTML=`<strong>${{esc(item.title||item.id)}}</strong><div>${{esc(item.signal||item.status||'')}} </div><span class="muted">${{esc(item.rationale||item.resolution||item.path||'')}}</span>`;el.onclick=()=>show(item);col.appendChild(el)}}tree.appendChild(col)}}}}
function show(item){{details.innerHTML=`<h2>${{esc(item.title||item.id)}}</h2><p>${{esc(item.rationale||item.resolution||'')}}</p><p>${{item.url?`<a target="_blank" rel="noreferrer" href="${{esc(item.url)}}">Open PR / Linear issue</a>`:''}} ${{item.score!=null?`<span class="badge">score ${{Number(item.score).toFixed(2)}}</span>`:''}}</p><pre>${{esc(item.action_command||item.commands?.record_feedback||item.path||'No action command')}}</pre><button id="copy">Copy action command</button>`;document.querySelector('#copy')?.addEventListener('click',()=>navigator.clipboard?.writeText(item.action_command||item.commands?.record_feedback||''))}}
document.querySelectorAll('.root-card').forEach(b=>b.onclick=()=>{{state.root=b.dataset.root;render()}});document.querySelectorAll('.filters button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('.filters button').forEach(x=>x.classList.remove('active'));b.classList.add('active');state.filter=b.dataset.filter;render()}});document.querySelector('#theme').onclick=()=>document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'':'dark';render();</script></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _timeline_preview(timeline: Mapping[str, Any], root: Mapping[str, Any] | None = None, snapshot_root: Mapping[str, Any] | None = None) -> dict[str, Any]:
    root = dict(root or {"root_id": "china-release", "name": "China Release"})
    snapshot_root = dict(snapshot_root or {"teams_timeline": timeline, "workstreams": _new_workstreams()})
    proposed = build_root_markdown(root, {**snapshot_root, "teams_timeline": {**timeline, "confirmed": True}})
    return {
        "confirmed": bool(timeline.get("confirmed")),
        "version": timeline.get("version"),
        "milestones": timeline.get("milestones", []),
        "targetDate": timeline.get("target_date") or None,
        "description": _timeline_description(timeline),
        "root_l0_preview": proposed,
        "deep_links": timeline.get("deep_links", []),
    }


class _SelfDisclosingRootTrackerParser(SelfDisclosingArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(
            f"[root_tracker] CLI error: {message}\n"
            "  Fix: run `python3 skills/linear-board/scripts/root_tracker.py {collect,refresh,sync-teams-timeline} --help`\n"
            "  Reference: skills/linear-board/references/daily-brief-and-bot.md\n"
        )
        raise SystemExit(2)


def _cli(argv: Sequence[str] | None = None) -> int:
    parser = _SelfDisclosingRootTrackerParser(description=CLI_DESCRIPTION)
    subs = parser.add_subparsers(dest="command", required=True)

    def add_timeline_args(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--teams-timeline", type=Path, default=DEFAULT_TEAMS_TIMELINE)
        command_parser.add_argument("--confirm-teams-timeline", action="store_true")

    collect_parser = subs.add_parser("collect")
    collect_parser.add_argument("--roots", type=Path, default=DEFAULT_SEED)
    collect_parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    collect_parser.add_argument("--devsync", type=Path, default=DEFAULT_DEVSYNC)
    collect_parser.add_argument("--worktrees", type=Path, default=DEFAULT_WORKTREES)
    collect_parser.add_argument("--supervisor", type=Path, default=DEFAULT_SUPERVISOR)
    collect_parser.add_argument("--eval-actions", type=Path, default=DEFAULT_EVAL_ACTIONS)
    collect_parser.add_argument("--output", type=Path, default=DEFAULT_SNAPSHOT)
    collect_parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    add_timeline_args(collect_parser)
    for name in ("sync-linear", "sync-roots"):
        sync = subs.add_parser(name)
        add_timeline_args(sync)
        sync.add_argument("--roots", type=Path, default=DEFAULT_SEED)
        sync.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
        sync.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
        sync.add_argument("--dry-run", action="store_true")
        sync.add_argument("--offline", action="store_true")
        sync.add_argument("--include-children", action="store_true")
        sync.add_argument("--token")
    classify = subs.add_parser("classify-jev")
    classify.add_argument("--item-json", required=True)
    classify.add_argument("--roots", type=Path, default=DEFAULT_SEED)
    render = subs.add_parser("render-html")
    render.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    render.add_argument("--output", type=Path, default=DEFAULT_DASHBOARD)
    refresh = subs.add_parser("refresh")
    refresh.add_argument("--roots", type=Path, default=DEFAULT_SEED)
    refresh.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    refresh.add_argument("--devsync", type=Path, default=DEFAULT_DEVSYNC)
    refresh.add_argument("--worktrees", type=Path, default=DEFAULT_WORKTREES)
    refresh.add_argument("--supervisor", type=Path, default=DEFAULT_SUPERVISOR)
    refresh.add_argument("--eval-actions", type=Path, default=DEFAULT_EVAL_ACTIONS)
    refresh.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    refresh.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    refresh.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    refresh.add_argument("--pull-remote", action="store_true")
    refresh.add_argument("--offline", action="store_true")
    refresh.add_argument("--dry-run", action="store_true")
    refresh.add_argument("--token")
    add_timeline_args(refresh)
    timeline_sync = subs.add_parser("sync-teams-timeline")
    timeline_sync.add_argument("--teams-timeline", type=Path, default=DEFAULT_TEAMS_TIMELINE)
    timeline_sync.add_argument("--preview", action="store_true")
    timeline_sync.add_argument("--confirm", action="store_true")
    timeline_sync.add_argument("--target-date")
    timeline_sync.add_argument("--dry-run", action="store_true")
    timeline_sync.add_argument("--offline", action="store_true")
    timeline_sync.add_argument("--token")
    timeline_sync.add_argument("--roots", type=Path, default=DEFAULT_SEED)
    timeline_sync.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    timeline_sync.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args(argv)
    if args.command == "refresh":
        if args.pull_remote and not args.offline and not args.dry_run:
            sup_script = Path.home() / "Development/job skills/my-pr-supervisor/scripts/my_pr_supervisor.py"
            if sup_script.is_file():
                subprocess.run([sys.executable, str(sup_script), "sync-linear-decisions", "--json"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run([sys.executable, str(sup_script), "discover", "--json"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        snapshot = collect(roots_path=args.roots, summary_path=args.summary, devsync_path=args.devsync, worktrees_path=args.worktrees, supervisor_path=args.supervisor, eval_path=args.eval_actions, teams_timeline_path=args.teams_timeline, confirm_teams_timeline=args.confirm_teams_timeline)
        _write_json(args.snapshot, snapshot)
        render_html(snapshot, args.dashboard)
        roots = load_roots(args.roots)
        snapshot_roots = {str(r.get("root_id")): r for r in snapshot.get("roots", []) if isinstance(r, Mapping)}
        token = args.token or _load_linear_token()
        client = LinearClient(token) if token and not args.offline and not args.dry_run else None
        result = sync_linear(roots, cache_path=args.cache, dry_run=args.dry_run, offline=args.offline or not token, client=client, snapshot_roots=snapshot_roots, teams_timeline_path=args.teams_timeline, confirm_teams_timeline=args.confirm_teams_timeline)
        print(json.dumps({"refreshed": True, "snapshot": str(args.snapshot), "dashboard": str(args.dashboard), "linear_mode": result.get("mode"), "roots_synced": len(result.get("roots", []))}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "collect":
        snapshot = collect(roots_path=args.roots, summary_path=args.summary, devsync_path=args.devsync, worktrees_path=args.worktrees, supervisor_path=args.supervisor, eval_path=args.eval_actions, teams_timeline_path=args.teams_timeline, confirm_teams_timeline=args.confirm_teams_timeline)
        _write_json(args.output, snapshot)
        render_html(snapshot, args.dashboard)
        return 0
    if args.command in {"sync-linear", "sync-roots"}:
        roots = load_roots(args.roots)
        timeline = load_teams_timeline(args.teams_timeline, confirm=args.confirm_teams_timeline)
        snapshot = _read_json(args.snapshot, {})
        snapshot_roots = {}
        child_items = []
        for snapshot_root in snapshot.get("roots", []) if isinstance(snapshot, Mapping) else []:
            if not isinstance(snapshot_root, Mapping):
                continue
            snapshot_roots[str(snapshot_root.get("root_id"))] = snapshot_root
            if args.include_children:
                for stream in (snapshot_root.get("workstreams") or {}).values():
                    for item in (stream.get("items") or []):
                        if isinstance(item, Mapping):
                            child = dict(item)
                            child["root_id"] = snapshot_root.get("root_id")
                            child_items.append(child)
        if "china-release" not in snapshot_roots:
            snapshot_roots["china-release"] = {"root_id": "china-release", "teams_timeline": timeline, "workstreams": _new_workstreams()}
        else:
            snapshot_roots["china-release"].setdefault("teams_timeline", timeline)
        token = args.token or _load_linear_token()
        client = LinearClient(token) if token and not args.offline and not args.dry_run else None
        result = sync_linear(roots, cache_path=args.cache, dry_run=args.dry_run, offline=args.offline or not token, client=client, child_items=child_items, snapshot_roots=snapshot_roots, teams_timeline_path=args.teams_timeline, confirm_teams_timeline=args.confirm_teams_timeline)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "sync-teams-timeline":
        timeline = load_teams_timeline(args.teams_timeline, confirm=args.confirm, target_date=args.target_date, persist=not args.dry_run)
        roots = load_roots(args.roots)
        root = next((item for item in roots if item.get("root_id") == timeline.get("root_id")), {"root_id": "china-release", "name": "China Release"})
        snapshot = _read_json(args.snapshot, {})
        snapshot_root = next((item for item in snapshot.get("roots", []) if isinstance(item, Mapping) and item.get("root_id") == timeline.get("root_id")), None) if isinstance(snapshot, Mapping) else None
        if args.preview or not timeline.get("confirmed") or args.dry_run or args.offline:
            print(json.dumps(_timeline_preview(timeline, root, snapshot_root), ensure_ascii=False, indent=2))
            if args.preview or not timeline.get("confirmed") or args.dry_run or args.offline:
                return 0
        token = args.token or _load_linear_token()
        client = LinearClient(token) if token else None
        snapshot_roots = {str(item.get("root_id")): item for item in snapshot.get("roots", []) if isinstance(item, Mapping)} if isinstance(snapshot, Mapping) else {}
        snapshot_roots[str(timeline.get("root_id"))] = {**(snapshot_roots.get(str(timeline.get("root_id"))) or {}), "teams_timeline": timeline}
        result = sync_linear(roots, cache_path=args.cache, client=client, offline=not token, snapshot_roots=snapshot_roots, teams_timeline_path=args.teams_timeline)
        print(json.dumps({"synced": True, "timeline": timeline, "linear_mode": result.get("mode"), "writes": result.get("writes", 0)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "classify-jev":
        item = _read_json(Path(args.item_json), {}) if Path(args.item_json).is_file() else json.loads(args.item_json)
        print(json.dumps(classify_jev(item, load_roots(args.roots)), ensure_ascii=False, indent=2))
        return 0
    if args.command == "render-html":
        snapshot = _read_json(args.snapshot, {})
        render_html(snapshot, args.output)
        return 0
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    return _cli(argv)
