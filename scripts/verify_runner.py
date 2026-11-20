#!/usr/bin/env python3
"""Tiered, parallel, and path-scoped verification runner for user-skills."""

from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
SKILL_CONTRACT_SCRIPT = SKILLS_DIR / "skill-contract" / "scripts" / "skill_contract.py"

UNITTEST_DISCOVERY_ROOTS: tuple[Path, ...] = (
    SKILLS_DIR / "code-review" / "scripts",
    SKILLS_DIR / "design-studio" / "scripts",
    REPO_ROOT / "scripts",
)

# Any modification outside isolated skills/<skill>/... triggers full --all verification.
FULL_RUN_PREFIXES: tuple[str, ...] = (
    "scripts/",
    ".github/",
    ".githooks/",
    ".claude-plugin/",
    "AGENTS.md",
    "README.md",
)

RAN_TESTS_RE = re.compile(r"Ran\s+(\d+)\s+tests?\s+in\s+([0-9.]+)s")
# Empirical priority weights so heavy subprocess-spawning suites launch in Wave Slot 0 first.
HEAVY_SHARD_WEIGHTS: dict[str, float] = {
    "skill-contract-eval-all": 50.0,
    "skills/design-studio/scripts/test_design_catalog.py::AppNavigationBehaviorTests": 45.0,
    "scripts/test_verify_skills.py::VerifySkillsTests": 42.0,
    "scripts/test_verify_skills.py::SkillContractV2Tests": 40.0,
    "scripts/test_loop_local_hygiene_patrol.py": 35.0,
    "skills/design-studio/scripts/test_design_catalog.py::IngestTests": 32.0,
    "scripts/test_gateway_dispatch.py": 30.0,
    "skills/design-studio/scripts/test_design_catalog.py::PromptAndRenderedGuardTests": 28.0,
    "skills/design-studio/scripts/test_design_catalog.py::MultiplatformGalleryRegressionTests": 26.0,
    "scripts/test_goal_queue.py": 24.0,
    "skills/design-studio/scripts/test_design_catalog.py::TaxonomyV2AndVaultTests": 22.0,
    "skills/design-studio/scripts/test_design_catalog.py::DesignCatalogTests": 20.0,
    "scripts/test_m2_router.py": 18.0,
    "scripts/test_goal_supervisor_and_jev.py": 17.0,
    "scripts/test_verify_skills.py::JevGateTests": 16.0,
}
SKILL_PATH_RE_1 = re.compile(r"skills[\"']?\s*/\s*[\"']([A-Za-z0-9_-]+)[\"']")
SKILL_PATH_RE_2 = re.compile(r"skills/([A-Za-z0-9_-]+)")
SKILL_PATH_RE_3 = re.compile(r"SKILLS_DIR\s*/\s*[\"']([A-Za-z0-9_-]+)")


@dataclass(frozen=True)
class TaskSpec:
    """One executable verification shard."""

    task_id: str
    tier: int
    label: str
    cmd: tuple[str, ...]
    expected_tests: int = 0
    weight: float = 1.0


@dataclass(frozen=True)
class TaskResult:
    """Outcome of executing one TaskSpec."""

    spec: TaskSpec
    returncode: int
    duration_sec: float
    tests_ran: int
    stdout: str
    stderr: str


def known_skills(repo_root: Path = REPO_ROOT) -> set[str]:
    skills_dir = repo_root / "skills"
    if not skills_dir.is_dir():
        return set()
    return {
        entry.name
        for entry in skills_dir.iterdir()
        if entry.is_dir() and (entry / "SKILL.md").is_file()
    }


def _git_lines(args: Sequence[str], repo_root: Path) -> list[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def detect_changed_files(repo_root: Path = REPO_ROOT, base_ref: str | None = None) -> list[str]:
    """Collect working-tree, staged, untracked, and branch-ahead changed file paths."""
    changed: set[str] = set()
    changed.update(_git_lines(["diff", "--name-only"], repo_root))
    changed.update(_git_lines(["diff", "--cached", "--name-only"], repo_root))
    changed.update(_git_lines(["ls-files", "--others", "--exclude-standard"], repo_root))

    if not changed:
        candidates = [base_ref] if base_ref else ["origin/main", "main"]
        for ref in candidates:
            if not ref:
                continue
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            mb = subprocess.run(
                ["git", "merge-base", "HEAD", ref],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if mb.returncode == 0 and mb.stdout.strip():
                base_sha = mb.stdout.strip()
                head_sha = _git_lines(["rev-parse", "HEAD"], repo_root)
                if head_sha and head_sha[0] != base_sha:
                    changed.update(_git_lines(["diff", "--name-only", f"{base_sha}...HEAD"], repo_root))
                break

    return sorted(changed)


def classify_scope(
    changed_files: Sequence[str],
    *,
    force_all: bool = False,
    repo_root: Path = REPO_ROOT,
) -> tuple[str, list[str], str]:
    """Return (scope_mode, target_skills, reason). scope_mode is 'all' or 'scoped'."""
    if force_all:
        return "all", [], "explicit --all flag"
    if not changed_files:
        return "all", [], "clean tree on base ref (full baseline)"

    skills = known_skills(repo_root)
    touched_skills: set[str] = set()
    for raw_path in changed_files:
        norm = raw_path.strip().lstrip("./")
        if not norm:
            continue
        if any(norm == p or norm.startswith(p) for p in FULL_RUN_PREFIXES):
            return "all", [], f"shared path touched ({norm})"
        parts = Path(norm).parts
        if len(parts) >= 2 and parts[0] == "skills":
            skill_name = parts[1]
            # Touching skill-contract's own engine affects global verification semantics
            if skill_name == "skill-contract" and len(parts) >= 3 and parts[2] == "scripts":
                return "all", [], "skill-contract engine modified"
            if skill_name in skills:
                touched_skills.add(skill_name)
            else:
                return "all", [], f"unregistered or deleted skill directory ({norm})"
        else:
            return "all", [], f"root path touched ({norm})"

    if not touched_skills:
        return "all", [], "no isolatable skill paths matched"
    return "scoped", sorted(touched_skills), f"isolated to {len(touched_skills)} skill(s)"


def _extract_skill_refs(source_text: str, valid_skills: set[str]) -> set[str]:
    refs = set(SKILL_PATH_RE_1.findall(source_text))
    refs |= set(SKILL_PATH_RE_2.findall(source_text))
    refs |= set(SKILL_PATH_RE_3.findall(source_text))
    return refs & valid_skills


def discover_all_test_files(repo_root: Path = REPO_ROOT) -> list[Path]:
    """Return every test_*.py file covered by the canonical discovery roots."""
    roots = (
        repo_root / "skills" / "code-review" / "scripts",
        repo_root / "skills" / "design-studio" / "scripts",
        repo_root / "scripts",
    )
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found.extend(sorted(root.glob("test_*.py")))
    return found


def parse_test_file_structure(
    test_file: Path,
    valid_skills: set[str],
    repo_root: Path = REPO_ROOT,
) -> tuple[dict[str, list[str]], set[str]]:
    """Return ({ClassName: [test_method, ...]}, referenced_skills) via AST."""
    source = test_file.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(test_file))
    class_methods: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [
                item.name
                for item in node.body
                if isinstance(item, ast.FunctionDef) and item.name.startswith("test_")
            ]
            if methods:
                class_methods[node.name] = methods

    refs = _extract_skill_refs(source, valid_skills)
    try:
        rel = test_file.relative_to(repo_root)
        if len(rel.parts) >= 2 and rel.parts[0] == "skills" and rel.parts[1] in valid_skills:
            refs.add(rel.parts[1])
    except ValueError:
        pass
    return class_methods, refs


def map_verify_skills_methods(
    verify_test_file: Path,
    valid_skills: set[str],
) -> tuple[list[str], dict[str, list[str]]]:
    """Partition scripts/test_verify_skills.py into universal vs per-skill dotted targets."""
    source = verify_test_file.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(verify_test_file))
    universal: list[str] = []
    by_skill: dict[str, list[str]] = {sk: [] for sk in valid_skills}

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        helpers = {
            item.name: "\n".join(lines[item.lineno - 1 : item.end_lineno])
            for item in node.body
            if isinstance(item, ast.FunctionDef) and not item.name.startswith("test_")
        }
        for item in node.body:
            if not (isinstance(item, ast.FunctionDef) and item.name.startswith("test_")):
                continue
            body = "\n".join(lines[item.lineno - 1 : item.end_lineno])
            for helper_name, helper_src in helpers.items():
                if f"self.{helper_name}" in body:
                    body += "\n" + helper_src
            refs = _extract_skill_refs(body, valid_skills)
            if node.name == "JevGateTests" and "my-designer" in valid_skills:
                refs.add("my-designer")
            elif node.name == "SkillContractV2Tests" and "skill-contract" in valid_skills:
                refs.add("skill-contract")
            dotted = f"test_verify_skills.{node.name}.{item.name}"
            if not refs:
                universal.append(dotted)
            else:
                for sk in refs:
                    by_skill[sk].append(dotted)

    return universal, by_skill


def _unittest_class_cmd(test_file: Path, class_name: str | None = None, methods: Sequence[str] = ()) -> tuple[str, ...]:
    start_dir = str(test_file.parent)
    stem = test_file.stem
    if methods:
        return (sys.executable, "-I", "-B", "-m", "unittest", *methods)
    if class_name:
        return (sys.executable, "-I", "-B", "-m", "unittest", f"{stem}.{class_name}")
    return (
        sys.executable,
        "-I",
        "-B",
        "-m",
        "unittest",
        "discover",
        "-s",
        start_dir,
        "-p",
        test_file.name,
    )


def build_execution_plan(
    *,
    scope_mode: str,
    target_skills: Sequence[str],
    jobs: int,
    repo_root: Path = REPO_ROOT,
) -> tuple[list[TaskSpec], list[TaskSpec]]:
    """Build (tier0_specs, parallel_wave_specs)."""
    skill_contract_py = str(repo_root / "skills" / "skill-contract" / "scripts" / "skill_contract.py")
    tier0: list[TaskSpec] = [
        TaskSpec("git-diff-check", 0, "git diff --check", ("git", "diff", "--check")),
        TaskSpec("git-diff-cached-check", 0, "git diff --cached --check", ("git", "diff", "--cached", "--check")),
        # Always run global S1/S2/S3 + Valve 1-4 audit in Tier 0 (<0.4s) to catch cross-skill link/fork issues immediately.
        TaskSpec(
            "skill-contract-audit",
            0,
            "skill_contract.py audit --all --strict",
            (sys.executable, skill_contract_py, "audit", "--all", "--strict"),
        ),
    ]

    valid_skills = known_skills(repo_root)
    all_test_files = discover_all_test_files(repo_root)
    wave: list[TaskSpec] = []

    if scope_mode == "all":
        eval_jobs = max(2, min(4, jobs // 2))
        wave.append(
            TaskSpec(
                "skill-contract-eval-all",
                1,
                "skill_contract.py eval --all --strict",
                (sys.executable, skill_contract_py, "eval", "--all", "--strict", "--jobs", str(eval_jobs)),
                weight=HEAVY_SHARD_WEIGHTS["skill-contract-eval-all"],
            )
        )
        for tf in all_test_files:
            rel = str(tf.relative_to(repo_root))
            classes, _ = parse_test_file_structure(tf, valid_skills, repo_root=repo_root)
            total_methods = sum(len(m) for m in classes.values())
            if total_methods == 0:
                continue
            # Shard multi-class heavy suites (>1 class and >=25 tests) by TestCase class so no single worker straggles >12s.
            if len(classes) > 1 and total_methods >= 25:
                for cls_name, methods in classes.items():
                    tid = f"{rel}::{cls_name}"
                    wave.append(
                        TaskSpec(
                            task_id=tid,
                            tier=2,
                            label=tid,
                            cmd=_unittest_class_cmd(tf, class_name=cls_name),
                            expected_tests=len(methods),
                            weight=HEAVY_SHARD_WEIGHTS.get(tid, max(1.0, len(methods) * 0.4)),
                        )
                    )
            else:
                wave.append(
                    TaskSpec(
                        task_id=rel,
                        tier=2,
                        label=rel,
                        cmd=_unittest_class_cmd(tf),
                        expected_tests=total_methods,
                        weight=HEAVY_SHARD_WEIGHTS.get(rel, max(0.5, total_methods * 0.2)),
                    )
                )
    else:
        target_set = set(target_skills)
        for sk in sorted(target_set):
            wave.append(
                TaskSpec(
                    task_id=f"skill-contract-eval:{sk}",
                    tier=1,
                    label=f"skill_contract.py eval --skill {sk} --strict",
                    cmd=(sys.executable, skill_contract_py, "eval", "--skill", sk, "--strict"),
                    weight=2.0,
                )
            )

        for tf in all_test_files:
            rel = str(tf.relative_to(repo_root))
            if rel == "scripts/test_verify_skills.py":
                universal, by_skill = map_verify_skills_methods(tf, valid_skills)
                selected_methods: list[str] = list(universal)
                for sk in sorted(target_set):
                    for m in by_skill.get(sk, []):
                        if m not in selected_methods:
                            selected_methods.append(m)
                if selected_methods:
                    chunk_size = 8
                    for idx in range(0, len(selected_methods), chunk_size):
                        chunk = selected_methods[idx : idx + chunk_size]
                        shard_no = idx // chunk_size + 1
                        wave.append(
                            TaskSpec(
                                task_id=f"scripts/test_verify_skills.py::scoped-{shard_no}",
                                tier=1,
                                label=f"scripts/test_verify_skills.py::scoped-{shard_no} ({len(chunk)} tests)",
                                cmd=_unittest_class_cmd(tf, methods=chunk),
                                expected_tests=len(chunk),
                                weight=max(1.0, len(chunk) * 0.4),
                            )
                        )
                continue

            classes, refs = parse_test_file_structure(tf, valid_skills, repo_root=repo_root)
            total_methods = sum(len(m) for m in classes.values())
            if total_methods == 0 or not (refs & target_set):
                continue
            if len(classes) > 1 and total_methods >= 25:
                for cls_name, methods in classes.items():
                    tid = f"{rel}::{cls_name}"
                    wave.append(
                        TaskSpec(
                            task_id=tid,
                            tier=1,
                            label=tid,
                            cmd=_unittest_class_cmd(tf, class_name=cls_name),
                            expected_tests=len(methods),
                            weight=HEAVY_SHARD_WEIGHTS.get(tid, max(1.0, len(methods) * 0.5)),
                        )
                    )
            elif len(classes) == 1 and rel in HEAVY_SHARD_WEIGHTS and total_methods >= 6:
                cls_name, methods = next(iter(classes.items()))
                dotted_methods = [f"{tf.stem}.{cls_name}.{m}" for m in methods]
                chunk_size = 2
                for idx in range(0, len(dotted_methods), chunk_size):
                    chunk = dotted_methods[idx : idx + chunk_size]
                    shard_no = idx // chunk_size + 1
                    wave.append(
                        TaskSpec(
                            task_id=f"{rel}::shard-{shard_no}",
                            tier=1,
                            label=f"{rel}::shard-{shard_no}",
                            cmd=_unittest_class_cmd(tf, methods=chunk),
                            expected_tests=len(chunk),
                            weight=max(2.0, len(chunk) * 1.5),
                        )
                    )
            else:
                wave.append(
                    TaskSpec(
                        task_id=rel,
                        tier=1,
                        label=rel,
                        cmd=_unittest_class_cmd(tf),
                        expected_tests=total_methods,
                        weight=HEAVY_SHARD_WEIGHTS.get(rel, max(0.5, total_methods * 0.2)),
                    )
                )

    # Longest-Job-First scheduling minimizes parallel tail latency.
    wave.sort(key=lambda item: (-item.weight, item.task_id))
    return tier0, wave


def _run_one_task(spec: TaskSpec, repo_root: Path) -> TaskResult:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.setdefault("GTT_LINEAR_SYNC", "0")
    # For class/method-level unittest commands that disable cwd import via -I, run inside the test file's parent dir
    cwd = repo_root
    if "-m" in spec.cmd and "unittest" in spec.cmd and "discover" not in spec.cmd:
        if spec.task_id.startswith("skills/design-studio/scripts/"):
            cwd = repo_root / "skills" / "design-studio" / "scripts"
        elif spec.task_id.startswith("skills/code-review/scripts/"):
            cwd = repo_root / "skills" / "code-review" / "scripts"
        elif spec.task_id.startswith("scripts/"):
            cwd = repo_root / "scripts"
        # Python -I ignores PYTHONPATH/cwd; pass no -I only when loading module by dotted name from cwd
        cmd = tuple(arg for arg in spec.cmd if arg != "-I")
    else:
        cmd = spec.cmd

    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    dt = time.perf_counter() - t0
    ran = 0
    for match in RAN_TESTS_RE.finditer(proc.stderr):
        ran += int(match.group(1))
    return TaskResult(
        spec=spec,
        returncode=proc.returncode,
        duration_sec=dt,
        tests_ran=ran,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def execute_plan(
    tier0: Sequence[TaskSpec],
    wave: Sequence[TaskSpec],
    *,
    jobs: int,
    repo_root: Path = REPO_ROOT,
) -> tuple[bool, list[TaskResult]]:
    """Run Tier 0 fast-fail checks sequentially, then run Tier 1/2 wave across bounded workers."""
    results: list[TaskResult] = []
    for spec in tier0:
        res = _run_one_task(spec, repo_root)
        results.append(res)
        if res.returncode != 0:
            return False, results

    if not wave:
        return True, results

    worker_count = max(1, min(jobs, len(wave)))
    if worker_count == 1:
        for spec in wave:
            res = _run_one_task(spec, repo_root)
            results.append(res)
            if res.returncode != 0:
                return False, results
        return True, results

    wave_results: dict[str, TaskResult] = {}
    ok = True
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        future_map = {pool.submit(_run_one_task, spec, repo_root): spec for spec in wave}
        for future in as_completed(future_map):
            res = future.result()
            wave_results[res.spec.task_id] = res
            if res.returncode != 0:
                ok = False

    # Deterministic output order matching the scheduled plan
    for spec in wave:
        if spec.task_id in wave_results:
            results.append(wave_results[spec.task_id])
    return ok, results


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="verify", description="Tiered parallel verification gate for user-skills.")
    mode = p.add_mutually_exclusive_group(required=False)
    mode.add_argument("--all", action="store_true", help="Run full repository verification suite in parallel.")
    mode.add_argument("--changed", action="store_true", help="Force path-scoped incremental verification when applicable.")
    p.add_argument("--base", default=None, help="Optional git base ref for --changed diff detection.")
    p.add_argument("--jobs", type=int, default=None, help="Maximum parallel workers (defaults to CPU count).")
    p.add_argument("--json", action="store_true", help="Emit structured JSON summary.")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    t_start = time.perf_counter()
    jobs = max(1, int(args.jobs)) if args.jobs is not None else max(2, min(12, os.cpu_count() or 4))

    changed_files = [] if args.all else detect_changed_files(REPO_ROOT, base_ref=args.base)
    scope_mode, target_skills, reason = classify_scope(
        changed_files,
        force_all=bool(args.all),
        repo_root=REPO_ROOT,
    )

    tier0, wave = build_execution_plan(
        scope_mode=scope_mode,
        target_skills=target_skills,
        jobs=jobs,
        repo_root=REPO_ROOT,
    )
    ok, results = execute_plan(tier0, wave, jobs=jobs, repo_root=REPO_ROOT)
    wall_sec = time.perf_counter() - t_start

    expected_tests = sum(spec.expected_tests for spec in wave)
    actual_tests = sum(r.tests_ran for r in results if r.spec.expected_tests > 0)
    if ok and actual_tests < expected_tests:
        ok = False

    failures = [r for r in results if r.returncode != 0]

    if args.json:
        payload: dict[str, Any] = {
            "ok": ok,
            "mode": scope_mode,
            "reason": reason,
            "target_skills": target_skills,
            "changed_files": changed_files,
            "jobs": jobs,
            "tasks_executed": len(results),
            "expected_tests": expected_tests,
            "unittest_cases_ran": actual_tests,
            "wall_clock_sec": round(wall_sec, 3),
            "results": [
                {
                    "task_id": r.spec.task_id,
                    "tier": r.spec.tier,
                    "returncode": r.returncode,
                    "duration_sec": round(r.duration_sec, 3),
                    "tests_ran": r.tests_ran,
                }
                for r in results
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        scope_banner = (
            f"all ({reason})"
            if scope_mode == "all"
            else f"scoped [{', '.join(target_skills)}] ({reason})"
        )
        print(
            f"[verify] mode={scope_banner} | jobs={jobs} | shards={len(results)} | "
            f"tests={actual_tests}/{expected_tests} | wall={wall_sec:.2f}s"
        )
        for r in failures:
            sys.stderr.write(f"\n=== FAIL [{r.spec.label}] (exit {r.returncode}, {r.duration_sec:.2f}s) ===\n")
            if r.stdout.strip():
                sys.stderr.write(r.stdout.rstrip() + "\n")
            if r.stderr.strip():
                sys.stderr.write(r.stderr.rstrip() + "\n")
    if not ok and not failures and actual_tests < expected_tests:
        sys.stderr.write(
            f"\n=== FAIL [test-count-guard] expected {expected_tests} unittest cases, ran {actual_tests} ===\n"
        )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
