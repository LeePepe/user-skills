---
name: asset-screener
description: "Evaluate, screen, and compare objective investment assets (funds, ETFs, gold, bonds, broad beta) via 1 universal base + 4 category plugins with Morningstar, WGC, Graham ERP, AkShare f-fund-screener, and Fuli Time consensus/divergence citations. Not for personal ledger holdings, FIFO windows, or portfolio rebalancing."
---

# asset-screener

Pure, stateless investment asset evaluation and screening skill with **zero personal-ledger coupling**. It scores any candidate or存量 asset strictly on public fundamentals, fee drag, macro spread, crowding pulse, and multi-source institutional/open-source models, without touching personal shares, buy dates, or account balances.

## `/howto` P7 Global 5-Tuple

| Dimension | Contract |
|---|---|
| **Inbound** | Public asset metadata JSON/CLI flags (`code`, `name`, `category`, fees, valuation percentiles, yield, tracking error, technical crowding, top-10 holdings). Rejects personal ledger keys (`holding_shares`, `remaining_shares`, `unrealized_pnl`, `first_buy_date`, `account_balance`) by default (`--strict-no-personal-ledger`). |
| **Internal** | `scripts/asset_evaluator.py`: **1 Universal Base Layer** (10Y risk-free opportunity cost, fee drag + C-share long-hold penalty, `RSI_14`/`KDJ_J`/`BB_pct_b`/`52w_percentile` crowding) + **4 Category Plugins** (`dividend_value`, `broad_beta`, `tech_cycle_growth`, `macro_hedge_gold_bond`) + **5-Source Consensus/Divergence Engine**. |
| **Calls** | Pure functional Layer-0 CLI (`evaluate`, `screen`, `compare`, `list-models`). Optional `--research-models-path` cross-validates thresholds against external `research-models.md`. Zero personal database or ledger calls. |
| **PointsTo** | Deterministic JSON or Markdown scorecard (`total_score` 0–100, `base_score`, `plugin_score`, `tier` in `A_CORE / B_SATELLITE / C_WATCH / D_AVOID`, `consensus`, `divergence`, `style_drift_flags`), consumed directly by users or downstream [`portfolio-advisor`](../portfolio-advisor/SKILL.md). |
| **Trigger & Notify** | Manual asset research/screening requests or subprocess/module invocation from [`portfolio-advisor`](../portfolio-advisor/SKILL.md). |

## When-to-Read & Progressive Disclosure Routing

- **Layer 0 (Default Execution)**: Run `scripts/asset_evaluator.py` first to compute deterministic scores, tiers, style-drift alerts, and multi-source consensus/divergence before writing prose analysis.
- **Layer 1 (Model Formulas & Citations)**: Read [`references/multi-source-models.md`](references/multi-source-models.md) when explaining factor weights, institutional citation rules (Morningstar MRAR, WGC, Graham ERP, AkShare `f-fund-screener`, Fuli Time), or calibrating category thresholds.
- **Layer 2 (Contract & Trigger Drawer)**: Inspect [`references/eval-cases.json`](references/eval-cases.json) when adding regression gates or boundary tests via `skill_contract.py`.
- **Sibling Boundary**: If the user asks about personal holdings, unrealized PnL, FIFO 30/180/365-day fee-free windows, holding overlap in their account, or monthly DCA switches, route to [`portfolio-advisor`](../portfolio-advisor/SKILL.md).

## Architecture: 1 Universal Base + 4 Category Plugins

1. **Universal Base Layer (40 pts max)**:
   - **Risk-Free Opportunity Cost (`rf_10y_pct`, default `2.10%`)**: Benchmarks expected yield/earnings against the 10Y sovereign bond anchor.
   - **Fee Structure & C-Share Drag**: Evaluates `mgmt_fee_pct + custody_fee_pct + sales_service_fee_pct`; flags high active fees (`>1.20%/yr`) and C-share sales-service drag (`>=0.40%/yr`) for long-horizon core allocation.
   - **Short-Term Crowding & Overbought/Oversold Pulse**: Synthesizes `rsi_14`, `kdj_j`, `bb_pct_b` (`%B`), `percentile_52w`, and `turnover_crowding_pct` to separate dip-entry opportunities from overheated chases.
2. **4 Category Plugins (60 pts max)**:
   - **`dividend_value` (红利价值)**: `DY - 10Y国债` spread (`>=200bps` golden zone), Total Return vs. Price Return index discipline, consecutive dividend years, and payout coverage.
   - **`broad_beta` (宽基国运)**: Graham Earnings Yield Spread (`ERP = 1/PE - 10Y国债`, `>=400bps` high-conviction), 5–10Y PE/PB percentile, annualized tracking error, and enhanced-index information ratio.
   - **`tech_cycle_growth` (科技/周期成长与风格漂移)**: PEG/momentum elasticity, Max Drawdown & Calmar/Sortino ratios, Q-report CR10 concentration, and **Style Drift Detection** (e.g., nominal AI/balanced fund betting `>=80%` on a single sub-industry or lagging its benchmark by `>15%` in a quarter).
   - **`macro_hedge_gold_bond` (黄金/纯债宏观对冲)**: Gold evaluates 10Y TIPS real yield, WGC central-bank net buying, drawdown from peak, and accumulation-gold vs. ETF fee spread; Pure Bond evaluates duration, YTM vs. `rf_10y_pct`, policy-bank credit quality, and equity negative correlation.

## Multi-Source Consensus & Divergence Citations

Every evaluation cites five independent perspectives and separates **`consensus`** (aligned bullish/bearish signals) from **`divergence`** (conflicting horizon signals, such as short-term oversold pulse vs. structural style drift or C-share fee drag):

1. **Morningstar (`morningstar_mrar`)**: Fee-adjusted total return, downside risk penalty (MRAR), and category fee quintile.
2. **World Gold Council (`wgc_macro_gold`)**: Real interest rate (10Y TIPS), central-bank gold demand, and tail-risk hedge role.
3. **Graham ERP / FED (`graham_erp`)**: Equity Risk Premium (`1/PE - rf_10y`) and bond-equity relative valuation.
4. **AkShare `f-fund-screener` (`akshare_quant`)**: Quantitative Sharpe/Calmar, max drawdown, tracking error, and peer percentile rank.
5. **Fuli Time (`fuli_low_vol_dca`)**: Low-valuation accumulation zone, dividend cashflow cushion, and anti-chasing discipline.

## CLI Quick Reference

```bash
# List universal base + 4 category plugins + 5 model citations (cross-checking research-models.md if present)
python3 skills/asset-screener/scripts/asset_evaluator.py list-models

# Evaluate a single asset or JSON array (enforces --strict-no-personal-ledger by default)
python3 skills/asset-screener/scripts/asset_evaluator.py evaluate --input-json path/to/assets.json --format json

# Screen and rank candidates by minimum tier or category
python3 skills/asset-screener/scripts/asset_evaluator.py screen --input-json path/to/assets.json --min-tier B_SATELLITE

# Side-by-side comparison in Markdown
python3 skills/asset-screener/scripts/asset_evaluator.py compare --input-json path/to/assets.json --format markdown
```

## Hard Red Lines

- **Zero Personal-Ledger Coupling**: Never read personal SQLite databases, account balances, lot histories, or unrealized PnL inside `asset-screener`. Keep `--strict-no-personal-ledger` enabled by default so personal fields fail closed with exit code `2`.
- **No Single-Model Worship**: Always emit both `consensus` and `divergence` across institutional/quant/valuation perspectives instead of collapsing conflicting signals into opaque prose.
