from __future__ import annotations
from .common import *

# AST-partitioned skill-retro implementation module.

def _read_l2_channel_counts(wh: Path, start30: datetime, end30: datetime) -> dict[str, dict[str, int]]:
    """Read optional 30d per-skill call counts from sibling L2 clean DBs when present."""
    counts: dict[str, dict[str, int]] = {}
    clean_dir = wh.parent.parent / "L2_normalize" / "clean"
    if not clean_dir.is_dir():
        return counts
    day_from = start30.date().isoformat()
    day_to = (end30 - timedelta(days=1)).date().isoformat()
    specs = (
        ("codex", clean_dir / "codex_prompts.db", "SELECT skill, COUNT(*) FROM skill_use WHERE day BETWEEN ? AND ? GROUP BY skill"),
        ("hermes", clean_dir / "hermes_tools.db", "SELECT skill, COALESCE(SUM(n), 0) FROM skill_day WHERE day BETWEEN ? AND ? GROUP BY skill"),
        ("copilot", clean_dir / "copilot_cli.db", "SELECT skill, COALESCE(SUM(n), 0) FROM skill_day WHERE day BETWEEN ? AND ? GROUP BY skill"),
        ("claude", clean_dir / "claude_prompts.db", "SELECT slash_skill, COUNT(*) FROM prompt WHERE slash_skill IS NOT NULL AND slash_skill != '' AND day BETWEEN ? AND ? GROUP BY slash_skill"),
    )
    for channel, db_path, sql in specs:
        if not db_path.is_file():
            continue
        try:
            with contextlib.closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
                for raw_sk, raw_n in conn.execute(sql, (day_from, day_to)):
                    sk = str(raw_sk or "").strip().lstrip("/")
                    if not sk or sk == "(unrecorded)":
                        continue
                    norm = "team-lead" if sk in {"goal-target-task", "goal-task"} else sk
                    bucket = counts.setdefault(norm, {"claude": 0, "copilot": 0, "codex": 0, "hermes": 0, "gtt": 0})
                    bucket[channel] = max(bucket[channel], int(raw_n or 0))
        except sqlite3.Error:
            continue
    return counts

def _collect_skill_details(
    *,
    inventory: Mapping[str, Mapping[str, Any]],
    skill_rows: Sequence[Mapping[str, Any]],
    per_skill_channels: Mapping[str, Mapping[str, Any]],
    l2_channels: Mapping[str, Mapping[str, Any]],
    per_skill_cache: Mapping[str, Mapping[str, Any]],
    s11_context: Mapping[str, Any],
    per_skill_refs: Mapping[str, Mapping[str, Any]],
    per_skill_scripts: Mapping[str, Mapping[str, Any]],
    per_skill_eval_cases: Mapping[str, Mapping[str, Any]],
    warehouse_present: bool,
) -> list[dict[str, Any]]:
    """Build per-skill inspector details with redacted doc_preview and references."""
    row_by_skill = {r["skill"]: r for r in skill_rows}
    global_eval_catalog: dict[str, dict[str, Any]] = {}
    for inv_m in inventory.values():
        for c_entry in inv_m.get("eval_cases", []):
            cid = str(c_entry.get("id") or "").strip()
            if cid and cid not in global_eval_catalog:
                global_eval_catalog[cid] = dict(c_entry)

    all_detail_skills = sorted(
        set(inventory.keys()) | {sk for sk, row_d in row_by_skill.items() if int(row_d.get("loads_30d") or 0) > 0},
        key=lambda sk: (
            0 if sk in inventory else 1,
            -(row_by_skill.get(sk, {}).get("loads_30d") or 0),
            0 if (inventory.get(sk, {}).get("scope") or "global") == "global" else 1,
            sk,
        ),
    )
    skill_details: list[dict[str, Any]] = []
    for sk in all_detail_skills:
        is_ghost = sk not in inventory
        inv_meta = inventory.get(sk, {})
        row_data = row_by_skill.get(sk, {})
        scope = str(inv_meta.get("scope") or "global")
        repo_label = str(inv_meta.get("repo") or "user-skills")
        skill_dir_str = str(inv_meta.get("skill_dir") or f"~/Development/user-skills/skills/{sk}")
        skill_md_lines = int(inv_meta.get("l0_lines") or 0)
        fm_tok_est = int(inv_meta.get("frontmatter_tokens_est") or 1)
        body_tok_est = int(inv_meta.get("body_tokens_est") or max(1, skill_md_lines * 12))

        loads_7d_val = int(row_data.get("loads_7d") or 0)
        loads_30d_val = int(row_data.get("loads_30d") or 0)
        needs_opt = bool(row_data.get("needs_optimization"))
        if is_ghost:
            status_label = "ghost"
        elif loads_30d_val == 0:
            status_label = "zombie_30d"
        elif needs_opt:
            status_label = "needs_optimization"
        else:
            status_label = "active"

        obs_ch = per_skill_channels.get(sk, {})
        l2_ch = l2_channels.get(sk, {})
        source_breakdown = {
            ch_key: max(int(obs_ch.get(ch_key) or 0), int(l2_ch.get(ch_key) or 0))
            for ch_key in ("claude", "copilot", "codex", "hermes", "gtt")
        }

        cache_b = per_skill_cache.get(sk, {})
        tot_in_sk = int(cache_b.get("in") or 0)
        tot_cr_sk = int(cache_b.get("cache_read") or 0)
        sk_kv_cache_pct = (
            round(100.0 * tot_cr_sk / (tot_in_sk + tot_cr_sk), 1)
            if (tot_in_sk + tot_cr_sk) > 0
            else s11_context.get("kv_cache_hit_pct")
        )

        # Merge physical references + observed warehouse reference_hits
        phys_refs = {entry["rel_path"]: dict(entry) for entry in inv_meta.get("reference_entries", [])}
        obs_refs = per_skill_refs.get(sk, {})
        merged_ref_paths = sorted(set(phys_refs.keys()) | set(obs_refs.keys()))
        ref_items: list[dict[str, Any]] = []
        for rel_p in merged_ref_paths:
            p_info = phys_refs.get(rel_p)
            o_info = obs_refs.get(rel_p)
            hits_val: int | None
            sessions_val: int | None
            if warehouse_present:
                hits_val = int(o_info["hits"]) if o_info else 0
                sessions_val = len(o_info["sessions"]) if o_info else 0
            else:
                hits_val = None
                sessions_val = None
            obs_bytes = int(o_info["max_bytes"]) if o_info else 0
            tok_est = int(p_info["tokens_est"]) if p_info else max(1, obs_bytes // 4)
            ln_cnt = int(p_info["line_count"]) if p_info else max(1, obs_bytes // 48) if obs_bytes else 0
            ref_item: dict[str, Any] = {
                "rel_path": rel_p,
                "line_count": ln_cnt,
                "tokens_est": tok_est,
                "hits_30d": hits_val,
                "sessions_30d": sessions_val,
                "is_unhit": bool(hits_val == 0),
            }
            if p_info and isinstance(p_info.get("doc_preview"), str) and p_info["doc_preview"]:
                ref_item["doc_preview"] = p_info["doc_preview"]
            ref_items.append(ref_item)

        unhit_refs_count = sum(1 for r in ref_items if r["is_unhit"])
        unhit_refs_ratio = round(unhit_refs_count / len(ref_items), 4) if ref_items else 0.0
        refs_tokens_est = (
            int(inv_meta["refs_tokens_est"])
            if "refs_tokens_est" in inv_meta
            else sum(int(r.get("tokens_est") or 0) for r in ref_items)
        )
        refs_kb = (
            float(inv_meta["refs_kb"])
            if "refs_kb" in inv_meta
            else round((refs_tokens_est * 4) / 1024.0, 1)
        )
        bundle_kb = (
            float(inv_meta["bundle_kb"])
            if "bundle_kb" in inv_meta
            else round(((body_tok_est + refs_tokens_est) * 4) / 1024.0, 1)
        )

        # Merge physical scripts + observed warehouse script_call (strictly filtered)
        phys_scripts = {sc for sc in inv_meta.get("script_entries", []) if _is_valid_skill_script_rel_path(sc)}
        obs_scripts = {
            sc: info
            for sc, info in per_skill_scripts.get(sk, {}).items()
            if _is_valid_skill_script_rel_path(sc)
        }
        merged_script_paths = sorted(phys_scripts | set(obs_scripts.keys()))
        script_items: list[dict[str, Any]] = []
        for rel_sc in merged_script_paths:
            o_sc = obs_scripts.get(rel_sc)
            if warehouse_present:
                calls_val: int | None = int(o_sc["calls"]) if o_sc else 0
            else:
                calls_val = None
            if o_sc and int(o_sc["calls"]) > 0:
                graded = int(o_sc.get("graded_calls") or 0)
                succ_rate = round(int(o_sc["successes"]) / graded, 4) if graded > 0 else None
                durs = [float(v) for v in o_sc.get("durations", [])]
                p95_val = round(float(_p95(durs)), 2) if durs else None
            else:
                succ_rate = None
                p95_val = None
            script_items.append({
                "rel_path": rel_sc,
                "calls_30d": calls_val,
                "success_rate": succ_rate,
                "p95_ms": p95_val,
                "is_unpracticed": bool(calls_val == 0),
            })

        gate_script_calls_30d = sum(
            int(s.get("calls_30d") or 0)
            for s in script_items
            if Path(str(s["rel_path"])).name in _GATE_SCRIPT_BASENAMES
        )

        # Merge parsed eval_cases + observed per-skill eval case events
        parsed_cases = [dict(c) for c in inv_meta.get("eval_cases", [])]
        obs_cases = per_skill_eval_cases.get(sk, {})
        known_case_ids = {c["id"] for c in parsed_cases}
        eval_case_items: list[dict[str, Any]] = []
        for idx_c, c_entry in enumerate(parsed_cases):
            cid = c_entry["id"]
            c_obs = obs_cases.get(cid, {})
            int_30d = int(c_obs.get("interceptions_30d") or 0)
            if idx_c == 0 and int_30d == 0 and gate_script_calls_30d > 0 and c_entry.get("status", "active") == "active":
                int_30d = gate_script_calls_30d
            raw_title = str(c_entry.get("title") or "").strip()
            safe_title = (
                raw_title
                if raw_title and raw_title != cid
                else f"{cid} — {c_entry.get('assertion_summary') or 'Contract gate verification check'}"
            )
            eval_case_items.append({
                "id": cid,
                "title": safe_title,
                "kind": c_entry["kind"],
                "assertion_summary": c_entry["assertion_summary"],
                "status": c_entry.get("status", "active"),
                "interceptions_30d": int_30d,
                "escapes_30d": int(c_obs.get("escapes_30d") or 0),
            })
        for cid, c_obs in sorted(obs_cases.items()):
            if cid in known_case_ids or not _EVAL_CASE_ID_RE.match(cid):
                continue
            global_match = global_eval_catalog.get(cid)
            if global_match:
                m_title = str(global_match.get("title") or "").strip()
                m_summary = str(global_match.get("assertion_summary") or f"Observed gate event for {cid}")
                m_kind = str(global_match.get("kind") or "contract")
                safe_title = m_title if m_title and m_title != cid else f"{cid} — {m_summary}"
            else:
                m_summary = f"Observed gate verification event for {cid}"
                m_kind = "contract"
                safe_title = f"{cid} — Gate contract verification"
            eval_case_items.append({
                "id": cid,
                "title": safe_title,
                "kind": m_kind,
                "assertion_summary": m_summary,
                "status": "active",
                "interceptions_30d": int(c_obs.get("interceptions_30d") or 0),
                "escapes_30d": int(c_obs.get("escapes_30d") or 0),
            })

        total_eval_interceptions = max(
            int(row_data.get("eval_interceptions") or 0),
            sum(int(c.get("interceptions_30d") or 0) for c in eval_case_items),
        )

        detail_meta = {
            key: row_data[key]
            for key in ("window_cst", "window_start", "window_end", "source_as_of", "sample_size", "coverage_status")
            if key in row_data
        }
        if not detail_meta:
            detail_meta = _freshness([], warehouse_present, 30)

        skill_details.append({
            **detail_meta,
            "skill": sk,
            "is_ghost": is_ghost,
            "scope": scope,
            "repo": repo_label,
            "skill_dir": skill_dir_str,
            "skill_md_path": "SKILL.md",
            "skill_md_lines": skill_md_lines,
            "frontmatter_tokens_est": fm_tok_est,
            "body_tokens_est": body_tok_est,
            "bundle_kb": bundle_kb,
            "refs_kb": refs_kb,
            "refs_tokens_est": refs_tokens_est,
            "doc_preview": str(inv_meta.get("doc_preview") or ""),
            "loads_7d": loads_7d_val,
            "loads_30d": loads_30d_val,
            "empty_load_rate": row_data.get("empty_load_rate"),
            "unpracticed_rate": row_data.get("unpracticed_rate"),
            "unhit_refs_count": unhit_refs_count,
            "unhit_refs_ratio": unhit_refs_ratio,
            "roi_score": row_data.get("roi_score"),
            "r1_pass_rate": row_data.get("r1_pass_rate"),
            "avg_context_tokens": row_data.get("avg_context_tokens"),
            "kv_cache_hit_pct": sk_kv_cache_pct,
            "edit_churn": int(row_data.get("edit_churn_count") or 0),
            "retry_churn": int(row_data.get("retry_commands") or 0),
            "impl_bug_escapes": int(row_data.get("impl_bug_escapes") or 0),
            "spec_gap_escapes": int(row_data.get("spec_gap_escapes") or 0),
            "eval_interceptions": total_eval_interceptions,
            "eval_escapes": int(row_data.get("eval_escapes") or 0),
            "status_label": status_label,
            "source_breakdown": source_breakdown,
            "references": ref_items,
            "scripts": script_items,
            "eval_cases": eval_case_items,
        })
    return skill_details
