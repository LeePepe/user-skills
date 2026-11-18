#!/usr/bin/env python3
"""Personal portfolio diagnosis & cashflow/FIFO rebalancing engine.

Consumes personal Financial CLI/JSON ledgers and delegates ALL objective asset scoring
to sibling skill `asset-screener` (`skills/asset-screener/scripts/asset_evaluator.py`)
after stripping personal ledger fields.

Core engines:
1. All-Weather Barbell Allocation Diagnosis (Offensive Growth vs Trunk Broad Beta vs Defensive Ballast)
2. FIFO Holding Period & Zero-Fee Redemption Window Engine (7 / 30 / 180 / 365 days)
3. Track & Top-10 Holding Overlap Matrix (A/C duplicate, Top-10 stock collision, Broad-beta redundancy)
4. Incremental Cashflow & Non-Capitulation Rebalancing (`hold_underwater_stop_dca` vs `redeem_unlocked_fifo`)
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

VALID_ACTION_ENUMS: tuple[str, ...] = (
    "STOP_DCA",
    "REDUCE_DCA",
    "REDEEM_FIFO_UNLOCKED",
    "HOLD_KEEP_DCA",
    "HOLD_WAIT_REBOUND",
    "ADD_NEW_DCA",
)

DEFAULT_BARBELL_TARGETS: dict[str, float] = {
    "offensive_growth": 35.0,
    "trunk_broad_beta": 30.0,
    "defensive_ballast": 35.0,
}

DEFAULT_DEFENSIVE_CANDIDATES: tuple[dict[str, Any], ...] = (
    {
        "code": "AU_ACC_000217",
        "name": "银行积存金 + 华安黄金易ETF联接C(000217)",
        "category": "macro_hedge_gold_bond",
        "sub_type": "gold",
        "mgmt_fee_pct": 0.25,
        "custody_fee_pct": 0.05,
        "sales_service_fee_pct": 0.0,
        "tips_10y_real_yield_pct": 1.65,
        "central_bank_tonnes_qtr": 321.0,
        "drawdown_from_peak_pct": 31.0,
        "rsi_14": 35.0,
        "bb_pct_b": 0.22,
        "percentile_52w": 24.0,
        "target_budget_share": 0.48,
        "role": "抗通胀与科技尾部风险对冲底仓",
    },
    {
        "code": "007466",
        "name": "华泰柏瑞中证红利低波ETF联接A",
        "category": "dividend_value",
        "mgmt_fee_pct": 0.15,
        "custody_fee_pct": 0.05,
        "sales_service_fee_pct": 0.0,
        "dividend_yield_pct": 5.15,
        "consecutive_dividend_years": 8,
        "payout_coverage_ratio": 1.85,
        "total_return_index": True,
        "rsi_14": 44.0,
        "bb_pct_b": 0.40,
        "percentile_52w": 42.0,
        "target_budget_share": 0.28,
        "role": "高股息央国企现金奶牛（与科技成长负相关）",
    },
    {
        "code": "003376",
        "name": "广发中债7-10年国开债指数A",
        "category": "macro_hedge_gold_bond",
        "sub_type": "pure_bond",
        "mgmt_fee_pct": 0.15,
        "custody_fee_pct": 0.05,
        "sales_service_fee_pct": 0.0,
        "duration_years": 7.2,
        "ytm_pct": 2.32,
        "equity_exposure_pct": 0.0,
        "equity_corr_3y": -0.28,
        "credit_tier": "SOVEREIGN_POLICY",
        "rsi_14": 48.0,
        "bb_pct_b": 0.45,
        "percentile_52w": 50.0,
        "target_budget_share": 0.24,
        "role": "0股票政金债安全垫（满7天0赎回费抄底蓄水池）",
    },
)

FEE_SCHEDULES: dict[str, dict[str, Any]] = {
    "pure_bond_7d": {
        "zero_fee_days": 7,
        "brackets": ((7, 1.50), (10_000, 0.00)),
    },
    "c_share_30d": {
        "zero_fee_days": 30,
        "brackets": ((7, 1.50), (30, 0.50), (10_000, 0.00)),
    },
    "a_share_180d": {
        "zero_fee_days": 180,
        "brackets": ((7, 1.50), (30, 0.50), (180, 0.25), (10_000, 0.00)),
    },
    "a_share_365d": {
        "zero_fee_days": 365,
        "brackets": ((7, 1.50), (30, 0.50), (365, 0.25), (10_000, 0.00)),
    },
    "qdii_sticky_50bps": {
        "zero_fee_days": None,
        "low_friction_days": 30,
        "brackets": ((7, 1.50), (10_000, 0.50)),
    },
}


def _load_asset_screener_module():
    evaluator_path = (
        Path(__file__).resolve().parents[2] / "asset-screener" / "scripts" / "asset_evaluator.py"
    )
    if not evaluator_path.is_file():
        raise FileNotFoundError(f"Sibling asset-screener script not found: {evaluator_path}")
    spec = importlib.util.spec_from_file_location("asset_screener_evaluator", evaluator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load asset-screener module from {evaluator_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fnum(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_day(raw: Any, fallback: date) -> date:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str) and len(raw) >= 10:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return fallback
    return fallback


def _category_to_sleeve(category: str) -> str:
    if category == "tech_cycle_growth":
        return "offensive_growth"
    if category == "broad_beta":
        return "trunk_broad_beta"
    return "defensive_ballast"


def _infer_fee_schedule(holding: Mapping[str, Any], category: str) -> str:
    explicit = str(holding.get("fee_schedule") or "").strip()
    if explicit in FEE_SCHEDULES:
        return explicit
    name = str(holding.get("fund_name") or holding.get("name") or "")
    code = str(holding.get("fund_code") or holding.get("code") or "")
    if category == "macro_hedge_gold_bond" and any(tok in name for tok in ("债", "国开")):
        return "pure_bond_7d"
    if name.endswith("C") or holding.get("share_class") == "C":
        return "c_share_30d"
    if "QDII" in name.upper() and bool(holding.get("sticky_redemption_fee", False)):
        return "qdii_sticky_50bps"
    if "ETF联接A" in name or ("QDII" in name.upper() and name.endswith("A")):
        return "a_share_180d"
    if code == "000041":
        return "qdii_sticky_50bps"
    return "a_share_365d"


def _fee_pct_for_days(schedule_name: str, held_days: int) -> float:
    sched = FEE_SCHEDULES[schedule_name]
    for max_days, fee_pct in sched["brackets"]:
        if held_days < max_days:
            return float(fee_pct)
    return float(sched["brackets"][-1][1])


def _strip_for_asset_screener(holding: Mapping[str, Any], evaluator_module: Any) -> dict[str, Any]:
    forbidden = evaluator_module.PERSONAL_LEDGER_FORBIDDEN_KEYS
    public_asset: dict[str, Any] = {}
    for key, val in holding.items():
        if key in forbidden:
            continue
        public_asset[key] = val
    public_asset["code"] = str(holding.get("code") or holding.get("fund_code") or "UNKNOWN")
    public_asset["name"] = str(holding.get("name") or holding.get("fund_name") or public_asset["code"])
    return public_asset


def compute_fifo_window(holding: Mapping[str, Any], as_of: date, category: str) -> dict[str, Any]:
    schedule_name = _infer_fee_schedule(holding, category)
    sched = FEE_SCHEDULES[schedule_name]
    zero_days: int | None = sched.get("zero_fee_days")
    unlock_threshold = zero_days if zero_days is not None else int(sched.get("low_friction_days", 30))

    latest_nav = _fnum(holding.get("latest_nav"), 0.0)
    remaining_shares = _fnum(holding.get("remaining_shares", holding.get("holding_shares")), 0.0)
    market_value = _fnum(holding.get("market_value"), remaining_shares * latest_nav)
    if latest_nav <= 0 and remaining_shares > 0 and market_value > 0:
        latest_nav = market_value / remaining_shares

    raw_lots = holding.get("lots")
    fifo_lots: list[dict[str, Any]] = []
    precision = "exact_fifo_lots"

    if isinstance(raw_lots, list) and raw_lots:
        open_lots: list[dict[str, Any]] = []
        sorted_events = sorted(
            (dict(item) for item in raw_lots if isinstance(item, Mapping)),
            key=lambda x: str(x.get("confirmed_date") or x.get("trade_date") or "1970-01-01"),
        )
        for ev in sorted_events:
            direction = str(ev.get("direction") or "purchase").lower()
            shares = _fnum(ev.get("shares", ev.get("confirmed_shares")), 0.0)
            amount = _fnum(ev.get("amount", ev.get("confirmed_amount")), 0.0)
            lot_day = _parse_day(ev.get("confirmed_date") or ev.get("trade_date"), as_of)
            if direction in {"purchase", "buy", "transfer_out"} and shares > 0:
                unit_cost = amount / shares if amount > 0 else _fnum(ev.get("nav"), latest_nav)
                open_lots.append({"date": lot_day, "shares": shares, "unit_cost": unit_cost})
            elif direction in {"redemption", "sell", "transfer_in"} and shares > 0:
                to_consume = shares
                while to_consume > 1e-9 and open_lots:
                    head = open_lots[0]
                    take = min(head["shares"], to_consume)
                    head["shares"] -= take
                    to_consume -= take
                    if head["shares"] <= 1e-7:
                        open_lots.pop(0)
        fifo_lots = open_lots
    else:
        precision = "estimated_from_summary"
        first_day = _parse_day(holding.get("first_buy_date"), as_of - timedelta(days=120))
        last_day = _parse_day(holding.get("last_trade_date"), first_day)
        avg_cost = _fnum(holding.get("avg_cost_nav"), latest_nav)
        recent_dca_cny = _fnum(holding.get("monthly_dca_cny"), 0.0)
        if (as_of - last_day).days < 30 and recent_dca_cny > 0 and latest_nav > 0 and remaining_shares > 0:
            locked_est = min(remaining_shares, recent_dca_cny / latest_nav)
            older_est = max(0.0, remaining_shares - locked_est)
            if older_est > 0:
                fifo_lots.append({"date": first_day, "shares": older_est, "unit_cost": avg_cost})
            if locked_est > 0:
                fifo_lots.append({"date": last_day, "shares": locked_est, "unit_cost": avg_cost})
        elif remaining_shares > 0:
            fifo_lots.append({"date": first_day, "shares": remaining_shares, "unit_cost": avg_cost})

    buckets = {
        "lt_7d_punitive_shares": 0.0,
        "ge_30d_shares": 0.0,
        "ge_180d_shares": 0.0,
        "ge_365d_shares": 0.0,
        "zero_fee_unlocked_shares": 0.0,
        "locked_shares": 0.0,
    }
    unlocked_value = 0.0
    locked_value = 0.0
    next_unlock_dates: list[date] = []

    for lot in fifo_lots:
        shares = float(lot["shares"])
        if shares <= 1e-7:
            continue
        held_days = max(0, (as_of - lot["date"]).days)
        fee_pct = _fee_pct_for_days(schedule_name, held_days)
        if held_days < 7:
            buckets["lt_7d_punitive_shares"] += shares
        if held_days >= 30:
            buckets["ge_30d_shares"] += shares
        if held_days >= 180:
            buckets["ge_180d_shares"] += shares
        if held_days >= 365:
            buckets["ge_365d_shares"] += shares

        is_unlocked = (fee_pct == 0.0) if zero_days is not None else False
        if is_unlocked:
            buckets["zero_fee_unlocked_shares"] += shares
            unlocked_value += shares * latest_nav
        else:
            buckets["locked_shares"] += shares
            locked_value += shares * latest_nav
            if zero_days is not None:
                next_unlock_dates.append(lot["date"] + timedelta(days=zero_days))
            elif held_days < unlock_threshold:
                next_unlock_dates.append(lot["date"] + timedelta(days=unlock_threshold))

    return {
        "code": str(holding.get("fund_code") or holding.get("code") or "UNKNOWN"),
        "name": str(holding.get("fund_name") or holding.get("name") or ""),
        "fee_schedule": schedule_name,
        "zero_fee_threshold_days": zero_days,
        "lot_precision": precision,
        "lt_7d_punitive_shares": round(buckets["lt_7d_punitive_shares"], 2),
        "ge_30d_shares": round(buckets["ge_30d_shares"], 2),
        "ge_180d_shares": round(buckets["ge_180d_shares"], 2),
        "ge_365d_shares": round(buckets["ge_365d_shares"], 2),
        "zero_fee_unlocked_shares": round(buckets["zero_fee_unlocked_shares"], 2),
        "zero_fee_unlocked_value_cny": round(unlocked_value, 2),
        "locked_shares": round(buckets["locked_shares"], 2),
        "locked_value_cny": round(locked_value, 2),
        "next_fee_free_unlock_date": min(next_unlock_dates).isoformat() if next_unlock_dates else None,
    }


def _normalize_fund_stem(name: str) -> str:
    cleaned = re.sub(r"\(QDII\)|（QDII）|ETF联接|发起式|[AC]$", "", name.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", "", cleaned)


def detect_portfolio_overlaps(holdings: Sequence[Mapping[str, Any]], scores_by_code: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    overlaps: list[dict[str, Any]] = []
    n = len(holdings)
    for i in range(n):
        for j in range(i + 1, n):
            left = holdings[i]
            right = holdings[j]
            l_code = str(left.get("fund_code") or left.get("code"))
            r_code = str(right.get("fund_code") or right.get("code"))
            l_name = str(left.get("fund_name") or left.get("name") or l_code)
            r_name = str(right.get("fund_name") or right.get("name") or r_code)

            # 1) Same-fund A/C share duplicate
            l_master = left.get("canonical_master_code") or _normalize_fund_stem(l_name)
            r_master = right.get("canonical_master_code") or _normalize_fund_stem(r_name)
            if l_master and l_master == r_master and l_code != r_code:
                l_score = float(scores_by_code[l_code]["total_score"])
                r_score = float(scores_by_code[r_code]["total_score"])
                weaker = r_code if l_score >= r_score else l_code
                overlaps.append(
                    {
                        "type": "SAME_FUND_AC_DUPLICATE",
                        "pair": [l_code, r_code],
                        "names": [l_name, r_name],
                        "overlap_ratio": 1.0,
                        "shared_holdings": ["100% identical underlying master portfolio"],
                        "weaker_code": weaker,
                        "recommendation": f"Consolidate duplicate A/C classes; halt/redeem weaker share class `{weaker}`.",
                    }
                )
                continue

            # 2) Top-10 stock collision (weighted or Jaccard)
            l_top = left.get("top10_holdings") or {}
            r_top = right.get("top10_holdings") or {}
            if isinstance(l_top, list):
                l_map = {str(k): 8.0 for k in l_top}
            elif isinstance(l_top, Mapping):
                l_map = {str(k): _fnum(v, 8.0) for k, v in l_top.items()}
            else:
                l_map = {}

            if isinstance(r_top, list):
                r_map = {str(k): 8.0 for k in r_top}
            elif isinstance(r_top, Mapping):
                r_map = {str(k): _fnum(v, 8.0) for k, v in r_top.items()}
            else:
                r_map = {}

            shared = sorted(set(l_map) & set(r_map))
            if shared:
                union = set(l_map) | set(r_map)
                jaccard = len(shared) / len(union) if union else 0.0
                min_weight_sum = sum(min(l_map[k], r_map[k]) for k in shared) / 100.0
                effective_overlap = round(max(jaccard, min_weight_sum), 3)
                if effective_overlap >= 0.35 or len(shared) >= 3:
                    l_score = float(scores_by_code[l_code]["total_score"])
                    r_score = float(scores_by_code[r_code]["total_score"])
                    weaker = r_code if l_score >= r_score else l_code
                    overlaps.append(
                        {
                            "type": "TOP10_STOCK_COLLISION",
                            "pair": [l_code, r_code],
                            "names": [l_name, r_name],
                            "overlap_ratio": effective_overlap,
                            "shared_holdings": shared,
                            "weaker_code": weaker,
                            "recommendation": f"High underlying stock collision ({', '.join(shared[:5])}); keep higher-scored `{l_code if weaker == r_code else r_code}` and stop/trim `{weaker}`.",
                        }
                    )
                    continue

            # 3) Domestic broad-beta redundancy
            l_cat = scores_by_code[l_code]["category"]
            r_cat = scores_by_code[r_code]["category"]
            l_sub = str(left.get("sub_track") or ("cn_broad" if any(t in l_name for t in ("沪深300", "上证综指", "中证A500")) else ""))
            r_sub = str(right.get("sub_track") or ("cn_broad" if any(t in r_name for t in ("沪深300", "上证综指", "中证A500")) else ""))
            if l_cat == "broad_beta" and r_cat == "broad_beta" and l_sub == "cn_broad" and r_sub == "cn_broad":
                l_score = float(scores_by_code[l_code]["total_score"])
                r_score = float(scores_by_code[r_code]["total_score"])
                weaker = r_code if l_score >= r_score else l_code
                overlaps.append(
                    {
                        "type": "BROAD_BETA_REDUNDANCY",
                        "pair": [l_code, r_code],
                        "names": [l_name, r_name],
                        "overlap_ratio": 0.70,
                        "shared_holdings": ["CN large-cap broad beta overlap"],
                        "weaker_code": weaker,
                        "recommendation": f"Redundant CN broad-beta feeders; concentrate core DCA into `{l_code if weaker == r_code else r_code}`.",
                    }
                )
    return overlaps


def compute_barbell_diagnosis(
    holdings: Sequence[Mapping[str, Any]],
    scores_by_code: Mapping[str, Mapping[str, Any]],
    targets: Mapping[str, float] = DEFAULT_BARBELL_TARGETS,
) -> dict[str, Any]:
    sleeve_values = {"offensive_growth": 0.0, "trunk_broad_beta": 0.0, "defensive_ballast": 0.0}
    for h in holdings:
        code = str(h.get("fund_code") or h.get("code"))
        mv = _fnum(h.get("market_value"), 0.0)
        sleeve = _category_to_sleeve(scores_by_code[code]["category"])
        sleeve_values[sleeve] += mv

    total_mv = sum(sleeve_values.values())
    sleeves_out: dict[str, dict[str, Any]] = {}
    alerts: list[str] = []
    for sleeve, val in sleeve_values.items():
        actual_pct = (val / total_mv * 100.0) if total_mv > 0 else 0.0
        target_pct = float(targets[sleeve])
        drift_pct = actual_pct - target_pct
        # Leland (1999) / Masters 5/25 tolerance band: min(5.0%, 25% * target_weight)
        band_half_width_pct = round(min(5.0, 0.25 * target_pct), 2)
        in_no_trade_zone = abs(drift_pct) <= band_half_width_pct
        sleeves_out[sleeve] = {
            "market_value_cny": round(val, 2),
            "actual_weight_pct": round(actual_pct, 2),
            "target_weight_pct": round(target_pct, 2),
            "drift_pct": round(drift_pct, 2),
            "tolerance_band_pct": band_half_width_pct,
            "zone_status": "NO_TRADE_BUFFER" if in_no_trade_zone else ("OVERWEIGHT_BREACH" if drift_pct > 0 else "UNDERWEIGHT_BREACH"),
        }

    if sleeves_out["offensive_growth"]["actual_weight_pct"] > 55.0:
        alerts.append(
            f"OVERWEIGHT_OFFENSIVE: offensive_growth is {sleeves_out['offensive_growth']['actual_weight_pct']:.1f}% (target {targets['offensive_growth']:.1f}%)."
        )
    if sleeves_out["defensive_ballast"]["actual_weight_pct"] < 5.0:
        alerts.append(
            f"DEFENSIVE_VACUUM: defensive_ballast is {sleeves_out['defensive_ballast']['actual_weight_pct']:.1f}% (target {targets['defensive_ballast']:.1f}%); zero cushion against equity drawdowns."
        )
    elif sleeves_out["defensive_ballast"]["actual_weight_pct"] < 20.0:
        alerts.append(
            f"UNDERWEIGHT_DEFENSIVE: defensive_ballast is {sleeves_out['defensive_ballast']['actual_weight_pct']:.1f}% (target {targets['defensive_ballast']:.1f}%)."
        )

    return {
        "total_fund_market_value_cny": round(total_mv, 2),
        "sleeves": sleeves_out,
        "imbalance_alerts": alerts,
    }


def generate_rebalance_plan(
    holdings: Sequence[Mapping[str, Any]],
    scores_by_code: Mapping[str, Mapping[str, Any]],
    fifo_by_code: Mapping[str, Mapping[str, Any]],
    overlaps: Sequence[Mapping[str, Any]],
    barbell: Mapping[str, Any],
    *,
    mode: str = "hold_underwater_stop_dca",
    monthly_budget_override: float | None = None,
    candidate_defs: Sequence[Mapping[str, Any]] = DEFAULT_DEFENSIVE_CANDIDATES,
    evaluator_module: Any,
) -> dict[str, Any]:
    weaker_overlap_codes = {str(item["weaker_code"]) for item in overlaps}
    offensive_overweight = barbell["sleeves"]["offensive_growth"]["actual_weight_pct"] > 55.0

    holding_actions: list[dict[str, Any]] = []
    current_total_dca = 0.0
    retained_dca = 0.0
    redeemed_unlocked_cash = 0.0

    for h in holdings:
        code = str(h.get("fund_code") or h.get("code"))
        name = str(h.get("fund_name") or h.get("name") or code)
        mv = _fnum(h.get("market_value"), 0.0)
        pnl = _fnum(h.get("unrealized_pnl"), 0.0)
        pnl_pct = _fnum(h.get("unrealized_pnl_pct"), 0.0)
        cur_dca = _fnum(h.get("monthly_dca_cny"), 0.0)
        max_keep_dca = _fnum(h.get("target_dca_cap_cny"), 1500.0 if scores_by_code[code]["category"] == "broad_beta" else 500.0)
        current_total_dca += cur_dca

        score_card = scores_by_code[code]
        tier = score_card["tier"]
        fifo = fifo_by_code[code]
        unlocked_shares = float(fifo["zero_fee_unlocked_shares"])
        unlocked_val = float(fifo["zero_fee_unlocked_value_cny"])
        is_weaker_dup = code in weaker_overlap_codes

        if mode == "redeem_unlocked_fifo" and (tier == "D_AVOID" or is_weaker_dup) and unlocked_shares > 0:
            action = "REDEEM_FIFO_UNLOCKED"
            new_dca = 0.0
            redeemed_unlocked_cash += unlocked_val
            reason = (
                f"Objective tier `{tier}` ({score_card['total_score']:.1f})"
                + (" + duplicate/overlap weaker leg" if is_weaker_dup else "")
                + f"; redeem {unlocked_shares:.2f} zero-fee unlocked shares (~¥{unlocked_val:,.2f}) and keep {fifo['locked_shares']:.2f} locked shares until {fifo['next_fee_free_unlock_date'] or 'unlock'}."
            )
        elif tier == "D_AVOID" or is_weaker_dup or (tier == "C_WATCH" and offensive_overweight and cur_dca > 0 and pnl_pct < -10.0 and not h.get("keep_reduced_dca", False)):
            if cur_dca > 0:
                action = "STOP_DCA"
                new_dca = 0.0
                reason = (
                    f"Halt monthly DCA (¥{cur_dca:,.0f} -> ¥0): tier `{tier}` ({score_card['total_score']:.1f})"
                    + (", weaker leg in track overlap" if is_weaker_dup else "")
                    + ("; hold underwater shares without panic selling." if pnl < 0 else ".")
                )
            else:
                action = "HOLD_WAIT_REBOUND"
                new_dca = 0.0
                reason = f"DCA already ¥0; hold existing position (PnL {pnl_pct:+.1f}%) waiting for rebound or zero-fee exit window."
        elif cur_dca > max_keep_dca and (offensive_overweight or tier in {"B_SATELLITE", "C_WATCH"} or scores_by_code[code]["category"] == "broad_beta"):
            action = "REDUCE_DCA"
            new_dca = max_keep_dca
            reason = f"Trim oversized DCA from ¥{cur_dca:,.0f} to ¥{new_dca:,.0f}/mo to free cashflow for underweight defensive ballast."
        elif cur_dca > 0 and tier in {"A_CORE", "B_SATELLITE"}:
            action = "HOLD_KEEP_DCA"
            new_dca = cur_dca
            reason = f"High-conviction `{tier}` ({score_card['total_score']:.1f}) with disciplined sizing; maintain ¥{cur_dca:,.0f}/mo DCA."
        else:
            action = "HOLD_WAIT_REBOUND"
            new_dca = 0.0
            reason = f"No active DCA; retain `{tier}` ({score_card['total_score']:.1f}) sleeve and monitor valuation/unlock window."

        retained_dca += new_dca
        holding_actions.append(
            {
                "code": code,
                "name": name,
                "category": score_card["category"],
                "sleeve": _category_to_sleeve(score_card["category"]),
                "objective_score": score_card["total_score"],
                "objective_tier": tier,
                "market_value_cny": round(mv, 2),
                "unrealized_pnl_cny": round(pnl, 2),
                "unrealized_pnl_pct": round(pnl_pct, 2),
                "current_monthly_dca_cny": round(cur_dca, 2),
                "new_monthly_dca_cny": round(new_dca, 2),
                "freed_monthly_cashflow_cny": round(max(0.0, cur_dca - new_dca), 2),
                "zero_fee_unlocked_shares": unlocked_shares,
                "next_fee_free_unlock_date": fifo["next_fee_free_unlock_date"],
                "action": action,
                "reason": reason,
            }
        )

    freed_dca = max(0.0, current_total_dca - retained_dca)
    deployable_monthly = (
        max(0.0, monthly_budget_override - retained_dca)
        if monthly_budget_override is not None
        else freed_dca
    )

    new_dca_Additions: list[dict[str, Any]] = []
    existing_codes = {str(h.get("fund_code") or h.get("code")) for h in holdings}
    eligible_candidates: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    for cand in candidate_defs:
        if str(cand.get("code")) in existing_codes:
            continue
        clean_cand = _strip_for_asset_screener(cand, evaluator_module)
        clean_cand.pop("target_budget_share", None)
        clean_cand.pop("role", None)
        scored = evaluator_module.evaluate_asset(clean_cand, strict_no_personal_ledger=True)
        if scored["tier"] in {"A_CORE", "B_SATELLITE"}:
            eligible_candidates.append((cand, scored))

    share_sum = sum(_fnum(c.get("target_budget_share"), 1.0) for c, _ in eligible_candidates) or 1.0
    if deployable_monthly > 0 and eligible_candidates:
        for cand, scored in eligible_candidates:
            weight = _fnum(cand.get("target_budget_share"), 1.0) / share_sum
            alloc_cny = round(deployable_monthly * weight / 50.0) * 50.0
            if alloc_cny <= 0:
                continue
            new_dca_Additions.append(
                {
                    "code": scored["code"],
                    "name": scored["name"],
                    "category": scored["category"],
                    "sleeve": _category_to_sleeve(scored["category"]),
                    "objective_score": scored["total_score"],
                    "objective_tier": scored["tier"],
                    "recommended_monthly_dca_cny": round(alloc_cny, 2),
                    "action": "ADD_NEW_DCA",
                    "role": str(cand.get("role") or "Defensive / Barbell rebalance sleeve"),
                    "consensus": scored["consensus"],
                }
            )

    # 6-month projection
    proj_sleeves = {
        k: float(v["market_value_cny"]) for k, v in barbell["sleeves"].items()
    }
    if mode == "redeem_unlocked_fifo" and redeemed_unlocked_cash > 0:
        proj_sleeves["offensive_growth"] = max(0.0, proj_sleeves["offensive_growth"] - redeemed_unlocked_cash)
        proj_sleeves["defensive_ballast"] += redeemed_unlocked_cash
    for item in holding_actions:
        proj_sleeves[item["sleeve"]] += 6.0 * float(item["new_monthly_dca_cny"])
    for add_item in new_dca_Additions:
        proj_sleeves[add_item["sleeve"]] += 6.0 * float(add_item["recommended_monthly_dca_cny"])

    proj_total = sum(proj_sleeves.values())
    projected_6m = {
        sleeve: {
            "projected_value_cny": round(val, 2),
            "projected_weight_pct": round((val / proj_total * 100.0) if proj_total > 0 else 0.0, 2),
        }
        for sleeve, val in proj_sleeves.items()
    }

    return {
        "mode": mode,
        "current_monthly_dca_total_cny": round(current_total_dca, 2),
        "retained_existing_dca_cny": round(retained_dca, 2),
        "freed_monthly_cashflow_cny": round(freed_dca, 2),
        "new_defensive_dca_cny": round(sum(x["recommended_monthly_dca_cny"] for x in new_dca_Additions), 2),
        "redeemed_fifo_unlocked_cash_cny": round(redeemed_unlocked_cash, 2),
        "holding_actions": holding_actions,
        "new_dca_recommendations": new_dca_Additions,
        "projected_6m_barbell": projected_6m,
    }


def _load_from_financial_cli(financial_root: Path) -> dict[str, Any]:
    cli_bin = financial_root / "cli" / "finance"
    if not cli_bin.is_file():
        raise FileNotFoundError(f"Financial CLI not found at {cli_bin}")

    funds_proc = subprocess.run(
        [sys.executable, str(cli_bin), "funds", "--json"],
        cwd=financial_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if funds_proc.returncode != 0:
        raise RuntimeError(f"finance funds --json failed ({funds_proc.returncode}): {funds_proc.stderr.strip()}")
    funds_data = json.loads(funds_proc.stdout)

    accounts_proc = subprocess.run(
        [sys.executable, str(cli_bin), "accounts", "--json"],
        cwd=financial_root,
        capture_output=True,
        text=True,
        check=False,
    )
    accounts_data = json.loads(accounts_proc.stdout) if accounts_proc.returncode == 0 else []

    confirmations_by_code: dict[str, list[dict[str, Any]]] = {}
    for conf in funds_data.get("confirmations") or []:
        code = str(conf.get("fund_code") or "")
        if code:
            confirmations_by_code.setdefault(code, []).append(
                {
                    "trade_date": conf.get("trade_date"),
                    "confirmed_date": conf.get("confirmed_date"),
                    "direction": "purchase" if conf.get("direction") == "transfer_out" else "redemption",
                    "shares": conf.get("confirmed_shares"),
                    "amount": conf.get("confirmed_amount"),
                    "nav": conf.get("confirmed_nav"),
                }
            )

    holdings: list[dict[str, Any]] = []
    for raw_h in funds_data.get("holdings") or []:
        item = dict(raw_h)
        code = str(item.get("fund_code") or "")
        if code in confirmations_by_code and "lots" not in item:
            item["lots"] = confirmations_by_code[code]
        holdings.append(item)

    return {
        "source": "financial_cli",
        "holdings": holdings,
        "accounts": accounts_data,
        "summary": {
            "total_holding_cost": funds_data.get("total_holding_cost"),
            "total_holding_market_value": funds_data.get("total_holding_market_value"),
            "total_unrealized_pnl": funds_data.get("total_unrealized_pnl"),
            "total_net_assets": funds_data.get("total_net_assets"),
        },
    }


def _load_portfolio_input(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "portfolio_json", None):
        candidate = Path(args.portfolio_json).expanduser()
        raw = candidate.read_text(encoding="utf-8") if candidate.is_file() else args.portfolio_json
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return {"source": "portfolio_json", "holdings": [dict(x) for x in parsed], "accounts": [], "candidates": []}
        if isinstance(parsed, dict):
            holdings = parsed.get("holdings")
            if not isinstance(holdings, list):
                raise ValueError("Portfolio JSON object must contain a 'holdings' list.")
            return {
                "source": "portfolio_json",
                "holdings": [dict(x) for x in holdings],
                "accounts": list(parsed.get("accounts") or []),
                "candidates": list(parsed.get("candidates") or []),
                "as_of": parsed.get("as_of"),
            }
        raise ValueError("Invalid portfolio JSON payload.")

    fin_root = (
        Path(args.financial_root).expanduser()
        if getattr(args, "financial_root", None)
        else Path.home() / "Development" / "Financial"
    )
    return _load_from_financial_cli(fin_root)


def _render_markdown_report(payload: Mapping[str, Any]) -> str:
    lines = [
        "# Personal Portfolio Diagnosis & Rebalancing Plan (`portfolio-advisor`)",
        "",
        f"- **As-Of Date**: `{payload['as_of']}`",
        f"- **Asset Screener Coupling**: `delegated to skills/asset-screener/scripts/asset_evaluator.py`",
        f"- **Research Models Sync**: `{payload['research_models_sync']['status']}`",
        "",
        "## 1. All-Weather Barbell Allocation",
        "",
        "| Sleeve | Current CNY | Actual % | Target % | Drift % |",
        "|---|---:|---:|---:|---:|",
    ]
    for sleeve, info in payload["barbell"]["sleeves"].items():
        lines.append(
            f"| `{sleeve}` | ¥{info['market_value_cny']:,.2f} | {info['actual_weight_pct']:.1f}% | "
            f"{info['target_weight_pct']:.1f}% | {info['drift_pct']:+.1f}% |"
        )
    if "rebalance_plan" in payload:
        plan = payload["rebalance_plan"]
        lines.extend(
            [
                "",
                f"## 2. Holding Actions (`{plan['mode']}` — Freed Cashflow: ¥{plan['freed_monthly_cashflow_cny']:,.0f}/mo)",
                "",
                "| Code | Name | Score / Tier | Unlocked Shares | Current DCA | New DCA | Action |",
                "|---|---|---|---:|---:|---:|---|",
            ]
        )
        for act in plan["holding_actions"]:
            lines.append(
                f"| `{act['code']}` | {act['name']} | {act['objective_score']:.1f} (`{act['objective_tier']}`) | "
                f"{act['zero_fee_unlocked_shares']:.2f} | ¥{act['current_monthly_dca_cny']:,.0f} | "
                f"¥{act['new_monthly_dca_cny']:,.0f} | **`{act['action']}`** |"
            )
        if plan["new_dca_recommendations"]:
            lines.extend(
                [
                    "",
                    "## 3. New Defensive / Core DCA Allocations (`ADD_NEW_DCA`)",
                    "",
                    "| Code | Name | Sleeve | Score / Tier | Monthly DCA | Role |",
                    "|---|---|---|---|---:|---|",
                ]
            )
            for rec in plan["new_dca_recommendations"]:
                lines.append(
                    f"| `{rec['code']}` | {rec['name']} | `{rec['sleeve']}` | "
                    f"{rec['objective_score']:.1f} (`{rec['objective_tier']}`) | "
                    f"**¥{rec['recommended_monthly_dca_cny']:,.0f}** | {rec['role']} |"
                )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portfolio_rebalancer.py",
        description="Personal portfolio diagnosis, FIFO fee-window calculator, overlap detector, and Barbell DCA rebalancer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for cmd_name, help_text in (
        ("diagnose", "Diagnose Barbell drift, objective scores, FIFO windows, and holding overlaps."),
        ("rebalance", "Generate per-holding DCA/FIFO rebalancing actions and 6-month Barbell projection."),
        ("fifo-window", "Compute FIFO 7/30/180/365-day redemption fee windows and unlock dates."),
        ("overlap", "Detect A/C share duplicates, top-10 stock collisions, and broad-beta redundancy."),
    ):
        p = sub.add_parser(cmd_name, help=help_text)
        src = p.add_mutually_exclusive_group(required=False)
        src.add_argument("--portfolio-json", default=None, help="Inline JSON or path to portfolio JSON fixture.")
        src.add_argument("--financial-root", default=None, help="Path to local Financial repo checkout.")
        p.add_argument("--as-of", default=None, help="Reference date YYYY-MM-DD (default: portfolio as_of or today).")
        p.add_argument("--research-models-path", default=None, help="Optional path to research-models.md.")
        p.add_argument("--format", choices=("json", "markdown"), default="json")
        if cmd_name == "rebalance":
            p.add_argument(
                "--mode",
                choices=("hold_underwater_stop_dca", "redeem_unlocked_fifo"),
                default="hold_underwater_stop_dca",
            )
            p.add_argument("--monthly-budget-cny", type=float, default=None, help="Optional total monthly DCA budget.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        evaluator = _load_asset_screener_module()
        portfolio = _load_portfolio_input(args)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": "portfolio_load_failed", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    raw_as_of = args.as_of or portfolio.get("as_of")
    as_of = _parse_day(raw_as_of, date.today())
    holdings = portfolio["holdings"]
    model_sync = evaluator.discover_research_models(args.research_models_path)

    scores_by_code: dict[str, dict[str, Any]] = {}
    fifo_by_code: dict[str, dict[str, Any]] = {}
    for h in holdings:
        code = str(h.get("fund_code") or h.get("code") or "UNKNOWN")
        clean_asset = _strip_for_asset_screener(h, evaluator)
        score_card = evaluator.evaluate_asset(clean_asset, strict_no_personal_ledger=True)
        scores_by_code[code] = score_card
        fifo_by_code[code] = compute_fifo_window(h, as_of, score_card["category"])

    if args.command == "fifo-window":
        out = {
            "ok": True,
            "command": "fifo-window",
            "as_of": as_of.isoformat(),
            "windows": list(fifo_by_code.values()),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    overlaps = detect_portfolio_overlaps(holdings, scores_by_code)
    if args.command == "overlap":
        out = {
            "ok": True,
            "command": "overlap",
            "as_of": as_of.isoformat(),
            "overlap_count": len(overlaps),
            "overlaps": overlaps,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    barbell = compute_barbell_diagnosis(holdings, scores_by_code)
    payload: dict[str, Any] = {
        "ok": True,
        "command": args.command,
        "as_of": as_of.isoformat(),
        "source": portfolio["source"],
        "research_models_sync": model_sync,
        "asset_screener_delegated": True,
        "barbell": barbell,
        "fifo_windows": list(fifo_by_code.values()),
        "overlaps": overlaps,
        "asset_scores": list(scores_by_code.values()),
    }

    if args.command == "rebalance":
        custom_candidates = portfolio.get("candidates") or DEFAULT_DEFENSIVE_CANDIDATES
        payload["rebalance_plan"] = generate_rebalance_plan(
            holdings,
            scores_by_code,
            fifo_by_code,
            overlaps,
            barbell,
            mode=args.mode,
            monthly_budget_override=args.monthly_budget_cny,
            candidate_defs=custom_candidates,
            evaluator_module=evaluator,
        )

    if args.format == "markdown":
        print(_render_markdown_report(payload), end="")
        return 0

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
