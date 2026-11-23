"""Wait for the status checks required by active tag rulesets."""

import fnmatch
import json
import os
import subprocess
import sys
import time


def api(path, paginate=False):
    args = ["gh", "api", path]
    if paginate:
        args += ["--paginate", "--slurp"]
    return json.loads(subprocess.check_output(args, text=True))


def enforced_on_tags(summary):
    """Tell whether a ruleset summary is an active ruleset on tags."""
    return summary["target"] == "tag" and summary["enforcement"] == "active"


def checks_required_by(rule, ref):
    """Say which status checks one ruleset requires before `ref` is created.

    A ruleset applies when one of its include patterns matches the ref and
    none of its exclude patterns does. A check is a context together with the
    app that must report it, or None where any app or a legacy status may.
    The tag is validated as vX.Y.Z before this runs, so fnmatch's `*` matching
    `/` too never differs from GitHub's matching here.
    """
    patterns = rule.get("conditions", {}).get("ref_name", {})

    def matches(pattern):
        return pattern == "~ALL" or fnmatch.fnmatchcase(ref, pattern)

    if not any(map(matches, patterns.get("include", []))):
        return set()
    if any(map(matches, patterns.get("exclude", []))):
        return set()
    return {
        (check["context"], check.get("integration_id"))
        for item in rule["rules"]
        if item["type"] == "required_status_checks"
        for check in item["parameters"]["required_status_checks"]
    }


def required_checks(repo, tag):
    ref = f"refs/tags/{tag}"
    required = set()
    for page in api(f"repos/{repo}/rulesets?per_page=100", paginate=True):
        for summary in page:
            if enforced_on_tags(summary):
                required |= checks_required_by(api(f"repos/{repo}/rulesets/{summary['id']}"), ref)
    return sorted(required, key=lambda item: (item[0], item[1] or 0))


def check_states(repo, sha, required):
    runs = []
    for page in api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100&filter=latest", paginate=True):
        runs.extend(page["check_runs"])
    statuses = []
    if any(app is None for _, app in required):
        for page in api(f"repos/{repo}/commits/{sha}/status?per_page=100", paginate=True):
            statuses.extend(page["statuses"])
    return classify(required, runs, statuses)


def classify(required, runs, statuses):
    """Sort the required checks into those still pending and those that failed.

    A check run from the required app answers for its check, and the newest
    one (highest id) wins. Without one, a legacy commit status of the same
    context does, again the newest. A check nothing has reported yet is
    pending. Each required check lands in at most one of the two lists, and a
    check in neither has passed.
    """
    pending, failed = [], []
    for name, app in required:
        matching = [run for run in runs if run["name"] == name and (app is None or run["app"]["id"] == app)]
        if matching:
            run = max(matching, key=lambda item: item["id"])
            if run["status"] != "completed":
                pending.append(f"{name}: {run['html_url']}")
            elif run["conclusion"] not in {"success", "neutral", "skipped"}:
                failed.append(f"{name} ({run['conclusion']}): {run['html_url']}")
        else:
            matching_status = [status for status in statuses if status["context"] == name]
            if matching_status:
                status = max(matching_status, key=lambda item: item["id"])
                if status["state"] == "pending":
                    pending.append(f"{name}: {status.get('target_url') or ''}")
                elif status["state"] != "success":
                    failed.append(f"{name} ({status['state']}): {status.get('target_url') or ''}")
            else:
                pending.append(f"{name} (not reported yet)")
    return pending, failed


def main():
    repo, sha, tag = (os.environ[name] for name in ("GH_REPO", "SOURCE_SHA", "RELEASE_TAG"))
    required = required_checks(repo, tag)
    if not required:
        print(f"No required status checks configured for {tag}.", flush=True)
        return
    deadline = time.monotonic() + int(os.environ.get("WAIT_SECONDS", "600"))
    print(f"Checking required CI for {sha}: " + ", ".join(name for name, _ in required), flush=True)
    while True:
        pending, failed = check_states(repo, sha, required)
        if failed:
            sys.exit("Required CI failed; no tag was created:\n" + "\n".join(failed))
        if not pending:
            print("All required CI checks passed; ready to create the tag.", flush=True)
            return
        print("Waiting for required CI:\n" + "\n".join(pending), flush=True)
        if time.monotonic() >= deadline:
            sys.exit("Timed out waiting for required CI; no tag was created. Rerun after the linked checks finish.")
        time.sleep(10)


if __name__ == "__main__":
    main()
