"""Ordinary navigation across domains in a consolidated initialization review."""

import json
import shutil
import sys
import unittest

from tests import test_module_workflows as module_fixtures
from tests.ai_rulers_test_support import file_sha256, materialize_v2_fixture, write_profile


METADATA = "```yaml\nmetadata:\n  applies_to: []\n  trigger_keywords: []\n  must_load_with: []\n```\n"


class InitializationNavigationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = module_fixtures.ModuleWorkflowTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def write_candidates(self, project):
        (project / "DECISIONS.md").write_text("Keep the service boundary and migration history explicit.\n")
        write_profile(project, [("Keep service and migration rules", "approved", "DECISIONS.md",
                                 "backend,database", "high")],
                      unresolved=[("无", "Fixture decisions are recorded", "core", "fixture-owner")],
                      rulers_dir=".rulers-work")
        candidates = {
            "backend": ("ARCHITECTURE.md", "Keep the service boundary explicit.", "../database/INDEX.md"),
            "database": ("MIGRATION.md", "Keep every migration reversible.", "documents/rulers/backend/INDEX.md"),
        }
        for domain, (leaf, rule, neighbor) in candidates.items():
            directory = project / ".rulers-work" / domain
            directory.mkdir()
            (directory / "INDEX.md").write_text(METADATA + f"\n[Rule]({leaf})\n[Related]({neighbor})\n")
            (directory / leaf).write_text(METADATA + "\n" + rule + "\n")

    def plan(self, project, domains):
        settings = {"profile": ".rulers-work/PROJECT_PROFILE.md",
                    "domains": {name: f".rulers-work/{name}" for name in domains}}
        path = ".rulers-work/candidates.json"
        (project / path).write_text(json.dumps({"projects": {"workspace": settings}}))
        return json.loads(self.fixture.cli(project, "init-plan", "--candidates", path).stdout)

    def apply(self, project, summary, *, success=True):
        return self.fixture.cli(project, "init-apply", "--plan", summary["plan_path"],
                                "--reviewed-by", "fixture-reviewer", "--evidence", "fixture-only",
                                success=success)

    def load(self, project, domain, leaf):
        validator = project / "documents/rulers/scripts/validate_rulers.py"
        return self.fixture.command([sys.executable, str(validator), "--mode", "load",
                                     "--project-root", str(project), "--domain", domain,
                                     "--rule", f"{domain}/{leaf}"], cwd=project).stdout

    def test_mutually_navigating_candidates_can_be_reviewed_in_either_order(self):
        for order in (("backend", "database"), ("database", "backend")):
            with self.subTest(order=order):
                project = self.fixture.root / order[0]
                project.mkdir()
                self.write_candidates(project)
                planned = self.plan(project, order)
                self.assertEqual("review", planned["status"], planned["preparation"])
                self.assertFalse((project / "documents/rulers").exists())
                self.apply(project, planned)
                backend = self.load(project, "backend", "ARCHITECTURE.md")
                database = self.load(project, "database", "MIGRATION.md")
                self.assertIn("Keep the service boundary explicit.", backend)
                self.assertIn("Keep every migration reversible.", database)
                self.assertNotIn("Keep every migration reversible.", backend)
                self.assertNotIn("Keep the service boundary explicit.", database)

    def test_removing_a_navigation_target_reports_its_retained_source_and_keeps_approved_rules(self):
        project = self.fixture.root / "service"
        project.mkdir()
        self.write_candidates(project)
        drafts = project / ".rulers-work"
        backend_index = drafts / "backend/INDEX.md"
        backend_index.write_text(backend_index.read_text() + "\n[Migrations](../database/MIGRATION.md)\n")
        first = self.plan(project, ["backend", "database"])
        self.apply(project, first)
        state = project / "documents/rulers/RULERS_STATE.json"
        before = state.read_bytes()

        (drafts / "database/MIGRATION.md").unlink()
        index = drafts / "database/INDEX.md"
        index.write_text(index.read_text().replace("[Rule](MIGRATION.md)\n", ""))
        planned = self.plan(project, ["database"])
        self.assertEqual("preparation", planned["status"], planned["preparation"])
        self.assertTrue(any("documents/rulers/backend/INDEX.md -> ../database/MIGRATION.md" in p["reason"]
                            for p in planned["preparation"]), planned["preparation"])
        self.apply(project, planned, success=False)
        self.assertEqual(before, state.read_bytes())
        self.assertIn("Keep the service boundary explicit.", self.load(project, "backend", "ARCHITECTURE.md"))
        self.assertIn("Keep every migration reversible.", self.load(project, "database", "MIGRATION.md"))

    def test_removing_a_linked_and_required_target_identifies_the_requiring_rule(self):
        project = self.fixture.root / "required-migration"
        project.mkdir()
        self.write_candidates(project)
        drafts = project / ".rulers-work"
        backend = drafts / "backend/ARCHITECTURE.md"
        backend.write_text(backend.read_text().replace("must_load_with: []",
                           "must_load_with:\n    - ../database/MIGRATION.md"))
        domains = ["backend", "database"]
        self.apply(project, self.plan(project, domains))
        state = project / "documents/rulers/RULERS_STATE.json"
        approved_state = state.read_bytes()
        approved_load = self.load(project, "backend", "ARCHITECTURE.md")
        self.assertIn("Keep every migration reversible.", approved_load)

        # The database index still links to the target the backend rule requires.
        (drafts / "database/MIGRATION.md").unlink()
        planned = self.plan(project, domains)
        self.assertEqual("preparation", planned["status"], planned["preparation"])
        self.apply(project, planned, success=False)
        self.assertEqual(approved_state, state.read_bytes())
        self.assertEqual(approved_load, self.load(project, "backend", "ARCHITECTURE.md"))
        self.assertTrue(any(item["scope"] == "workspace"
                            and "documents/rulers/backend/ARCHITECTURE.md" in item["reason"]
                            and "documents/rulers/database/MIGRATION.md" in item["reason"]
                            for item in planned["preparation"]), planned["preparation"])

    def test_copied_nested_domain_is_checked_against_the_original_in_either_order(self):
        for order in (("frontend-web", "frontend-app"), ("frontend-app", "frontend-web")):
            with self.subTest(order=order):
                project = self.fixture.root / order[0]
                project.mkdir()
                (project / "README.md").write_text("Web and native clients.\n")
                write_profile(project, [("Web and native clients", "observed", "README.md", "core", "high")],
                              unresolved=[("无", "Fixture decisions are recorded", "core", "fixture-owner")],
                              rulers_dir=".rulers-work")
                drafts = project / ".rulers-work"
                web = drafts / "frontend-web/INDEX.md"
                app = drafts / "frontend-app/INDEX.md"
                web.parent.mkdir()
                app.parent.mkdir()
                web.write_text(METADATA + "\nBrowser rules. [Mobile](app/INDEX.md)\n")
                app.write_text(METADATA + "\nNative rules.\n")
                initial = self.plan(project, ["frontend-web", "frontend-app"])
                self.assertEqual("review", initial["status"], initial["preparation"])
                self.apply(project, initial)

                shutil.copytree(project / "documents/rulers/frontend", web.parent, dirs_exist_ok=True)
                carried = web.parent / "app/INDEX.md"
                original = carried.read_bytes()
                web.write_text(web.read_text() + "\nUse browser focus.\n")
                app.write_text(app.read_text() + "\nUse native focus.\n")
                legitimate = self.plan(project, order)
                self.assertEqual("review", legitimate["status"], legitimate["preparation"])

                carried.write_bytes(app.read_bytes())
                invalid = self.plan(project, order)
                self.assertEqual("preparation", invalid["status"], invalid["preparation"])
                self.assertTrue(any("another domain subtree: app" in p["reason"] for p in invalid["preparation"]))
                self.apply(project, invalid, success=False)
                carried.write_bytes(original)
                self.apply(project, legitimate)
                loaded_web = self.fixture.load_installed(project, "--domain", "frontend-web")
                self.assertIn("Use browser focus.", loaded_web)
                self.assertNotIn("Use native focus.", loaded_web)
                self.assertIn("Use native focus.", self.fixture.load_installed(project, "--domain", "frontend-app"))

    def test_must_load_requires_the_adopted_target_even_when_navigation_is_pending(self):
        project = self.fixture.root / "required-migrations"
        project.mkdir()
        self.write_candidates(project)
        backend = project / ".rulers-work/backend/ARCHITECTURE.md"
        backend.write_text(backend.read_text().replace("must_load_with: []",
                           "must_load_with:\n    - ../database/MIGRATION.md"))
        missing = self.plan(project, ["backend"])
        self.assertEqual("preparation", missing["status"])
        self.assertTrue(any("outside adopted inventory: documents/rulers/database/MIGRATION.md" in p["reason"]
                            for p in missing["preparation"]), missing["preparation"])
        self.assertFalse((project / "documents/rulers").exists())

        migration = project / ".rulers-work/database/MIGRATION.md"
        valid = migration.read_bytes()
        migration.write_text("# Incomplete rule\n")
        invalid = self.plan(project, ["backend", "database"])
        self.assertEqual("preparation", invalid["status"])
        self.assertTrue(any("Incomplete rule candidate: MIGRATION.md" in p["reason"]
                            for p in invalid["preparation"]), invalid["preparation"])
        self.assertFalse((project / "documents/rulers").exists())
        migration.write_bytes(valid)
        planned = self.plan(project, ["backend", "database"])
        self.assertEqual("review", planned["status"], planned["preparation"])
        self.apply(project, planned)
        self.assertIn("Keep every migration reversible.", self.load(project, "backend", "ARCHITECTURE.md"))

    def test_batch_keeps_targets_referenced_by_v2_project_generated_rules(self):
        project = self.fixture.root / "legacy"
        project.mkdir()
        state_path, state = materialize_v2_fixture(project, "schema2_runtime_ready.json")
        rules = state_path.parent
        installed = state["rulers_dir"]
        # This historical project used just the core and backend domains.
        del state["domains"]["security"], state["domains"]["delivery"]
        backend = rules / "backend/INDEX.md"
        guide = rules / "guide.md"
        guide.write_text("Historical guide.\n")
        backend.write_text("# Historical backend\n\n[Guide](../guide.md)\n[Migration](../database/MIGRATION.md)\n")
        state["managed_files"][f"{installed}/backend/INDEX.md"]["rendered_sha256"] = file_sha256(backend)
        state_path.write_text(json.dumps(state))
        migration = rules / "database/MIGRATION.md"
        migration.parent.mkdir()
        migration.write_text("Historical migration target.\n")
        upgrade = json.loads(self.fixture.cli(project, "plan", "--rulers-dir", installed, "--operation", "upgrade").stdout)
        self.fixture.cli(project, "apply", "--plan", upgrade["plan_path"], *module_fixtures.REVIEW)

        self.write_candidates(project)
        drafts = project / ".rulers-work"
        shutil.copyfile(rules / "PROJECT_PROFILE.md", drafts / "PROJECT_PROFILE.md")
        index = drafts / "database/INDEX.md"
        index.write_text(index.read_text().replace("documents/rulers/backend/INDEX.md", "../backend/INDEX.md"))
        self.apply(project, self.plan(project, ["database"]))
        validator = rules / "scripts/validate_rulers.py"
        backend_load = [sys.executable, str(validator), "--mode", "load", "--project-root", str(project),
                        "--rulers-dir", installed, "--domain", "backend"]
        self.assertIn("# Historical backend", self.fixture.command(backend_load).stdout)

        guide.unlink()
        index.write_text(index.read_text() + "\nDatabase-only edit.\n")
        unrelated = self.plan(project, ["database"])
        self.assertEqual("review", unrelated["status"], unrelated["preparation"])
        self.apply(project, unrelated)
        guide.write_text("Historical guide.\n")

        (drafts / "database/MIGRATION.md").unlink()
        index.write_text(index.read_text().replace("[Rule](MIGRATION.md)\n", ""))
        deletion = self.plan(project, ["database"])
        self.assertEqual("preparation", deletion["status"], deletion["preparation"])
        self.assertTrue(any(f"{installed}/backend/INDEX.md -> ../database/MIGRATION.md" in p["reason"]
                            for p in deletion["preparation"]), deletion["preparation"])
        self.apply(project, deletion, success=False)
        self.assertTrue(migration.is_file())
        self.assertIn("[Migration](../database/MIGRATION.md)", self.fixture.command(backend_load).stdout)

    def test_missing_first_domain_index_identifies_the_other_candidate_that_links_to_it(self):
        project = self.fixture.root / "missing-index"
        project.mkdir()
        self.write_candidates(project)
        (project / ".rulers-work/database/INDEX.md").unlink()
        summary = self.plan(project, ["database", "backend"])
        self.assertEqual("preparation", summary["status"])
        self.assertTrue(any("documents/rulers/backend/INDEX.md -> ../database/INDEX.md" in p["reason"]
                            for p in summary["preparation"]), summary["preparation"])
        self.assertFalse((project / "documents/rulers").exists())
