import importlib.util
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("design_catalog.py")
SPEC = importlib.util.spec_from_file_location("design_catalog", SCRIPT)
DESIGN_CATALOG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DESIGN_CATALOG)
CATALOG = SCRIPT.parents[1] / "references" / "catalog.json"
MARKDOWN = SCRIPT.parents[1] / "references" / "toolchain-20-matrix.md"
TEMP_DIR_ROOT = SCRIPT.parents[3]


def run(*args, **kwargs):
    return subprocess.run([sys.executable, str(SCRIPT), *args], text=True,
                          capture_output=True, **kwargs)


class DesignCatalogTests(unittest.TestCase):
    def test_repository_schema_and_render(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertIsInstance(catalog, dict)
        self.assertEqual(len(catalog["tools"]), 20)
        self.assertEqual(len(catalog["patterns"]), 11)
        self.assertEqual(run("render", "--check").returncode, 0)

    def test_search_prompts(self):
        for query, expected in (
            ("揭角", "MP-001"),
            ("滑动确认", "MP-002"),
            ("拉绳", "MP-003"),
            ("图表联动筛选", "IP-001"),
            ("无结果恢复", "IP-004"),
        ):
            result = json.loads(run("search", "--query", query).stdout)
            self.assertTrue(result["has_duplicate"])
            self.assertEqual(result["matches"][0]["id"], expected)

    def test_duplicate_and_temp_add(self):
        with tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT) as directory:
            catalog = Path(directory) / "catalog.json"
            markdown = Path(directory) / "matrix.md"
            catalog.write_text(CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
            markdown.write_text(MARKDOWN.read_text(encoding="utf-8"), encoding="utf-8")
            args = ["add-pattern", "--id", "MP-NEW", "--name", "卡片揭角", "--category",
                    "gesture-physical", "--platforms", "web,mobile,shared", "--surface-modes", "Operate",
                    "--motion-tier", "M2", "--source-prompt-zh", "卡片揭角", "--canonical-prompt",
                    "Peel a card corner.", "--state-machine", "idle -> drag -> settle", "--web-impl",
                    "Motion", "--mobile-impl", "SwiftUI", "--guardrails", "Reduce Motion", "--catalog",
                    str(catalog), "--markdown", str(markdown)]
            self.assertEqual(run(*args).returncode, 2)
            args[4] = "全新拖拽反馈"
            self.assertEqual(run(*args).returncode, 0)
            ip_args = [
                "add-pattern", "--id", "IP-NEW", "--name", "卡片联动统计", "--category",
                "data-linkage", "--platforms", "web,mobile,shared", "--surface-modes", "Operate,Read",
                "--motion-tier", "M1", "--source-prompt-zh", "点卡片后联动统计",
                "--canonical-prompt", "Click a card to update linked metrics.",
                "--state-machine", "idle -> select -> sync", "--web-impl", "shared state",
                "--mobile-impl", "SwiftUI", "--guardrails", "derived metrics", "--catalog",
                str(catalog), "--markdown", str(markdown),
            ]
            self.assertEqual(run(*ip_args).returncode, 0)
            self.assertIn("MP-NEW", markdown.read_text(encoding="utf-8"))
            self.assertIn("IP-NEW", markdown.read_text(encoding="utf-8"))

    def test_new_categories_and_prefix_validation(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(
            {item["category"] for item in catalog["patterns"] if item["id"].startswith("IP-")},
            {"data-linkage", "filter-sort-recovery", "master-detail-flow"},
        )
        self.assertTrue(all(item["id"].startswith(("MP-", "IP-")) for item in catalog["patterns"]))

    def test_usage_and_recommendation(self):
        with tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT) as directory:
            catalog = Path(directory) / "catalog.json"
            markdown = Path(directory) / "matrix.md"
            catalog.write_text(CATALOG.read_text(encoding="utf-8"), encoding="utf-8")
            markdown.write_text(MARKDOWN.read_text(encoding="utf-8"), encoding="utf-8")
            common = ["--catalog", str(catalog), "--markdown", str(markdown)]
            self.assertEqual(run("record-use", "--id", "IP-001", "--context", "test",
                                 "--rating", "liked", *common).returncode, 0)
            self.assertEqual(run("record-use", "--id", "MP-002", "--context", "test",
                                 "--rating", "disliked", *common).returncode, 0)
            result = json.loads(run("recommend", "--kind", "pattern", *common).stdout)
            ids = [item["id"] for item in result["recommendations"]]
            self.assertEqual(ids[0], "IP-001")
            self.assertNotIn("MP-002", ids)


class IngestTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=TEMP_DIR_ROOT)
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.catalog = self.directory / "catalog.json"
        self.markdown = self.directory / "matrix.md"
        self.catalog.write_bytes(CATALOG.read_bytes())
        self.markdown.write_bytes(MARKDOWN.read_bytes())

    def ingest(self, *args):
        return run("ingest", *args, "--catalog", str(self.catalog),
                   "--markdown", str(self.markdown))

    def snapshot(self):
        return self.catalog.read_bytes(), self.markdown.read_bytes()

    def test_classification_and_dry_run(self):
        before = self.snapshot()
        for text, category, prefix in (
            ("Link chart segments with a summary", "data-linkage", "IP"),
            ("图表联动统计", "data-linkage", "IP"),
            ("Search and sort with scoped recovery", "filter-sort-recovery", "IP"),
            ("搜索无结果时保留排序", "filter-sort-recovery", "IP"),
            ("Select a record for the detail pane", "master-detail-flow", "IP"),
            ("主从详情保持位置", "master-detail-flow", "IP"),
            ("Responsive grid spacing and alignment", "layout-craft", "IP"),
            ("容器布局间距对齐", "layout-craft", "IP"),
            ("Swipe a spring-loaded card", "gesture-physical", "MP"),
            ("揭角手势带弹簧回位", "gesture-physical", "MP"),
            ("Confirm destructive changes with undo", "confirmation-safety", "MP"),
            ("删除前确认并可撤销", "confirmation-safety", "MP"),
            ("Scrub the navigation toolbar", "spatial-navigation", "MP"),
            ("工具栏滑选导航", "spatial-navigation", "MP"),
            ("Like reaction badge feedback", "micro-feedback-social", "MP"),
            ("点赞表情反馈", "micro-feedback-social", "MP"),
            ("Drag and drop files on a drop target", "drag-drop-choreography", "MP"),
            ("拖入文件夹时显示拖放落点", "drag-drop-choreography", "MP"),
            ("An unrecognized visual idea", "layout-craft", "IP"),
        ):
            with self.subTest(text=text):
                result = self.ingest("--text", text, "--dry-run", "--force")
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(data["classification"]["category"], category)
                self.assertEqual(data["classification"]["prefix"], prefix)
                self.assertEqual(data["pattern"]["id"], "IP-006" if prefix == "IP" else "MP-007")
                self.assertEqual(data["pattern"]["motion_tier"], "M1" if prefix == "IP" else "M2")
                self.assertTrue(data["dry_run"])
                self.assertFalse(data["created"])
                self.assertEqual(data["actions"], [])
        self.assertEqual(self.snapshot(), before)

    def test_numbering_uses_maximum_per_prefix_and_persists_render(self):
        catalog = json.loads(self.catalog.read_text(encoding="utf-8"))
        extra = dict(catalog["patterns"][-1], id="IP-042")
        catalog["patterns"].insert(0, extra)
        self.catalog.write_text(json.dumps(catalog), encoding="utf-8")
        for text, expected in (("Responsive editorial grid", "IP-043"),
                               ("Swipe an elastic paper edge", "MP-007"),
                               ("Align a compact calendar layout", "IP-044")):
            with self.subTest(expected=expected):
                result = self.ingest("--text", text)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertTrue(data["created"])
                self.assertFalse(data["dry_run"])
                self.assertEqual(data["duplicate_decision"], "unique")
                self.assertEqual(data["actions"], ["write_catalog", "render_markdown"])
                stored = json.loads(self.catalog.read_text(encoding="utf-8"))["patterns"][-1]
                self.assertEqual(stored, data["pattern"])
                self.assertEqual(stored["id"], expected)
                for field in ("source_prompt_zh", "canonical_prompt", "state_machine",
                              "web_implementation", "mobile_implementation", "token_guardrails"):
                    self.assertTrue(stored[field], field)
                self.assertIn(expected, self.markdown.read_text(encoding="utf-8"))
                check = run("render", "--check", "--catalog", str(self.catalog),
                            "--markdown", str(self.markdown))
                self.assertEqual(check.returncode, 0, check.stderr)

    def test_duplicate_lists_all_matches_and_force(self):
        catalog = json.loads(self.catalog.read_text(encoding="utf-8"))
        existing = next(item for item in catalog["patterns"] if item["id"] == "IP-001")
        catalog["patterns"].append(dict(existing, id="IP-099"))
        self.catalog.write_text(json.dumps(catalog), encoding="utf-8")
        before = self.snapshot()
        for flags in ([], ["--dry-run"]):
            blocked = self.ingest("--text", existing["source_prompt_zh"], *flags)
            self.assertEqual(blocked.returncode, 2, blocked.stderr)
            data = json.loads(blocked.stdout)
            self.assertEqual(data["duplicate_decision"], "blocked")
            self.assertEqual({item["id"] for item in data["duplicate_matches"]}, {"IP-001", "IP-099"})
            self.assertFalse(data["created"])
            self.assertEqual(self.snapshot(), before)
        preview = self.ingest("--text", existing["source_prompt_zh"], "--force", "--dry-run")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(json.loads(preview.stdout)["duplicate_decision"], "forced")
        self.assertEqual(self.snapshot(), before)
        forced = self.ingest("--text", existing["source_prompt_zh"], "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        data = json.loads(forced.stdout)
        self.assertEqual(data["duplicate_decision"], "forced")
        self.assertEqual(data["pattern"]["id"], "IP-100")
        self.assertTrue(data["created"])

    def test_duplicate_similarity_threshold(self):
        catalog = json.loads(self.catalog.read_text(encoding="utf-8"))
        base = catalog["patterns"][0]
        catalog["patterns"] = [dict(base, name="different name", aliases=[],
                                    source_prompt_zh="alpha beta gamma delta epsilon zeta",
                                    canonical_prompt="different canonical prompt")]
        self.catalog.write_text(json.dumps(catalog), encoding="utf-8")
        before = self.snapshot()
        for text, expected in (("alpha beta gamma delta epsilon theta", 2),
                               ("alpha eta beta theta gamma delta", 0)):
            result = self.ingest("--text", text, "--dry-run")
            self.assertEqual(result.returncode, expected, result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_split_and_no_split_batch_compact(self):
        prompt = "Context before\n- first linked chart\n2、second filter\n③ third detail\n(4) fourth layout\n**Fifth**: fifth gesture\nContext after"
        result = self.ingest("--text", prompt, "--no-jev", "--compact")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 5)
        self.assertEqual(len(json.loads(self.catalog.read_text(encoding="utf-8"))["patterns"]), 16)
        single = self.ingest("--text", "- first\n- second", "--no-split", "--no-jev", "--dry-run")
        self.assertEqual(single.returncode, 0, single.stderr)
        self.assertEqual(json.loads(single.stdout)["pattern"]["source_prompt_zh"], "- first\n- second")

    def test_mocked_jev_filter_category_and_motion(self):
        args = types.SimpleNamespace(no_jev=False, jev_url="http://unused", category=None, motion_tier=None)
        original = DESIGN_CATALOG.jev_result
        try:
            DESIGN_CATALOG.jev_result = lambda text, args: {
                "noul": 0.9, "is_design_pattern": True, "category": "data-linkage",
                "category_confidence": 0.9, "motion_tier": "M3", "motion_confidence": 0.9,
            }
            result = DESIGN_CATALOG.classify_segment("chart linkage", args)
            self.assertEqual(result[:2], ("data-linkage", "M3"))
            DESIGN_CATALOG.jev_result = lambda text, args: {"noul": 0.1, "is_design_pattern": True}
            self.assertIsNone(DESIGN_CATALOG.classify_segment("prose", args))
        finally:
            DESIGN_CATALOG.jev_result = original

    def test_image_ocr_injection_and_empty_error(self):
        completed = type("Completed", (), {"stdout": "中文 heading\nEnglish prompt\n"})()
        self.assertEqual(DESIGN_CATALOG.ocr_image("image.png", lambda *a, **k: completed),
                         "中文 heading\nEnglish prompt")
        empty = type("Completed", (), {"stdout": "  "})()
        with self.assertRaises(ValueError):
            DESIGN_CATALOG.ocr_image("image.png", lambda *a, **k: empty)

    def test_file_input_and_overrides(self):
        prompt = self.directory / "prompt.txt"
        text = "  面板布局\n  保留完整的需求与层级。  \n"
        prompt.write_text(text, encoding="utf-8")
        result = self.ingest("--file", str(prompt), "--id", "IP-123", "--category", "layout-craft",
                             "--platforms", " web, mobile ", "--surface-modes", "Read,Experience",
                             "--motion-tier", "M3")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["classification"]["source"], "override")
        item = data["pattern"]
        self.assertEqual(item["id"], "IP-123")
        self.assertEqual(item["source_prompt_zh"], text.strip())
        self.assertEqual(item["canonical_prompt"], "面板布局 保留完整的需求与层级。")
        self.assertEqual(item["platforms"], ["web", "mobile"])
        self.assertEqual(item["surface_modes"], ["Read", "Experience"])
        self.assertEqual(item["motion_tier"], "M3")
        self.assertEqual(data["paths"], {"catalog": str(self.catalog), "markdown": str(self.markdown)})
        self.assertTrue(data["created"])

    def test_invalid_input_and_overrides_never_write(self):
        before = self.snapshot()
        for args in (
            [], ["--text", "one", "--file", "other"],
            ["--text", "grid", "--category", "code-to-video"],
            ["--text", "grid", "--platforms", "desktop"],
            ["--text", "grid", "--platforms", ""],
            ["--text", "grid", "--surface-modes", "Unknown"],
            ["--text", "grid", "--motion-tier", "M5"],
            ["--text", "grid", "--id", "T-001"],
            ["--text", "grid", "--id", "IP-not-numeric"],
        ):
            with self.subTest(args=args):
                result = self.ingest(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("error:", result.stderr)
        for args in (
            ["--text", " \n "],
            ["--file", str(self.directory / "missing.txt")],
            ["--text", "grid", "--id", "MP-123"],
            ["--text", "grid", "--id", "IP-001", "--force"],
        ):
            with self.subTest(args=args):
                result = self.ingest(*args)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("error:", result.stderr)
        empty = self.directory / "empty.txt"
        empty.write_text("", encoding="utf-8")
        self.assertEqual(self.ingest("--file", str(empty)).returncode, 1)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
