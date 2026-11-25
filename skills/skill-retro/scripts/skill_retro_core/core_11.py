from __future__ import annotations
from .common import *

# AST-partitioned skill-retro implementation module.

def record_m1_retro_receipt(
    *,
    skill: str,
    status: str,
    handoff_root: Path | None = None,
    summary: str = "",
    pr_url: str | None = None,
    pr_number: int | None = None,
    commit_sha: str | None = None,
    merge_sha: str | None = None,
    writers_spawned: int | None = None,
    reviewers_spawned: int | None = None,
    relay_to: str | None = None,
    relay_requirement: str = "",
    mode: str = "full",
    aspects: Sequence[str] | str | None = None,
    observation_notes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Write a canonical M1 TL deep-reasoning receipt (`FIXED_AND_MERGED` | `HEALTHY_NO_ISSUES` | `CROSS_SKILL_RELAY`) for M2 Rollup."""
    norm_sk = _norm_skill(skill)
    if not norm_sk:
        raise ValueError("--skill is required to record an M1 retro receipt.")
    norm_status = str(status or "").strip().upper()
    canonical_statuses = {"FIXED_AND_MERGED", "HEALTHY_NO_ISSUES", "CROSS_SKILL_RELAY"}
    if norm_status not in canonical_statuses:
        raise ValueError(f"Invalid M1 receipt status {status!r}; must be one of {sorted(canonical_statuses)}.")

    resolved_root = (handoff_root or DEFAULT_HANDOFF_ROOT).expanduser()
    safe_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", norm_sk).strip("-") or "skill"
    goal_dir = _resolve_per_skill_goal_handoff_dir(resolved_root, safe_slug)
    receipts_dir = goal_dir / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipts_dir / "m1-tl-receipt.json"

    norm_mode = "partial" if str(mode or "full").strip().lower() == "partial" else "full"
    aspect_list = _normalize_aspects(aspects, mode=norm_mode)
    if norm_status == "HEALTHY_NO_ISSUES":
        eff_writers = 0 if writers_spawned is None else int(writers_spawned)
        eff_reviewers = 0 if reviewers_spawned is None else int(reviewers_spawned)
        default_summary = f"M1 TL deep reasoning confirmed `{norm_sk}` signal is healthy/benign (0 Writers/Reviewers spawned)."
    elif norm_status == "CROSS_SKILL_RELAY":
        eff_writers = 0 if writers_spawned is None else int(writers_spawned)
        eff_reviewers = 0 if reviewers_spawned is None else int(reviewers_spawned)
        default_summary = f"M1 TL emitted Wave 2 Cross-Skill Relay from `{norm_sk}` to `{_norm_skill(relay_to or '')}`."
    else:
        eff_writers = 1 if writers_spawned is None else int(writers_spawned)
        eff_reviewers = 1 if reviewers_spawned is None else int(reviewers_spawned)
        default_summary = f"M1 TL fixed `{norm_sk}` via Writer -> Reviewer, passed Positive + Counter-Case Replay, and merged PR."

    eff_sha = commit_sha or merge_sha
    payload: dict[str, Any] = {
        "ok": True,
        "schema": "skill-retro-m1-receipt/v1",
        "skill": norm_sk,
        "goal_id": f"skill-retro-{safe_slug}",
        "m1_tl_agent_name": f"skill-retro-{safe_slug}-goal-tl-r1",
        "status": norm_status,
        "mode": norm_mode,
        "aspects": aspect_list,
        "writers_spawned": eff_writers,
        "reviewers_spawned": eff_reviewers,
        "pr_url": pr_url,
        "pr_number": pr_number,
        "commit_sha": eff_sha,
        "merge_sha": merge_sha or eff_sha,
        "relay_to": _norm_skill(relay_to) if relay_to else None,
        "relay_requirement": relay_requirement,
        "summary": summary.strip() or default_summary,
        "observation_notes": list(observation_notes or []),
        "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "receipt_path": str(receipt_path),
    }
    receipt_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload

def rollup_m1_retro_receipts(
    *,
    handoff_root: Path | None = None,
    skills: Sequence[str] | str | None = None,
    observation_pool: Sequence[Mapping[str, Any]] | None = None,
    target_date: str | None = None,
    output_path: Path | None = None,
    repo_root: Path | None = None,
    verify_grounding: bool = False,
) -> dict[str, Any]:
    """Aggregate K (0..3) independent M1 TL receipts (`FIXED_AND_MERGED` | `HEALTHY_NO_ISSUES` | `CROSS_SKILL_RELAY`) back to the single M2."""
    resolved_root = (handoff_root or DEFAULT_HANDOFF_ROOT).expanduser()
    date_str = str(target_date or _cst_today_str()).strip()
    filter_skills: list[str] = []
    if isinstance(skills, str):
        filter_skills = [_norm_skill(s) for s in re.split(r"[,;\s]+", skills) if _norm_skill(s)]
    elif isinstance(skills, Sequence):
        for item in skills:
            for s in re.split(r"[,;\s]+", str(item)):
                if _norm_skill(s):
                    filter_skills.append(_norm_skill(s))

    candidate_dirs: list[Path] = []
    if filter_skills:
        for sk in filter_skills:
            safe_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", sk).strip("-") or "skill"
            candidate_dirs.append(_resolve_per_skill_goal_handoff_dir(resolved_root, safe_slug))
    elif _probe_readable_dir(resolved_root):
        if resolved_root.name.startswith("skill-retro-") and _safe_is_file(resolved_root / "spec.md"):
            candidate_dirs.append(resolved_root)
        else:
            for child in _safe_glob(resolved_root, "skill-retro-*"):
                if _safe_is_dir(child) and child.name not in {"skill-retro-m2-3m1-rollup"}:
                    candidate_dirs.append(child)

    m1_receipts: list[dict[str, Any]] = []
    relays_collected: list[dict[str, Any]] = []
    for gdir in candidate_dirs:
        if not _probe_readable_dir(gdir):
            continue
        sk_guess = gdir.name[len("skill-retro-") :] if gdir.name.startswith("skill-retro-") else gdir.name
        r_file = gdir / "receipts" / "m1-tl-receipt.json"
        loaded_receipt: dict[str, Any] | None = None
        if _safe_is_file(r_file):
            try:
                raw_r = json.loads(r_file.read_text(encoding="utf-8"))
                if isinstance(raw_r, dict):
                    loaded_receipt = dict(raw_r)
            except (OSError, ValueError, TypeError):
                loaded_receipt = None
        if loaded_receipt is None:
            # Fallback: inspect ledger.md or relay JSONs inside goal handoff dir
            ledger_file = gdir / "ledger.md"
            try:
                ledger_txt = ledger_file.read_text(encoding="utf-8") if _safe_is_file(ledger_file) else ""
            except OSError:
                ledger_txt = ""
            relay_files = _safe_glob(gdir / "briefs", "skill-retro-relay-*.json")
            if "HEALTHY_NO_ISSUES" in ledger_txt:
                loaded_receipt = {
                    "skill": sk_guess,
                    "goal_id": gdir.name,
                    "m1_tl_agent_name": f"{gdir.name}-goal-tl-r1",
                    "status": "HEALTHY_NO_ISSUES",
                    "writers_spawned": 0,
                    "reviewers_spawned": 0,
                    "summary": f"M1 TL deep reasoning found `{sk_guess}` healthy (`0` Writers spawned).",
                }
            elif "PR_MERGED" in ledger_txt or "GOAL_DONE" in ledger_txt or "FIXED_AND_MERGED" in ledger_txt:
                pr_match = re.search(r"(https://github\.com/\S+/pull/\d+|#\d+)", ledger_txt)
                sha_match = re.search(r"\b([0-9a-f]{7,40})\b", ledger_txt)
                loaded_receipt = {
                    "skill": sk_guess,
                    "goal_id": gdir.name,
                    "m1_tl_agent_name": f"{gdir.name}-goal-tl-r1",
                    "status": "FIXED_AND_MERGED",
                    "writers_spawned": 1,
                    "reviewers_spawned": 1,
                    "pr_url": pr_match.group(1) if pr_match else None,
                    "commit_sha": sha_match.group(1) if sha_match else None,
                    "summary": f"M1 TL completed Writer -> Reviewer fix and merged PR for `{sk_guess}`.",
                }
            elif relay_files:
                with contextlib.suppress(OSError, ValueError, TypeError):
                    rel_data = json.loads(relay_files[0].read_text(encoding="utf-8"))
                    loaded_receipt = {
                        "skill": sk_guess,
                        "goal_id": gdir.name,
                        "m1_tl_agent_name": f"{gdir.name}-goal-tl-r1",
                        "status": "CROSS_SKILL_RELAY",
                        "writers_spawned": 0,
                        "reviewers_spawned": 0,
                        "relay_to": rel_data.get("to_skill"),
                        "relay_requirement": rel_data.get("requirement"),
                        "summary": f"M1 TL scheduled Wave 2 Cross-Skill Relay `{sk_guess}` -> `{rel_data.get('to_skill')}`.",
                    }
        if loaded_receipt is not None:
            is_grounded, g_issues = _verify_m1_receipt_grounding(
                loaded_receipt,
                repo_root=repo_root,
                verify_grounding=verify_grounding,
            )
            loaded_receipt["receipt_grounded"] = is_grounded
            loaded_receipt["grounding_issues"] = g_issues
            loaded_receipt["grounding_errors"] = g_issues
            if loaded_receipt.get("status") == "FIXED_AND_MERGED" and not is_grounded:
                loaded_receipt["claimed_status"] = "FIXED_AND_MERGED"
                loaded_receipt["effective_status"] = "UNGROUNDED_RECEIPT"
            else:
                loaded_receipt["effective_status"] = loaded_receipt.get("status")
            m1_receipts.append(loaded_receipt)
        for rf in _safe_glob(gdir / "briefs", "skill-retro-relay-*.json"):
            try:
                relays_collected.append(json.loads(rf.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                pass

    for rf in _safe_glob(resolved_root / "briefs", "skill-retro-relay-*.json"):
        try:
            rel_obj = json.loads(rf.read_text(encoding="utf-8"))
            if not any(r.get("relay_id") == rel_obj.get("relay_id") for r in relays_collected):
                relays_collected.append(rel_obj)
        except (OSError, ValueError, TypeError):
            pass

    fixed_items = [r for r in m1_receipts if r.get("status") == "FIXED_AND_MERGED" and r.get("receipt_grounded", True)]
    ungrounded_items = [r for r in m1_receipts if r.get("status") == "FIXED_AND_MERGED" and not r.get("receipt_grounded", True)]
    healthy_items = [r for r in m1_receipts if r.get("status") == "HEALTHY_NO_ISSUES"]
    relay_items = [r for r in m1_receipts if r.get("status") == "CROSS_SKILL_RELAY"]
    overall_status = (
        "FIXED_AND_MERGED"
        if fixed_items
        else (
            "UNGROUNDED_RECEIPT"
            if ungrounded_items
            else ("CROSS_SKILL_RELAY" if (relay_items or relays_collected) else "HEALTHY_NO_ISSUES")
        )
    )
    all_grounding_errors = [
        f"{r.get('skill')}:{err}"
        for r in m1_receipts
        for err in (r.get("grounding_issues") or [])
    ]

    md_lines = [
        f"## `[Skill Retro {date_str}]` `1 M2 -> K ({len(m1_receipts)}) M1 TLs -> Single M2 Rollup` Summary",
        "",
        "- **Topology**: `1 M2 Coordinator -> 1 Cheap Probe Worker -> K (0..3) Independent M1 TLs -> Single M2 Rollup`",
        f"- **M1 TLs Spawned (`K`)**: `{len(m1_receipts)}` (max cap `3`, never a forced quota)",
        f"- **Rollup Breakdown**: `FIXED_AND_MERGED={len(fixed_items)}` · `HEALTHY_NO_ISSUES={len(healthy_items)}` · `CROSS_SKILL_RELAY={len(relay_items) or len(relays_collected)}` · `UNGROUNDED={len(ungrounded_items)}`",
        "",
        "| Skill | Independent M1 TL | Outcome | Grounded | Writers Spawned | PR / Relay / Evidence |",
        "|---|---|---|---|---|---|",
    ]
    if m1_receipts:
        for rec in m1_receipts:
            ev_cell = str(rec.get("pr_url") or (f"relay->{rec.get('relay_to')}" if rec.get("relay_to") else rec.get("summary") or "—")).replace("|", "\\|")
            grounded_cell = "yes" if rec.get("receipt_grounded", True) else f"NO ({','.join(rec.get('grounding_issues') or [])})"
            md_lines.append(
                f"| `{rec.get('skill')}` | `{rec.get('m1_tl_agent_name')}` | `{rec.get('effective_status') or rec.get('status')}` | `{grounded_cell}` | `{rec.get('writers_spawned', 0)}` | {ev_cell} |"
            )
    else:
        md_lines.append("| — | `0 M1 TLs spawned` | `HEALTHY_NO_ISSUES` | `yes` | `0` | Probe Worker found 0 issues; no M1 TL or Writer spawned |")

    obs_list = list(observation_pool or [])
    if obs_list:
        md_lines.extend(["", "### Observation Pool (Held Below Threshold)", ""])
        for ob in obs_list[:6]:
            if isinstance(ob, Mapping):
                md_lines.append(f"- `{ob.get('skill', 'unknown')}` (`{ob.get('category', 'signal')}`): {ob.get('title', '')}")

    summary_md = "\n".join(md_lines).rstrip() + "\n"
    rollup_payload: dict[str, Any] = {
        "ok": True,
        "schema": "skill-retro-m2-rollup/v1",
        "date": date_str,
        "topology": "1_m2 -> 1_cheap_probe_worker -> 0..3_parallel_m1_tls -> 1_m2_rollup",
        "status": overall_status,
        "receipt_grounded": len(ungrounded_items) == 0,
        "grounding_issues": all_grounding_errors,
        "grounding_errors": all_grounding_errors,
        "handoff_root": str(resolved_root),
        "m1_tls_spawned": len(m1_receipts),
        "writers_spawned_total": sum(int(r.get("writers_spawned") or 0) for r in m1_receipts),
        "counts": {
            "fixed_and_merged": len(fixed_items),
            "ungrounded_receipts": len(ungrounded_items),
            "healthy_no_issues": len(healthy_items),
            "cross_skill_relay": max(len(relay_items), len(relays_collected)),
            "observation_pool": len(obs_list),
        },
        "m1_receipts": m1_receipts,
        "cross_skill_relays": relays_collected,
        "observation_pool": obs_list,
        "daily_summary_issue_title": f"[Skill Retro {date_str}] Autonomous Evolution Summary ({len(fixed_items)} fixed, {len(healthy_items)} healthy)",
        "markdown_summary": summary_md,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(rollup_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return rollup_payload

class _SelfDisclosingArgumentParser(SelfDisclosingArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(
            f"[skill-retro] CLI error: {message}\n"
            "  Fix: run `python3 skills/skill-retro/scripts/skill_retro.py {probe,triage,diagnose,emit-briefs,emit-relay,record-m1-receipt,rollup,roi} --help`\n"
            "  Reference: skills/skill-retro/references/progressive-evolution-playbook.md\n"
        )
        raise SystemExit(2)

def _add_common_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--skill", "--target-skill", dest="skill", default=None, help="Target skill name (or single-skill direct mode).")
    p.add_argument("--warehouse-db", type=Path, default=None)
    p.add_argument("--copilot-db", type=Path, default=None)
    p.add_argument("--copilot-sessions-dir", type=Path, default=None)
    p.add_argument("--usage-jsonl", type=Path, default=None)
    p.add_argument("--trace-export", type=Path, default=None)
    p.add_argument("--handoff-root", type=Path, default=None)
    p.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    p.add_argument("--dev-root", type=Path, default=DEFAULT_DEV_ROOT)
    p.add_argument("--icloud-sync-dir", type=Path, default=None)
    p.add_argument("--telemetry-shards-dir", type=Path, default=None)
    p.add_argument("--isolated-fixtures", action="store_true", default=False, help="Treat custom --handoff-root as isolated test fixture mode and disable default home telemetry sources.")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--limit-sessions", type=int, default=DEFAULT_MAX_COPILOT_SESSIONS_SCAN, help="Bound recent Copilot session scanning (default 60).")
    p.add_argument("--deep-all", action="store_true", default=False, help="Opt into an unbounded historical session scan.")
    p.add_argument("--summary", action="store_true", default=False, help="Bound output arrays for compact ACI consumption.")
    p.add_argument("--json", action="store_true", default=True)

def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    known_subcommands = {
        "probe",
        "triage",
        "diagnose",
        "uptake-check",
        "emit-briefs",
        "emit-relay",
        "record-m1-receipt",
        "rollup",
        "skill-retro",
        "roi",
        "skill-roi",
    }

    if not args_list or args_list[0] in {"-h", "--help"}:
        top = _SelfDisclosingArgumentParser(
            prog="skill_retro.py",
            description="3-Stage Skill Progressive Self-Evolution: Fast Probe -> Cheap Worker + Jev Triage -> 1 M2 -> K (0..3) Parallel M1 TLs -> M2 Rollup.",
        )
        top.add_argument(
            "command",
            nargs="?",
            choices=sorted(known_subcommands),
            help="Subcommand: probe | triage | diagnose | uptake-check | emit-briefs | emit-relay | record-m1-receipt | rollup | roi",
        )
        _add_common_source_args(top)
        top.parse_args(args_list)
        return 0

    cmd = args_list[0]
    rest = args_list[1:]
    if cmd.startswith("-"):
        cmd = "diagnose"
        rest = args_list
    elif cmd not in known_subcommands:
        sys.stderr.write(
            f"[skill-retro] Unknown subcommand: {cmd!r}\n"
            "  Valid subcommands: probe, triage, diagnose, uptake-check, emit-briefs, emit-relay, record-m1-receipt, rollup, roi\n"
            "  Reference: skills/skill-retro/references/progressive-evolution-playbook.md\n"
        )
        raise SystemExit(2)

    if cmd == "uptake-check":
        p = _SelfDisclosingArgumentParser(prog="skill_retro.py uptake-check")
        p.add_argument("--skill", "--skills", "--target-skill", dest="skill", default=None, help="Comma-separated expected skill(s) to evaluate.")
        p.add_argument("--triggered-skills", default=None, help="Comma-separated triggered skills observed in trace (defaults to expected skills).")
        p.add_argument("--target-dir", "--worktree", dest="target_dir", type=Path, default=None, help="Target workspace/directory to inspect.")
        p.add_argument(
            "--scenario",
            default="skills_available_unmentioned",
            choices=sorted(VALID_SKILL_SCENARIOS),
            help="3-condition scenario: skills_unavailable | skills_available_unmentioned | skills_available_mentioned.",
        )
        p.add_argument("--uptake-map", dest="uptake_map_path", type=Path, default=None, help="Custom uptake_map.json path.")
        p.add_argument("--baseline-pct", type=float, default=None, help="Baseline (skills_unavailable) evaluator score percentage.")
        p.add_argument("--skill-pct", type=float, default=None, help="Skill-enabled evaluator score percentage.")
        p.add_argument("--build-success", action="store_true", default=None)
        p.add_argument("--strict", action="store_true", default=False, help="Exit 1 if any hard uptake check fails.")
        p.add_argument("--json", action="store_true", default=True)
        ns = p.parse_args(rest)
        out = evaluate_skill_uptake(
            skills=ns.skill,
            triggered_skills=ns.triggered_skills,
            target_dir=ns.target_dir,
            scenario=ns.scenario,
            uptake_map_path=ns.uptake_map_path,
            baseline_evaluator_pct=ns.baseline_pct,
            skill_evaluator_pct=ns.skill_pct,
            build_success=ns.build_success,
        )
        if not out["ok"]:
            drawers = sorted(
                {
                    str(c.get("remediation_drawer"))
                    for c in (out.get("static_checks") or [])
                    if c.get("status") == UPTAKE_STATUS_FAILED and c.get("remediation_drawer")
                }
            )
            if drawers:
                sys.stderr.write(
                    f"[skill-retro] Hard uptake check failure(s) detected. "
                    f"Read {', '.join(drawers[:3])} for remediation rules.\n"
                )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1 if (ns.strict and not out["ok"]) else 0

    if cmd == "probe":
        p = _SelfDisclosingArgumentParser(prog="skill_retro.py probe")
        _add_common_source_args(p)
        p.add_argument("--aspect", "--aspects", dest="aspect", default=None, help="Optional aspect filter (l0,l1,l2,l3,creative,eval).")
        p.add_argument("--fast", action="store_true", default=True, help="Sub-second watermark/signal scan (default True).")
        p.add_argument("--deep", dest="fast", action="store_false", help="Force full session/DB scan during probe.")
        ns = p.parse_args(rest)
        out = probe_skills(
            skill=ns.skill,
            aspect=ns.aspect,
            fast=ns.fast,
            summary=ns.summary,
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            copilot_sessions_dir=ns.copilot_sessions_dir,
            usage_jsonl=ns.usage_jsonl,
            trace_export_path=ns.trace_export,
            handoff_root=ns.handoff_root,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            telemetry_shards_dir=ns.telemetry_shards_dir,
            days=ns.days,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if cmd == "triage":
        p = _SelfDisclosingArgumentParser(prog="skill_retro.py triage")
        _add_common_source_args(p)
        p.add_argument("--aspect", "--aspects", dest="aspect", default=None)
        p.add_argument("--pick-one", action="store_true", default=False, help="Pick the single top actionable skill.")
        p.add_argument("--pick-top", type=int, default=None, help="Pick top N mutually disjoint skills for parallel Wave 1 (default 3 in daily autonomous batch).")
        p.add_argument("--use-jev", action="store_true", default=True, help="Use jev_client.py jev_decide with deterministic fallback.")
        p.add_argument("--no-jev", dest="use_jev", action="store_false")
        p.add_argument("--mock-jev-json", default=None, help="Inline JSON or file path for deterministic Jev testing.")
        p.add_argument("--emit-briefs", "--handoff-dir", dest="emit_briefs", type=Path, default=None)
        p.add_argument("--coupled-with", default=None, help="Explicitly lock a coupled Guest Skill into the same worktree.")
        p.add_argument("--relay-to", default=None, help="Emit a Wave 2 Cross-Skill Relay Brief to this Guest Skill.")
        p.add_argument("--relay-requirement", default="", help="Requirement text for --relay-to.")
        ns = p.parse_args(rest)
        relays_list = (
            [{"from_skill": ns.skill or "", "to_skill": ns.relay_to, "requirement": ns.relay_requirement}]
            if ns.relay_to
            else None
        )
        out = triage_skills(
            skill=ns.skill,
            aspect=ns.aspect,
            pick_one=ns.pick_one,
            pick_top=ns.pick_top,
            use_jev=ns.use_jev,
            mock_jev_json=ns.mock_jev_json,
            emit_briefs_dir=ns.emit_briefs,
            coupled_with=ns.coupled_with,
            relays=relays_list,
            summary=ns.summary,
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            copilot_sessions_dir=ns.copilot_sessions_dir,
            usage_jsonl=ns.usage_jsonl,
            trace_export_path=ns.trace_export,
            handoff_root=ns.handoff_root,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            telemetry_shards_dir=ns.telemetry_shards_dir,
            days=ns.days,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if cmd == "emit-relay":
        p = _SelfDisclosingArgumentParser(prog="skill_retro.py emit-relay")
        p.add_argument("--from", "--from-skill", "--host", dest="from_skill", required=True, help="Host skill originating the relay.")
        p.add_argument("--to", "--to-skill", "--guest", dest="to_skill", required=True, help="Guest skill to update in Wave 2.")
        p.add_argument("--requirement", required=True, help="Concrete contract/CLI change required in Guest skill.")
        p.add_argument("--evidence-summary", default="", help="Optional observed failure/telemetry summary.")
        p.add_argument("--wave", type=int, default=2, help="Target wave number (default 2).")
        p.add_argument("--emit-briefs", "--handoff-dir", dest="emit_briefs", type=Path, required=True)
        p.add_argument("--json", action="store_true", default=True)
        ns = p.parse_args(rest)
        try:
            out = emit_cross_skill_relay(
                from_skill=ns.from_skill,
                to_skill=ns.to_skill,
                requirement=ns.requirement,
                emit_dir=ns.emit_briefs,
                evidence_summary=ns.evidence_summary,
                wave=ns.wave,
            )
        except ValueError as exc:
            sys.stderr.write(
                f"[skill-retro] Relay error: {exc}\n"
                "  Fix: pass distinct `--from <host>` and `--to <guest>` skill names.\n"
                "  Reference: skills/skill-retro/references/progressive-evolution-playbook.md\n"
            )
            raise SystemExit(2) from exc
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if cmd == "record-m1-receipt":
        p = _SelfDisclosingArgumentParser(prog="skill_retro.py record-m1-receipt")
        p.add_argument("--skill", "--target-skill", dest="skill", required=True, help="Skill investigated by this independent M1 TL.")
        p.add_argument(
            "--status",
            required=True,
            choices=("FIXED_AND_MERGED", "HEALTHY_NO_ISSUES", "CROSS_SKILL_RELAY"),
            help="Final M1 TL deep-reasoning outcome.",
        )
        p.add_argument("--handoff-root", "--emit-briefs", "--handoff-dir", dest="handoff_root", type=Path, default=DEFAULT_HANDOFF_ROOT)
        p.add_argument("--summary", default="", help="Concise M1 TL finding / fix summary.")
        p.add_argument("--pr-url", default=None)
        p.add_argument("--pr-number", type=int, default=None)
        p.add_argument("--commit-sha", default=None)
        p.add_argument("--writers-spawned", type=int, default=None)
        p.add_argument("--reviewers-spawned", type=int, default=None)
        p.add_argument("--relay-to", default=None)
        p.add_argument("--relay-requirement", default="")
        p.add_argument("--mode", choices=("full", "partial"), default="full")
        p.add_argument("--aspects", "--aspect", dest="aspects", default=None)
        p.add_argument("--json", action="store_true", default=True)
        ns = p.parse_args(rest)
        out = record_m1_retro_receipt(
            skill=ns.skill,
            status=ns.status,
            handoff_root=ns.handoff_root,
            summary=ns.summary,
            pr_url=ns.pr_url,
            pr_number=ns.pr_number,
            commit_sha=ns.commit_sha,
            writers_spawned=ns.writers_spawned,
            reviewers_spawned=ns.reviewers_spawned,
            relay_to=ns.relay_to,
            relay_requirement=ns.relay_requirement,
            mode=ns.mode,
            aspects=ns.aspects,
        )
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if cmd == "rollup":
        p = _SelfDisclosingArgumentParser(prog="skill_retro.py rollup")
        p.add_argument("--handoff-root", "--emit-briefs", "--handoff-dir", dest="handoff_root", type=Path, default=DEFAULT_HANDOFF_ROOT)
        p.add_argument("--skills", "--skill", dest="skills", default=None, help="Optional comma-separated skill list to roll up.")
        p.add_argument("--date", dest="target_date", default=None, help="Target date YYYY-MM-DD (default CST today).")
        p.add_argument("--repo-root", type=Path, default=None, help="Optional git repository root to verify claimed commit SHAs.")
        p.add_argument("--verify-grounding", action="store_true", default=False, help="Verify claimed FIXED_AND_MERGED SHAs against git history.")
        p.add_argument("--output", type=Path, default=None, help="Optional output JSON path.")
        p.add_argument("--markdown-output", type=Path, default=None, help="Optional markdown summary output path.")
        p.add_argument("--json", action="store_true", default=True)
        ns = p.parse_args(rest)
        out = rollup_m1_retro_receipts(
            handoff_root=ns.handoff_root,
            skills=ns.skills,
            target_date=ns.target_date,
            output_path=ns.output,
            repo_root=ns.repo_root,
            verify_grounding=ns.verify_grounding,
        )
        if ns.markdown_output is not None:
            ns.markdown_output.parent.mkdir(parents=True, exist_ok=True)
            ns.markdown_output.write_text(str(out.get("markdown_summary") or ""), encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if cmd in {"roi", "skill-roi"}:
        p = _SelfDisclosingArgumentParser(prog=f"skill_retro.py {cmd}")
        _add_common_source_args(p)
        p.add_argument("--magpie-usage", type=Path, default=None)
        p.add_argument("--dsh-sessions", type=Path, default=DEFAULT_DSH_SESSIONS)
        p.add_argument("--icloud-roi-path", type=Path, default=None)
        p.add_argument("--no-icloud-mirror", action="store_true")
        p.add_argument("--auto-propose", action="store_true")
        p.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
        ns = p.parse_args(rest)
        report = compute_skill_roi(
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            magpie_usage=ns.magpie_usage,
            dsh_sessions=ns.dsh_sessions,
            telemetry_shards_dir=ns.telemetry_shards_dir,
            icloud_sync_dir=ns.icloud_sync_dir,
            mirror_icloud_roi=False if ns.no_icloud_mirror else None,
            icloud_roi_path=ns.icloud_roi_path,
        )
        if ns.auto_propose:
            queued = _queue_recommendations(report.get("low_roi_proposals", []), ns.proposals_path)
            report.update(
                auto_proposed=queued,
                proposals_written=len(queued),
                proposals_skipped=len(report.get("low_roi_proposals", [])) - len(queued),
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    # `diagnose`, `emit-briefs`, or `skill-retro`
    p = _SelfDisclosingArgumentParser(prog=f"skill_retro.py {cmd}")
    _add_common_source_args(p)
    p.add_argument("--emit-briefs", "--handoff-dir", dest="emit_briefs", type=Path, default=None)
    p.add_argument("--mode", choices=("full", "partial", "auto"), default="full")
    p.add_argument("--aspects", "--aspect", dest="aspects", default=None, help="Comma-separated aspects (l0,l1,l2,l3,creative,eval).")
    p.add_argument("--pick-one", action="store_true", default=False, help="Auto-pick top 1 actionable skill via Stage 1+2 if --skill is omitted.")
    p.add_argument("--pick-top", type=int, default=None, help="Auto-pick top N disjoint skills via Stage 1+2.")
    p.add_argument("--use-jev", action="store_true", default=False)
    p.add_argument("--mock-jev-json", default=None)
    p.add_argument("--coupled-with", default=None, help="Lock a coupled Guest Skill into the same worktree Owned Paths.")
    p.add_argument("--relay-to", default=None, help="Emit a Wave 2 Cross-Skill Relay Brief to this Guest Skill.")
    p.add_argument("--relay-requirement", default="", help="Requirement text for --relay-to.")
    p.add_argument("--icloud-roi-path", type=Path, default=None)
    p.add_argument("--local-roi-path", type=Path, default=None)
    p.add_argument("--no-roi-mirror", action="store_true")
    p.add_argument("--auto-propose", action="store_true")
    p.add_argument("--proposals-path", type=Path, default=DEFAULT_PROPOSALS_PATH)
    ns = p.parse_args(rest)

    target_skill = ns.skill
    eff_mode = "full" if ns.mode == "auto" else ns.mode
    eff_aspects = ns.aspects
    triage_meta: dict[str, Any] | None = None

    if (ns.pick_one or ns.pick_top is not None or ns.mode == "auto") and not target_skill:
        triage_meta = triage_skills(
            skill=None,
            aspect=ns.aspects,
            pick_one=True if ns.pick_top is None else False,
            pick_top=ns.pick_top,
            use_jev=ns.use_jev,
            mock_jev_json=ns.mock_jev_json,
            warehouse_db=ns.warehouse_db,
            copilot_db=ns.copilot_db,
            copilot_sessions_dir=ns.copilot_sessions_dir,
            usage_jsonl=ns.usage_jsonl,
            trace_export_path=ns.trace_export,
            handoff_root=ns.handoff_root,
            skills_root=ns.skills_root,
            dev_root=ns.dev_root,
            telemetry_shards_dir=ns.telemetry_shards_dir,
            days=ns.days,
        )
        if ns.pick_top is not None and int(ns.pick_top) > 1 and ns.emit_briefs is not None:
            multi_selected = list(triage_meta.get("selected_skills") or [])
            combined_briefs: list[dict[str, Any]] = []
            combined_specs: list[dict[str, Any]] = []
            combined_relays: list[dict[str, Any]] = []
            wave1_hosts: list[dict[str, Any]] = []
            wave2_relays: list[dict[str, Any]] = []
            last_report: dict[str, Any] | None = None
            for item in multi_selected:
                sk_name = str(item.get("skill") or "")
                sk_mode = str(item.get("mode") or ("full" if ns.mode == "auto" else ns.mode))
                sk_aspects = item.get("aspects") if ns.aspects is None else ns.aspects
                sub_rep = compute_skill_retro(
                    warehouse_db=ns.warehouse_db,
                    copilot_db=ns.copilot_db,
                    copilot_sessions_dir=ns.copilot_sessions_dir,
                    usage_jsonl=ns.usage_jsonl,
                    trace_export_path=ns.trace_export,
                    handoff_root=ns.handoff_root,
                    skills_root=ns.skills_root,
                    dev_root=ns.dev_root,
                    telemetry_shards_dir=ns.telemetry_shards_dir,
                    skill=sk_name,
                    emit_briefs_dir=ns.emit_briefs,
                    mode=sk_mode,
                    aspects=sk_aspects,
                    coupled_with=ns.coupled_with,
                    days=ns.days,
                    limit_sessions=None if ns.deep_all else ns.limit_sessions,
                    auto_propose=ns.auto_propose,
                    proposals_path=ns.proposals_path,
                    icloud_sync_dir=ns.icloud_sync_dir,
                    icloud_roi_path=ns.icloud_roi_path,
                    local_roi_path=ns.local_roi_path,
                    mirror_roi_snapshot=False if ns.no_roi_mirror else None,
                    isolated_fixtures=bool(ns.isolated_fixtures),
                )
                last_report = sub_rep
                eb = sub_rep.get("emitted_briefs") or {}
                combined_briefs.extend(eb.get("briefs") or [])
                combined_specs.extend(eb.get("m1_goal_specs") or [])
                combined_relays.extend(eb.get("cross_skill_relays") or [])
                wp = eb.get("wave_plan") or {}
                wave1_hosts.extend(wp.get("wave_1_disjoint_hosts") or [])
                wave2_relays.extend(wp.get("wave_2_cross_skill_relays") or [])
            base_report = last_report or compute_skill_retro(
                warehouse_db=ns.warehouse_db,
                copilot_db=ns.copilot_db,
                copilot_sessions_dir=ns.copilot_sessions_dir,
                usage_jsonl=ns.usage_jsonl,
                trace_export_path=ns.trace_export,
                handoff_root=ns.handoff_root,
                skills_root=ns.skills_root,
                dev_root=ns.dev_root,
                telemetry_shards_dir=ns.telemetry_shards_dir,
                skill=None,
                emit_briefs_dir=None,
                mode=eff_mode,
                aspects=eff_aspects,
                days=ns.days,
                limit_sessions=None if ns.deep_all else ns.limit_sessions,
                mirror_roi_snapshot=False,
                isolated_fixtures=bool(ns.isolated_fixtures),
            )
            m2_plan = build_m2_dispatch_plan(combined_briefs, handoff_root=ns.emit_briefs, dev_root=ns.dev_root)
            base_report["emitted_briefs"] = {
                "briefs_dir": str(ns.emit_briefs / "briefs" if ns.emit_briefs.name != "briefs" else ns.emit_briefs),
                "handoff_root": str(ns.emit_briefs.parent if ns.emit_briefs.name == "briefs" else ns.emit_briefs),
                "status": "ACTIONABLE" if combined_briefs else "HEALTHY_NO_ISSUES",
                "status_message": (
                    f"Emitted {len(combined_briefs)} per-skill M1 Goal spec(s) & brief(s)"
                    if combined_briefs
                    else "healthy / 0 issues found (no changes needed)"
                ),
                "m1_tls_to_spawn": len(combined_briefs),
                "writers_spawned": len(combined_briefs),
                "mode": eff_mode,
                "role_slots_reused": ["M1-TL", "writer", "reviewer"] if combined_briefs else [],
                "new_agent_roles_created": 0,
                "count": len(combined_briefs),
                "briefs": combined_briefs,
                "m1_goal_specs": combined_specs,
                "m2_dispatch_plan": m2_plan,
                "cross_skill_relays": combined_relays,
                "wave_plan": {
                    "wave_1_disjoint_hosts": wave1_hosts,
                    "wave_2_cross_skill_relays": wave2_relays,
                },
            }
            base_report["m2_dispatch_plan"] = m2_plan
            base_report["stage2_triage"] = {
                "picked_skill": triage_meta.get("picked_skill"),
                "picked_mode": triage_meta.get("picked_mode"),
                "picked_aspects": triage_meta.get("picked_aspects"),
                "selected_skills": triage_meta.get("selected_skills"),
                "excluded_by_active_lease": triage_meta.get("excluded_by_active_lease"),
                "m2_dispatch_plan": m2_plan,
            }
            print(json.dumps(base_report, ensure_ascii=False, indent=2))
            return 0
        target_skill = triage_meta.get("picked_skill")
        if ns.mode == "auto" and triage_meta.get("picked_mode") in {"partial", "full"}:
            eff_mode = str(triage_meta["picked_mode"])
        if not eff_aspects and triage_meta.get("picked_aspects"):
            eff_aspects = ",".join(triage_meta["picked_aspects"])

    relays_list = (
        [{"from_skill": target_skill or "", "to_skill": ns.relay_to, "requirement": ns.relay_requirement}]
        if ns.relay_to
        else None
    )
    report = compute_skill_retro(
        warehouse_db=ns.warehouse_db,
        copilot_db=ns.copilot_db,
        copilot_sessions_dir=ns.copilot_sessions_dir,
        usage_jsonl=ns.usage_jsonl,
        trace_export_path=ns.trace_export,
        handoff_root=ns.handoff_root,
        skills_root=ns.skills_root,
        dev_root=ns.dev_root,
        telemetry_shards_dir=ns.telemetry_shards_dir,
        skill=target_skill,
        emit_briefs_dir=ns.emit_briefs,
        mode=eff_mode,
        aspects=eff_aspects,
        coupled_with=ns.coupled_with,
        relays=relays_list,
        days=ns.days,
        limit_sessions=None if ns.deep_all else ns.limit_sessions,
        auto_propose=ns.auto_propose,
        proposals_path=ns.proposals_path,
        icloud_sync_dir=ns.icloud_sync_dir,
        icloud_roi_path=ns.icloud_roi_path,
        local_roi_path=ns.local_roi_path,
        mirror_roi_snapshot=False if ns.no_roi_mirror else None,
        isolated_fixtures=bool(ns.isolated_fixtures),
    )
    if triage_meta is not None:
        report["stage2_triage"] = {
            "picked_skill": triage_meta.get("picked_skill"),
            "picked_mode": triage_meta.get("picked_mode"),
            "picked_aspects": triage_meta.get("picked_aspects"),
            "selected_skills": triage_meta.get("selected_skills"),
            "excluded_by_active_lease": triage_meta.get("excluded_by_active_lease"),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
