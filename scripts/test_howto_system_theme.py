from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOWTO_FLOW = ROOT / "skills" / "howto" / "scripts" / "howto_flow.py"
HOWTO_GUARD = ROOT / "skills" / "howto" / "scripts" / "howto_guard.py"
HOWTO_SKILL_MD = ROOT / "skills" / "howto" / "SKILL.md"
HOWTO_NODES_MD = ROOT / "skills" / "workflow-hub" / "references" / "how-to-nodes.md"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class HowtoSystemThemeAndGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.flow = _load_module("test_howto_flow_apple_theme", HOWTO_FLOW)
        cls.guard = _load_module("test_howto_guard_apple_theme", HOWTO_GUARD)

    def test_semantic_catalog_restored_and_graph_neighbors_render(self) -> None:
        catalog = self.flow.parse_howto_catalog()
        self.assertGreaterEqual(catalog["node_count"], 30)
        self.assertEqual(catalog["broken_edge_count"], 16)
        for node in catalog["nodes"]:
            self.assertNotEqual(node["title"], node["id"], f"Node {node['id']} missing semantic title")
            self.assertNotIn("node-owned contract", node["inbound_internal"])
            self.assertNotIn("child evidence", node["calls"])
            self.assertNotIn("parent receipt", node["points_to"])
        for tk in catalog["broken_edges"]:
            self.assertNotEqual(tk["edge"], "restore declared edge")

        graph_proc = subprocess.run(
            [sys.executable, str(HOWTO_FLOW), "--graph"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(graph_proc.returncode, 0, graph_proc.stderr)
        self.assertIn("How-To 5-Stage Macro Lifecycle Pipeline", graph_proc.stdout)
        self.assertIn("1. 信号与入口", graph_proc.stdout)
        self.assertIn("5. 个人数据飞轮", graph_proc.stdout)

        graph_json = self.flow.render_macro_graph()
        self.assertTrue(graph_json["ok"])
        self.assertEqual(len(graph_json["stages"]), 5)
        self.assertGreater(graph_json["calls_edge_count"], 25)
        self.assertGreater(graph_json["points_to_edge_count"], 40)

        nb_proc = subprocess.run(
            [sys.executable, str(HOWTO_FLOW), "--neighbors", "E2"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(nb_proc.returncode, 0, nb_proc.stderr)
        self.assertIn("Local Neighborhood Card: E2 Bug List", nb_proc.stdout)
        self.assertIn("Upstream (被谁调用/流转自)", nb_proc.stdout)
        self.assertIn("E4a Triage Bug", nb_proc.stdout)
        self.assertIn("TK-02", nb_proc.stdout)

        bad_nb = subprocess.run(
            [sys.executable, str(HOWTO_FLOW), "--neighbors", "E999"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(bad_nb.returncode, 1)
        self.assertFalse(json.loads(bad_nb.stdout)["ok"])

    def test_lookup_surfaces_apple_system_appearance_without_web_css_framing(self) -> None:
        for query in (
            "dark mode system theme",
            "system theme",
            "designtheme",
            "nsappearance",
            "uitraitcollection",
            "暗黑模式 系统外观",
            "平行主题",
        ):
            with self.subTest(query=query):
                proc = subprocess.run(
                    [sys.executable, str(HOWTO_FLOW), "--lookup", query, "--json"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertTrue(payload["ok"])
                node_ids = [node["id"] for node in payload["matched_nodes"][:4]]
                self.assertEqual(node_ids, ["E7", "P7", "E4", "E9"])
                self.assertIn("my-designer", payload["target_skills"])
                self.assertIn("skill-contract", payload["target_skills"])
                guidance = payload.get("guidance", "")
                self.assertIn("Apple OS-Native Dynamic System Appearance", guidance)
                self.assertIn("NSAppearance", guidance)
                self.assertIn("UITraitCollection", guidance)
                self.assertIn("@Environment(\\.colorScheme)", guidance)
                self.assertNotIn("prefers-color-scheme", guidance)

    def test_validate_recipe_rejects_custom_and_parallel_theme_engines_with_3line_pointer(self) -> None:
        bad_recipes = (
            (
                "- **Inbound**: Product UI settings request\n"
                "- **Internal**: Add `.designTheme` modifier and persist theme state machine\n"
                "- **Calls**: E7, P7, E4, E9\n"
                "- **PointsTo**: G1, I5\n"
                "- **Trigger & Notify**: Manual P0/P1\n"
            ),
            (
                "- **Inbound**: iOS Settings\n"
                "- **Internal**: Persist @AppStorage(\"themeMode\") with an in-app theme toggle\n"
                "- **Calls**: E7, E4, E9\n"
                "- **PointsTo**: G1\n"
                "- **Trigger & Notify**: Manual P1\n"
            ),
            (
                "- **Inbound**: macOS UI task\n"
                "- **Internal**: Build a Web-CSS-style parallel theme engine for dark mode\n"
                "- **Calls**: E7, E4, E9\n"
                "- **PointsTo**: I5\n"
                "- **Trigger & Notify**: Manual P1\n"
            ),
            (
                "- **Inbound**: 桌面端设置页改版\n"
                "- **Internal**: 自建应用内暗黑模式开关与平行主题状态机\n"
                "- **Calls**: E7, E4, E9\n"
                "- **PointsTo**: I5\n"
                "- **Trigger & Notify**: Manual P1\n"
            ),
            (
                "- **Inbound**: UI migration\n"
                "- **Internal**: Never use .designTheme, but add an in-app theme switcher\n"
                "- **Calls**: E7, P7, E4, E9\n"
                "- **PointsTo**: G1\n"
                "- **Trigger & Notify**: Manual P1\n"
            ),
            (
                "- **Inbound**: UI cleanup\n"
                "- **Internal**: Remove .designTheme and wire an in-app theme toggle\n"
                "- **Calls**: E7, P7, E4, E9\n"
                "- **PointsTo**: G1\n"
                "- **Trigger & Notify**: Manual P1\n"
            ),
        )
        for recipe in bad_recipes:
            with self.subTest(recipe=recipe):
                proc = subprocess.run(
                    [sys.executable, str(HOWTO_GUARD), "validate-recipe", "--text", recipe],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 1, proc.stdout)
                payload = json.loads(proc.stdout)
                self.assertFalse(payload["ok"])
                codes = {v["code"] for v in payload["violations"]}
                self.assertIn("custom_theme_engine_violation", codes)
                stderr_lines = [ln for ln in proc.stderr.strip().splitlines() if ln.strip()]
                self.assertGreaterEqual(len(stderr_lines), 1)
                self.assertLessEqual(len(stderr_lines), 3, proc.stderr)
                self.assertTrue(stderr_lines[-1].startswith("Reference:"), proc.stderr)

    def test_validate_recipe_allows_apple_native_appearance_and_prohibition_clauses(self) -> None:
        good_recipes = (
            (
                "- **Inbound**: Apple UI appearance update\n"
                "- **Internal**: Follow Apple OS system appearance via NSAppearance / UITraitCollection / "
                "@Environment(\\.colorScheme) + semantic dynamic tokens; never build custom .designTheme, "
                "in-app theme switchers, or Web-CSS-style parallel theme engines.\n"
                "- **Calls**: E7, P7, E4, E9\n"
                "- **PointsTo**: G1, I5\n"
                "- **Trigger & Notify**: Manual P0/P1\n"
            ),
            (
                "- **Inbound**: Legacy cleanup\n"
                "- **Internal**: Remove .designTheme plumbing and use @Environment(\\.colorScheme) instead of custom theme switcher.\n"
                "- **Calls**: E7, E4, E9\n"
                "- **PointsTo**: G1\n"
                "- **Trigger & Notify**: Manual P1\n"
            ),
            HOWTO_SKILL_MD.read_text(encoding="utf-8"),
            HOWTO_NODES_MD.read_text(encoding="utf-8"),
        )
        for text in good_recipes:
            with self.subTest(sample=text[:60]):
                report = self.guard.validate_recipe(text)
                self.assertTrue(report["ok"], report)

        scaffold = self.flow.scaffold_tuple("replace .designTheme with system appearance")
        self.assertTrue(scaffold["ok"], scaffold)
        self.assertTrue(scaffold["guard_validation"]["ok"])

    def test_doc_budgets_and_no_web_css_parallel_theme_assumption(self) -> None:
        skill_text = HOWTO_SKILL_MD.read_text(encoding="utf-8")
        nodes_text = HOWTO_NODES_MD.read_text(encoding="utf-8")
        self.assertLessEqual(len(skill_text.splitlines()), 180)
        self.assertLessEqual(len(skill_text.encode("utf-8")), 12 * 1024)
        self.assertLessEqual(len(nodes_text.splitlines()), 160)
        self.assertLessEqual(len(nodes_text.encode("utf-8")), 12 * 1024)
        self.assertNotIn("prefers-color-scheme", skill_text)
        self.assertNotIn("prefers-color-scheme", nodes_text)


if __name__ == "__main__":
    unittest.main()
