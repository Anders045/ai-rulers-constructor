"""Public replay accounting must bind the installed inputs on both ends of the run."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RULE = "documents/rulers/frontend/INDEX.md"
VALIDATOR = "documents/rulers/scripts/validate_rulers.py"
STATE = "documents/rulers/RULERS_STATE.json"


class WebLoadingReplayMeasurementTest(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.target = Path(temporary) / "target"
        self.evidence = Path(temporary) / "evidence"
        self.evidence.mkdir()
        self.rule_text = "# Frontend\nRead the local design contract.\n"
        for name, text in {
            "AGENTS.md": "Read the project rules.\n",
            "documents/rulers/PROJECT_PROFILE.md": "## 阻塞性未决问题\nNone.\n",
            RULE: self.rule_text, STATE: "{}\n", VALIDATOR: "# Installed validator\n",
            "index.html": "<p>Original page</p>\n",
        }.items():
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.before = self.hashes()
        event = {"type": "item.completed", "item": {
            "id": "item_1", "type": "command_execution", "aggregated_output": self.rule_text,
        }}
        (self.evidence / "events.jsonl").write_text(json.dumps(event) + "\n")

    def hashes(self):
        return {p.relative_to(self.target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.target.rglob("*") if p.is_file()}

    def report(self, *, after=None):
        manifest = {"exit_code": 0, "started_at_unix": 0, "finished_at_unix": 1,
                    "before": {"files": self.before}, "after": {"files": self.hashes() if after is None else after}}
        (self.evidence / "manifest.json").write_text(json.dumps(manifest))
        return subprocess.run([sys.executable, "-m", "scripts.measure_web_loading_replay",
                               "--target", str(self.target), "--evidence", str(self.evidence)],
                              cwd=ROOT, capture_output=True, text=True)

    def test_rule_read_then_deleted_cannot_be_counted_as_zero(self):
        (self.target / RULE).unlink()
        result = self.report()
        self.assertNotEqual(0, result.returncode)
        self.assertIn(RULE, result.stderr)

    def test_validator_changed_during_replay_invalidates_measurement(self):
        (self.target / VALIDATOR).write_text("# Different validator\n")
        result = self.report()
        self.assertNotEqual(0, result.returncode)
        self.assertIn(VALIDATOR, result.stderr)

    def test_disk_state_must_still_match_the_recorded_run(self):
        (self.target / STATE).write_text('{"phase":"changed"}\n')
        result = self.report(after=self.before)
        self.assertNotEqual(0, result.returncode)
        self.assertIn(STATE, result.stderr)

    def test_task_output_and_generated_cache_leave_rule_accounting_intact(self):
        (self.target / "index.html").write_text("<p>Updated page</p>\n")
        cache = self.target / "documents/rulers/scripts/__pycache__/validate_rulers.pyc"
        cache.parent.mkdir()
        cache.write_bytes(b"test cache")
        result = self.report()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(len(self.rule_text.encode()), json.loads(result.stdout)["rule_bytes"])
