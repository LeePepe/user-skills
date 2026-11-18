#!/usr/bin/env python3
"""Unit tests for orthogonal `asset-screener` and `portfolio-advisor` skills."""

from __future__ import annotations

from datetime import date
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT: Path = Path(__file__).resolve().parent.parent
SCREENER_SCRIPT: Path = REPO_ROOT / "skills" / "asset-screener" / "scripts" / "asset_evaluator.py"
REBALANCER_SCRIPT: Path = REPO_ROOT / "skills" / "portfolio-advisor" / "scripts" / "portfolio_rebalancer.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InvestmentSkillsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.screener = _load_module("test_asset_screener_mod", SCREENER_SCRIPT)
        cls.rebalancer = _load_module("test_portfolio_rebalancer_mod", REBALANCER_SCRIPT)

    def test_asset_screener_enforces_zero_personal_ledger_coupling(self) -> None:
        polluted = {
            "code": "017811",
            "name": "东方人工智能主题混合C",
            "holding_shares": 800.0,
            "unrealized_pnl": -3946.21,
            "first_buy_date": "2025-10-12",
        }
        with self.assertRaises(self.screener.LedgerCouplingViolation):
            self.screener.evaluate_asset(polluted, strict_no_personal_ledger=True)

        proc = subprocess.run(
            [
                sys.executable,
                str(SCREENER_SCRIPT),
                "evaluate",
                "--input-json",
                json.dumps(polluted, ensure_ascii=False),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("personal_ledger_coupling_forbidden", proc.stderr)

    def test_asset_screener_four_category_plugins_and_consensus_divergence(self) -> None:
        assets = [
            {
                "code": "007466",
                "name": "华泰柏瑞中证红利低波ETF联接A",
                "category": "dividend_value",
                "mgmt_fee_pct": 0.15,
                "custody_fee_pct": 0.05,
                "dividend_yield_pct": 5.15,
                "consecutive_dividend_years": 8,
                "payout_coverage_ratio": 1.9,
                "rsi_14": 41,
                "bb_pct_b": 0.35,
            },
            {
                "code": "025481",
                "name": "华夏沪深300指数量化增强C",
                "category": "broad_beta",
                "enhanced_index": True,
                "mgmt_fee_pct": 0.50,
                "custody_fee_pct": 0.10,
                "sales_service_fee_pct": 0.30,
                "pe_ttm": 12.2,
                "pe_percentile_10y": 28,
                "pb_percentile_10y": 24,
                "tracking_error_pct": 1.8,
                "info_ratio": 0.85,
                "rsi_14": 46,
            },
            {
                "code": "017811",
                "name": "东方人工智能主题混合C",
                "category": "tech_cycle_growth",
                "mgmt_fee_pct": 1.20,
                "custody_fee_pct": 0.20,
                "sales_service_fee_pct": 0.50,
                "rsi_14": 28,
                "kdj_j": 16,
                "bb_pct_b": 0.14,
                "percentile_52w": 18,
                "turnover_crowding_pct": 28,
                "top10_weight_pct": 80.85,
                "single_subsector_pct": 80.85,
                "quarterly_excess_return_pct": -19.2,
            },
            {
                "code": "AU_ACC",
                "name": "银行积存金",
                "category": "macro_hedge_gold_bond",
                "sub_type": "gold",
                "mgmt_fee_pct": 0.0,
                "custody_fee_pct": 0.0,
                "tips_10y_real_yield_pct": 1.60,
                "central_bank_tonnes_qtr": 321,
                "drawdown_from_peak_pct": 31.0,
                "rsi_14": 33,
                "bb_pct_b": 0.20,
            },
            {
                "code": "003376",
                "name": "广发中债7-10年国开债指数A",
                "category": "macro_hedge_gold_bond",
                "sub_type": "pure_bond",
                "mgmt_fee_pct": 0.15,
                "custody_fee_pct": 0.05,
                "duration_years": 7.2,
                "ytm_pct": 2.32,
                "equity_exposure_pct": 0.0,
                "equity_corr_3y": -0.25,
            },
        ]

        results = {item["code"]: self.screener.evaluate_asset(item) for item in assets}
        self.assertEqual(results["007466"]["tier"], "A_CORE")
        self.assertEqual(results["AU_ACC"]["tier"], "A_CORE")
        self.assertEqual(results["003376"]["tier"], "A_CORE")
        self.assertIn(results["025481"]["tier"], {"B_SATELLITE", "A_CORE"})
        self.assertIn("C_CLASS_LONG_HOLD_DRAG", results["025481"]["flags"])

        drifted = results["017811"]
        self.assertEqual(drifted["tier"], "D_AVOID")
        self.assertIn("STYLE_DRIFT_SUBSECTOR_BET", drifted["flags"])
        self.assertIn("BENCHMARK_LAG_SEVERE", drifted["flags"])
        self.assertTrue(any("Oversold bounce trap" in d for d in drifted["divergence"]))
        self.assertEqual(
            set(drifted["multi_source_views"]),
            {"morningstar_mrar", "wgc_macro_gold", "graham_erp", "akshare_quant", "fuli_low_vol_dca"},
        )

    def test_research_models_cross_validation_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "research-models.md"
            doc.write_text(
                "# Research Models\nCitations: Morningstar MRAR, WGC gold, Graham ERP, AkShare f-fund-screener, 复利时光.\n",
                encoding="utf-8",
            )
            synced = self.screener.discover_research_models(doc)
            self.assertEqual(synced["status"], "cross_validated_external")
            self.assertEqual(len(synced["matched_sources"]), 5)

    def test_fifo_windows_overlap_and_dual_rebalancing_modes(self) -> None:
        portfolio = {
            "as_of": "2026-11-18",
            "holdings": [
                {
                    "fund_code": "017811",
                    "fund_name": "东方人工智能主题混合C",
                    "category": "tech_cycle_growth",
                    "latest_nav": 2.356,
                    "market_value": 20528.79,
                    "unrealized_pnl": -3946.21,
                    "unrealized_pnl_pct": -16.12,
                    "monthly_dca_cny": 1900,
                    "mgmt_fee_pct": 1.2,
                    "custody_fee_pct": 0.2,
                    "sales_service_fee_pct": 0.5,
                    "top10_weight_pct": 80.85,
                    "single_subsector_pct": 80.85,
                    "quarterly_excess_return_pct": -18.5,
                    "top10_holdings": {"中微公司": 9.01, "北方华创": 9.00, "寒武纪": 7.91, "拓荆科技": 8.83},
                    "lots": [
                        {"confirmed_date": "2026-07-10", "direction": "purchase", "shares": 8413.41, "amount": 23800},
                        {"confirmed_date": "2026-09-15", "direction": "redemption", "shares": 500.00, "amount": 1225},
                        {"confirmed_date": "2026-10-29", "direction": "purchase", "shares": 800.00, "amount": 1900},
                    ],
                },
                {
                    "fund_code": "020839",
                    "fund_name": "南方中证半导体产业指数A",
                    "category": "tech_cycle_growth",
                    "is_passive_index": True,
                    "latest_nav": 2.743,
                    "market_value": 8151.45,
                    "unrealized_pnl": 651.45,
                    "unrealized_pnl_pct": 8.69,
                    "monthly_dca_cny": 500,
                    "mgmt_fee_pct": 0.15,
                    "custody_fee_pct": 0.05,
                    "top10_holdings": {"中微公司": 14.29, "北方华创": 13.48, "寒武纪": 9.86, "拓荆科技": 5.10},
                    "lots": [
                        {"confirmed_date": "2025-10-01", "direction": "purchase", "shares": 2971.51, "amount": 7500},
                    ],
                },
                {
                    "fund_code": "005698",
                    "fund_name": "华夏全球科技先锋(QDII)A",
                    "canonical_master_code": "HX_GLOBAL_TECH",
                    "category": "tech_cycle_growth",
                    "latest_nav": 1.723,
                    "market_value": 5711.21,
                    "unrealized_pnl": -3288.79,
                    "unrealized_pnl_pct": -36.54,
                    "monthly_dca_cny": 1000,
                    "mgmt_fee_pct": 1.8,
                    "custody_fee_pct": 0.35,
                    "quarterly_excess_return_pct": -28.04,
                    "lots": [
                        {"confirmed_date": "2026-02-01", "direction": "purchase", "shares": 3078.06, "amount": 8500},
                        {"confirmed_date": "2026-10-28", "direction": "purchase", "shares": 236.63, "amount": 500},
                    ],
                },
                {
                    "fund_code": "024239",
                    "fund_name": "华夏全球科技先锋(QDII)C",
                    "canonical_master_code": "HX_GLOBAL_TECH",
                    "category": "tech_cycle_growth",
                    "latest_nav": 1.698,
                    "market_value": 3923.15,
                    "unrealized_pnl": -1076.85,
                    "unrealized_pnl_pct": -21.54,
                    "monthly_dca_cny": 0,
                    "mgmt_fee_pct": 1.8,
                    "custody_fee_pct": 0.35,
                    "sales_service_fee_pct": 0.5,
                    "quarterly_excess_return_pct": -28.14,
                    "lots": [
                        {"confirmed_date": "2026-05-01", "direction": "purchase", "shares": 2310.32, "amount": 5000},
                    ],
                },
                {
                    "fund_code": "025856",
                    "fund_name": "华夏中证电网设备ETF联接A",
                    "category": "tech_cycle_growth",
                    "is_passive_index": True,
                    "latest_nav": 0.952,
                    "market_value": 7762.68,
                    "unrealized_pnl": -2437.32,
                    "unrealized_pnl_pct": -23.90,
                    "monthly_dca_cny": 1700,
                    "target_dca_cap_cny": 500,
                    "keep_reduced_dca": True,
                    "mgmt_fee_pct": 0.15,
                    "custody_fee_pct": 0.05,
                    "lots": [
                        {"confirmed_date": "2026-04-01", "direction": "purchase", "shares": 6451.52, "amount": 8500},
                        {"confirmed_date": "2026-10-29", "direction": "purchase", "shares": 1701.70, "amount": 1700},
                    ],
                },
                {
                    "fund_code": "025481",
                    "fund_name": "华夏沪深300指数量化增强C",
                    "category": "broad_beta",
                    "sub_track": "cn_broad",
                    "enhanced_index": True,
                    "latest_nav": 0.932,
                    "market_value": 7028.55,
                    "unrealized_pnl": -771.45,
                    "unrealized_pnl_pct": -9.89,
                    "monthly_dca_cny": 2550,
                    "target_dca_cap_cny": 1500,
                    "mgmt_fee_pct": 0.5,
                    "custody_fee_pct": 0.1,
                    "sales_service_fee_pct": 0.3,
                    "pe_ttm": 12.1,
                    "pe_percentile_10y": 27,
                    "info_ratio": 0.85,
                    "lots": [
                        {"confirmed_date": "2026-06-01", "direction": "purchase", "shares": 4927.33, "amount": 5250},
                        {"confirmed_date": "2026-10-29", "direction": "purchase", "shares": 2615.65, "amount": 2550},
                    ],
                },
                {
                    "fund_code": "013286",
                    "fund_name": "富国上证综指ETF联接C",
                    "category": "broad_beta",
                    "sub_track": "cn_broad",
                    "latest_nav": 1.807,
                    "market_value": 9487.13,
                    "unrealized_pnl": -512.87,
                    "unrealized_pnl_pct": -5.13,
                    "monthly_dca_cny": 0,
                    "mgmt_fee_pct": 0.5,
                    "custody_fee_pct": 0.1,
                    "sales_service_fee_pct": 0.4,
                    "pe_ttm": 13.8,
                    "pe_percentile_10y": 48,
                    "lots": [
                        {"confirmed_date": "2026-04-10", "direction": "purchase", "shares": 5249.34, "amount": 10000},
                    ],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "portfolio.json"
            fixture.write_text(json.dumps(portfolio, ensure_ascii=False), encoding="utf-8")

            conservative = subprocess.run(
                [
                    sys.executable,
                    str(REBALANCER_SCRIPT),
                    "rebalance",
                    "--portfolio-json",
                    str(fixture),
                    "--mode",
                    "hold_underwater_stop_dca",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            c_data = json.loads(conservative.stdout)
            self.assertTrue(c_data["asset_screener_delegated"])

            fifo_017811 = next(w for w in c_data["fifo_windows"] if w["code"] == "017811")
            self.assertAlmostEqual(fifo_017811["zero_fee_unlocked_shares"], 7913.41, places=2)
            self.assertAlmostEqual(fifo_017811["locked_shares"], 800.00, places=2)
            self.assertEqual(fifo_017811["next_fee_free_unlock_date"], "2026-11-28")

            overlap_types = {item["type"] for item in c_data["overlaps"]}
            self.assertEqual(
                overlap_types,
                {"TOP10_STOCK_COLLISION", "SAME_FUND_AC_DUPLICATE", "BROAD_BETA_REDUNDANCY"},
            )

            actions_by_code = {a["code"]: a["action"] for a in c_data["rebalance_plan"]["holding_actions"]}
            self.assertEqual(actions_by_code["017811"], "STOP_DCA")
            self.assertEqual(actions_by_code["005698"], "STOP_DCA")
            self.assertEqual(actions_by_code["024239"], "HOLD_WAIT_REBOUND")
            self.assertEqual(actions_by_code["020839"], "HOLD_KEEP_DCA")
            self.assertEqual(actions_by_code["025856"], "REDUCE_DCA")
            self.assertEqual(actions_by_code["025481"], "REDUCE_DCA")
            self.assertGreater(c_data["rebalance_plan"]["freed_monthly_cashflow_cny"], 5000.0)
            self.assertTrue(
                all(r["action"] == "ADD_NEW_DCA" for r in c_data["rebalance_plan"]["new_dca_recommendations"])
            )

            active = subprocess.run(
                [
                    sys.executable,
                    str(REBALANCER_SCRIPT),
                    "rebalance",
                    "--portfolio-json",
                    str(fixture),
                    "--mode",
                    "redeem_unlocked_fifo",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            a_data = json.loads(active.stdout)
            active_actions = {a["code"]: a["action"] for a in a_data["rebalance_plan"]["holding_actions"]}
            self.assertEqual(active_actions["017811"], "REDEEM_FIFO_UNLOCKED")
            self.assertEqual(active_actions["024239"], "REDEEM_FIFO_UNLOCKED")
            self.assertEqual(active_actions["013286"], "REDEEM_FIFO_UNLOCKED")

    def test_financial_cli_subprocess_adapter_with_hermetic_mock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fin_root = Path(tmp) / "Financial"
            cli_dir = fin_root / "cli"
            cli_dir.mkdir(parents=True)
            mock_cli = cli_dir / "finance"
            mock_cli.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "cmd = sys.argv[1]\n"
                "if cmd == 'funds':\n"
                "    print(json.dumps({\n"
                "        'total_holding_cost': 10000,\n"
                "        'total_holding_market_value': 10500,\n"
                "        'total_unrealized_pnl': 500,\n"
                "        'total_net_assets': 25000,\n"
                "        'holdings': [{\n"
                "            'fund_code': '020839',\n"
                "            'fund_name': '南方中证半导体产业指数A',\n"
                "            'remaining_shares': 2971.51,\n"
                "            'latest_nav': 2.74,\n"
                "            'market_value': 8151.45,\n"
                "            'unrealized_pnl': 651.45,\n"
                "            'unrealized_pnl_pct': 8.69,\n"
                "            'first_buy_date': '2025-10-01',\n"
                "            'last_trade_date': '2025-10-01'\n"
                "        }],\n"
                "        'confirmations': [{\n"
                "            'fund_code': '020839',\n"
                "            'trade_date': '2025-10-01',\n"
                "            'confirmed_date': '2025-10-02',\n"
                "            'direction': 'transfer_out',\n"
                "            'confirmed_shares': 2971.51,\n"
                "            'confirmed_amount': 7500.0,\n"
                "            'confirmed_nav': 2.524\n"
                "        }]\n"
                "    }))\n"
                "elif cmd == 'accounts':\n"
                "    print(json.dumps([{'id': 1, 'name': 'CMB', 'type': 'asset', 'current_balance': 14500.0}]))\n",
                encoding="utf-8",
            )
            mock_cli.chmod(0o755)

            proc = subprocess.run(
                [
                    sys.executable,
                    str(REBALANCER_SCRIPT),
                    "diagnose",
                    "--financial-root",
                    str(fin_root),
                    "--as-of",
                    date(2026, 11, 18).isoformat(),
                    "--format",
                    "markdown",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("All-Weather Barbell Allocation", proc.stdout)
            self.assertIn("offensive_growth", proc.stdout)


if __name__ == "__main__":
    unittest.main()
