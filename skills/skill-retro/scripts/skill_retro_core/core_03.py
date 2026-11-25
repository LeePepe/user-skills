from __future__ import annotations
from .common import *

# AST-partitioned skill-retro implementation module.

def _evaluate_session_progressive_and_preread(
    loaded_for_sk: Sequence[SessionObservation],
    sk_name: str,
) -> tuple[int, list[str], int]:
    """Compute (progressive_catch_count, wasted_preread_sids, unresolved_failure_count) for a skill across sessions.

    Establishes a unified chronological global timeline (`timeline_idx`) across all reads, edits, script calls,
    and bash commands in the session (sorting by `(ts, seq)` and deduplicating mirrored cross-source calls):
      - Wasted pre-read: reading >=3 distinct drawers cold-start before the first script call or edit on the
        unified timeline, OR reading drawer(s) after which zero downstream actions occur (`action_idx > last_read_idx`).
      - Progressive catch: `exit=1` on `script_rel` followed within 1-2 turns (`<=6` unified global timeline steps,
        `<=2` intervening calls of the same `script_rel`) by reading a drawer or editing code and passing `exit=0`
        on the SAME `script_rel`.
      - Unresolved failure count: count of distinct `(session_id, script_rel)` validators whose chronologically
        last failure is not followed by a subsequent `exit=0` on the same `script_rel`.
    """
    wasted_sids: list[str] = []
    prog_catches = 0
    unresolved_failures = 0

    for o in loaded_for_sk:
        # Build unified chronological session timeline across all sources
        raw_timeline: list[tuple[datetime, int, str, Any]] = []
        for r_sk, r_rel, r_ts, r_seq in o.ref_reads:
            raw_timeline.append((r_ts, r_seq, "read", (r_sk, r_rel)))

        edit_ev_list = list(o.edit_events)
        if not edit_ev_list and o.edit_seqs:
            edit_ev_list = [(o.started_at, e_seq) for e_seq in o.edit_seqs]
        for e_ts, e_seq in edit_ev_list:
            raw_timeline.append((e_ts, e_seq, "edit", None))

        all_call_seqs = {c.seq for c in o.script_calls}
        for c_ev in _deduplicate_session_script_calls(o):
            raw_timeline.append((c_ev.ts, c_ev.seq, "script", c_ev))

        bash_ev_list = list(o.bash_events)
        if not bash_ev_list and o.bash_commands:
            bash_ev_list = [(cmd, ec, dur, o.started_at, b_seq) for cmd, ec, dur, b_seq in o.bash_commands]
        for _, _, _, b_ts, b_seq in bash_ev_list:
            if b_seq in all_call_seqs:
                continue
            raw_timeline.append((b_ts, b_seq, "bash", None))

        raw_timeline.sort(key=lambda item: (item[0], item[1]))

        sk_reads_tl: list[tuple[str, int]] = []
        sk_edits_tl: list[int] = []
        all_actions_tl: list[int] = []
        sk_calls_tl: list[tuple[int, ScriptCallEvent]] = []

        for tl_idx, (_, _, kind, payload) in enumerate(raw_timeline, 1):
            if kind == "read":
                r_sk, r_rel = payload
                if r_sk == sk_name:
                    sk_reads_tl.append((r_rel, tl_idx))
            elif kind == "edit":
                sk_edits_tl.append(tl_idx)
                all_actions_tl.append(tl_idx)
            elif kind == "script":
                c_obj: ScriptCallEvent = payload
                all_actions_tl.append(tl_idx)
                if c_obj.skill == sk_name:
                    sk_calls_tl.append((tl_idx, c_obj))
            elif kind == "bash":
                all_actions_tl.append(tl_idx)

        first_call_or_edit_idx = min(
            [idx for idx, _ in sk_calls_tl] + sk_edits_tl,
            default=None,
        )
        cold_start_distinct_drawers = {
            r_rel
            for r_rel, r_idx in sk_reads_tl
            if first_call_or_edit_idx is None or r_idx < first_call_or_edit_idx
        }
        last_read_idx = max((r_idx for _, r_idx in sk_reads_tl), default=None)
        has_action_after_reads = (
            any(a_idx > last_read_idx for a_idx in all_actions_tl)
            if last_read_idx is not None
            else bool(all_actions_tl or o.edit_count > 0)
        )
        if len(cold_start_distinct_drawers) >= 3 or (sk_reads_tl and not has_action_after_reads):
            wasted_sids.append(o.session_id)

        # Evaluate per-validator (script_rel) failure and recovery on the unified global timeline
        by_validator: dict[str, list[tuple[int, ScriptCallEvent]]] = {}
        for tl_idx, c_obj in sk_calls_tl:
            by_validator.setdefault(c_obj.script_rel, []).append((tl_idx, c_obj))

        for _, v_items in by_validator.items():
            failed_positions = [pos for pos, (_, c_obj) in enumerate(v_items) if c_obj.exit_code not in (None, 0, 2)]
            if not failed_positions:
                continue
            validator_recovered = False
            last_recovered_pos = -1
            for pos, (fail_tl_idx, call_ev) in enumerate(v_items):
                if call_ev.exit_code in (None, 0, 2):
                    continue
                if pos < last_recovered_pos:
                    continue
                later_same_script = [
                    (pos + offset, pass_tl_idx, later)
                    for offset, (pass_tl_idx, later) in enumerate(v_items[pos + 1 : pos + 3], 1)
                    if later.exit_code == 0 and 0 < (pass_tl_idx - fail_tl_idx) <= 6
                ]
                for pass_pos, pass_tl_idx, _ in later_same_script:
                    intervening_read_or_edit = any(
                        fail_tl_idx < r_idx < pass_tl_idx for _, r_idx in sk_reads_tl
                    ) or any(
                        fail_tl_idx < e_idx < pass_tl_idx for e_idx in sk_edits_tl
                    )
                    if intervening_read_or_edit:
                        validator_recovered = True
                        last_recovered_pos = max(last_recovered_pos, pass_pos)
                        break
            if validator_recovered:
                prog_catches += 1
            last_fail_pos = max(failed_positions)
            has_pass_after_last_fail = any(c_obj.exit_code == 0 for _, c_obj in v_items[last_fail_pos + 1 :])
            if not has_pass_after_last_fail:
                unresolved_failures += 1

    return prog_catches, wasted_sids, unresolved_failures
