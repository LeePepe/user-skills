---
name: portfolio-advisor
description: "Diagnose personal investment portfolios and generate rebalancing plans from Financial CLI/JSON ledgers: invokes asset-screener for objective scores, computes All-Weather Barbell weights, FIFO 30/180/365d fee-free redemption windows, top-10 track overlap, and monthly DCA cashflow switches. Not for ledgerless screening."
---

# portfolio-advisor

Personal portfolio diagnosis, FIFO fee-window calculation, track-overlap detection, and cashflow-driven rebalancing skill. It consumes personal ledger snapshots (from `Financial` CLI `--json` or `--portfolio-json` fixtures) and delegates all objective fund/ETF/gold/bond scoring to `asset-screener` (`scripts/asset_evaluator.py`) without duplicating asset valuation formulas.

## `/howto` P7 Global 5-Tuple

| Dimension | Contract |
|---|---|
| **Inbound** | 1) Personal Financial ledger data via `--financial-root` (`cli/finance funds --json`, `cli/finance funds --by-fund --json`, `cli/finance accounts --json`) or hermetic `--portfolio-json`; 2) Objective asset scores fetched from `asset-screener`; 3) Optional `research-models.md` cross-check. |
| **Internal** | `scripts/portfolio_rebalancer.py`: **All-Weather Barbell Allocation** (Offensive Growth vs. Trunk Broad Beta vs. Defensive Gold + Dividend Low-Vol + Pure Bond), **FIFO Holding Window Engine** (`<7d` punitive, `7–29d`, `>=30d`, `>=180d`, `>=365d` 0-fee unlocked shares + exact unlock dates), **Track & Top-10 Overlap Matrix** (Jaccard/weighted stock overlap + same-fund A/C share duplicate + redundant broad-beta feeders), and **Cashflow-Driven Rebalancing Engine**. |
| **Calls** | Invokes `skills/asset-screener/scripts/asset_evaluator.py` after stripping personal ledger keys so `asset-screener`'s `--strict-no-personal-ledger` lock stays intact; invokes `Financial` CLI read-only when `--financial-root` is used. |
| **PointsTo** | Deterministic JSON or Markdown advisory plan containing Barbell drift table, FIFO fee-free share breakdown, overlap alerts, monthly freed DCA cashflow, 6-month projected allocation, and per-holding action enums. |
| **Trigger & Notify** | Manual portfolio review, monthly DCA calibration, or Owner rebalancing sign-off. |

## When-to-Read & Progressive Disclosure Routing

- **Layer 0 (Default Execution)**: Run `scripts/portfolio_rebalancer.py` (`diagnose`, `rebalance`, `fifo-window`, `overlap`) to produce exact numbers before drafting recommendations.
- **Layer 1 (Allocation, FIFO & Action Rules)**: Read [`references/rebalance-and-fifo-rules.md`](references/rebalance-and-fifo-rules.md) when customizing Barbell target sleeves, redemption fee schedules, overlap thresholds, or conservative vs. active rebalancing modes.
- **Layer 2 (Contract & Trigger Drawer)**: Read [`references/rebalance-and-fifo-rules.md`](references/rebalance-and-fifo-rules.md) for domain context; run regression assertions through `skill_contract.py eval` without viewing the JSON drawer.
- **Upstream Objective Scoring**: For pure, ledgerless asset screening or model explanations, route to `asset-screener`.

## Four Core Engines

1. **All-Weather Barbell Allocation Diagnosis**:
   - Classifies holdings into three macro sleeves:
     - **Offensive Sleeve (`offensive_growth`)**: Tech, semiconductor, AI, thematic/cyclical growth (default target `35%`, warning cap `55%`).
     - **Trunk Sleeve (`trunk_broad_beta`)**: Domestic & global broad-beta indices such as CSI 300 / S&P 500 (default target `30%`).
     - **Defensive Ballast Sleeve (`defensive_ballast`)**: Gold (`15%`) + Dividend Low-Vol (`10%`) + Pure Sovereign/Policy Bond (`10%`) = `35%` total defensive anchor.
   - Detects extreme single-ended concentration (e.g., `>75%` in offensive tech with `0%` defensive ballast).
2. **FIFO 30 / 180 / 365-Day Fee-Free Window Engine**:
   - Replays purchase/redemption lots in chronological FIFO order as of `--as-of YYYY-MM-DD`.
   - Splits remaining shares into **unlocked zero-fee shares** vs. **locked penalty-fee shares** according to fund fee schedules (`>=7d` pure bond, `>=30d` C-share / ETF feeder, `>=180d` or `>=365d` A-share / QDII, or sticky `0.50%` QDII rules) and reports the exact `next_fee_free_unlock_date`.
3. **Track & Top-10 Holding Overlap Engine**:
   - Flags **`SAME_FUND_AC_DUPLICATE`** (holding both A and C share classes of the same underlying fund), **`TOP10_STOCK_COLLISION`** (weighted top-10 stock overlap `>=35%`, e.g., nominal AI fund colliding with semiconductor index on equipment names), and **`BROAD_BETA_REDUNDANCY`** (overlapping domestic broad indices where a higher-fee C-share feeder duplicates an enhanced core index).
4. **Incremental Cashflow & Non-Capitulation Rebalancing**:
   - **`hold_underwater_stop_dca` (Default Conservative Mode)**: Preserves deeply underwater positions from panic realization while immediately halting/trimming DCA on `D_AVOID`, `C_WATCH`, or overlapping sleeves and redirecting freed monthly cashflow into `A_CORE` defensive/broad-beta candidates (`ADD_NEW_DCA`).
   - **`redeem_unlocked_fifo` (Active Rotation Mode)**: Recommends `REDEEM_FIFO_UNLOCKED` strictly on the `0%`-fee unlocked lot portion of `D_AVOID` / duplicate holdings while leaving `<30d` penalty lots untouched until their unlock date.
   - **Six Action Enums**: `STOP_DCA`, `REDUCE_DCA`, `REDEEM_FIFO_UNLOCKED`, `HOLD_KEEP_DCA`, `HOLD_WAIT_REBOUND`, `ADD_NEW_DCA`.

## CLI Quick Reference

```bash
# Full diagnosis + conservative non-capitulation DCA rebalancing from a JSON snapshot
python3 skills/portfolio-advisor/scripts/portfolio_rebalancer.py rebalance \
  --portfolio-json path/to/portfolio.json --mode hold_underwater_stop_dca --format json

# Active rotation mode: redeem only FIFO 0%-fee unlocked shares on avoid/duplicate funds
python3 skills/portfolio-advisor/scripts/portfolio_rebalancer.py rebalance \
  --portfolio-json path/to/portfolio.json --mode redeem_unlocked_fifo --format markdown

# Live read-only diagnosis from a local Financial repository checkout
python3 skills/portfolio-advisor/scripts/portfolio_rebalancer.py diagnose \
  --financial-root ~/Development/Financial --as-of 2026-11-18

# Standalone FIFO unlock table or track overlap matrix
python3 skills/portfolio-advisor/scripts/portfolio_rebalancer.py fifo-window --portfolio-json path/to/portfolio.json
python3 skills/portfolio-advisor/scripts/portfolio_rebalancer.py overlap --portfolio-json path/to/portfolio.json
```

## Hard Red Lines

- **Never Re-Implement Asset Scoring**: Always obtain objective `total_score`, `tier`, `consensus`, and `divergence` by calling `skills/asset-screener/scripts/asset_evaluator.py` with personal keys stripped.
- **Never Recommend Punitive `<7d` (`1.50%`) Redemptions**: Any redemption action must respect FIFO lot ages and disclose locked shares plus exact unlock dates.
- **Zero Hardcoded Personal Paths or Secrets**: Resolve `--financial-root` dynamically or via `Path.home() / "Development" / "Financial"` only when requested at runtime.
## When-to-Read L1 抽屉 + 必跑 L2 CLI 路由表

| Trigger | Single L1 drawer | Single L2 CLI |
|---|---|---|
| primary task | `references/rebalance-and-fifo-rules.md` | `python3 skills/portfolio-advisor/scripts/portfolio_rebalancer.py --help` |
| verification | [`references/eval-cases.json`](references/eval-cases.json) (Script-Only Regression Drawer: run via `skill_contract.py eval`, do not view) | `skill_contract.py eval --skill portfolio-advisor --strict` |
