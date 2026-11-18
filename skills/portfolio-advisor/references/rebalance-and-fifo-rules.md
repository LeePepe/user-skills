# All-Weather Barbell, FIFO Fee Windows & Cashflow Rebalancing Rules

Canonical L1 rulebook for [`SKILL.md`](../SKILL.md) and `scripts/portfolio_rebalancer.py`. Objective fund/ETF/gold/bond scores are always delegated to [`asset-screener`](../../asset-screener/SKILL.md).

## 1. All-Weather Barbell Target Architecture

`portfolio_rebalancer.py` maps every holding's objective category (from `asset-screener`) into three portfolio sleeves:

| Portfolio Sleeve | Mapped `asset-screener` Categories | Default Target Weight | Drift Alert Threshold | Core Role |
|---|---|---:|---:|---|
| **`offensive_growth`** | `tech_cycle_growth` | `35.0%` | `> 55.0%` (`OVERWEIGHT_OFFENSIVE`) | Equity alpha & secular tech/cyclical upside; capped to prevent catastrophic drawdown |
| **`trunk_broad_beta`** | `broad_beta` | `30.0%` | `< 15.0%` or `> 50.0%` | Low-cost domestic + global index compounding backbone (CSI 300 / S&P 500) |
| **`defensive_ballast`** | `macro_hedge_gold_bond` + `dividend_value` | `35.0%` (`15%` Gold + `10%` Dividend + `10%` Pure Bond) | `< 20.0%` (`DEFENSIVE_VACUUM` when `< 5%`) | Negative/low equity correlation, cashflow dividends, and dry-powder liquidity |

- **Leland 5/25 No-Trade Buffer**: Computes `tolerance_band_pct = min(5.0%, 25% * target_weight_pct)` per sleeve (`NO_TRADE_BUFFER` vs `OVERWEIGHT_BREACH` / `UNDERWEIGHT_BREACH`) to avoid unnecessary turnover friction.
- **6-Month Cashflow Projection**: Simulates `current_market_value + 6 * new_monthly_dca_cny` across sleeves (plus optional initial lump-sum seed or unlocked FIFO rotation proceeds) to verify how fast offensive concentration compresses toward the Barbell target without forced underwater capitulation.

## 2. FIFO Redemption Fee Schedule & Unlock Windows

Lots are consumed oldest-first (`trade_date` / `confirmed_date` ascending) against historical redemptions; remaining lots are aged against `as_of_date`:

| Fee Profile (`fee_schedule`) | `< 7d` Punitive | `7–29d` | `30–179d` | `180–364d` | `>= 365d` | Zero-Fee Threshold |
|---|---:|---:|---:|---:|---:|---:|
| **`pure_bond_7d`** (政金债/纯债 A/C) | 1.50% | **0.00%** | 0.00% | 0.00% | 0.00% | **7 days** |
| **`c_share_30d`** (标准 C 类 / 黄金 ETF 联接 C) | 1.50% | 0.50% | **0.00%** | 0.00% | 0.00% | **30 days** |
| **`a_share_180d`** (部分 ETF 联接 A / QDII A) | 1.50% | 0.50% | 0.25% | **0.00%** | 0.00% | **180 days** |
| **`a_share_365d`** (常规主动股票/行业联接 A) | 1.50% | 0.50% | 0.50% | 0.25% | **0.00%** | **365 days** |
| **`qdii_sticky_50bps`** (部分老牌主动 QDII) | 1.50% | 0.50% | 0.50% | 0.50% | 0.50% | Never `0%` (`>=30d` treated as low-friction floor, disclosed explicitly) |
| **`zero_fee_gold_acc`** (银行积存金长期底仓) | 0.00–0.50% | 0.00–0.50% | **0.00%** | 0.00% | 0.00% | Channel conversion spread only; `0%` annual management fee |

- If granular `lots` are absent (only aggregate `remaining_shares`, `first_buy_date`, `last_trade_date` from summary tables), `portfolio_rebalancer.py` conservatively estimates unlocked vs. recent `<30d` DCA shares from `last_trade_date` and marks `lot_precision = "estimated_from_summary"`; when confirmation `lots` are present, `lot_precision = "exact_fifo_lots"`.

## 3. Track & Top-10 Overlap Matrix Rules

Pairwise comparison across active holdings emits three deterministic collision classes:

1. **`SAME_FUND_AC_DUPLICATE`**: Two holdings share `canonical_master_code` or normalized base fund stem (e.g., holding both Class A and Class C of the same global tech fund). Lower-efficiency C-share or smaller duplicate sleeve is marked for `STOP_DCA` / `REDEEM_FIFO_UNLOCKED`.
2. **`TOP10_STOCK_COLLISION`**: Weighted overlap `sum(min(w_i, w_j))` or Jaccard similarity across `top10_holdings` `>= 0.35` (35%), or `>= 3` shared top-10 semiconductor/AI/optical stocks. Retains the lower-fee / purer index (`A_CORE / B_SATELLITE`) and halts/rotates the higher-fee style-drifted active fund.
3. **`BROAD_BETA_REDUNDANCY`**: Multiple broad domestic beta feeders (`sub_track = cn_broad_large`) where a C-share broad index with ongoing sales-service drag overlaps `>= 60%` with an enhanced CSI 300 core position.

## 4. Rebalancing Modes & Six Action Enums

| Action Enum | Meaning | Trigger Condition |
|---|---|---|
| **`STOP_DCA`** | Set monthly DCA to `¥0` immediately; keep existing shares | Active DCA > 0 on `D_AVOID` / style-drifted / duplicate fund, or overweight sleeve holding in `hold_underwater_stop_dca` mode |
| **`REDUCE_DCA`** | Trim monthly DCA to target cap and free cashflow | Good/acceptable asset (`A_CORE`/`B_SATELLITE`/`C_WATCH`) whose monthly DCA exceeds sleeve budget |
| **`REDEEM_FIFO_UNLOCKED`** | Redeem **only** unlocked zero-fee shares (`>=threshold`); hold `<30d` lots until unlock date | Mode is `redeem_unlocked_fifo` (or PnL is near breakeven/positive) on `D_AVOID` or `SAME_FUND_AC_DUPLICATE` / `BROAD_BETA_REDUNDANCY` |
| **`HOLD_KEEP_DCA`** | Retain shares and maintain current disciplined DCA | `A_CORE` / `B_SATELLITE` asset with zero overlap conflict and right-sized DCA |
| **`HOLD_WAIT_REBOUND`** | Monthly DCA is already `¥0`; hold existing underwater shares for cyclical rebound | Underwater `C_WATCH` / `D_AVOID` / satellite position in `hold_underwater_stop_dca` mode with `current_dca == 0` |
| **`ADD_NEW_DCA`** | Allocate freed monthly cashflow into missing `A_CORE` sleeve candidates | Underweight `defensive_ballast` (Gold / Dividend Low-Vol / 7-10Y Policy Bond) or `trunk_broad_beta` |
