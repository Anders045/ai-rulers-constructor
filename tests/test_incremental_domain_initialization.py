"""Compare newly detected domains through fresh and repeated default initialization."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.ai_rulers_test_support import read_state, write_profile
from tests.test_module_workflows import CLI


CANDIDATES = ".rulers-work/candidates"
METADATA = """```yaml
metadata:
  applies_to:
    - '**/*'
  trigger_keywords:
    - review
  must_load_with: []
```
"""


class IncrementalDomainInitializationTest(unittest.TestCase):
    def command(self, project, script, *args):
        result = subprocess.run(
            [sys.executable, str(script), *args], cwd=project, capture_output=True, text=True
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        return result.stdout

    def initialize(self, project, facts, domains):
        profile = write_profile(project, facts, rulers_dir=CANDIDATES,
                                unresolved=[("Who reviews version tags?", "Release planning", "core", "owner")])
        settings = ".rulers-work/candidates.json"
        (project / settings).write_text(json.dumps({"projects": {"workspace": {
            "profile": profile.relative_to(project).as_posix(), "domains": domains,
        }}}), encoding="utf-8")
        plan = json.loads(self.command(project, CLI, "init-plan", "--candidates", settings))
        self.assertEqual("review", plan["status"], plan["preparation"])
        self.command(project, CLI, "init-apply", "--plan", plan["plan_path"],
                     "--reviewed-by", "test-owner", "--evidence", "synthetic fixture review")

    def test_new_domain_matches_fresh_definition_route_and_loaded_rules(self):
        examples = [
            ("delivery", "Dockerfile", "FROM scratch\n", "delivery",
             "Build release images using the repository Dockerfile.", "Check the image build before tagging."),
            ("frontend-app", "app.json", "{}\n", "frontend/app",
             "Use the native status bar on mobile screens.", "Check the status bar in both themes."),
        ]
        for domain, hint, hint_text, target, fact, rule in examples:
            with self.subTest(domain=domain), tempfile.TemporaryDirectory() as temporary:
                results = []
                for mode in ("fresh", "incremental"):
                    project = Path(temporary).resolve() / mode
                    project.mkdir()
                    (project / "README.md").write_text("Fixture application\n")
                    facts = [("Fixture application", "observed", "README.md", "core", "high")]
                    if mode == "incremental":
                        self.initialize(project, facts, {})
                        self.assertEqual({"core"}, set(read_state(project)["domains"]))

                    (project / hint).write_text(hint_text)
                    (project / "ADR.md").write_text(fact + "\n")
                    facts.append((fact, "approved", "ADR.md", domain, "high"))
                    candidate = project / CANDIDATES / domain
                    candidate.mkdir(parents=True, exist_ok=True)
                    (candidate / "INDEX.md").write_text("# Review\n" + METADATA + "\n[Check](CHECK.md)\n")
                    (candidate / "CHECK.md").write_text("# Check\n" + METADATA + "\n" + rule + "\n")
                    self.initialize(project, facts, {domain: candidate.relative_to(project).as_posix()})

                    domains = read_state(project)["domains"]
                    self.assertEqual({"core", domain}, set(domains))
                    self.assertEqual([], domains[domain]["requires_active"])
                    self.assertEqual(target, domains[domain]["target_dir"])
                    self.assertEqual(["CHECK.md", "INDEX.md"], domains[domain]["required_files"])
                    validator = project / "documents/rulers/scripts/validate_rulers.py"
                    context = json.loads(self.command(project, validator, "--mode", "context", "--domain", domain))
                    self.assertFalse(context["blocked"])
                    self.assertEqual({"level": 2, "index": f"documents/rulers/{target}/INDEX.md"},
                                     context["routes"][domain])
                    loaded = self.command(project, validator, "--mode", "load", "--domain", domain,
                                          "--rule", f"{target}/CHECK.md")
                    self.assertIn(fact, loaded)
                    self.assertIn(rule, loaded)
                    results.append(loaded)
                self.assertEqual(results[0], results[1])
