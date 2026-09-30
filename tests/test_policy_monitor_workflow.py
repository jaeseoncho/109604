"""Offline regression tests using the workflow's real shell steps and local Git."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/policy-monitor.yml"


def workflow_script(step_name):
    """Read a named multiline shell step without adding a YAML dependency."""
    step = WORKFLOW.read_text(encoding="utf-8").split(
        f"      - name: {step_name}\n", 1
    )[1].split("\n      - name:", 1)[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


class BaselineWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "origin.git"
        self.runner = self.root / "runner"
        self.runner.mkdir()
        self.env = dict(os.environ, GITHUB_REF_NAME="main")
        # These tests never authenticate or contact an external Git remote.
        self.command("git", "init", "--bare", "--initial-branch=main", str(self.remote))
        self.git("init", "--initial-branch=main")
        self.git("config", "user.name", "Baseline test")
        self.git("config", "user.email", "baseline-test@example.invalid")
        for name in ("policy_monitor.py", "sources.json", "sources_extra.json"):
            shutil.copyfile(ROOT / name, self.runner / name)
        self.git("add", ".")
        self.git("commit", "-m", "Initial test sources")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "main")

    def command(self, *args, cwd=None, check=True):
        return subprocess.run(
            args, cwd=cwd or self.runner, env=self.env,
            text=True, capture_output=True, check=check,
        )

    def git(self, *args, cwd=None):
        return self.command("git", *args, cwd=cwd).stdout.strip()

    def step(self, name):
        return self.command("bash", "-c", workflow_script(name))

    def prepare(self):
        self.step("Prepare runtime configuration")
        self.step("Bound slow official-site requests")

    def write_state(self, content):
        path = self.runner / "state/policy_state.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(content) + "\n", encoding="utf-8")

    def remote_state(self):
        return json.loads(self.git("--git-dir", str(self.remote), "show", "main:state/policy_state.json"))

    def test_runtime_copy_keeps_tracked_sources_clean_and_limits_requests(self):
        self.prepare()
        self.assertEqual("", self.git("diff", "--name-only"))
        runtime = (self.runner / "runtime_policy_monitor.py").read_text(encoding="utf-8")
        self.assertIn("def fetch_html(url: str, attempts: int = 2, timeout: int = 12)", runtime)
        self.assertIn("for raw_target in follow_links[:1]:", runtime)
        self.assertIn("fetch_html(target, attempts=1, timeout=10)", runtime)
        self.assertIn("python runtime_policy_monitor.py --config runtime_sources.json", workflow_script("Check official policy sources"))
        config = json.loads((self.runner / "runtime_sources.json").read_text(encoding="utf-8"))
        self.assertEqual(18, len(config["sources"]))

    def test_initial_baseline_is_pushed_without_runtime_files(self):
        self.prepare()
        state = {"schema_version": 2, "sources": {"test": {"failure_count": 0}}}
        self.write_state(state)
        self.step("Save comparison baseline")
        self.assertEqual(state, self.remote_state())
        self.assertEqual("state/policy_state.json", self.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"))
        self.assertEqual("", self.git("diff", "--name-only"))

    def test_updated_baseline_is_pushed_and_unchanged_state_is_a_noop(self):
        self.prepare()
        self.write_state({"run": 1})
        self.step("Save comparison baseline")
        self.write_state({"run": 2})
        self.step("Save comparison baseline")
        self.assertEqual({"run": 2}, self.remote_state())
        saved = self.git("rev-parse", "HEAD")
        result = self.step("Save comparison baseline")
        self.assertIn("No state update to commit.", result.stdout)
        self.assertEqual(saved, self.git("rev-parse", "HEAD"))

    def test_baseline_rebases_over_a_remote_update_without_losing_it(self):
        self.prepare()
        other = self.root / "other"
        self.git("clone", str(self.remote), str(other))
        self.git("config", "user.name", "Other test", cwd=other)
        self.git("config", "user.email", "other-test@example.invalid", cwd=other)
        (other / "README.md").write_text("Keep this concurrent update.\n", encoding="utf-8")
        self.git("add", "README.md", cwd=other)
        self.git("commit", "-m", "Concurrent documentation change", cwd=other)
        self.git("push", "origin", "main", cwd=other)
        self.write_state({"run": 1})
        self.step("Save comparison baseline")
        self.assertEqual({"run": 1}, self.remote_state())
        self.assertEqual("Keep this concurrent update.", self.git("--git-dir", str(self.remote), "show", "main:README.md"))

    def run_fixture_monitor(self, text):
        # Execute the generated runtime module with a deterministic source fixture.
        script = """
import runpy
import sys
scope = runpy.run_path("runtime_policy_monitor.py", run_name="test_monitor")
main = scope["main"]
fixture_text = sys.argv[1]
main.__globals__["fetch_visible_text"] = lambda url: (fixture_text, url)
sys.argv = ["runtime_policy_monitor.py", "--config", "fixture_sources.json"]
raise SystemExit(main())
"""
        self.command("python", "-c", script, text)
        return json.loads((self.runner / "monitor_result.json").read_text(encoding="utf-8"))

    def test_saved_baseline_is_reused_for_unchanged_and_changed_sources(self):
        self.prepare()
        config = {
            "sources": [{"id": "fixture", "name": "Fixture", "url": "https://example.invalid/policy",
                         "area": "test", "why": "test", "keywords": ["policy"], "actions": []}],
            "material_terms": ["policy"], "urgent_terms": [],
        }
        (self.runner / "fixture_sources.json").write_text(json.dumps(config), encoding="utf-8")
        first = self.run_fixture_monitor("policy version one")
        self.assertEqual(1, first["baselined_sources"])
        self.assertFalse(first["alert"])
        self.step("Save comparison baseline")
        # Load exactly what a fresh runner obtains from the persisted Git baseline.
        self.write_state(self.remote_state())
        unchanged = self.run_fixture_monitor("policy version one")
        self.assertEqual(0, unchanged["baselined_sources"])
        self.assertEqual(0, unchanged["changed_sources"])
        changed = self.run_fixture_monitor("policy version two")
        self.assertEqual(0, changed["baselined_sources"])
        self.assertEqual(1, changed["changed_sources"])
        self.assertTrue(changed["alert"])
        self.step("Save comparison baseline")
        self.assertEqual(["policy version two"], self.remote_state()["sources"]["fixture"]["fragments"])


if __name__ == "__main__":
    unittest.main()
