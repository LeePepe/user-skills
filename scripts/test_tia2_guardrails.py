import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FEEDBACK_GUARD = ROOT / "skills" / "team-lead" / "scripts" / "feedback_guard.py"
HERDR_DISPATCH = ROOT / "skills" / "team-lead" / "scripts" / "herdr-dispatch.sh"
HOWTO_GUARD = ROOT / "skills" / "howto" / "scripts" / "howto_guard.py"
HOWTO_FLOW = ROOT / "skills" / "howto" / "scripts" / "howto_flow.py"
RETRO_ENGINE = ROOT / "skills" / "retro-loop" / "scripts" / "retro_story_engine.py"
DIGEST_VAULT = ROOT / "skills" / "knowledge-digest" / "scripts" / "digest_vault.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class Tia2TeamLeadGuardrailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fg = _load_module("tia2_feedback_guard", FEEDBACK_GUARD)

    def _init_git_repo(self, root: Path) -> None:
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Test Bot"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "bot@example.com"], check=True)
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / "allowed.py").write_text("VALUE = 1\n", encoding="utf-8")
        (root / "src" / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-m", "initial"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "branch", "-M", "main"], check=True)
        subprocess.run(["git", "-C", str(root), "checkout", "-b", "feat/test-guard"], check=True, capture_output=True)

    def test_pre_review_check_passes_clean_owned_commit_and_valid_r10(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "wt"
            wt.mkdir()
            self._init_git_repo(wt)
            (wt / "src" / "allowed.py").write_text("VALUE = 2\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(wt), "commit", "-am", "update allowed"], check=True, capture_output=True)
            sha = subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "--short", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            brief = Path(tmp) / "TK1.md"
            brief.write_text(
                "# Brief: TK1\n- **Owned Paths**:\n  - `src/allowed.py`\n- **Verification**: `pytest`\n",
                encoding="utf-8",
            )
            receipt = Path(tmp) / "TK1-writer-r1.md"
            receipt.write_text(
                f"# R10 Receipt\n- Commit SHA: {sha}\n- Role / Model: writer / gpt-5.6-luna\n- V5 Verdict: PASS (`pytest`)\n",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    str(FEEDBACK_GUARD),
                    "pre-review-check",
                    "--worktree",
                    str(wt),
                    "--brief",
                    str(brief),
                    "--receipt",
                    str(receipt),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["changed_files"], ["src/allowed.py"])

    def test_pre_review_check_catches_outside_owned_paths_leaks_dirty_and_bad_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "wt"
            wt.mkdir()
            self._init_git_repo(wt)
            leaked_home = "/" + "Users/alice/secret.txt"
            leaked_tok = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
            (wt / "src" / "other.py").write_text(
                f'LEAK_PATH = "{leaked_home}"\nTOKEN = "{leaked_tok}"\n',
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(wt), "commit", "-am", "bad edit"], check=True, capture_output=True)
            (wt / "src" / "allowed.py").write_text("VALUE = 99\n", encoding="utf-8")

            brief = Path(tmp) / "TK1.md"
            brief.write_text("# Brief: TK1\n- **Owned Paths**:\n  - `src/allowed.py`\n", encoding="utf-8")
            receipt = Path(tmp) / "TK1-writer-r1.md"
            receipt.write_text("# Incomplete Receipt\nJust some prose without sha or model.\n", encoding="utf-8")

            report = self.fg.run_pre_review_check(
                worktree=wt,
                brief_path=brief,
                receipt_path=receipt,
            )
            self.assertFalse(report["ok"])
            codes = {v["code"] for v in report["violations"]}
            self.assertIn("dirty_tracked_worktree", codes)
            self.assertIn("outside_owned_paths", codes)
            self.assertIn("hardcoded_personal_path", codes)
            self.assertIn("leaked_secret_token", codes)
            self.assertIn("missing_receipt_commit_sha", codes)
            self.assertIn("missing_receipt_role_or_model", codes)
            self.assertIn("missing_receipt_verification", codes)

    def test_pre_review_check_rejects_empty_r10_labels_and_invalid_or_missing_base_ref(self):
        blank_r10 = self.fg.validate_r10_receipt("Commit SHA: no-commit\nRole / Model:\nV5:\n")
        self.assertEqual(len(blank_r10), 3, blank_r10)
        role_only_r10 = self.fg.validate_r10_receipt(
            "Commit SHA: 7a193be9b1879cdb82f7f56a150fd42648908e6b\nRole: writer\nV5 Verdict: PASS\n"
        )
        self.assertEqual(len(role_only_r10), 1, role_only_r10)
        self.assertIn("missing_receipt_role_or_model", role_only_r10[0])
        empty_model_slash = self.fg.validate_r10_receipt(
            "Commit SHA: 7a193be9b1879cdb82f7f56a150fd42648908e6b\nRole / Model: writer /\nV5 Verdict: PASS\n"
        )
        self.assertEqual(len(empty_model_slash), 1, empty_model_slash)
        self.assertIn("missing_receipt_role_or_model", empty_model_slash[0])
        separate_rm_ok = self.fg.validate_r10_receipt(
            "Commit SHA: 7a193be9b1879cdb82f7f56a150fd42648908e6b\nRole: writer\nModel: gpt-5.6-luna\nV5 Verdict: PASS\n"
        )
        self.assertEqual(separate_rm_ok, [])

        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp) / "wt"
            wt.mkdir()
            self._init_git_repo(wt)
            (wt / "src" / "other.py").write_text("OTHER = 2\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(wt), "commit", "-am", "out of scope"], check=True, capture_output=True)
            sha = subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "--short", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            brief = Path(tmp) / "TK1.md"
            brief.write_text("# Brief: TK1\n- **Owned Paths**:\n  - `src/allowed.py`\n", encoding="utf-8")
            receipt = Path(tmp) / "TK1-writer-r1.md"
            receipt.write_text(
                f"# R10\n- Commit SHA: {sha}\n- Role / Model: writer / gpt-5.6-luna\n- V5 Verdict: PASS\n",
                encoding="utf-8",
            )
            bad_base = self.fg.run_pre_review_check(
                worktree=wt,
                brief_path=brief,
                receipt_path=receipt,
                base_ref="nonexistent-base-ref-xyz",
            )
            self.assertFalse(bad_base["ok"])
            self.assertIn("invalid_base_ref", {v["code"] for v in bad_base["violations"]})

            single_wt = Path(tmp) / "single"
            single_wt.mkdir()
            subprocess.run(["git", "init", str(single_wt)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(single_wt), "config", "user.name", "Bot"], check=True)
            subprocess.run(["git", "-C", str(single_wt), "config", "user.email", "bot@example.com"], check=True)
            (single_wt / "only.py").write_text("X = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(single_wt), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(single_wt), "commit", "-m", "root"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(single_wt), "branch", "-M", "main"], check=True)
            no_base = self.fg.run_pre_review_check(worktree=single_wt, brief_path=brief, receipt_path=receipt)
            self.assertFalse(no_base["ok"])
            self.assertIn("unresolvable_base_ref", {v["code"] for v in no_base["violations"]})

    def test_herdr_dispatch_injects_opt_homebrew_baseline_add_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_herdr = tmp_path / "herdr"
            fake_herdr.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "args = sys.argv[1:]\n"
                "if args[:2] == ['pane', 'split']:\n"
                "    print(json.dumps({'result': {'pane': {'pane_id': 'w1:p2'}}}))\n"
                "elif args[:2] == ['agent', 'start']:\n"
                "    open(os.environ['CAPTURE_START'], 'w').write(json.dumps(args))\n"
                "    print(json.dumps({'ok': True}))\n"
                "elif args[:2] == ['agent', 'get']:\n"
                "    print(json.dumps({'interactive_ready': True, 'agent_status': 'working'}))\n"
                "elif args[:2] == ['agent', 'prompt']:\n"
                "    print(json.dumps({'ok': True}))\n",
                encoding="utf-8",
            )
            fake_herdr.chmod(0o755)
            capture = tmp_path / "start_args.json"
            env = {
                **os.environ,
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "HERDR_ENV": "1",
                "HERDR_PANE_ID": "w1:p1",
                "HERDR_MOCK_MAGPIE_ONLINE": "0",
                "HERDR_PROMPT_IDLE_GRACE": "0",
                "MOCK_HERDR_LOG": str(capture),
                "CAPTURE_START": str(capture),
            }
            proc = subprocess.run(
                [
                    str(HERDR_DISPATCH),
                    "--name",
                    "tia2-test-writer-r1",
                    "--kind",
                    "copilot",
                    "--cwd",
                    str(tmp_path),
                    "--prompt",
                    "Read brief",
                    "--role",
                    "writer",
                ],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            started = json.loads(capture.read_text(encoding="utf-8"))
            self.assertIn("/opt/homebrew", started)
            for required in (
                str(tmp_path),
                str(tmp_path),
                str(Path.home() / "Development" / "user-skills"),
                str(Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "AIDashSync"),
            ):
                self.assertIn(required, started)

    def test_tia14_section_interactivity_gate_accepts_state_and_rejects_placeholder(self):
        gate = ROOT / "skills" / "my-designer" / "scripts" / "verify_design_cases.py"
        good = 'struct Good: View { @State private var open = false; var body: some View { DisclosureGroup("More", isExpanded: $open) { Text("x") } } }'
        bad = 'struct Bad: View { var body: some View { RoundedRectangle(cornerRadius: 8).fill(.blue) } }'
        accepted = subprocess.run([sys.executable, str(gate), "--check-section-interactivity", "<snippet>", "--snippet", good], capture_output=True, text=True)
        rejected = subprocess.run([sys.executable, str(gate), "--check-section-interactivity", "<snippet>", "--snippet", bad], capture_output=True, text=True)
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
        self.assertEqual(json.loads(rejected.stdout)["status"], "REJECT_STATIC_PLACEHOLDER")

    def test_tia14_swiftui_environment_and_retry_guards_have_positive_and_negative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            swift = root / "Screen.swift"
            swift.write_text("@Environment(AppNavigation.self) var nav\\n@EnvironmentObject var old: AppNavigation\\nlet c = UIColor.red\\ncase .error: Text(\\\"failed\\\")\\n", encoding="utf-8")
            bad = self.fg.check_swiftui_and_error_retry_guards(root, ["Screen.swift"])
            codes = {item["code"] for item in bad}
            self.assertIn("swiftui_environment_or_macos_incompat", codes)
            self.assertIn("missing_error_state_retry_action", codes)
            swift.write_text("#if os(iOS)\\nlet c = UIColor.red\\n#endif\\ncase .error: Button(\\\"Retry\\\") {}\\n", encoding="utf-8")
            self.assertEqual(self.fg.check_swiftui_and_error_retry_guards(root, ["Screen.swift"]), [])


class Tia2HowtoGuardrailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hg = _load_module("tia2_howto_guard", HOWTO_GUARD)

    def test_valid_recipe_passes_5tuple_nodes_and_redlines(self):
        text = (
            "- **Inbound**: Manual Pulse from Owner\n"
            "- **Internal**: Filter unread items via Layer 0 watermark\n"
            "- **Calls**: I2, I3, I7, E4a, O4 (see TK-09)\n"
            "- **PointsTo**: I5 Notify User\n"
            "- **Trigger & Notify**: Daily Pulse + P1 receipt; knowledge-vault 永不自动同步 design-studio; save-tool 零执行\n"
        )
        res = self.hg.validate_recipe(text)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["nodes"]["unknown_nodes"], [])
        self.assertEqual(res["nodes"]["unknown_broken_edges"], [])

    def test_invalid_recipe_fails_missing_tuple_unknown_node_and_vault_violation(self):
        bad_text = (
            "- **Inbound**: User request\n"
            "- **Calls**: E99 and TK-42\n"
            "- Note: knowledge-vault 自动同步到 design-studio，并且 save-tool 直接运行脚本。\n"
        )
        res = self.hg.validate_recipe(bad_text)
        self.assertFalse(res["ok"])
        codes = {v["code"] for v in res["violations"]}
        self.assertIn("missing_5tuple_dimensions", codes)
        self.assertIn("unknown_howto_nodes", codes)
        self.assertIn("unknown_broken_edges", codes)
        self.assertIn("vault_isolation_violation", codes)
        self.assertIn("save_tool_zero_exec_violation", codes)

    def test_clause_scoped_negation_and_strict_canonical_catalog_requirement(self):
        mixed_text = (
            "- **Inbound**: User\n- **Internal**: Route\n- **Calls**: E1\n- **PointsTo**: I5\n"
            "- **Trigger & Notify**: knowledge-vault 永不自动同步 design-studio; save-tool 直接运行脚本\n"
        )
        mixed_res = self.hg.validate_recipe(mixed_text)
        self.assertFalse(mixed_res["ok"])
        mixed_codes = [v["code"] for v in mixed_res["violations"]]
        self.assertIn("save_tool_zero_exec_violation", mixed_codes)
        self.assertNotIn("vault_isolation_violation", mixed_codes)

        en_period_res = self.hg.validate_recipe(
            "Inbound Internal Calls E1 PointsTo I5 Trigger\n"
            "Never publish. save-tool 直接运行脚本\n"
            "Never publish. knowledge-vault 自动同步 design-studio\n"
        )
        self.assertFalse(en_period_res["ok"])
        en_codes = {v["code"] for v in en_period_res["violations"]}
        self.assertIn("save_tool_zero_exec_violation", en_codes)
        self.assertIn("vault_isolation_violation", en_codes)

        with tempfile.TemporaryDirectory() as tmp:
            missing_doc = Path(tmp) / "missing-how-to-nodes.md"
            missing_res = self.hg.validate_recipe(
                "Inbound Internal Calls E1 PointsTo I5 Trigger",
                nodes_doc=missing_doc,
            )
            self.assertFalse(missing_res["ok"])
            self.assertIn("missing_or_invalid_catalog", {v["code"] for v in missing_res["violations"]})

            narrow_doc = Path(tmp) / "narrow-nodes.md"
            narrow_doc.write_text(
                "| 节点 | 触发 |\n|---|---|\n| E1 Feature | Manual |\n| TK-01 | Edge |\n",
                encoding="utf-8",
            )
            narrow_res = self.hg.check_nodes("Calls E1 and E2 with TK-01 and TK-02", nodes_doc=narrow_doc)
            self.assertFalse(narrow_res["ok"])
            self.assertEqual(narrow_res["unknown_nodes"], ["E2"])
            self.assertEqual(narrow_res["unknown_broken_edges"], ["TK-02"])

    def test_howto_flow_cli_and_retro_engine_speckit_exclusion(self):
        flow = _load_module("tia2_howto_flow", HOWTO_FLOW)
        listed = flow.list_nodes()
        self.assertTrue(listed["ok"])
        self.assertGreaterEqual(listed["node_count"], 30)
        self.assertEqual(listed["broken_edge_count"], 16)

        looked_up = flow.lookup_howto("AIDash AI metrics")
        self.assertTrue(looked_up["ok"])
        self.assertIn("aidash-metrics", looked_up["target_skills"])

        scaffolded = flow.scaffold_tuple("fix bug list")
        self.assertTrue(scaffolded["ok"])
        self.assertTrue(scaffolded["guard_validation"]["ok"])

        engine = _load_module("tia2_retro_engine", RETRO_ENGINE)
        with tempfile.TemporaryDirectory() as tmp:
            fake_dev = Path(tmp) / "dev"
            speckit_pkg = fake_dev / "VoxPocket" / ".agents" / "skills" / "speckit-checklist"
            speckit_pkg.mkdir(parents=True)
            (speckit_pkg / "SKILL.md").write_text("\n".join(["line"] * 300), encoding="utf-8")
            inv = engine._discover_skill_inventory(ROOT / "skills", fake_dev)
            self.assertNotIn("speckit-checklist", inv)
            self.assertTrue(all(v["budget_ok"] for v in inv.values()))


class Tia2KnowledgeDigestGuardrailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dv = _load_module("tia2_digest_vault", DIGEST_VAULT)

    def test_precheck_card_validates_sections_index_and_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "knowledge-vault"
            (vault / "concepts").mkdir(parents=True)
            card = vault / "concepts" / "zero-knowledge.md"
            card.write_text(
                "---\n"
                "title: 零知识证明：不亮底牌的自证\n"
                "category: 计算机与网络\n"
                "updated: 2026-11-19\n"
                "---\n\n"
                "# 零知识证明\n\n"
                "## ⚡ 一句话本质（TL;DR）\n> 证明我知道秘密而不泄露秘密本身。\n\n"
                "## 🎯 核心痛点直答（Pain-Point Q&A）\n### Q1：为什么不用哈希比对？\n- 直答：防重放与字典猜测。\n\n"
                "## ✅ 立即可用清单（Actionable Checklist）\n1. 区分交互式与非交互式证明。\n",
                encoding="utf-8",
            )
            no_idx = self.dv.precheck_card(file_path=card, vault_dir=vault, require_index=True)
            self.assertFalse(no_idx["ok"])
            self.assertIn("missing_index_registration", {v["code"] for v in no_idx["violations"]})

            self.dv.archive_card(
                slug="zero-knowledge",
                category="计算机与网络",
                title="零知识证明：不亮底牌的自证",
                file_path=card,
                vault_dir=vault,
                updated_date="2026-11-19",
            )
            ok_res = self.dv.precheck_card(file_path=card, vault_dir=vault, require_index=True)
            self.assertTrue(ok_res["ok"], ok_res)

            bad_card = vault / "concepts" / "bad-card.md"
            bad_card.write_text(
                "---\ntitle: Bad\ncategory: 工程与设计\nupdated: 2026-11-19\nauto_sync_design_studio: true\n---\n\n# Bad\nOnly summary.\n",
                encoding="utf-8",
            )
            bad_res = self.dv.precheck_card(file_path=bad_card, vault_dir=vault)
            self.assertFalse(bad_res["ok"])
            bad_codes = {v["code"] for v in bad_res["violations"]}
            self.assertIn("missing_card_section", bad_codes)
            self.assertIn("design_studio_auto_sync_violation", bad_codes)

            mixed_card = vault / "concepts" / "mixed-clause.md"
            mixed_card.write_text(
                "---\n"
                "title: Mixed Clause\n"
                "category: 工程与设计\n"
                "updated: 2026-11-19\n"
                "---\n\n"
                "# Mixed\n\n"
                "## ⚡ 一句话本质（TL;DR）\n> Do not publish. Auto-sync to design-studio.\n\n"
                "## 🎯 核心痛点直答（Pain-Point Q&A）\n### Q1？\n- 直答：A。\n\n"
                "## ✅ 立即可用清单（Actionable Checklist）\n1. Check.\n",
                encoding="utf-8",
            )
            mixed_card_res = self.dv.precheck_card(file_path=mixed_card, vault_dir=vault)
            self.assertFalse(mixed_card_res["ok"])
            self.assertIn("design_studio_auto_sync_violation", {v["code"] for v in mixed_card_res["violations"]})

    def test_reconcile_state_repairs_learned_ledger_drift_in_state_and_proposals(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "concepts").mkdir(parents=True)
            ledger_path = vault / "topic-ledger.json"
            ledger_path.write_text(
                json.dumps(
                    {
                        "schema_version": "personal-topic-ledger/v1",
                        "topics": [
                            {
                                "topic_key": "zk-rollup-proof",
                                "source_id": "zk-rollup",
                                "source_uri": "https://github.com/example/zk-rollup",
                                "source_fingerprint": "sha256:abc123",
                                "question": "如何用零知识证明压缩 L2 状态转换验证开销？",
                                "learning_state": "learned",
                                "revision": 3,
                                "aliases": ["zk-rollup"],
                                "coverage_refs": ["concepts/zk-rollup-proof.md"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            state_dir = Path(tmp) / "state"
            state_dir.mkdir(parents=True)
            proposals_path = state_dir / "proposals.jsonl"
            state_path = state_dir / "learning-topics-state.json"
            proposals_path.write_text(
                json.dumps(
                    {
                        "id": "learning-topic-zk-rollup-proof",
                        "ts": "2026-11-18T08:00:00Z",
                        "created_at": "2026-11-18T08:00:00Z",
                        "proposed_on": "2026-11-18",
                        "agent": "knowledge-digest",
                        "title": "ZK Rollup Proof",
                        "detail": "如何用零知识证明压缩 L2 状态转换验证开销？",
                        "priority": "P2",
                        "status": "pending",
                        "ref": "learning-topic://zk-rollup-proof",
                        "topic_key": "zk-rollup-proof",
                        "learning_state": "proposed",
                        "revision": 1,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            state_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "updated_at": "2026-11-18T08:00:00Z",
                        "topics": {
                            "zk-rollup-proof": {
                                "topic_key": "zk-rollup-proof",
                                "learning_state": "proposed",
                                "revision": 1,
                            }
                        },
                        "consumed_events": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            check_only = self.dv.reconcile_learning_state(
                vault_dir=vault,
                proposals_path=proposals_path,
                state_path=state_path,
                topic_ledger_path=ledger_path,
                apply=False,
            )
            self.assertFalse(check_only["ok"])
            self.assertEqual(check_only["drift_count"], 1)

            repaired = self.dv.reconcile_learning_state(
                vault_dir=vault,
                proposals_path=proposals_path,
                state_path=state_path,
                topic_ledger_path=ledger_path,
                apply=True,
            )
            self.assertTrue(repaired["ok"])
            self.assertEqual(repaired["reconciled"], ["zk-rollup-proof"])

            after_check = self.dv.reconcile_learning_state(
                vault_dir=vault,
                proposals_path=proposals_path,
                state_path=state_path,
                topic_ledger_path=ledger_path,
                apply=False,
            )
            self.assertTrue(after_check["ok"])
            self.assertEqual(after_check["drift_count"], 0)


if __name__ == "__main__":
    unittest.main()
