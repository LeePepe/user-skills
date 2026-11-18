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
    rf_10y_pct: float = DEFAULT_THRESHOLDS["rf_10y_pct"],
) -> dict[str, Any]:
    ignored_personal = validate_no_personal_ledger(asset, strict=strict_no_personal_ledger)
    category = infer_category(asset)
    base_score, base_breakdown, base_flags = _score_universal_base(asset, category, rf_10y_pct)

    if category == "dividend_value":
        plugin_score, plugin_breakdown, plugin_flags = _score_dividend_plugin(asset, rf_10y_pct)
    elif category == "broad_beta":
        plugin_score, plugin_breakdown, plugin_flags = _score_broad_beta_plugin(asset, rf_10y_pct)
    elif category == "macro_hedge_gold_bond":
        plugin_score, plugin_breakdown, plugin_flags = _score_macro_hedge_plugin(asset, rf_10y_pct)
    else:
        plugin_score, plugin_breakdown, plugin_flags = _score_tech_growth_plugin(asset)

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
        asset, category, base_breakdown, plugin_breakdown, flags
    )

    return {
        "code": str(asset.get("code") or "UNKNOWN"),
        "name": str(asset.get("name") or asset.get("code") or "Unnamed Asset"),
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
            "strict_enforced": strict_no_personal_ledger,
            "ignored_personal_keys": ignored_personal,
        },
    }


def _load_assets_payload(raw_arg: str) -> list[dict[str, Any]]:
    candidate = Path(raw_arg).expanduser()
    text = candidate.read_text(encoding="utf-8") if candidate.is_file() else raw_arg
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        if isinstance(parsed.get("assets"), list):
            return [dict(item) for item in parsed["assets"]]
        return [dict(parsed)]
    if isinstance(parsed, list):
        return [dict(item) for item in parsed]
    raise ValueError("Input JSON must be an asset object, {'assets': [...]}, or a list of asset objects.")


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
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
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
