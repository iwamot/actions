import importlib.util
import unittest
from copy import deepcopy
from itertools import chain, combinations, product
from pathlib import Path

spec = importlib.util.spec_from_file_location("release_wait", Path(__file__).with_name("wait.py"))
wait = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wait)


def subsets(items):
    return chain.from_iterable(combinations(items, size) for size in range(len(items) + 1))


# --- which rulesets apply ------------------------------------------------------

PATTERNS = ("~ALL", "refs/tags/v*.*.*", "refs/tags/v1.*", "refs/tags/v2.*")
REFS = ("refs/tags/v1.2.3", "refs/tags/v2.0.0")
# Which pattern matches which ref, written out by hand.
MATCHES = {
    ("~ALL", "refs/tags/v1.2.3"): True,
    ("~ALL", "refs/tags/v2.0.0"): True,
    ("refs/tags/v*.*.*", "refs/tags/v1.2.3"): True,
    ("refs/tags/v*.*.*", "refs/tags/v2.0.0"): True,
    ("refs/tags/v1.*", "refs/tags/v1.2.3"): True,
    ("refs/tags/v1.*", "refs/tags/v2.0.0"): False,
    ("refs/tags/v2.*", "refs/tags/v1.2.3"): False,
    ("refs/tags/v2.*", "refs/tags/v2.0.0"): True,
}
RULES = [
    {"type": "deletion"},
    {"type": "required_signatures"},
    {"type": "required_deployments", "parameters": {"required_deployment_environments": ["production"]}},
    {
        "type": "required_status_checks",
        "parameters": {
            "required_status_checks": [
                {"context": "validate", "integration_id": 15368},
                {"context": "legacy"},
            ]
        },
    },
    {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "self-test", "integration_id": 15368}]}},
]
DECLARED = {("validate", 15368), ("legacy", None), ("self-test", 15368)}


class RulesetTests(unittest.TestCase):
    def test_only_active_rulesets_on_tags_are_read(self):
        for target, enforcement in product(("tag", "branch", "push"), ("active", "evaluate", "disabled")):
            summary = {"id": 1, "target": target, "enforcement": enforcement}
            self.assertEqual(wait.enforced_on_tags(summary), (target, enforcement) == ("tag", "active"), summary)

    def test_a_ruleset_requires_all_its_checks_where_it_applies_and_none_elsewhere(self):
        for include, exclude, ref in product(subsets(PATTERNS), subsets(PATTERNS), REFS):
            rule = {"conditions": {"ref_name": {"include": list(include), "exclude": list(exclude)}}, "rules": RULES}
            before = deepcopy(rule)
            applies = any(MATCHES[p, ref] for p in include) and not any(MATCHES[p, ref] for p in exclude)
            case = (include, exclude, ref)
            self.assertEqual(wait.checks_required_by(rule, ref), DECLARED if applies else set(), case)
            self.assertEqual(rule, before, case)

    def test_a_ruleset_without_conditions_applies_nowhere(self):
        self.assertEqual(wait.checks_required_by({"rules": RULES}, REFS[0]), set())


# --- which required checks are still pending or failed --------------------------

PASSING = ("success", "neutral", "skipped")
FAILING = ("failure", "cancelled", "timed_out", "action_required", "stale", "startup_failure")
RUN_STATES = [("queued", None), ("in_progress", None)] + [("completed", c) for c in PASSING + FAILING]
STATUS_STATES = ("pending", "success", "failure", "error")


def outcome_of_run(status, conclusion):
    if status != "completed":
        return "pending"
    return "passed" if conclusion in PASSING else "failed"


def outcome_of_status(state):
    return {"pending": "pending", "success": "passed"}.get(state, "failed")


def run(run_id, name, app, status, conclusion):
    return {"id": run_id, "name": name, "app": {"id": app}, "status": status, "conclusion": conclusion, "html_url": f"https://example/run/{run_id}"}


def status(status_id, context, state):
    return {"id": status_id, "context": context, "state": state, "target_url": f"https://example/status/{status_id}"}


def outcome(check, runs, statuses):
    pending, failed = wait.classify([check], runs, statuses)
    if pending:
        return "pending"
    return "failed" if failed else "passed"


def answers(check, item):
    """Tell whether a check run or status reports for the required check.

    A commit status carries no app, so one under the check's name reports for
    it whichever app the check names.
    """
    name, app = check
    if "app" in item:
        return item["name"] == name and app in (None, item["app"]["id"])
    return item["context"] == name


class ClassifyTests(unittest.TestCase):
    def test_one_run_or_status_decides_as_its_state_says(self):
        for check in (("validate", 15368), ("validate", None)):
            for state in RUN_STATES:
                self.assertEqual(outcome(check, [run(1, "validate", 15368, *state)], []), outcome_of_run(*state), (check, state))
        for state in STATUS_STATES:
            self.assertEqual(outcome(("legacy", None), [], [status(1, "legacy", state)]), outcome_of_status(state), state)

    def test_the_newest_report_for_a_check_decides_and_others_do_not_matter(self):
        # Two runs and a status, from the required app or another, under the
        # required name or another, in either order.
        run_shapes = list(product(("validate", "other"), (15368, 999), RUN_STATES))
        status_shapes = [None] + list(product(("validate", "other"), STATUS_STATES))
        for check in (("validate", 15368), ("validate", None)):
            for first, second, status_shape in product(run_shapes, run_shapes, status_shapes):
                runs = [run(1, first[0], first[1], *first[2]), run(2, second[0], second[1], *second[2])]
                statuses = [] if status_shape is None else [status(7, *status_shape)]
                case = (check, first, second, status_shape)
                before = deepcopy((runs, statuses))
                got = outcome(check, runs, statuses)
                self.assertEqual((runs, statuses), before, case)
                self.assertEqual(outcome(check, runs[::-1], statuses), got, case)

                reporting_runs = [r for r in runs if answers(check, r)]
                reporting_statuses = [s for s in statuses if answers(check, s)]
                if reporting_runs:
                    newest = max(reporting_runs, key=lambda r: r["id"])
                    self.assertEqual(got, outcome_of_run(newest["status"], newest["conclusion"]), case)
                elif reporting_statuses:
                    self.assertEqual(got, outcome_of_status(reporting_statuses[0]["state"]), case)
                else:
                    self.assertEqual(got, "pending", case)

    def test_the_newest_status_decides_among_statuses(self):
        for older, newer in product(STATUS_STATES, repeat=2):
            statuses = [status(2, "legacy", newer), status(1, "legacy", older)]
            self.assertEqual(outcome(("legacy", None), [], statuses), outcome_of_status(newer), (older, newer))

    def test_several_checks_are_sorted_independently_and_named(self):
        required = [("validate", 15368), ("legacy", None), ("self-test", 15368)]
        for states in product(RUN_STATES + [None], STATUS_STATES + (None,), RUN_STATES + [None]):
            runs = [run(n, name, 15368, *state) for n, (name, state) in enumerate(zip(("validate", None, "self-test"), states)) if name and state]
            statuses = [status(1, "legacy", states[1])] if states[1] else []
            pending, failed = wait.classify(required, runs, statuses)
            expected_pending = [c for c in required if outcome(c, runs, statuses) == "pending"]
            expected_failed = [c for c in required if outcome(c, runs, statuses) == "failed"]
            self.assertEqual([p.split(":")[0].split(" ")[0] for p in pending], [c[0] for c in expected_pending], states)
            self.assertEqual([f.split(" ")[0] for f in failed], [c[0] for c in expected_failed], states)

    def test_each_line_links_to_what_reported_it(self):
        pending, failed = wait.classify([("validate", 15368)], [run(1, "validate", 15368, "in_progress", None)], [])
        self.assertEqual(pending, ["validate: https://example/run/1"])
        pending, failed = wait.classify([("validate", 15368)], [run(1, "validate", 15368, "completed", "failure")], [])
        self.assertEqual(failed, ["validate (failure): https://example/run/1"])
        pending, failed = wait.classify([("legacy", None)], [], [status(1, "legacy", "error")])
        self.assertEqual(failed, ["legacy (error): https://example/status/1"])
        pending, failed = wait.classify([("validate", 15368)], [], [])
        self.assertEqual(pending, ["validate (not reported yet)"])


if __name__ == "__main__":
    unittest.main()
