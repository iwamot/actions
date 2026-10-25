import contextlib
import importlib.util
import io
import os
import unittest
from unittest.mock import patch
from pathlib import Path

spec = importlib.util.spec_from_file_location("release_wait", Path(__file__).with_name("wait.py"))
wait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wait)


class RequiredChecksTests(unittest.TestCase):
    def test_only_matching_active_tag_rulesets_are_used(self):
        summaries = [[{"id": 1, "target": "tag", "enforcement": "active"}, {"id": 2, "target": "branch", "enforcement": "active"}, {"id": 3, "target": "tag", "enforcement": "evaluate"}, {"id": 4, "target": "tag", "enforcement": "active"}]]
        def api(path, paginate=False):
            if path.endswith("rulesets?per_page=100"):
                return summaries
            return {"conditions": {"ref_name": {"include": ["refs/tags/v*.*.*"], "exclude": ["refs/tags/v1.*"] if path.endswith("/4") else []}}, "rules": [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "validate", "integration_id": 15368}]}}]}
        with patch.object(wait, "api", side_effect=api):
            self.assertEqual(wait.required_checks("owner/repo", "v1.2.3"), [("validate", 15368)])

    def states(self, runs, statuses=None, required=None):
        def api(path, paginate=False):
            return [{"check_runs": runs}] if "check-runs" in path else [{"statuses": statuses or []}]
        with patch.object(wait, "api", side_effect=api):
            return wait.check_states("owner/repo", "abc", required or [("validate", 15368)])

    def run_check(self, **overrides):
        run = dict(id=1, name="validate", app={"id": 15368}, status="completed", conclusion="success", html_url="https://example/check")
        run.update(overrides)
        return run

    def test_success_neutral_and_skipped_are_accepted(self):
        for conclusion in ["success", "neutral", "skipped"]:
            self.assertEqual(self.states([self.run_check(conclusion=conclusion)]), ([], []))

    def test_pending_and_missing_checks_are_waited_for(self):
        for runs in [[], [self.run_check(status="in_progress", conclusion=None)]]:
            pending, failed = self.states(runs)
            self.assertEqual(len(pending), 1)
            self.assertFalse(failed)

    def test_wrong_app_cannot_satisfy_required_check(self):
        pending, failed = self.states([self.run_check(app={"id": 999})])
        self.assertEqual(len(pending), 1)
        self.assertFalse(failed)

    def test_latest_result_wins(self):
        pending, failed = self.states([self.run_check(conclusion="success"), self.run_check(id=2, conclusion="failure")])
        self.assertFalse(pending)
        self.assertIn("failure", failed[0])

    def test_failure_and_cancellation_stop_release(self):
        for conclusion in ["failure", "cancelled", "timed_out", "action_required"]:
            self.assertEqual(len(self.states([self.run_check(conclusion=conclusion)])[1]), 1)

    def test_legacy_status_is_supported(self):
        self.assertEqual(self.states([], [{"id": 1, "context": "legacy", "state": "success"}], [("legacy", None)]), ([], []))
        self.assertTrue(self.states([], [{"id": 1, "context": "legacy", "state": "failure"}], [("legacy", None)])[1])

    def test_pending_then_success_retries(self):
        with patch.dict(os.environ, GH_REPO="owner/repo", SOURCE_SHA="abc", RELEASE_TAG="v1.2.3"), patch.object(wait, "required_checks", return_value=[("validate", 15368)]), patch.object(wait, "check_states", side_effect=[(["validate: https://example/check"], []), ([], [])]), patch.object(wait.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()) as output:
            wait.main()
            sleep.assert_called_once_with(10)
            self.assertIn("https://example/check", output.getvalue())

    def test_failure_and_timeout_exit(self):
        for result, message in [(([], ["validate: https://example/check"]), "Required CI failed"), ((["validate"], []), "Timed out")]:
            with patch.dict(os.environ, GH_REPO="owner/repo", SOURCE_SHA="abc", RELEASE_TAG="v1.2.3", WAIT_SECONDS="0"), patch.object(wait, "required_checks", return_value=[("validate", 15368)]), patch.object(wait, "check_states", return_value=result), contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(SystemExit, message):
                wait.main()

    def test_no_requirements_does_not_poll(self):
        with patch.dict(os.environ, GH_REPO="owner/repo", SOURCE_SHA="abc", RELEASE_TAG="v1.2.3"), patch.object(wait, "required_checks", return_value=[]), patch.object(wait, "check_states") as checks, contextlib.redirect_stdout(io.StringIO()):
            wait.main()
            checks.assert_not_called()

    def test_api_failure_is_not_treated_as_success(self):
        with patch.dict(os.environ, GH_REPO="owner/repo", SOURCE_SHA="abc", RELEASE_TAG="v1.2.3"), patch.object(wait, "required_checks", side_effect=RuntimeError("API denied")), self.assertRaisesRegex(RuntimeError, "API denied"):
            wait.main()


if __name__ == "__main__":
    unittest.main()
