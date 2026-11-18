# Multi-Source Asset Evaluation Models & Plugin Thresholds

Canonical L1 reference for [`SKILL.md`](../SKILL.md) and `scripts/asset_evaluator.py`. Cross-validates thresholds with external `research-models.md` (from `learning-and-blog` M2 or knowledge-vault) whenever present.

## 1. Universal Base Layer (40 Points Max)

Applied identically to every asset before category-specific scoring:

| Sub-Factor | Weight | Bullish / Full-Score Zone | Caution / Penalty Zone | Primary Citation |
|---|---:|---|---|---|
| **10Y Risk-Free Anchor (`rf_10y_pct`)** | 12 | Implied yield/carry beats `rf_10y_pct` (default `2.10%` CN 10Y) by `>=150bps` or provides negative-equity-beta hedge | Expected carry `< rf_10y_pct` with high equity drawdown risk | Graham ERP / Macro Opportunity Cost |
| **Fee Drag & Share-Class Discipline** | 14 | Total annual fee (`mgmt + custody + sales_service`) `<=0.25%`; A/C breakeven `T* = 365 * F_front / c_service` respected | Total annual fee `>1.20%` or C-share `sales_service_fee_pct >= 0.25%` held beyond `max(180d, T*)` | Morningstar MRAR (`gamma=2`) |
| **Crowding & Pulse (`RSI/KDJ/BB%B/52w`)** | 14 | Oversold / uncrowded entry (`rsi_14 <= 38`, `bb_pct_b <= 0.25`, `percentile_52w <= 30%`) | Overheated / crowded chase (`rsi_14 >= 72`, `bb_pct_b >= 0.88`, `kdj_j >= 92`, or `turnover_crowding_pct >= 85%`) | AkShare Quant / Fuli Anti-Chasing |

## 2. Four Category Plugins (60 Points Max)

### Plugin ①: `dividend_value` (红利价值收息类)
- **Dividend-Bond Spread (`dy_pct - rf_10y_pct`, 24 pts)**: `>= 2.50%` (250bps) scores full marks; `2.00%–2.49%` golden accumulation zone; `< 1.00%` warns of dividend valuation compression.
- **Total Return Index & Reinvestment (12 pts)**: Prefers Total Return (`全收益`) benchmark tracking and low turnover (`<= 40%/yr`) so dividend compounding is not eroded by trading friction.
- **Dividend Continuity & Payout Health (24 pts)**: `consecutive_dividend_years >= 5` and `payout_coverage_ratio >= 1.5` (or stable SOE/utility cashflow) score high; cyclical one-off special dividends without coverage are discounted.

### Plugin ②: `broad_beta` (宽基国运指数类)
- **Graham Earnings Yield Spread (`ERP = 100/pe_ttm - rf_10y_pct`, 24 pts)**: `ERP >= 4.50%` signals deep value; `3.00%–4.49%` fair accumulation; `< 1.50%` signals equity overvaluation vs. sovereign bonds.
- **5–10Y Historical Valuation Percentile (18 pts)**: `pe_percentile_10y <= 30%` and `pb_percentile_10y <= 35%` favor systematic accumulation; `>= 80%` triggers valuation caution.
- **Tracking Error & Enhancement Stability (18 pts)**: Passive ETF feeders require `tracking_error_pct <= 0.50%`; enhanced index funds require positive `info_ratio >= 0.50` with controlled `tracking_error_pct <= 2.50%`.

### Plugin ③: `tech_cycle_growth` (科技/周期成长与风格漂移检测类)
- **PEG & Earnings Momentum (16 pts)**: `0 < peg <= 1.2` with positive earnings revision scores best; `peg > 2.2` without margin expansion penalizes valuation fragility.
- **Drawdown & Risk-Adjusted Elasticity (20 pts)**: Evaluates `max_drawdown_pct`, `calmar_ratio`, and `sortino_ratio`. Drawdowns `> 35%` with `calmar_ratio < 0.30` cap satellite sizing.
- **Concentration (`CR10`) & Style Drift Gate (24 pts)**:
  - Flags **`STYLE_DRIFT_SUBSECTOR_BET`** when a nominal broad-theme/balanced fund holds `top10_weight_pct >= 75%` and `single_subsector_pct >= 75%` (e.g., nominal AI or balanced growth fund betting `80%+` on a single semiconductor-equipment or optical-transceiver niche).
  - Flags **`BENCHMARK_LAG_SEVERE`** when `quarterly_excess_return_pct <= -15.0%` (e.g., lagging benchmark by `28%` in a single quarter) or AUM shrinks `>= 35%` QoQ.

### Plugin ④: `macro_hedge_gold_bond` (黄金/纯债宏观对冲类)
- **Gold Sub-Mode (`sub_type = gold`)**:
  - **10Y TIPS Real Yield & Central Bank Demand (26 pts)**: Uses WGC framework—falling/capped `tips_10y_real_yield_pct` plus strong global central-bank net buying (`central_bank_tonnes_qtr >= 250t`) supports structural allocation.
  - **Drawdown Pullback & Channel Fee Efficiency (34 pts)**: Pullbacks of `15%–35%` from all-time high improve medium-term entry odds; zero-management-fee bank accumulation gold or low-fee ETF feeders (`<= 0.60%/yr`, 0% redemption `>= 30d`) outscore high-spread retail channels.
- **Pure Bond Sub-Mode (`sub_type = pure_bond`)**:
  - **Duration & YTM Carry (`ytm_pct`, `duration_years`, 30 pts)**: Policy-bank / sovereign bond funds (`7–10Y CDB` or short-medium treasury) with low fee (`<= 0.25%/yr`) and `>= 7d` zero redemption fee act as dry-powder reserves.
  - **Credit Purity & Equity Negative Correlation (30 pts)**: Zero equity/convertible exposure (`equity_exposure_pct == 0`), AAA/sovereign credit, and negative `equity_corr_3y <= -0.10` earn full defensive marks.

## 3. Five-Source Consensus & Divergence Matrix

`asset_evaluator.py` evaluates five independent model lenses (`BULLISH`, `NEUTRAL`, `BEARISH`) per asset:

| Model ID | Source Framework | Primary Lens | Typical Divergence Trigger |
|---|---|---|---|
| `morningstar_mrar` | Morningstar Rating / Fee Quintile | Fee drag + downside-penalized return (MRAR) | Penalizes expensive active/C-share funds even during short-term momentum rebounds |
| `wgc_macro_gold` | World Gold Council (WGC) | 10Y TIPS real yield + central-bank buying + tail hedge | Stays structurally constructive on gold during tactical technical pullbacks |
| `graham_erp` | Benjamin Graham / FED ERP | `1/PE - 10Y Bond` equity risk premium | Flags high-PE growth exuberance while endorsing cheap broad-beta/dividend spreads |
| `akshare_quant` | AkShare `f-fund-screener` Factors | Sharpe, Calmar, Max Drawdown, peer rank, style drift | Flags severe benchmark lag (`<-15%/qtr`) and subsector concentration drift |
| `fuli_low_vol_dca` | Fuli Time (复利时光) | Valuation percentile + dividend cushion + pulse | Favors oversold low-vol/broad-beta DCA while warning against crowded high-RSI chases |

- **Consensus (`consensus`)**: Emitted when `>= 4` applicable models agree (`BULLISH` -> `A_CORE / B_SATELLITE`; `BEARISH` -> `D_AVOID`).
- **Divergence (`divergence`)**: Emitted whenever short-term technical/valuation signals conflict with structural fee/drift/risk signals (e.g., `fuli_low_vol_dca = NEUTRAL/BULLISH` on deep drawdown vs. `morningstar_mrar = BEARISH` + `akshare_quant = BEARISH` on `80%+` style drift and `1.5%+` fee drag).

## 4. Objective Tier Mapping & Orthogonality Lock

- `80–100` -> **`A_CORE`** (Core building block; eligible for primary accumulation).
- `65–79.9` -> **`B_SATELLITE`** (Tactical/satellite sleeve; size-capped).
- `50–64.9` -> **`C_WATCH`** (Hold/watch only; avoid fresh aggressive inflows).
- `< 50` (or severe style drift + benchmark collapse) -> **`D_AVOID`** (Objective avoid/exit candidate).
- **Orthogonality Lock**: Any payload containing personal ledger keys (`holding_shares`, `remaining_shares`, `holding_cost`, `market_value`, `unrealized_pnl`, `first_buy_date`, `lots`, `account_balance`) is rejected under `--strict-no-personal-ledger`.
