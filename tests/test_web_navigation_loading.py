"""Web rule selection through the validator installed in an isolated target."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

from scripts.prepare_web_loading_replay import replay_skill
from scripts.runtime_fixture import SKILL_ROOT, runtime_fixture


WEB_INDEXES = (
    "frontend/INDEX.md",
    "frontend/web/INDEX.md",
    "frontend/web/design/INDEX.md",
    "frontend/common/INDEX.md",
)
ACCESSIBILITY = "frontend/web/design/ACCESSIBILITY.md"
TOKENS = "frontend/common/DESIGN_TOKENS.md"
FIXTURE_SKILL = Path(os.environ.get("RULERS_TEST_SKILL_ROOT", SKILL_ROOT)).resolve()


class WebNavigationLoadingTest(unittest.TestCase):
    def project(self, rulers_dir="documents/rulers", *, candidate=True):
        self.rulers_dir = rulers_dir
        self.skill = self.enterContext(replay_skill(FIXTURE_SKILL, candidate=candidate))
        self.root = self.enterContext(runtime_fixture(
            rulers_dir=rulers_dir, domains=("frontend-app",), policy="project-native", skill_root=self.skill,
        ))
        self.draft = self.root / "web-candidate"
        registry = json.loads((self.skill / "templates/domain-registry.json").read_text())
        domain = registry["domains"]["frontend-web"]
        source = self.skill / "templates/runtime" / domain["template_root"]
        for name in domain["templates"]:
            target = self.draft / name
            target.parent.mkdir(parents=True, exist_ok=True)
            template = (source / name).read_text().replace("{{RULERS_DIR}}", rulers_dir)
            body = re.split(r"(?m)^##\s+(?:\d+\.\s+)?AI_FILL", template)[0].rstrip() + "\n"
            target.write_text(body)
        # Accessing Tokens in this fixture requires traversing the design index.
        leaf = self.draft / "web/design/ACCESSIBILITY.md"
        leaf.write_text(leaf.read_text().replace(f"    - {rulers_dir}/{TOKENS}\n", ""))

    def adopt(self, plan_name="web-plan.json"):
        cli = self.skill / "scripts/rulers_init.py"
        self.command(cli, "rules-plan", "--project-root", str(self.root), "--rulers-dir", self.rulers_dir,
                     "--domain", "frontend-web", "--candidate-dir", self.draft.name,
                     "--reason", "Synthetic Web navigation fixture", "--activate-on-apply", "--output", plan_name)
        self.command(cli, "rules-apply", "--plan", plan_name, "--reviewed-by", "fixture-owner",
                     "--evidence", "synthetic-test-only")

    def command(self, script, *args, expected_exit=0):
        result = subprocess.run([sys.executable, str(script), *args], cwd=self.root, text=True,
                                capture_output=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(expected_exit, result.returncode, result.stderr or result.stdout)
        if expected_exit:
            self.assertEqual("", result.stdout)
            return result.stderr
        return result.stdout

    def validate(self, mode, *args, expected_exit=0):
        return self.command(self.root / self.rulers_dir / "scripts/validate_rulers.py", "--mode", mode,
                            "--project-root", str(self.root), "--rulers-dir", self.rulers_dir, *args,
                            expected_exit=expected_exit)

    def sections(self, body):
        parts = re.split(r"(?m)^<!-- ([^\n]+\.md) -->\n", body)
        return {path.removeprefix(self.rulers_dir + "/"): text.rstrip("\n")
                for path, text in zip(parts[1::2], parts[2::2])}

    def test_default_web_leaf_includes_complete_navigation_indexes(self):
        self.project(candidate=False)
        self.adopt()
        loaded = self.sections(self.validate("load", "--domain", "frontend-web", "--rule", ACCESSIBILITY))
        for index in WEB_INDEXES:
            with self.subTest(index=index):
                expected = (self.root / self.rulers_dir / index).read_text().rstrip("\n")
                self.assertIn("| 任务条件 | 还需加载 |", loaded[index])
                self.assertEqual(expected, loaded[index])

    def test_known_web_leaf_keeps_four_paths_dependencies_profile_and_byte_count(self):
        self.project()
        self.adopt()
        args = ("--domain", "frontend-web", "--rule", ACCESSIBILITY)
        body = self.validate("load", *args)
        sections = self.sections(body)
        self.assertEqual([
            "core/HARD_CONSTRAINTS.md", "core/WORKFLOW.md", "frontend/INDEX.md", ACCESSIBILITY,
            "frontend/web/design/INDEX.md", "frontend/web/INDEX.md", TOKENS,
            "frontend/common/INDEX.md", "PROJECT_PROFILE.md",
        ], list(sections))
        self.assertEqual(dict.fromkeys(WEB_INDEXES, ""), {path: sections[path] for path in WEB_INDEXES})
        self.assertIn("模态框、菜单", sections[ACCESSIBILITY])
        self.assertIn("唯一 token 权威来源", sections[TOKENS])
        self.assertIn("临时测试项目", sections["PROJECT_PROFILE.md"])
        metadata = json.loads(self.validate("load", *args, "--format", "json"))
        self.assertEqual([self.rulers_dir + "/" + path for path in sections], metadata["files"])
        self.assertEqual(len(body.removesuffix("\n").encode("utf-8")), metadata["bytes"])

    def test_discovery_domain_only_and_named_indexes_keep_their_navigation_tables(self):
        self.project()
        self.adopt()
        domain = ("--domain", "frontend-web")
        context = json.loads(self.validate("context", *domain))
        root_index = context["routes"]["frontend-web"]["index"]
        self.assertFalse(context["blocked"])
        self.assertIn(root_index, context["load"]["indexes"])
        for selectors in (domain, (*domain, "--rule", root_index)):
            with self.subTest(selectors=selectors):
                sections = self.sections(self.validate("load", *selectors))
                self.assertIn("| 任务条件 | 还需加载 |", sections["frontend/INDEX.md"])
                self.assertNotIn(ACCESSIBILITY, sections)
        design = "frontend/web/design/INDEX.md"
        named = self.sections(self.validate("load", *domain, "--rule", self.rulers_dir + "/" + design,
                                            "--rule", ACCESSIBILITY))
        self.assertEqual((self.root / self.rulers_dir / design).read_text().rstrip("\n"), named[design])
        self.assertEqual("", named["frontend/common/INDEX.md"])

    def test_approved_one_line_navigation_edit_stays_visible_and_later_drift_blocks_load(self):
        self.project()
        self.adopt()
        design = "frontend/web/design/INDEX.md"
        candidate = self.draft / "web/design/INDEX.md"
        before = candidate.read_text()
        revised = before.replace("键盘、焦点、语义、标签、对比度、减少动态效果", "键盘、焦点及团队弹窗验收")
        self.assertNotEqual(before, revised)
        candidate.write_text(revised)
        self.adopt("revised-plan.json")
        args = ("--domain", "frontend-web", "--rule", ACCESSIBILITY)
        sections = self.sections(self.validate("load", *args))
        self.assertEqual(revised.rstrip("\n"), sections[design])
        self.assertEqual("", sections["frontend/web/INDEX.md"])

        (self.root / self.rulers_dir / design).write_text(revised + "\nUnreviewed navigation edit.\n")
        self.assertTrue(json.loads(self.validate("context", "--domain", "frontend-web"))["blocked"])
        self.assertIn("blocked", self.validate("load", *args, expected_exit=2))

    def test_unknown_index_and_project_specific_constraint_remain_in_the_bundle(self):
        self.project()
        index = self.draft / "web/develop/INDEX.md"
        index.write_text(index.read_text() + "\n团队约定：变更后以项目验收清单核对。\n")
        self.adopt()
        args = ("--domain", "frontend-web", "--rule", "frontend/web/develop/TESTING.md")
        sections = self.sections(self.validate("load", *args))
        self.assertEqual(index.read_text().rstrip("\n"), sections["frontend/web/develop/INDEX.md"])
        self.assertEqual("", sections["frontend/web/INDEX.md"])

    def test_approved_index_with_different_bytes_keeps_its_text(self):
        self.project()
        candidate = self.draft / "web/design/INDEX.md"
        candidate.write_bytes(candidate.read_bytes().replace(b"\n", b"\r\n"))
        self.adopt()
        sections = self.sections(self.validate("load", "--domain", "frontend-web", "--rule", ACCESSIBILITY))
        self.assertEqual(candidate.read_text().rstrip("\n"), sections["frontend/web/design/INDEX.md"])

    def test_shared_or_app_leaf_does_not_suppress_web_domain_discovery(self):
        self.project()
        self.adopt()
        for selectors in (("--rule", TOKENS),
                          ("--domain", "frontend-app", "--rule", "frontend/app/develop/TESTING.md")):
            with self.subTest(selectors=selectors):
                sections = self.sections(self.validate("load", "--domain", "frontend-web", *selectors))
                self.assertIn("| 任务条件 | 还需加载 |", sections["frontend/INDEX.md"])

    def test_reviewed_defaults_project_with_a_different_installation_directory(self):
        self.project("docs/web rules")
        self.adopt()
        sections = self.sections(self.validate("load", "--domain", "frontend-web",
                                               "--rule", self.rulers_dir + "/" + ACCESSIBILITY))
        for path in WEB_INDEXES:
            self.assertEqual("", sections[path])
        self.assertIn("唯一 token 权威来源", sections[TOKENS])
