#!/usr/bin/env python3
"""Pure objective investment asset evaluator & screener (zero personal-ledger coupling).

Architecture:
- 1 Universal Base Layer (40 pts): 10Y risk-free opportunity cost, fee drag & C-share penalty,
  short-term crowding/overbought-oversold pulse (RSI_14, KDJ_J, BB_pct_b, 52w_percentile, turnover).
- 4 Category Plugins (60 pts):
  1) dividend_value
  2) broad_beta
  3) tech_cycle_growth (with Style Drift & Benchmark Lag detection)
  4) macro_hedge_gold_bond (gold & pure_bond sub-modes)
- Multi-Source Consensus & Divergence Engine:
  Morningstar MRAR, World Gold Council (WGC), Graham ERP / FED, AkShare f-fund-screener, Fuli Time.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

PERSONAL_LEDGER_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "holding_shares",
        "remaining_shares",
        "holding_cost",
        "market_value",
        "unrealized_pnl",
        "unrealized_pnl_pct",
        "first_buy_date",
        "last_trade_date",
        "account_balance",
        "current_balance",
        "dca_monthly_cny",
        "lots",
        "share_timeline",
    }
)

LOOKAHEAD_FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "forward_30d_return_pct",
        "forward_60d_return_pct",
        "forward_90d_return_pct",
        "forward_to_date_return_pct",
        "forward_30d_calendar_return_pct",
        "forward_60d_calendar_return_pct",
        "forward_21d_trading_return_pct",
        "forward_42d_trading_return_pct",
        "forward_to_t5_total_return_pct",
        "forward_60d_max_drawdown_pct",
        "forward_to_t5_max_drawdown_pct",
        "forward_max_dip_from_entry_pct",
        "forward_max_runup_from_entry_pct",
        "future_return",
        "future_return_pct",
        "ground_truth",
        "ground_truth_forward",
        "subsequent_drawdown_pct",
        "max_subsequent_drawdown_pct",
        "is_market_top",
        "realized_forward_pnl_cny",
        "latest_unit_nav",
        "latest_accum_nav",
    }
)

OBJECTIVE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("asset_id", "fund_code", "ticker", "symbol"),
    "name": ("asset_name", "fund_name", "title"),
    "category": ("asset_category", "plugin_category"),
    "sub_type": ("hedge_sub_type", "macro_sub_type"),
    "rf_10y_pct": ("cn_10y_yield_pct", "sovereign_10y_yield_pct", "risk_free_10y_pct"),
    "pe_ttm": ("pe", "index_pe_ttm"),
    "dividend_yield_pct": ("dy_pct", "trailing_dividend_yield_pct"),
    "ytm_pct": ("bond_ytm_pct", "yield_to_maturity_pct"),
    "mgmt_fee_pct": ("management_fee_pct",),
    "custody_fee_pct": ("custodian_fee_pct",),
    "sales_service_fee_pct": ("service_fee_pct", "c_share_service_fee_pct"),
    "rsi_14": ("rsi14", "rsi"),
    "kdj_j": ("kdj_j_val",),
    "bb_pct_b": ("bollinger_pct_b", "pct_b"),
    "percentile_52w": ("price_percentile_52w", "nav_percentile_52w", "pct_52w"),
    "turnover_crowding_pct": ("crowding_percentile", "volume_crowding_pct", "crowding_pct"),
    "pe_percentile_10y": ("pe_percentile_5y", "pe_percentile", "valuation_percentile"),
    "pb_percentile_10y": ("pb_percentile_5y", "pb_percentile"),
    "tracking_error_pct": ("annualized_tracking_error_pct", "tracking_error"),
    "info_ratio": ("information_ratio",),
    "peg": ("peg_ratio",),
    "earnings_growth_pct": ("revenue_growth_pct", "eps_growth_pct"),
    "max_drawdown_pct": ("trailing_mdd_pct", "historical_mdd_pct", "mdd_1y_pct"),
    "calmar_ratio": ("calmar",),
    "top10_weight_pct": ("cr10_pct", "top10_concentration_pct"),
    "single_subsector_pct": ("dominant_subsector_weight_pct", "subsector_share_pct", "max_subsector_pct"),
    "quarterly_excess_return_pct": ("quarterly_vs_benchmark_pct", "excess_return_qtr_pct", "qtr_alpha_pct"),
    "aum_change_qoq_pct": ("aum_qoq_pct",),
    "tips_10y_real_yield_pct": ("us_10y_tips_real_yield_pct", "real_yield_10y_pct"),
    "central_bank_tonnes_qtr": ("wgc_cb_net_buy_tonnes_qtr", "cb_gold_buying_tonnes_qtr"),
    "drawdown_from_peak_pct": ("pullback_from_ath_pct", "drawdown_from_ath_pct"),
    "duration_years": ("modified_duration_years", "duration"),
    "equity_exposure_pct": ("equity_weight_pct", "convertible_and_equity_pct"),
    "equity_corr_3y": ("corr_with_equity_3y", "equity_correlation_3y"),
    "consecutive_dividend_years": ("dividend_years",),
    "payout_coverage_ratio": ("dividend_coverage_ratio",),
}

VALID_CATEGORIES: tuple[str, ...] = (
    "dividend_value",
    "broad_beta",
    "tech_cycle_growth",
    "macro_hedge_gold_bond",
)

TIER_ORDER: dict[str, int] = {
    "A_CORE": 4,
    "B_SATELLITE": 3,
    "C_WATCH": 2,
    "D_AVOID": 1,
}

MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "morningstar_mrar": {
        "name": "Morningstar Fee Quintile & MRAR",
        "authority": "Morningstar",
        "focus": "Total annual fee drag, C-share long-hold cost, and downside risk-adjusted return (MRAR).",
        "applies_to": list(VALID_CATEGORIES),
    },
    "wgc_macro_gold": {
        "name": "World Gold Council Real-Yield & Central-Bank Demand",
        "authority": "World Gold Council (WGC)",
        "focus": "10Y TIPS real interest rate anchor, global central-bank net buying tonnes, and tail-risk hedge.",
        "applies_to": ["macro_hedge_gold_bond", "broad_beta"],
    },
    "graham_erp": {
        "name": "Graham Earnings Yield Spread (ERP / FED Model)",
        "authority": "Benjamin Graham / Macro Valuation",
        "focus": "Equity Risk Premium (1/PE - 10Y sovereign bond yield) and dividend-vs-bond spread.",
        "applies_to": ["broad_beta", "dividend_value", "tech_cycle_growth"],
    },
    "akshare_quant": {
        "name": "AkShare f-fund-screener Quantitative Factors",
        "authority": "AkShare Open Quant",
        "focus": "Sharpe/Calmar ratio, max drawdown, tracking error, peer rank, CR10 concentration, style drift.",
        "applies_to": list(VALID_CATEGORIES),
    },
    "fuli_low_vol_dca": {
        "name": "Fuli Time Low-Valuation & Dividend Cushion",
        "authority": "Fuli Time (复利时光)",
        "focus": "Historical valuation percentile, dividend cashflow cushion, and RSI/BB%B anti-chasing pulse.",
        "applies_to": list(VALID_CATEGORIES),
    },
}

DEFAULT_THRESHOLDS: dict[str, float] = {
    "rf_10y_pct": 2.10,
    "dividend_spread_golden_bps": 200.0,
    "erp_high_conviction_bps": 400.0,
    "style_drift_subsector_pct": 75.0,
    "severe_benchmark_lag_pct": -15.0,
}


def PersonalLedgerCouplingError(ValueError):  # pragma: no cover - alias helper avoided below
    pass


class LedgerCouplingViolation(ValueError):
    """Raised when personal ledger fields are passed into pure asset-screener."""


class LookaheadBiasViolation(ValueError):
    """Raised when forward-return / ground-truth fields are passed into blind scorer."""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _num(data: Mapping[str, Any], key: str, default: float) -> float:
    raw = data.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def discover_research_models(explicit_path: str | Path | None = None) -> dict[str, Any]:
    """Probe optional research-models.md produced by learning-and-blog M2 or knowledge-vault."""
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path).expanduser())
    home_dev = Path.home() / "Development"
    candidates.extend(
        [
            home_dev / "handoff" / "investment-advisor-skill" / "research-models.md",
            home_dev / "handoff" / "asset-screener-portfolio-advisor" / "research-models.md",
            home_dev / "Personal" / "knowledge-vault" / "research-models.md",
            home_dev / "Personal" / "knowledge-vault" / "concepts" / "research-models.md",
            home_dev / "Personal" / "research-models.md",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8", errors="replace")
            lower = text.lower()
            matched_sources = [
                model_id
                for model_id, token in (
                    ("morningstar_mrar", "morningstar"),
                    ("wgc_macro_gold", "wgc"),
                    ("graham_erp", "erp"),
                    ("akshare_quant", "akshare"),
                    ("fuli_low_vol_dca", "复利"),
                )
                if token in lower or model_id in lower
            ]
            return {
                "status": "cross_validated_external",
                "path": str(candidate),
                "matched_sources": matched_sources,
                "line_count": len(text.splitlines()),
            }
    return {
        "status": "builtin_canonical_defaults",
        "path": None,
        "matched_sources": list(MODEL_REGISTRY),
        "line_count": 0,
    }


def validate_no_personal_ledger(asset: Mapping[str, Any], *, strict: bool = True) -> list[str]:
    found = sorted(key for key in PERSONAL_LEDGER_FORBIDDEN_KEYS if key in asset)
    if found and strict:
        raise LedgerCouplingViolation(
            f"asset-screener rejects personal ledger field(s): {', '.join(found)}. "
            "Pass objective asset metadata only, or route personal holdings to portfolio-advisor."
        )
    return found


def validate_no_lookahead_bias(asset: Mapping[str, Any]) -> None:
    found = sorted(
        key
        for key in asset
        if key in LOOKAHEAD_FORBIDDEN_KEYS
        or str(key).startswith(("forward_", "future_", "subsequent_"))
    )
    if found:
        raise LookaheadBiasViolation(
            f"lookahead_bias_forbidden: evaluate_asset rejects future/ground-truth field(s): {', '.join(found)}."
        )


def normalize_objective_asset_fields(asset: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(asset)
    metrics_sub = asset.get("metrics") or asset.get("pit_metrics") or asset.get("features")
    if isinstance(metrics_sub, Mapping):
        for k, v in metrics_sub.items():
            if k not in normalized:
                normalized[k] = v
    for canonical, aliases in OBJECTIVE_FIELD_ALIASES.items():
        if canonical not in normalized or normalized[canonical] in (None, ""):
            for alias in aliases:
                if alias in normalized and normalized[alias] not in (None, ""):
                    normalized[canonical] = normalized[alias]
                    break
    return normalized


def infer_category(asset: Mapping[str, Any]) -> str:
    explicit = str(asset.get("category") or "").strip()
    if explicit in VALID_CATEGORIES:
        return explicit
    name = f"{asset.get('code', '')} {asset.get('name', '')}".lower()
    if any(tok in name for tok in ("黄金", "gold", "积存金", "国开债", "纯债", "国债", "政金债", "bond")):
        return "macro_hedge_gold_bond"
    if any(tok in name for tok in ("红利", "高股息", "dividend", "低波红利")):
        return "dividend_value"
    if any(tok in name for tok in ("沪深300", "上证综指", "中证500", "中证a500", "标普500", "s&p", "纳斯达克100", "宽基")):
        return "broad_beta"
    return "tech_cycle_growth"


def _score_universal_base(asset: Mapping[str, Any], category: str, rf_10y_pct: float) -> tuple[float, dict[str, Any], list[str]]:
    flags: list[str] = []
    # 1) Opportunity cost vs 10Y sovereign bond (12 pts)
    pe = _num(asset, "pe_ttm", 0.0)
    dy_pct = _num(asset, "dividend_yield_pct", 0.0)
    ytm_pct = _num(asset, "ytm_pct", 0.0)
    earnings_yield_pct = (100.0 / pe) if pe > 0 else 0.0

    if category == "macro_hedge_gold_bond":
        sub_type = str(asset.get("sub_type") or ("gold" if "金" in str(asset.get("name", "")) or "gold" in str(asset.get("name", "")).lower() else "pure_bond"))
        if sub_type == "gold":
            tips_real = _num(asset, "tips_10y_real_yield_pct", 1.8)
            opp_score = _clamp(12.0 - max(0.0, tips_real - 1.2) * 2.5, 5.5, 12.0)
            carry_spread_bps = round((2.5 - tips_real) * 100.0, 1)
        else:
            spread_pct = ytm_pct - rf_10y_pct
            opp_score = _clamp(8.5 + spread_pct * 4.0, 4.0, 12.0)
            carry_spread_bps = round(spread_pct * 100.0, 1)
    elif category == "dividend_value":
        spread_pct = dy_pct - rf_10y_pct
        opp_score = _clamp(5.0 + spread_pct * 2.8, 1.0, 12.0)
        carry_spread_bps = round(spread_pct * 100.0, 1)
    else:
        anchor_yield = max(earnings_yield_pct, dy_pct)
        spread_pct = anchor_yield - rf_10y_pct if anchor_yield > 0 else 0.5
        opp_score = _clamp(6.0 + spread_pct * 1.4, 2.0, 12.0)
        carry_spread_bps = round(spread_pct * 100.0, 1)

    # 2) Fee structure, Morningstar MRAR drag & A/C breakeven T* = 365 * F_front / c_service (14 pts)
    mgmt_fee = _num(asset, "mgmt_fee_pct", 0.50)
    custody_fee = _num(asset, "custody_fee_pct", 0.10)
    sales_service_fee = _num(asset, "sales_service_fee_pct", 0.0)
    front_load_discount_pct = _num(asset, "front_load_pct", 0.12)
    total_annual_fee = round(mgmt_fee + custody_fee + sales_service_fee, 4)
    share_class = str(asset.get("share_class") or ("C" if str(asset.get("name", "")).endswith("C") or sales_service_fee > 0 else "A")).upper()
    intended_horizon_days = _num(asset, "intended_horizon_days", 365.0)
    ac_breakeven_days = round(365.0 * front_load_discount_pct / sales_service_fee, 1) if sales_service_fee > 0 else None

    if total_annual_fee <= 0.25:
        fee_score = 14.0
    elif total_annual_fee <= 0.60:
        fee_score = 11.5
    elif total_annual_fee <= 1.00:
        fee_score = 8.5
    elif total_annual_fee <= 1.50:
        fee_score = 5.5
    else:
        fee_score = 2.5
        flags.append("HIGH_ANNUAL_FEE_DRAG")

    if share_class == "C" and sales_service_fee >= 0.25 and ac_breakeven_days is not None and intended_horizon_days > max(180.0, ac_breakeven_days):
        fee_score = max(1.5, fee_score - 2.5)
        flags.append("C_CLASS_LONG_HOLD_DRAG")

    # 3) Short-term overbought/oversold & crowding pulse (14 pts)
    rsi_14 = _num(asset, "rsi_14", 50.0)
    kdj_j = _num(asset, "kdj_j", 50.0)
    bb_pct_b = _num(asset, "bb_pct_b", 0.50)
    pct_52w = _num(asset, "percentile_52w", 50.0)
    crowding = _num(asset, "turnover_crowding_pct", 50.0)

    pulse_index = (
        0.30 * rsi_14
        + 0.15 * _clamp(kdj_j, 0.0, 100.0)
        + 0.25 * (_clamp(bb_pct_b, 0.0, 1.2) * 100.0)
        + 0.15 * pct_52w
        + 0.15 * crowding
    )
    if pulse_index <= 35.0:
        pulse_score = 13.5
        pulse_state = "OVERSOLD_UNCROWDED"
    elif pulse_index <= 58.0:
        pulse_score = 11.0
        pulse_state = "NEUTRAL_BALANCED"
    elif pulse_index <= 74.0:
        pulse_score = 7.5
        pulse_state = "WARM_WATCH_CHASE"
    else:
        pulse_score = 3.5
        pulse_state = "OVERBOUGHT_CROWDED"
        flags.append("SHORT_TERM_OVERBOUGHT_CROWDED")

    total_base = round(opp_score + fee_score + pulse_score, 2)
    breakdown = {
        "rf_10y_pct": rf_10y_pct,
        "carry_spread_bps": carry_spread_bps,
        "opportunity_cost_score": round(opp_score, 2),
        "total_annual_fee_pct": total_annual_fee,
        "share_class": share_class,
        "ac_breakeven_days": ac_breakeven_days,
        "fee_score": round(fee_score, 2),
        "pulse_index": round(pulse_index, 2),
        "pulse_state": pulse_state,
        "crowding_pulse_score": round(pulse_score, 2),
    }
    return total_base, breakdown, flags


def _score_dividend_plugin(asset: Mapping[str, Any], rf_10y_pct: float) -> tuple[float, dict[str, Any], list[str]]:
    flags: list[str] = []
    dy_pct = _num(asset, "dividend_yield_pct", 4.0)
    spread_bps = (dy_pct - rf_10y_pct) * 100.0
    if spread_bps >= 250:
        spread_score = 24.0
    elif spread_bps >= 200:
        spread_score = 21.0
    elif spread_bps >= 120:
        spread_score = 14.0
    else:
        spread_score = 6.0
        flags.append("DIVIDEND_SPREAD_THIN")

    total_return_口径 = bool(asset.get("total_return_index", True))
    turnover_pct = _num(asset, "annual_turnover_pct", 35.0)
    tr_score = (8.0 if total_return_口径 else 4.0) + (4.0 if turnover_pct <= 45.0 else 2.0)

    div_years = _num(asset, "consecutive_dividend_years", 5.0)
    payout_cov = _num(asset, "payout_coverage_ratio", 1.6)
    stability_score = _clamp(div_years * 2.2, 0.0, 14.0) + _clamp((payout_cov - 0.8) * 8.0, 0.0, 10.0)
    if payout_cov < 1.1:
        flags.append("DIVIDEND_COVERAGE_FRAGILE")

    score = round(_clamp(spread_score + tr_score + stability_score, 0.0, 60.0), 2)
    return score, {
        "plugin": "dividend_value",
        "dy_minus_rf_bps": round(spread_bps, 1),
        "spread_score": round(spread_score, 2),
        "total_return_score": round(tr_score, 2),
        "stability_score": round(stability_score, 2),
    }, flags


def _score_broad_beta_plugin(asset: Mapping[str, Any], rf_10y_pct: float) -> tuple[float, dict[str, Any], list[str]]:
    flags: list[str] = []
    pe = _num(asset, "pe_ttm", 14.0)
    erp_pct = (100.0 / pe - rf_10y_pct) if pe > 0 else 3.0
    erp_bps = erp_pct * 100.0
    if erp_bps >= 450:
        erp_score = 24.0
    elif erp_bps >= 300:
        erp_score = 19.5
    elif erp_bps >= 150:
        erp_score = 13.5
    else:
        erp_score = 7.0
        flags.append("LOW_ERP_VALUATION_RICH")

    pe_pct = _num(asset, "pe_percentile_10y", 45.0)
    pb_pct = _num(asset, "pb_percentile_10y", pe_pct)
    blended_pct = 0.6 * pe_pct + 0.4 * pb_pct
    val_score = _clamp(18.0 - blended_pct * 0.16, 2.0, 18.0)
    if blended_pct >= 80.0:
        flags.append("HISTORICAL_VALUATION_HIGH")

    tracking_err = _num(asset, "tracking_error_pct", 0.35)
    info_ratio = _num(asset, "info_ratio", 0.25)
    is_enhanced = bool(asset.get("enhanced_index", False)) or "增强" in str(asset.get("name", ""))
    if is_enhanced:
        track_score = _clamp(10.0 + info_ratio * 6.0 - max(0.0, tracking_err - 2.5) * 3.0, 3.0, 18.0)
    else:
        track_score = _clamp(18.0 - tracking_err * 8.0, 4.0, 18.0)

    score = round(_clamp(erp_score + val_score + track_score, 0.0, 60.0), 2)
    return score, {
        "plugin": "broad_beta",
        "graham_erp_bps": round(erp_bps, 1),
        "erp_score": round(erp_score, 2),
        "valuation_percentile_blended": round(blended_pct, 1),
        "valuation_score": round(val_score, 2),
        "tracking_score": round(track_score, 2),
    }, flags


def _score_tech_growth_plugin(asset: Mapping[str, Any]) -> tuple[float, dict[str, Any], list[str]]:
    flags: list[str] = []
    peg = _num(asset, "peg", 1.35)
    rev_growth = _num(asset, "earnings_growth_pct", 18.0)
    if 0 < peg <= 1.2:
        peg_score = 16.0
    elif peg <= 1.8:
        peg_score = 12.0
    elif peg <= 2.4:
        peg_score = 7.5
    else:
        peg_score = 3.5
        flags.append("GROWTH_PEG_STRETCHED")
    if rev_growth < 0:
        peg_score = max(1.0, peg_score - 4.0)

    mdd_pct = abs(_num(asset, "max_drawdown_pct", 28.0))
    calmar = _num(asset, "calmar_ratio", 0.55)
    risk_score = _clamp(14.0 - max(0.0, mdd_pct - 20.0) * 0.35 + calmar * 6.0, 2.0, 20.0)
    if mdd_pct >= 40.0:
        flags.append("EXTREME_DRAWDOWN_RISK")

    cr10_pct = _num(asset, "top10_weight_pct", 52.0)
    single_subsector_pct = _num(asset, "single_subsector_pct", 45.0)
    qtr_excess_pct = _num(asset, "quarterly_excess_return_pct", 0.0)
    aum_change_qoq_pct = _num(asset, "aum_change_qoq_pct", 0.0)
    is_passive_sector_index = bool(asset.get("is_passive_index", False)) or any(
        tok in str(asset.get("name", "")) for tok in ("指数", "ETF")
    )

    drift_score = 24.0
    if not is_passive_sector_index and cr10_pct >= 75.0 and single_subsector_pct >= 75.0:
        drift_score -= 13.0
        flags.append("STYLE_DRIFT_SUBSECTOR_BET")
    elif cr10_pct >= 80.0 and not is_passive_sector_index:
        drift_score -= 6.0
        flags.append("HIGH_TOP10_CONCENTRATION")

    if qtr_excess_pct <= -15.0:
        drift_score -= 11.0
        flags.append("BENCHMARK_LAG_SEVERE")
    elif qtr_excess_pct <= -8.0:
        drift_score -= 5.0
        flags.append("BENCHMARK_LAG_MODERATE")

    if aum_change_qoq_pct <= -35.0:
        drift_score -= 4.0
        flags.append("AUM_SHRINKAGE_ALERT")

    drift_score = _clamp(drift_score, 0.0, 24.0)
    score = round(_clamp(peg_score + risk_score + drift_score, 0.0, 60.0), 2)
    return score, {
        "plugin": "tech_cycle_growth",
        "peg": peg,
        "peg_score": round(peg_score, 2),
        "max_drawdown_pct": mdd_pct,
        "risk_score": round(risk_score, 2),
        "top10_weight_pct": cr10_pct,
        "single_subsector_pct": single_subsector_pct,
        "quarterly_excess_return_pct": qtr_excess_pct,
        "drift_governance_score": round(drift_score, 2),
    }, flags


def _score_macro_hedge_plugin(asset: Mapping[str, Any], rf_10y_pct: float) -> tuple[float, dict[str, Any], list[str]]:
    flags: list[str] = []
    name_lower = str(asset.get("name", "")).lower()
    sub_type = str(
        asset.get("sub_type")
        or ("gold" if ("金" in name_lower or "gold" in name_lower) else "pure_bond")
    )
    if sub_type == "gold":
        tips_real = _num(asset, "tips_10y_real_yield_pct", 1.75)
        cb_tonnes = _num(asset, "central_bank_tonnes_qtr", 300.0)
        pullback_pct = abs(_num(asset, "drawdown_from_peak_pct", 22.0))
        total_fee = _num(asset, "mgmt_fee_pct", 0.5) + _num(asset, "custody_fee_pct", 0.1) + _num(asset, "sales_service_fee_pct", 0.0)

        macro_score = _clamp(14.0 - max(0.0, tips_real - 1.5) * 3.0, 6.0, 14.0) + _clamp(cb_tonnes / 25.0, 4.0, 12.0)
        if 15.0 <= pullback_pct <= 38.0:
            entry_score = 18.0
        elif pullback_pct < 8.0:
            entry_score = 11.0
        else:
            entry_score = 14.0
        channel_score = 16.0 if total_fee <= 0.05 else (13.5 if total_fee <= 0.65 else 7.0)
        score = round(_clamp(macro_score + entry_score + channel_score, 0.0, 60.0), 2)
        return score, {
            "plugin": "macro_hedge_gold_bond",
            "sub_type": "gold",
            "tips_10y_real_yield_pct": tips_real,
            "central_bank_tonnes_qtr": cb_tonnes,
            "drawdown_from_peak_pct": pullback_pct,
            "macro_score": round(macro_score, 2),
            "entry_and_channel_score": round(entry_score + channel_score, 2),
        }, flags

    duration = _num(asset, "duration_years", 6.5)
    ytm_pct = _num(asset, "ytm_pct", rf_10y_pct + 0.15)
    equity_exp = _num(asset, "equity_exposure_pct", 0.0)
    eq_corr = _num(asset, "equity_corr_3y", -0.22)
    credit_tier = str(asset.get("credit_tier") or "SOVEREIGN_POLICY").upper()

    carry_score = _clamp(18.0 + (ytm_pct - rf_10y_pct) * 12.0, 10.0, 24.0)
    duration_fit = 6.0 if 1.0 <= duration <= 9.5 else 3.5
    purity_score = 16.0 if equity_exp == 0.0 else max(2.0, 16.0 - equity_exp * 0.8)
    if equity_exp > 0.0:
        flags.append("NON_PURE_BOND_EQUITY_EXPOSURE")
    corr_score = 14.0 if eq_corr <= -0.10 and "SOVEREIGN" in credit_tier else 9.5

    score = round(_clamp(carry_score + duration_fit + purity_score + corr_score, 0.0, 60.0), 2)
    return score, {
        "plugin": "macro_hedge_gold_bond",
        "sub_type": "pure_bond",
        "duration_years": duration,
        "ytm_pct": ytm_pct,
        "equity_corr_3y": eq_corr,
        "carry_and_duration_score": round(carry_score + duration_fit, 2),
        "purity_and_hedge_score": round(purity_score + corr_score, 2),
    }, flags


def _build_multi_source_views(
    asset: Mapping[str, Any],
    category: str,
    base_info: Mapping[str, Any],
    plugin_info: Mapping[str, Any],
    flags: Sequence[str],
) -> tuple[dict[str, dict[str, str]], list[str], list[str]]:
    views: dict[str, dict[str, str]] = {}
    fee = float(base_info["total_annual_fee_pct"])
    pulse_state = str(base_info["pulse_state"])

    # 1. Morningstar MRAR
    if fee <= 0.65 and "BENCHMARK_LAG_SEVERE" not in flags and "C_CLASS_LONG_HOLD_DRAG" not in flags:
        views["morningstar_mrar"] = {
            "stance": "BULLISH",
            "rationale": f"Low annual fee drag ({fee:.2f}%) and clean share-class structure preserve long-horizon MRAR.",
        }
    elif fee > 1.20 or "C_CLASS_LONG_HOLD_DRAG" in flags or "BENCHMARK_LAG_SEVERE" in flags:
        views["morningstar_mrar"] = {
            "stance": "BEARISH",
            "rationale": f"Fee drag ({fee:.2f}%/yr) or severe active benchmark lag erodes risk-adjusted return.",
        }
    else:
        views["morningstar_mrar"] = {
            "stance": "NEUTRAL",
            "rationale": f"Moderate fee burden ({fee:.2f}%/yr); suitable mainly for tactical horizon.",
        }

    # 2. World Gold Council (WGC)
    if category == "macro_hedge_gold_bond" and plugin_info.get("sub_type") == "gold":
        cb_t = plugin_info.get("central_bank_tonnes_qtr", 0.0)
        views["wgc_macro_gold"] = {
            "stance": "BULLISH" if cb_t >= 220 else "NEUTRAL",
            "rationale": f"WGC real-rate & central-bank net buying ({cb_t:.0f}t/qtr) support structural tail-hedge allocation.",
        }
    elif category == "macro_hedge_gold_bond":
        views["wgc_macro_gold"] = {
            "stance": "BULLISH",
            "rationale": "Sovereign/policy pure bond complements gold as zero-equity defensive ballast.",
        }
    else:
        views["wgc_macro_gold"] = {
            "stance": "NEUTRAL",
            "rationale": "Risk-on equity sleeve; requires external gold/bond ballast for macro drawdown protection.",
        }

    # 3. Graham ERP
    spread_bps = float(base_info["carry_spread_bps"])
    if category in {"broad_beta", "dividend_value"} and spread_bps >= 200:
        views["graham_erp"] = {
            "stance": "BULLISH",
            "rationale": f"Attractive yield/ERP spread (+{spread_bps:.0f}bps vs 10Y bond) offers strong margin of safety.",
        }
    elif "LOW_ERP_VALUATION_RICH" in flags or "GROWTH_PEG_STRETCHED" in flags:
        views["graham_erp"] = {
            "stance": "BEARISH",
            "rationale": "Compressed equity risk premium / stretched valuation leaves thin safety margin vs bonds.",
        }
    else:
        views["graham_erp"] = {
            "stance": "NEUTRAL",
            "rationale": f"Carry/earnings spread ({spread_bps:+.0f}bps vs 10Y bond) is near fair equilibrium.",
        }

    # 4. AkShare f-fund-screener quant
    if "STYLE_DRIFT_SUBSECTOR_BET" in flags or "BENCHMARK_LAG_SEVERE" in flags or "EXTREME_DRAWDOWN_RISK" in flags:
        views["akshare_quant"] = {
            "stance": "BEARISH",
            "rationale": f"Quant screen flags structural risk ({', '.join(f for f in flags if 'DRIFT' in f or 'LAG' in f or 'DRAWDOWN' in f)}).",
        }
    elif float(plugin_info.get("tracking_score", 12.0)) >= 14.0 or category in {"dividend_value", "macro_hedge_gold_bond"}:
        views["akshare_quant"] = {
            "stance": "BULLISH",
            "rationale": "Clean factor discipline, controlled tracking error, and resilient risk-adjusted profile.",
        }
    else:
        views["akshare_quant"] = {
            "stance": "NEUTRAL",
            "rationale": "Mid-pack quantitative Sharpe/drawdown profile; monitor quarterly top-10 concentration.",
        }

    # 5. Fuli Time low-vol / pulse DCA
    if pulse_state == "OVERSOLD_UNCROWDED" and "LOW_ERP_VALUATION_RICH" not in flags:
        views["fuli_low_vol_dca"] = {
            "stance": "BULLISH",
            "rationale": "Oversold technical pulse / pullback zone favors staged accumulation rather than panic selling.",
        }
    elif pulse_state == "OVERBOUGHT_CROWDED":
        views["fuli_low_vol_dca"] = {
            "stance": "BEARISH",
            "rationale": "Short-term RSI/BB%B crowding is overheated; pause lump-sum chasing.",
        }
    else:
        views["fuli_low_vol_dca"] = {
            "stance": "BULLISH" if category in {"dividend_value", "broad_beta"} and spread_bps >= 200 else "NEUTRAL",
            "rationale": "Balanced pulse; pace regular accumulation according to valuation spread.",
        }

    bullish = [m for m, v in views.items() if v["stance"] == "BULLISH"]
    bearish = [m for m, v in views.items() if v["stance"] == "BEARISH"]
    consensus: list[str] = []
    divergence: list[str] = []

    if len(bullish) >= 4:
        consensus.append(f"Strong multi-source bullish consensus ({len(bullish)}/5 models: {', '.join(bullish)}).")
    elif len(bearish) >= 3:
        consensus.append(f"Multi-source bearish caution ({len(bearish)}/5 models: {', '.join(bearish)}).")
    elif len(bullish) >= 3 and not bearish:
        consensus.append(f"Constructive alignment across {', '.join(bullish)} with zero bearish dissent.")

    if bullish and bearish:
        divergence.append(
            f"Horizon/factor divergence: constructive in [{', '.join(bullish)}] vs bearish in [{', '.join(bearish)}]."
        )
    if pulse_state == "OVERSOLD_UNCROWDED" and ("STYLE_DRIFT_SUBSECTOR_BET" in flags or "BENCHMARK_LAG_SEVERE" in flags):
        divergence.append(
            "Oversold bounce trap warning: Fuli short-term pulse is oversold, but AkShare/Morningstar flag structural style drift or severe benchmark lag."
        )
    if "C_CLASS_LONG_HOLD_DRAG" in flags and category in {"broad_beta", "dividend_value"}:
        divergence.append(
            "Index beta is sound (Graham/Fuli), but Morningstar flags C-share sales-service fee drag for >1Y core holding."
        )

    if not consensus and not divergence:
        consensus.append("Neutral equilibrium across institutional, macro, and quantitative lenses.")

    return views, consensus, divergence


def evaluate_asset(
    asset: Mapping[str, Any],
    *,
    strict_no_personal_ledger: bool = True,
    rf_10y_pct: float | None = None,
) -> dict[str, Any]:
    validate_no_lookahead_bias(asset)
    metrics_nested = asset.get("metrics") or asset.get("pit_metrics") or asset.get("features")
    if isinstance(metrics_nested, Mapping):
        validate_no_lookahead_bias(metrics_nested)
    ignored_personal = validate_no_personal_ledger(asset, strict=strict_no_personal_ledger)
    if isinstance(metrics_nested, Mapping):
        ignored_personal = sorted(
            set(ignored_personal) | set(validate_no_personal_ledger(metrics_nested, strict=strict_no_personal_ledger))
        )
    norm_asset = normalize_objective_asset_fields(asset)
    effective_rf = (
        float(rf_10y_pct)
        if rf_10y_pct is not None
        else _num(norm_asset, "rf_10y_pct", DEFAULT_THRESHOLDS["rf_10y_pct"])
    )
    category = infer_category(norm_asset)
    base_score, base_breakdown, base_flags = _score_universal_base(norm_asset, category, effective_rf)

    if category == "dividend_value":
        plugin_score, plugin_breakdown, plugin_flags = _score_dividend_plugin(norm_asset, effective_rf)
    elif category == "broad_beta":
        plugin_score, plugin_breakdown, plugin_flags = _score_broad_beta_plugin(norm_asset, effective_rf)
    elif category == "macro_hedge_gold_bond":
        plugin_score, plugin_breakdown, plugin_flags = _score_macro_hedge_plugin(norm_asset, effective_rf)
    else:
        plugin_score, plugin_breakdown, plugin_flags = _score_tech_growth_plugin(norm_asset)

    flags = list(dict.fromkeys([*base_flags, *plugin_flags]))
    total_score = round(_clamp(base_score + plugin_score, 0.0, 100.0), 2)

    severe_governance = "STYLE_DRIFT_SUBSECTOR_BET" in flags and "BENCHMARK_LAG_SEVERE" in flags
    if severe_governance or total_score < 50.0 or ("BENCHMARK_LAG_SEVERE" in flags and total_score < 58.0):
        tier = "D_AVOID"
    elif total_score >= 80.0 and "STYLE_DRIFT_SUBSECTOR_BET" not in flags:
        tier = "A_CORE"
    elif total_score >= 65.0:
        tier = "B_SATELLITE"
    else:
        tier = "C_WATCH"

    model_views, consensus, divergence = _build_multi_source_views(
        norm_asset, category, base_breakdown, plugin_breakdown, flags
    )

    return {
        "code": str(norm_asset.get("code") or norm_asset.get("blind_id") or "UNKNOWN"),
        "name": str(norm_asset.get("name") or norm_asset.get("code") or norm_asset.get("blind_id") or "Unnamed Asset"),
        "category": category,
        "total_score": total_score,
        "base_score": base_score,
        "plugin_score": plugin_score,
        "tier": tier,
        "flags": flags,
        "style_drift_flags": [f for f in flags if "DRIFT" in f or "LAG" in f or "CONCENTRATION" in f],
        "universal_base": base_breakdown,
        "category_plugin": plugin_breakdown,
        "multi_source_views": model_views,
        "consensus": consensus,
        "divergence": divergence,
        "orthogonality_guard": {
            "personal_ledger_coupled": False,
            "lookahead_bias_free": True,
            "strict_enforced": strict_no_personal_ledger,
            "ignored_personal_keys": ignored_personal,
        },
    }


def _load_json_payload(raw_arg: str | Path) -> Any:
    candidate = Path(raw_arg).expanduser() if isinstance(raw_arg, Path) else Path(str(raw_arg)).expanduser()
    text = candidate.read_text(encoding="utf-8") if candidate.is_file() else str(raw_arg)
    return json.loads(text)


def _load_assets_payload(raw_arg: str) -> list[dict[str, Any]]:
    parsed = _load_json_payload(raw_arg)
    if isinstance(parsed, dict):
        if isinstance(parsed.get("assets"), list):
            return [dict(item) for item in parsed["assets"]]
        return [dict(parsed)]
    if isinstance(parsed, list):
        return [dict(item) for item in parsed]
    raise ValueError("Input JSON must be an asset object, {'assets': [...]}, or a list of asset objects.")


def _opt_float(data: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            try:
                val = float(data[key])
                if not math.isnan(val):
                    return val
            except (TypeError, ValueError):
                continue
    return None


def _average_ranks(values: Sequence[float]) -> list[float]:
    n = len(values)
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def spearman_rank_ic(scores: Sequence[float], forward_returns: Sequence[float]) -> float | None:
    if len(scores) != len(forward_returns) or len(scores) < 2:
        return None
    rx = _average_ranks(scores)
    ry = _average_ranks(forward_returns)
    n = len(rx)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(rx, ry))
    var_x = sum((x - mean_x) ** 2 for x in rx)
    var_y = sum((y - mean_y) ** 2 for y in ry)
    denom = math.sqrt(var_x * var_y)
    if denom <= 1e-12:
        return None
    return round(cov / denom, 4)


def _mean_or_none(vals: Sequence[float]) -> float | None:
    return round(sum(vals) / len(vals), 4) if vals else None


def _strip_accidental_lookahead(item: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    stripped: list[str] = []
    clean: dict[str, Any] = {}
    for k, v in item.items():
        if k in LOOKAHEAD_FORBIDDEN_KEYS or str(k).startswith(("forward_", "future_", "subsequent_")):
            stripped.append(str(k))
            continue
        if k in PERSONAL_LEDGER_FORBIDDEN_KEYS:
            stripped.append(str(k))
            continue
        if isinstance(v, Mapping) and k in {"metrics", "pit_metrics", "features"}:
            sub_clean, sub_stripped = _strip_accidental_lookahead(v)
            clean[k] = sub_clean
            stripped.extend(sub_stripped)
        else:
            clean[k] = v
    return clean, sorted(set(stripped))


def _parse_snapshots_payload(parsed: Any) -> list[dict[str, Any]]:
    raw_snapshots: list[Mapping[str, Any]] = []
    if isinstance(parsed, Mapping):
        for list_key in ("snapshots", "cross_sections", "periods"):
            if isinstance(parsed.get(list_key), list):
                raw_snapshots = [s for s in parsed[list_key] if isinstance(s, Mapping)]
                break
        if not raw_snapshots and isinstance(parsed.get("items") or parsed.get("assets"), list):
            flat_items = parsed.get("items") or parsed.get("assets")
            parsed = flat_items
        elif not raw_snapshots and all(isinstance(v, list) for v in parsed.values()) and parsed:
            for snap_id, items in parsed.items():
                raw_snapshots.append({"snapshot_id": str(snap_id), "assets": items})

    if not raw_snapshots and isinstance(parsed, list):
        if parsed and isinstance(parsed[0], Mapping) and any(
            isinstance(parsed[0].get(k), list) for k in ("assets", "items", "candidates", "entries")
        ):
            raw_snapshots = [s for s in parsed if isinstance(s, Mapping)]
        else:
            grouped: dict[str, dict[str, Any]] = {}
            for item in parsed:
                if not isinstance(item, Mapping):
                    continue
                sid = str(item.get("snapshot_id") or item.get("as_of") or item.get("date") or "S1")
                sdate = str(item.get("as_of") or item.get("date") or item.get("snapshot_date") or "")
                bucket = grouped.setdefault(sid, {"snapshot_id": sid, "as_of": sdate, "assets": []})
                bucket["assets"].append(item)
            raw_snapshots = list(grouped.values())

    normalized_snapshots: list[dict[str, Any]] = []
    for idx, snap in enumerate(raw_snapshots, start=1):
        sid = str(snap.get("snapshot_id") or snap.get("id") or snap.get("period_id") or snap.get("as_of") or snap.get("date") or f"S{idx}")
        as_of = str(snap.get("as_of") or snap.get("date") or snap.get("snapshot_date") or "")
        label = str(snap.get("label") or snap.get("regime") or snap.get("title") or sid)
        snap_rf = _opt_float(snap, "rf_10y_pct", "cn_10y_yield_pct", "sovereign_10y_yield_pct", "risk_free_10y_pct")
        snap_macro = snap.get("macro_context") if isinstance(snap.get("macro_context"), Mapping) else {}
        if snap_rf is None and snap_macro:
            snap_rf = _opt_float(snap_macro, "rf_10y_pct", "cn_10y_yield_pct", "sovereign_10y_yield_pct")
        raw_assets = (
            snap.get("assets")
            or snap.get("items")
            or snap.get("candidates")
            or snap.get("entries")
            or []
        )
        assets_list: list[dict[str, Any]] = []
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, Mapping):
                continue
            merged_asset = dict(raw_asset)
            if snap_macro:
                for mk, mv in snap_macro.items():
                    if mk not in merged_asset:
                        merged_asset[mk] = mv
            if snap_rf is not None and "rf_10y_pct" not in merged_asset:
                merged_asset["rf_10y_pct"] = snap_rf
            assets_list.append(merged_asset)
        normalized_snapshots.append(
            {
                "snapshot_id": sid,
                "as_of": as_of,
                "label": label,
                "rf_10y_pct": snap_rf,
                "assets": assets_list,
            }
        )
    if not normalized_snapshots:
        raise ValueError("No valid snapshots found in --snapshots payload.")
    return normalized_snapshots


def _parse_ground_truth_index(parsed: Any) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    records: list[Mapping[str, Any]] = []
    meta: dict[str, Any] = {}
    if isinstance(parsed, Mapping):
        for k in ("metadata", "summary", "counterfactual_assumptions", "config"):
            if isinstance(parsed.get(k), Mapping):
                meta[k] = dict(parsed[k])
        if isinstance(parsed.get("snapshots") or parsed.get("cross_sections"), list):
            for snap in (parsed.get("snapshots") or parsed.get("cross_sections")):
                if not isinstance(snap, Mapping):
                    continue
                sid = str(snap.get("snapshot_id") or snap.get("id") or snap.get("as_of") or snap.get("date") or "")
                sdate = str(snap.get("as_of") or snap.get("cutoff_date") or snap.get("date") or snap.get("snapshot_date") or "")
                sregime = str(snap.get("regime_description") or snap.get("label") or snap.get("regime") or "")
                stimepoint = str(snap.get("internal_timepoint_id") or "")
                entries = (
                    snap.get("returns")
                    or snap.get("ground_truth")
                    or snap.get("assets")
                    or snap.get("items")
                    or snap.get("entries")
                    or []
                )
                if isinstance(entries, Mapping):
                    for key_id, rec in entries.items():
                        if isinstance(rec, Mapping):
                            row = dict(rec)
                            if isinstance(row.get("ground_truth_forward"), Mapping):
                                for gk, gv in row["ground_truth_forward"].items():
                                    row.setdefault(gk, gv)
                            row.setdefault("snapshot_id", sid)
                            row.setdefault("as_of", sdate)
                            if sregime:
                                row.setdefault("regime_description", sregime)
                            if stimepoint:
                                row.setdefault("internal_timepoint_id", stimepoint)
                            row.setdefault("code", str(key_id))
                            records.append(row)
                elif isinstance(entries, list):
                    for rec in entries:
                        if isinstance(rec, Mapping):
                            row = dict(rec)
                            if isinstance(row.get("ground_truth_forward"), Mapping):
                                for gk, gv in row["ground_truth_forward"].items():
                                    row.setdefault(gk, gv)
                            row.setdefault("snapshot_id", sid)
                            row.setdefault("as_of", sdate)
                            if sregime:
                                row.setdefault("regime_description", sregime)
                            if stimepoint:
                                row.setdefault("internal_timepoint_id", stimepoint)
                            records.append(row)
        elif isinstance(parsed.get("records") or parsed.get("items") or parsed.get("ground_truth") or parsed.get("entries"), list):
            raw_list = parsed.get("records") or parsed.get("items") or parsed.get("ground_truth") or parsed.get("entries")
            records = [r for r in raw_list if isinstance(r, Mapping)]
        elif isinstance(parsed.get("mapping") or parsed.get("by_blind_id"), Mapping):
            mp = parsed.get("mapping") or parsed.get("by_blind_id")
            for blind_id, rec in mp.items():
                if isinstance(rec, Mapping):
                    row = dict(rec)
                    row.setdefault("blind_id", str(blind_id))
                    records.append(row)
        else:
            for key_id, rec in parsed.items():
                if isinstance(rec, Mapping) and any(
                    rk in rec for rk in ("forward_30d_return_pct", "forward_60d_return_pct", "code", "asset_id", "blind_id")
                ):
                    row = dict(rec)
                    row.setdefault("blind_id", str(key_id))
                    records.append(row)
    elif isinstance(parsed, list):
        records = [r for r in parsed if isinstance(r, Mapping)]

    by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    by_blind: dict[str, dict[str, Any]] = {}
    for rec in records:
        row = dict(rec)
        sid = str(row.get("snapshot_id") or row.get("period_id") or "").strip()
        sdate = str(row.get("as_of") or row.get("date") or row.get("snapshot_date") or "").strip()
        blind_id = str(row.get("blind_id") or row.get("anon_id") or row.get("sample_id") or "").strip()
        code = str(row.get("code") or row.get("asset_id") or row.get("fund_code") or row.get("ticker") or "").strip()
        if blind_id:
            by_blind[blind_id] = row
            if sid:
                by_pair[(sid, blind_id)] = row
            if sdate:
                by_pair[(sdate, blind_id)] = row
        if code:
            if sid:
                by_pair[(sid, code)] = row
            if sdate:
                by_pair[(sdate, code)] = row
    if not by_pair and not by_blind:
        raise ValueError("No ground-truth forward return records found in --ground-truth payload.")
    return by_pair, by_blind, meta


def _compute_monotonicity(tier_means: Mapping[str, float | None]) -> dict[str, Any]:
    ordered_tiers = ("A_CORE", "B_SATELLITE", "C_WATCH", "D_AVOID")
    present = [(t, tier_means[t]) for t in ordered_tiers if tier_means.get(t) is not None]
    if len(present) < 2:
        return {
            "present_tiers": [t for t, _ in present],
            "is_strictly_monotonic": None,
            "is_weakly_monotonic": None,
            "pairwise_order_score": None,
        }
    vals = [float(v) for _, v in present if v is not None]
    concordant = 0
    total_pairs = 0
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            total_pairs += 1
            if vals[i] >= vals[j]:
                concordant += 1
    return {
        "present_tiers": [t for t, _ in present],
        "is_strictly_monotonic": all(vals[i] > vals[i + 1] for i in range(len(vals) - 1)),
        "is_weakly_monotonic": all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)),
        "pairwise_order_score": round(concordant / total_pairs, 4) if total_pairs else None,
    }


DEFAULT_COUNTERFACTUAL_PEAK_LOT_CNY: float = 10000.0


def run_blind_backtest(
    snapshots_payload: Any,
    ground_truth_payload: Any,
    *,
    rf_10y_pct: float | None = None,
    fail_on_lookahead_in_snapshots: bool = True,
    default_peak_lot_cny: float = DEFAULT_COUNTERFACTUAL_PEAK_LOT_CNY,
) -> dict[str, Any]:
    """Two-stage Point-in-Time backtest:
    Stage A: Blind scoring with zero ground-truth visibility.
    Stage B: Post-scoring alignment & statistical evaluation.
    """
    snapshots = _parse_snapshots_payload(snapshots_payload)

    # ---------------- STAGE A: PURE BLIND SCORING ----------------
    blind_stage_records: list[dict[str, Any]] = []
    stripped_keys_audit: list[str] = []
    for snap in snapshots:
        sid = snap["snapshot_id"]
        as_of = snap["as_of"]
        snap_results: list[dict[str, Any]] = []
        for raw_asset in snap["assets"]:
            if fail_on_lookahead_in_snapshots:
                validate_no_lookahead_bias(raw_asset)
                nested = raw_asset.get("metrics") or raw_asset.get("pit_metrics") or raw_asset.get("features")
                if isinstance(nested, Mapping):
                    validate_no_lookahead_bias(nested)
                clean_asset = dict(raw_asset)
            else:
                clean_asset, stripped = _strip_accidental_lookahead(raw_asset)
                stripped_keys_audit.extend(stripped)

            scored = evaluate_asset(
                clean_asset,
                strict_no_personal_ledger=True,
                rf_10y_pct=rf_10y_pct,
            )
            blind_id = str(raw_asset.get("blind_id") or raw_asset.get("anon_id") or raw_asset.get("sample_id") or "").strip()
            raw_code = str(
                raw_asset.get("code")
                or raw_asset.get("asset_id")
                or raw_asset.get("fund_code")
                or scored["code"]
            ).strip()
            snap_results.append(
                {
                    "snapshot_id": sid,
                    "as_of": as_of,
                    "snapshot_label": snap["label"],
                    "blind_id": blind_id or None,
                    "blind_code": raw_code,
                    "scored": scored,
                }
            )
        blind_stage_records.append(
            {
                "snapshot_id": sid,
                "as_of": as_of,
                "label": snap["label"],
                "items": snap_results,
            }
        )

    # ---------------- STAGE B: POST-SCORING GROUND-TRUTH ALIGNMENT ----------------
    gt_by_pair, gt_by_blind, gt_meta = _parse_ground_truth_index(ground_truth_payload)
    configured_lot_cny = _opt_float(
        gt_meta.get("counterfactual_assumptions", {}),
        "default_peak_entry_cny",
        "default_buy_amount_cny",
        "notional_lot_cny",
    )
    effective_default_lot_cny = configured_lot_cny if configured_lot_cny is not None else default_peak_lot_cny
    notional_basis = "explicit_ground_truth" if configured_lot_cny is not None else "standard_notional_lot_per_top_entry"

    cross_section_summaries: list[dict[str, Any]] = []
    aligned_rows: list[dict[str, Any]] = []
    unmatched_items: list[dict[str, Any]] = []

    all_ic_30d: list[float] = []
    all_ic_60d: list[float] = []
    all_ic_todate: list[float] = []

    for snap_bundle in blind_stage_records:
        sid = snap_bundle["snapshot_id"]
        as_of = snap_bundle["as_of"]
        snap_rows: list[dict[str, Any]] = []
        resolved_as_of = as_of
        resolved_label = snap_bundle["label"]

        for entry in snap_bundle["items"]:
            b_id = entry["blind_id"]
            b_code = entry["blind_code"]
            gt = None
            if b_id:
                gt = gt_by_pair.get((sid, b_id)) or (gt_by_pair.get((as_of, b_id)) if as_of else None) or gt_by_blind.get(b_id)
            if gt is None and b_code:
                gt = gt_by_pair.get((sid, b_code)) or (gt_by_pair.get((as_of, b_code)) if as_of else None)
            if gt is None:
                unmatched_items.append({"snapshot_id": sid, "blind_id": b_id, "code": b_code})
                continue

            if not resolved_as_of:
                resolved_as_of = str(gt.get("as_of") or gt.get("cutoff_date") or gt.get("pit_actual_nav_date") or "")
            if resolved_label == sid and gt.get("regime_description"):
                resolved_label = str(gt["regime_description"])

            scored = entry["scored"]
            fwd_30 = _opt_float(gt, "forward_30d_return_pct", "forward_30d_calendar_return_pct", "return_30d_pct", "fwd_30d_pct")
            fwd_60 = _opt_float(gt, "forward_60d_return_pct", "forward_60d_calendar_return_pct", "return_60d_pct", "fwd_60d_pct")
            fwd_to_date = _opt_float(
                gt,
                "forward_to_date_return_pct",
                "forward_to_t5_total_return_pct",
                "holding_return_to_date_pct",
                "return_to_date_pct",
            )
            max_sub_dd_60 = _opt_float(gt, "max_subsequent_drawdown_pct", "subsequent_drawdown_pct", "forward_60d_max_drawdown_pct", "forward_max_drawdown_pct")
            max_sub_dd_todate = _opt_float(gt, "forward_to_t5_max_drawdown_pct", "max_subsequent_drawdown_pct")
            max_sub_dd = max_sub_dd_60 if max_sub_dd_60 is not None else max_sub_dd_todate

            has_realized_forward_window = (
                fwd_30 is not None
                or fwd_60 is not None
                or (fwd_to_date is not None and abs(fwd_to_date) > 1e-9)
                or (max_sub_dd is not None and abs(max_sub_dd) > 1e-9)
            )
            explicit_top = gt.get("is_market_top")
            is_top_or_severe_dd = has_realized_forward_window and (
                bool(explicit_top) is True
                or (fwd_60 is not None and fwd_60 <= -12.0)
                or (fwd_30 is not None and fwd_60 is None and fwd_30 <= -12.0)
                or (max_sub_dd_60 is not None and max_sub_dd_60 <= -15.0)
            )
            explicit_buy_cny = _opt_float(
                gt,
                "counterfactual_buy_cny",
                "actual_buy_amount_cny",
                "historical_buy_amount_cny",
                "peak_entry_amount_cny",
                "principal_cny",
            )
            if explicit_buy_cny is not None:
                buy_amount_cny: float | None = explicit_buy_cny
                notional_basis = "explicit_ground_truth"
            elif is_top_or_severe_dd and effective_default_lot_cny > 0:
                buy_amount_cny = effective_default_lot_cny
            else:
                buy_amount_cny = None

            real_code = str(gt.get("code") or gt.get("asset_id") or gt.get("fund_code") or scored["code"])
            real_name = str(gt.get("name") or gt.get("asset_name") or gt.get("fund_name") or scored["name"])

            aligned = {
                "snapshot_id": sid,
                "timepoint_id": str(gt.get("internal_timepoint_id") or "") or None,
                "as_of": resolved_as_of,
                "snapshot_label": resolved_label,
                "blind_id": b_id,
                "code": real_code,
                "name": real_name,
                "category": scored["category"],
                "total_score": scored["total_score"],
                "base_score": scored["base_score"],
                "plugin_score": scored["plugin_score"],
                "tier": scored["tier"],
                "flags": scored["flags"],
                "pulse_state": scored["universal_base"]["pulse_state"],
                "has_realized_forward_window": has_realized_forward_window,
                "forward_30d_return_pct": fwd_30,
                "forward_60d_return_pct": fwd_60,
                "forward_to_date_return_pct": fwd_to_date if has_realized_forward_window else None,
                "max_subsequent_drawdown_pct": max_sub_dd if has_realized_forward_window else None,
                "forward_to_date_max_drawdown_pct": max_sub_dd_todate if has_realized_forward_window else None,
                "is_market_top_case": is_top_or_severe_dd,
                "explicit_market_top": bool(explicit_top) if explicit_top is not None else None,
                "counterfactual_buy_cny": buy_amount_cny,
                "explicit_realized_loss_cny": _opt_float(gt, "realized_forward_pnl_cny", "actual_loss_cny"),
            }
            snap_rows.append(aligned)
            aligned_rows.append(aligned)

        if not snap_rows:
            continue

        pairs_30 = [(r["total_score"], r["forward_30d_return_pct"]) for r in snap_rows if r["forward_30d_return_pct"] is not None]
        pairs_60 = [(r["total_score"], r["forward_60d_return_pct"]) for r in snap_rows if r["forward_60d_return_pct"] is not None]
        pairs_td = [(r["total_score"], r["forward_to_date_return_pct"]) for r in snap_rows if r["forward_to_date_return_pct"] is not None]
        ic_30 = spearman_rank_ic([p[0] for p in pairs_30], [p[1] for p in pairs_30])
        ic_60 = spearman_rank_ic([p[0] for p in pairs_60], [p[1] for p in pairs_60])
        ic_td = spearman_rank_ic([p[0] for p in pairs_td], [p[1] for p in pairs_td])
        if ic_30 is not None:
            all_ic_30d.append(ic_30)
        if ic_60 is not None:
            all_ic_60d.append(ic_60)
        if ic_td is not None:
            all_ic_todate.append(ic_td)

        tier_30: dict[str, float | None] = {}
        tier_60: dict[str, float | None] = {}
        tier_td: dict[str, float | None] = {}
        for t in ("A_CORE", "B_SATELLITE", "C_WATCH", "D_AVOID"):
            t30 = [float(r["forward_30d_return_pct"]) for r in snap_rows if r["tier"] == t and r["forward_30d_return_pct"] is not None]
            t60 = [float(r["forward_60d_return_pct"]) for r in snap_rows if r["tier"] == t and r["forward_60d_return_pct"] is not None]
            ttd_vals = [float(r["forward_to_date_return_pct"]) for r in snap_rows if r["tier"] == t and r["forward_to_date_return_pct"] is not None]
            tier_30[t] = _mean_or_none(t30)
            tier_60[t] = _mean_or_none(t60)
            tier_td[t] = _mean_or_none(ttd_vals)

        top_30_vals = [float(r["forward_30d_return_pct"]) for r in snap_rows if r["tier"] == "A_CORE" and r["forward_30d_return_pct"] is not None]
        if not top_30_vals:
            top_30_vals = [float(r["forward_30d_return_pct"]) for r in snap_rows if r["tier"] in {"A_CORE", "B_SATELLITE"} and r["forward_30d_return_pct"] is not None]
        bot_30_vals = [float(r["forward_30d_return_pct"]) for r in snap_rows if r["tier"] in {"C_WATCH", "D_AVOID"} and r["forward_30d_return_pct"] is not None]

        top_60_vals = [float(r["forward_60d_return_pct"]) for r in snap_rows if r["tier"] == "A_CORE" and r["forward_60d_return_pct"] is not None]
        if not top_60_vals:
            top_60_vals = [float(r["forward_60d_return_pct"]) for r in snap_rows if r["tier"] in {"A_CORE", "B_SATELLITE"} and r["forward_60d_return_pct"] is not None]
        bot_60_vals = [float(r["forward_60d_return_pct"]) for r in snap_rows if r["tier"] in {"C_WATCH", "D_AVOID"} and r["forward_60d_return_pct"] is not None]

        top_td_vals = [float(r["forward_to_date_return_pct"]) for r in snap_rows if r["tier"] == "A_CORE" and r["forward_to_date_return_pct"] is not None]
        if not top_td_vals:
            top_td_vals = [float(r["forward_to_date_return_pct"]) for r in snap_rows if r["tier"] in {"A_CORE", "B_SATELLITE"} and r["forward_to_date_return_pct"] is not None]
        bot_td_vals = [float(r["forward_to_date_return_pct"]) for r in snap_rows if r["tier"] in {"C_WATCH", "D_AVOID"} and r["forward_to_date_return_pct"] is not None]

        spread_30 = round((sum(top_30_vals) / len(top_30_vals)) - (sum(bot_30_vals) / len(bot_30_vals)), 4) if top_30_vals and bot_30_vals else None
        spread_60 = round((sum(top_60_vals) / len(top_60_vals)) - (sum(bot_60_vals) / len(bot_60_vals)), 4) if top_60_vals and bot_60_vals else None
        spread_td = round((sum(top_td_vals) / len(top_td_vals)) - (sum(bot_td_vals) / len(bot_td_vals)), 4) if top_td_vals and bot_td_vals else None
        spread_a_vs_d_30 = round(tier_30["A_CORE"] - tier_30["D_AVOID"], 4) if tier_30["A_CORE"] is not None and tier_30["D_AVOID"] is not None else None
        spread_a_vs_d_60 = round(tier_60["A_CORE"] - tier_60["D_AVOID"], 4) if tier_60["A_CORE"] is not None and tier_60["D_AVOID"] is not None else None

        snap_top_cases = [r for r in snap_rows if r["is_market_top_case"]]
        snap_top_intercepted = [r for r in snap_top_cases if r["tier"] in {"C_WATCH", "D_AVOID"}]
        snap_top_flagged = [
            r
            for r in snap_top_cases
            if r["tier"] in {"C_WATCH", "D_AVOID"}
            or bool(
                set(r["flags"])
                & {
                    "SHORT_TERM_OVERBOUGHT_CROWDED",
                    "STYLE_DRIFT_SUBSECTOR_BET",
                    "BENCHMARK_LAG_SEVERE",
                    "C_CLASS_LONG_HOLD_DRAG",
                    "GROWTH_PEG_STRETCHED",
                    "EXTREME_DRAWDOWN_RISK",
                    "HISTORICAL_VALUATION_HIGH",
                }
            )
        ]

        # Counterfactual avoided loss within this cross-section
        a_core_horizon_ret = _mean_or_none(
            [
                float(r["forward_to_date_return_pct"] if r["forward_to_date_return_pct"] is not None else r["forward_60d_return_pct"])
                for r in snap_rows
                if r["tier"] == "A_CORE"
                and (r["forward_to_date_return_pct"] is not None or r["forward_60d_return_pct"] is not None)
            ]
        )
        if a_core_horizon_ret is None:
            a_core_horizon_ret = _mean_or_none(
                [
                    float(r["forward_to_date_return_pct"] if r["forward_to_date_return_pct"] is not None else r["forward_60d_return_pct"])
                    for r in snap_rows
                    if r["tier"] in {"A_CORE", "B_SATELLITE"}
                    and (r["forward_to_date_return_pct"] is not None or r["forward_60d_return_pct"] is not None)
                ]
            )

        snap_avoided_loss_cny = 0.0
        snap_realloc_gain_cny = 0.0
        snap_filtered_principal_cny = 0.0
        for r in snap_rows:
            principal = r["counterfactual_buy_cny"]
            if principal is None or principal <= 0:
                continue
            horizon_ret = (
                r["forward_to_date_return_pct"]
                if r["forward_to_date_return_pct"] is not None
                else (r["forward_60d_return_pct"] if r["forward_60d_return_pct"] is not None else r["forward_30d_return_pct"])
            )
            if horizon_ret is None:
                continue
            is_filtered = r["tier"] in {"C_WATCH", "D_AVOID"}
            if is_filtered:
                snap_filtered_principal_cny += principal
                realized_pnl = principal * (horizon_ret / 100.0)
                avoided = max(0.0, -realized_pnl)
                snap_avoided_loss_cny += avoided
                if a_core_horizon_ret is not None:
                    alt_pnl = principal * (a_core_horizon_ret / 100.0)
                    snap_realloc_gain_cny += alt_pnl - realized_pnl
                r["avoided_loss_cny"] = round(avoided, 2)
                r["reallocated_alpha_gain_cny"] = round(
                    (principal * ((a_core_horizon_ret or 0.0) - horizon_ret) / 100.0), 2
                )
            else:
                r["avoided_loss_cny"] = 0.0
                r["reallocated_alpha_gain_cny"] = 0.0

        cross_section_summaries.append(
            {
                "snapshot_id": sid,
                "as_of": resolved_as_of,
                "label": resolved_label,
                "asset_count": len(snap_rows),
                "tier_counts": {t: sum(1 for r in snap_rows if r["tier"] == t) for t in ("A_CORE", "B_SATELLITE", "C_WATCH", "D_AVOID")},
                "tier_mean_forward_30d_pct": tier_30,
                "tier_mean_forward_60d_pct": tier_60,
                "tier_mean_forward_to_date_pct": tier_td,
                "spread_30d_pct": spread_30,
                "spread_60d_pct": spread_60,
                "spread_to_date_pct": spread_td,
                "spread_a_vs_d_30d_pct": spread_a_vs_d_30,
                "spread_a_vs_d_60d_pct": spread_a_vs_d_60,
                "monotonicity_30d": _compute_monotonicity(tier_30),
                "monotonicity_60d": _compute_monotonicity(tier_60),
                "monotonicity_to_date": _compute_monotonicity(tier_td),
                "rank_ic_30d": ic_30,
                "rank_ic_60d": ic_60,
                "rank_ic_to_date": ic_td,
                "top_cases_count": len(snap_top_cases),
                "top_avoid_tier_hits": len(snap_top_intercepted),
                "top_avoid_tier_or_flag_hits": len(snap_top_flagged),
                "top_avoid_hit_rate": round(len(snap_top_intercepted) / len(snap_top_cases), 4) if snap_top_cases else None,
                "filtered_principal_cny": round(snap_filtered_principal_cny, 2),
                "avoided_absolute_loss_cny": round(snap_avoided_loss_cny, 2),
                "reallocated_alpha_gain_cny": round(snap_realloc_gain_cny, 2),
                "a_core_benchmark_horizon_return_pct": a_core_horizon_ret,
            }
        )

    # Pooled / aggregate metrics
    pooled_tier_30: dict[str, float | None] = {}
    pooled_tier_60: dict[str, float | None] = {}
    pooled_tier_todate: dict[str, float | None] = {}
    for t in ("A_CORE", "B_SATELLITE", "C_WATCH", "D_AVOID"):
        pooled_tier_30[t] = _mean_or_none([float(r["forward_30d_return_pct"]) for r in aligned_rows if r["tier"] == t and r["forward_30d_return_pct"] is not None])
        pooled_tier_60[t] = _mean_or_none([float(r["forward_60d_return_pct"]) for r in aligned_rows if r["tier"] == t and r["forward_60d_return_pct"] is not None])
        pooled_tier_todate[t] = _mean_or_none([float(r["forward_to_date_return_pct"]) for r in aligned_rows if r["tier"] == t and r["forward_to_date_return_pct"] is not None])

    p_top_30 = [float(r["forward_30d_return_pct"]) for r in aligned_rows if r["tier"] == "A_CORE" and r["forward_30d_return_pct"] is not None]
    if not p_top_30:
        p_top_30 = [float(r["forward_30d_return_pct"]) for r in aligned_rows if r["tier"] in {"A_CORE", "B_SATELLITE"} and r["forward_30d_return_pct"] is not None]
    p_bot_30 = [float(r["forward_30d_return_pct"]) for r in aligned_rows if r["tier"] in {"C_WATCH", "D_AVOID"} and r["forward_30d_return_pct"] is not None]

    p_top_60 = [float(r["forward_60d_return_pct"]) for r in aligned_rows if r["tier"] == "A_CORE" and r["forward_60d_return_pct"] is not None]
    if not p_top_60:
        p_top_60 = [float(r["forward_60d_return_pct"]) for r in aligned_rows if r["tier"] in {"A_CORE", "B_SATELLITE"} and r["forward_60d_return_pct"] is not None]
    p_bot_60 = [float(r["forward_60d_return_pct"]) for r in aligned_rows if r["tier"] in {"C_WATCH", "D_AVOID"} and r["forward_60d_return_pct"] is not None]

    p_top_td = [float(r["forward_to_date_return_pct"]) for r in aligned_rows if r["tier"] == "A_CORE" and r["forward_to_date_return_pct"] is not None]
    if not p_top_td:
        p_top_td = [float(r["forward_to_date_return_pct"]) for r in aligned_rows if r["tier"] in {"A_CORE", "B_SATELLITE"} and r["forward_to_date_return_pct"] is not None]
    p_bot_td = [float(r["forward_to_date_return_pct"]) for r in aligned_rows if r["tier"] in {"C_WATCH", "D_AVOID"} and r["forward_to_date_return_pct"] is not None]

    pooled_spread_30 = round((sum(p_top_30) / len(p_top_30)) - (sum(p_bot_30) / len(p_bot_30)), 4) if p_top_30 and p_bot_30 else None
    pooled_spread_60 = round((sum(p_top_60) / len(p_top_60)) - (sum(p_bot_60) / len(p_bot_60)), 4) if p_top_60 and p_bot_60 else None
    pooled_spread_td = round((sum(p_top_td) / len(p_top_td)) - (sum(p_bot_td) / len(p_bot_td)), 4) if p_top_td and p_bot_td else None
    pooled_a_vs_d_30 = round(pooled_tier_30["A_CORE"] - pooled_tier_30["D_AVOID"], 4) if pooled_tier_30["A_CORE"] is not None and pooled_tier_30["D_AVOID"] is not None else None
    pooled_a_vs_d_60 = round(pooled_tier_60["A_CORE"] - pooled_tier_60["D_AVOID"], 4) if pooled_tier_60["A_CORE"] is not None and pooled_tier_60["D_AVOID"] is not None else None

    pooled_pairs_30 = [(r["total_score"], r["forward_30d_return_pct"]) for r in aligned_rows if r["forward_30d_return_pct"] is not None]
    pooled_pairs_60 = [(r["total_score"], r["forward_60d_return_pct"]) for r in aligned_rows if r["forward_60d_return_pct"] is not None]
    pooled_pairs_td = [(r["total_score"], r["forward_to_date_return_pct"]) for r in aligned_rows if r["forward_to_date_return_pct"] is not None]
    pooled_ic_30 = spearman_rank_ic([p[0] for p in pooled_pairs_30], [p[1] for p in pooled_pairs_30])
    pooled_ic_60 = spearman_rank_ic([p[0] for p in pooled_pairs_60], [p[1] for p in pooled_pairs_60])
    pooled_ic_td = spearman_rank_ic([p[0] for p in pooled_pairs_td], [p[1] for p in pooled_pairs_td])

    mean_ic_30 = _mean_or_none(all_ic_30d)
    mean_ic_60 = _mean_or_none(all_ic_60d)
    mean_ic_td = _mean_or_none(all_ic_todate)

    def _ic_ir(ics: Sequence[float]) -> float | None:
        if len(ics) < 2:
            return None
        m = sum(ics) / len(ics)
        std = math.sqrt(sum((x - m) ** 2 for x in ics) / (len(ics) - 1))
        return round(m / std, 4) if std > 1e-12 else None

    all_top_cases = [r for r in aligned_rows if r["is_market_top_case"]]
    all_top_tier_hits = [r for r in all_top_cases if r["tier"] in {"C_WATCH", "D_AVOID"}]
    all_top_flag_or_tier_hits = [
        r
        for r in all_top_cases
        if r["tier"] in {"C_WATCH", "D_AVOID"}
        or bool(
            set(r["flags"])
            & {
                "SHORT_TERM_OVERBOUGHT_CROWDED",
                "STYLE_DRIFT_SUBSECTOR_BET",
                "BENCHMARK_LAG_SEVERE",
                "C_CLASS_LONG_HOLD_DRAG",
                "GROWTH_PEG_STRETCHED",
                "EXTREME_DRAWDOWN_RISK",
                "HISTORICAL_VALUATION_HIGH",
            }
        )
    ]

    total_filtered_principal = round(sum(s["filtered_principal_cny"] for s in cross_section_summaries), 2)
    total_avoided_loss = round(sum(s["avoided_absolute_loss_cny"] for s in cross_section_summaries), 2)
    total_realloc_gain = round(sum(s["reallocated_alpha_gain_cny"] for s in cross_section_summaries), 2)

    cs_spreads_30 = [float(s["spread_30d_pct"]) for s in cross_section_summaries if s["spread_30d_pct"] is not None]
    cs_spreads_60 = [float(s["spread_60d_pct"]) for s in cross_section_summaries if s["spread_60d_pct"] is not None]
    cs_spreads_td = [float(s["spread_to_date_pct"]) for s in cross_section_summaries if s["spread_to_date_pct"] is not None]

    return {
        "ok": True,
        "command": "backtest",
        "anti_cheat_guard": {
            "stage_a_blind_scoring_isolated": True,
            "lookahead_keys_rejected": fail_on_lookahead_in_snapshots,
            "stripped_keys_in_non_strict_mode": sorted(set(stripped_keys_audit)),
            "hardcoded_codes_or_dates": False,
        },
        "aggregate": {
            "snapshot_count": len(cross_section_summaries),
            "aligned_sample_count": len(aligned_rows),
            "unmatched_sample_count": len(unmatched_items),
            "tier_counts": {t: sum(1 for r in aligned_rows if r["tier"] == t) for t in ("A_CORE", "B_SATELLITE", "C_WATCH", "D_AVOID")},
            "tier_mean_forward_30d_pct": pooled_tier_30,
            "tier_mean_forward_60d_pct": pooled_tier_60,
            "tier_mean_forward_to_date_pct": pooled_tier_todate,
            "pooled_spread_30d_pct": pooled_spread_30,
            "pooled_spread_60d_pct": pooled_spread_60,
            "pooled_spread_to_date_pct": pooled_spread_td,
            "mean_cross_section_spread_30d_pct": _mean_or_none(cs_spreads_30),
            "mean_cross_section_spread_60d_pct": _mean_or_none(cs_spreads_60),
            "mean_cross_section_spread_to_date_pct": _mean_or_none(cs_spreads_td),
            "pooled_spread_a_vs_d_30d_pct": pooled_a_vs_d_30,
            "pooled_spread_a_vs_d_60d_pct": pooled_a_vs_d_60,
            "monotonicity_30d": _compute_monotonicity(pooled_tier_30),
            "monotonicity_60d": _compute_monotonicity(pooled_tier_60),
            "monotonicity_to_date": _compute_monotonicity(pooled_tier_todate),
            "mean_rank_ic_30d": mean_ic_30,
            "mean_rank_ic_60d": mean_ic_60,
            "mean_rank_ic_to_date": mean_ic_td,
            "pooled_rank_ic_30d": pooled_ic_30,
            "pooled_rank_ic_60d": pooled_ic_60,
            "pooled_rank_ic_to_date": pooled_ic_td,
            "ic_ir_30d": _ic_ir(all_ic_30d),
            "ic_ir_60d": _ic_ir(all_ic_60d),
            "ic_ir_to_date": _ic_ir(all_ic_todate),
            "top_cases_count": len(all_top_cases),
            "top_avoid_tier_hits": len(all_top_tier_hits),
            "top_avoid_tier_or_flag_hits": len(all_top_flag_or_tier_hits),
            "top_avoid_hit_rate": round(len(all_top_tier_hits) / len(all_top_cases), 4) if all_top_cases else None,
            "top_avoid_with_risk_flags_hit_rate": round(len(all_top_flag_or_tier_hits) / len(all_top_cases), 4) if all_top_cases else None,
            "counterfactual_notional_basis": notional_basis,
            "default_peak_lot_cny": effective_default_lot_cny,
            "filtered_principal_cny": total_filtered_principal,
            "avoided_absolute_loss_cny": total_avoided_loss,
            "reallocated_alpha_gain_cny": total_realloc_gain,
        },
        "cross_sections": cross_section_summaries,
        "aligned_evaluations": aligned_rows,
        "unmatched_items": unmatched_items,
    }


def _fmt_pct(val: float | None) -> str:
    return f"{val:+.2f}%" if val is not None else "—"


def _fmt_num(val: float | None, digits: int = 3) -> str:
    return f"{val:.{digits}f}" if val is not None else "—"


def render_backtest_markdown(bt: Mapping[str, Any]) -> str:
    agg = bt["aggregate"]
    t30 = agg["tier_mean_forward_30d_pct"]
    t60 = agg["tier_mean_forward_60d_pct"]
    ttd = agg["tier_mean_forward_to_date_pct"]
    tcounts = agg["tier_counts"]
    mono30 = agg["monotonicity_30d"]
    mono60 = agg["monotonicity_60d"]
    monotd = agg["monotonicity_to_date"]

    lines = [
        "# Blind Point-in-Time (PIT) Backtest Report — `asset-screener`",
        "",
        "## 1. Anti-Cheat & Zero Look-Ahead Verification",
        "",
        f"- **Stage A Blind Isolation**: `{bt['anti_cheat_guard']['stage_a_blind_scoring_isolated']}` (scores and `A_CORE..D_AVOID` tiers frozen prior to reading ground-truth returns)",
        f"- **Look-Ahead Key Rejection**: `{bt['anti_cheat_guard']['lookahead_keys_rejected']}` (`forward_*`, `future_*`, `subsequent_*`, `is_market_top`, `ground_truth` forbidden in `evaluate_asset()`)",
        "- **Asset-Code / Date Hardcoding**: `NONE` (pure universal base + 4 category plugins)",
        f"- **Cross-Sections Evaluated**: `{agg['snapshot_count']}` (`{agg['aligned_sample_count']}` aligned asset-snapshots, `{agg['unmatched_sample_count']}` unmatched)",
        "",
        "## 2. Aggregate Predictive Performance Summary",
        "",
        "| Metric | 30-Day Horizon | 60-Day Horizon | Full-Cycle To-Date | Notes |",
        "|---|---:|---:|---:|---|",
        f"| **Pooled Spread (`A_CORE` vs `C_WATCH+D_AVOID`)** | **{_fmt_pct(agg['pooled_spread_30d_pct'])}** | **{_fmt_pct(agg['pooled_spread_60d_pct'])}** | **{_fmt_pct(agg['pooled_spread_to_date_pct'])}** | Mean CS: {_fmt_pct(agg['mean_cross_section_spread_30d_pct'])} / {_fmt_pct(agg['mean_cross_section_spread_60d_pct'])} / {_fmt_pct(agg['mean_cross_section_spread_to_date_pct'])} |",
        f"| **Extreme Spread (`A_CORE` vs `D_AVOID`)** | {_fmt_pct(agg['pooled_spread_a_vs_d_30d_pct'])} | {_fmt_pct(agg['pooled_spread_a_vs_d_60d_pct'])} | — | `D_AVOID` fires when severe governance/AUM/benchmark collapse discloses |",
        f"| **Mean Cross-Section Spearman Rank IC** | **{_fmt_num(agg['mean_rank_ic_30d'])}** | **{_fmt_num(agg['mean_rank_ic_60d'])}** | **{_fmt_num(agg['mean_rank_ic_to_date'])}** | Pooled IC: {_fmt_num(agg['pooled_rank_ic_30d'])} / {_fmt_num(agg['pooled_rank_ic_60d'])} / {_fmt_num(agg['pooled_rank_ic_to_date'])} |",
        f"| **Information Ratio (`IC-IR`)** | {_fmt_num(agg['ic_ir_30d'], 2)} | {_fmt_num(agg['ic_ir_60d'], 2)} | {_fmt_num(agg['ic_ir_to_date'], 2)} | Cross-sectional IC mean / std |",
        f"| **Grade Monotonicity (`A -> B -> C -> D`)** | `{mono30['is_weakly_monotonic']}` (order={_fmt_num(mono30['pairwise_order_score'], 2)}) | `{mono60['is_weakly_monotonic']}` (order={_fmt_num(mono60['pairwise_order_score'], 2)}) | `{monotd['is_weakly_monotonic']}` (order={_fmt_num(monotd['pairwise_order_score'], 2)}) | Strictly monotonic: `{mono30['is_strictly_monotonic']}` / `{mono60['is_strictly_monotonic']}` / `{monotd['is_strictly_monotonic']}` |",
        "",
        "### Counterfactual Risk Interception & Capital Preservation",
        "",
        f"- **Top-Avoid Hit Rate (`C_WATCH / D_AVOID` on market tops / severe drawdowns)**: "
        f"`{agg['top_avoid_tier_hits']}/{agg['top_cases_count']}` (**{(agg['top_avoid_hit_rate'] * 100.0):.1f}%**)"
        if agg["top_avoid_hit_rate"] is not None
        else "- **Top-Avoid Hit Rate**: `N/A (0 top cases)`",
        f"- **Top-Avoid + Explicit Risk-Flag Coverage**: "
        f"`{agg['top_avoid_tier_or_flag_hits']}/{agg['top_cases_count']}` (**{(agg['top_avoid_with_risk_flags_hit_rate'] * 100.0):.1f}%**)"
        if agg["top_avoid_with_risk_flags_hit_rate"] is not None
        else "- **Top-Avoid + Risk-Flag Coverage**: `N/A`",
        f"- **Counterfactual Notional Basis**: `{agg['counterfactual_notional_basis']}` (`¥{agg['default_peak_lot_cny']:,.0f}` standard lot per intercepted peak/drawdown entry when explicit ledger buy amounts are absent)",
        f"- **Filtered Peak/Weak-Tier Principal**: `¥{agg['filtered_principal_cny']:,.2f}`",
        f"- **Avoided Absolute Real Loss (`avoided_absolute_loss_cny`)**: **`¥{agg['avoided_absolute_loss_cny']:,.2f}`**",
        f"- **Counterfactual Reallocation Alpha (`reallocated_alpha_gain_cny` -> `A_CORE`)**: **`¥{agg['reallocated_alpha_gain_cny']:,.2f}`**",
        "",
        "## 3. Grade Monotonicity Breakdown (Pooled Across Cross-Sections)",
        "",
        "| Tier | Count | Mean Forward 30d | Mean Forward 60d | Mean Forward To-Date |",
        "|---|---:|---:|---:|---:|",
    ]
    for tier in ("A_CORE", "B_SATELLITE", "C_WATCH", "D_AVOID"):
        lines.append(
            f"| `{tier}` | {tcounts[tier]} | {_fmt_pct(t30[tier])} | {_fmt_pct(t60[tier])} | {_fmt_pct(ttd[tier])} |"
        )

    lines.extend(
        [
            "",
            "## 4. Per-Cross-Section Performance & Regime Context",
            "",
            "| Snapshot | Date | N | Tiers (`A/B/C/D`) | Spread 30d | Spread 60d | Spread To-Date | Rank IC 30d | Rank IC 60d | Rank IC To-Date | Top-Avoid (`Tier` / `+Flag`) | Avoided Loss (CNY) | Realloc Gain (CNY) |",
            "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for cs in bt["cross_sections"]:
        tc = cs["tier_counts"]
        top_str = (
            f"{cs['top_avoid_tier_hits']}/{cs['top_cases_count']} ({cs['top_avoid_tier_or_flag_hits']}/{cs['top_cases_count']})"
            if cs["top_cases_count"]
            else "—"
        )
        lines.append(
            f"| `{cs['snapshot_id']}` | {cs['as_of'] or '—'} | {cs['asset_count']} | "
            f"`{tc['A_CORE']}/{tc['B_SATELLITE']}/{tc['C_WATCH']}/{tc['D_AVOID']}` | "
            f"{_fmt_pct(cs['spread_30d_pct'])} | {_fmt_pct(cs['spread_60d_pct'])} | {_fmt_pct(cs['spread_to_date_pct'])} | "
            f"{_fmt_num(cs['rank_ic_30d'])} | {_fmt_num(cs['rank_ic_60d'])} | {_fmt_num(cs['rank_ic_to_date'])} | "
            f"{top_str} | ¥{cs['avoided_absolute_loss_cny']:,.2f} | ¥{cs['reallocated_alpha_gain_cny']:,.2f} |"
        )
    lines.append("")
    for cs in bt["cross_sections"]:
        if cs.get("label") and cs["label"] != cs["snapshot_id"]:
            lines.append(f"- **`{cs['snapshot_id']}` ({cs['as_of'] or 'PIT'})**: {cs['label']}")

    lines.extend(
        [
            "",
            "## 5. Blind Score vs. Realized Forward Returns (Item-Level Audit)",
            "",
            "| Snapshot | Code | Asset | Category | Score | Tier | Pulse | Fwd 30d | Fwd 60d | Max Sub DD | Top Case | Flags |",
            "|---|---|---|---|---:|---|---|---:|---:|---:|---|---|",
        ]
    )
    for r in bt["aligned_evaluations"]:
        flag_str = ", ".join(r["flags"]) if r["flags"] else "—"
        lines.append(
            f"| `{r['snapshot_id']}` | `{r['code']}` | {r['name']} | `{r['category']}` | "
            f"**{r['total_score']:.1f}** | `{r['tier']}` | `{r['pulse_state']}` | "
            f"{_fmt_pct(r['forward_30d_return_pct'])} | {_fmt_pct(r['forward_60d_return_pct'])} | "
            f"{_fmt_pct(r['max_subsequent_drawdown_pct'])} | `{'YES' if r['is_market_top_case'] else 'no'}` | {flag_str} |"
        )

    return "\n".join(lines) + "\n"


def _render_markdown(results: Sequence[Mapping[str, Any]], model_sync: Mapping[str, Any]) -> str:
    lines = [
        "# Objective Asset Evaluation Scorecard (`asset-screener`)",
        "",
        f"- **Model Calibration**: `{model_sync['status']}`"
        + (f" (`{model_sync['path']}`)" if model_sync.get("path") else ""),
        "- **Personal Ledger Coupling**: `NONE (stateless objective evaluation)`",
        "",
        "| Code | Name | Category | Base (40) | Plugin (60) | Total (100) | Tier | Flags |",
        "|---|---|---|---:|---:|---:|---|---|",
    ]
    for item in results:
        flag_str = ", ".join(item["flags"]) if item["flags"] else "—"
        lines.append(
            f"| `{item['code']}` | {item['name']} | `{item['category']}` | "
            f"{item['base_score']:.1f} | {item['plugin_score']:.1f} | **{item['total_score']:.1f}** | "
            f"`{item['tier']}` | {flag_str} |"
        )
    lines.append("")
    for item in results:
        lines.append(f"## `{item['code']}` {item['name']} — `{item['tier']}` ({item['total_score']:.1f}/100)")
        for c_line in item["consensus"]:
            lines.append(f"- **Consensus**: {c_line}")
        for d_line in item["divergence"]:
            lines.append(f"- **Divergence**: {d_line}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asset_evaluator.py",
        description="Objective investment asset evaluator & screener (1 universal base + 4 category plugins, zero personal ledger coupling).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_models = sub.add_parser("list-models", help="List universal base, 4 category plugins, and 5 model citations.")
    p_models.add_argument("--research-models-path", default=None, help="Optional path to research-models.md.")

    for cmd_name, help_text in (
        ("evaluate", "Evaluate one or more objective asset definitions."),
        ("screen", "Filter and rank candidate assets by objective score and tier."),
        ("compare", "Compare multiple candidate assets side-by-side."),
    ):
        p = sub.add_parser(cmd_name, help=help_text)
        p.add_argument("--input-json", required=True, help="Inline JSON string or path to JSON file of asset(s).")
        p.add_argument("--rf-10y-pct", type=float, default=DEFAULT_THRESHOLDS["rf_10y_pct"], help="10Y sovereign bond anchor (percent).")
        p.add_argument("--research-models-path", default=None, help="Optional path to research-models.md.")
        p.add_argument(
            "--strict-no-personal-ledger",
            dest="strict_no_personal_ledger",
            action="store_true",
            default=True,
            help="Fail closed if personal holdings/PnL/date keys appear in input (default: enabled).",
        )
        p.add_argument(
            "--allow-ignore-personal-keys",
            dest="strict_no_personal_ledger",
            action="store_false",
            help="Strip/ignore personal keys instead of failing (for explicit adapter tests only).",
        )
        p.add_argument("--format", choices=("json", "markdown"), default="json")
        if cmd_name == "screen":
            p.add_argument("--category", choices=VALID_CATEGORIES, default=None)
            p.add_argument("--min-tier", choices=tuple(TIER_ORDER), default="B_SATELLITE")

    p_bt = sub.add_parser(
        "backtest",
        help="Run two-stage zero-lookahead Point-in-Time (PIT) blind backtest against ground-truth forward returns.",
    )
    p_bt.add_argument("--snapshots", required=True, help="Path or inline JSON of blind PIT asset snapshots.")
    p_bt.add_argument("--ground-truth", required=True, help="Path or inline JSON of post-snapshot forward returns.")
    p_bt.add_argument("--report-md", default=None, help="Optional output file path to write Markdown backtest report.")
    p_bt.add_argument("--output-json", default=None, help="Optional output file path to write full JSON backtest results.")
    p_bt.add_argument("--rf-10y-pct", type=float, default=None, help="Optional override for 10Y sovereign bond anchor (defaults to per-snapshot rf_10y_pct or 2.10%%).")
    p_bt.add_argument(
        "--default-peak-lot-cny",
        type=float,
        default=DEFAULT_COUNTERFACTUAL_PEAK_LOT_CNY,
        help="Standard notional CNY lot per top/severe-drawdown entry when ground-truth omits explicit buy amounts (default: 10000).",
    )
    p_bt.add_argument(
        "--strip-lookahead-fields",
        action="store_true",
        default=False,
        help="Strip accidental forward-return fields before Stage A scoring instead of failing closed.",
    )
    p_bt.add_argument("--format", choices=("json", "markdown"), default="json")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    model_sync = discover_research_models(getattr(args, "research_models_path", None))

    if args.command == "list-models":
        payload = {
            "ok": True,
            "architecture": {
                "universal_base_max_points": 40,
                "category_plugin_max_points": 60,
                "categories": list(VALID_CATEGORIES),
            },
            "models": MODEL_REGISTRY,
            "research_models_sync": model_sync,
            "forbidden_personal_ledger_keys": sorted(PERSONAL_LEDGER_FORBIDDEN_KEYS),
            "forbidden_lookahead_keys": sorted(LOOKAHEAD_FORBIDDEN_KEYS),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "backtest":
        try:
            snapshots_payload = _load_json_payload(args.snapshots)
            gt_payload = _load_json_payload(args.ground_truth)
            bt_result = run_blind_backtest(
                snapshots_payload,
                gt_payload,
                rf_10y_pct=args.rf_10y_pct,
                fail_on_lookahead_in_snapshots=not args.strip_lookahead_fields,
                default_peak_lot_cny=args.default_peak_lot_cny,
            )
            md_report = render_backtest_markdown(bt_result)
            if args.report_md:
                report_path = Path(args.report_md).expanduser()
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(md_report, encoding="utf-8")
            if args.output_json:
                out_json_path = Path(args.output_json).expanduser()
                out_json_path.parent.mkdir(parents=True, exist_ok=True)
                out_json_path.write_text(json.dumps(bt_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except LookaheadBiasViolation as exc:
            print(json.dumps({"ok": False, "error": "lookahead_bias_forbidden", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        except LedgerCouplingViolation as exc:
            print(json.dumps({"ok": False, "error": "personal_ledger_coupling_forbidden", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"ok": False, "error": "invalid_input", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2

        if args.format == "markdown":
            print(md_report, end="")
        else:
            print(json.dumps(bt_result, ensure_ascii=False, indent=2))
        return 0

    try:
        assets = _load_assets_payload(args.input_json)
        evaluated = [
            evaluate_asset(
                item,
                strict_no_personal_ledger=args.strict_no_personal_ledger,
                rf_10y_pct=args.rf_10y_pct,
            )
            for item in assets
        ]
    except LookaheadBiasViolation as exc:
        print(json.dumps({"ok": False, "error": "lookahead_bias_forbidden", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except LedgerCouplingViolation as exc:
        print(json.dumps({"ok": False, "error": "personal_ledger_coupling_forbidden", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": "invalid_input", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    if args.command == "screen":
        min_rank = TIER_ORDER[args.min_tier]
        evaluated = [
            item
            for item in evaluated
            if TIER_ORDER[item["tier"]] >= min_rank and (args.category is None or item["category"] == args.category)
        ]
    evaluated.sort(key=lambda item: (-item["total_score"], item["code"]))

    if args.format == "markdown":
        print(_render_markdown(evaluated, model_sync), end="")
        return 0

    payload = {
        "ok": True,
        "command": args.command,
        "count": len(evaluated),
        "research_models_sync": model_sync,
        "results": evaluated,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
