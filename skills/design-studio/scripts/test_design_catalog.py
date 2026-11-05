import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("design_catalog.py")
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


if __name__ == "__main__":
    unittest.main()
