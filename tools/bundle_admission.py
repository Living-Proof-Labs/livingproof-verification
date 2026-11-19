# SPDX-License-Identifier: Apache-2.0
"""Fail-closed public evidence reads and bounded same-run publication receipts."""

import hashlib
import json
import os
import re
import time
import urllib.request

import bundle_proposal as writer

ACTIONS_APP = 15368
WORKFLOWS = {
    ".github/workflows/checks.yml": "checks",
    ".github/workflows/verify.yml": "verification",
}
WORKFLOW_REF = "Living-Proof-Labs/.github/.github/workflows/bundle-completion.yml@refs/heads/main"


class PendingEvidence(ValueError):
    """A scheduled exact-head check has not produced complete evidence yet."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("evidence redirect refused")


def public_read(path):
    # The App intentionally has no Checks/Actions read grants. Public evidence uses
    # no credential; rate limits, denied reads, malformed or truncated data refuse.
    if not re.fullmatch(r"[A-Za-z0-9_./?=&-]+", path) or ".." in path:
        raise ValueError("invalid evidence path")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{writer.REPO}/{path}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "bundle-admission"},
    )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
        return json.load(response)


def context():
    env = os.environ
    if (
        env.get("GITHUB_REPOSITORY") != "Living-Proof-Labs/.github"
        or env.get("GITHUB_REF") != "refs/heads/main"
        or env.get("GITHUB_WORKFLOW_REF") != WORKFLOW_REF
        or env.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}
        or not writer.SHA.fullmatch(env.get("GITHUB_SHA", ""))
    ):
        raise ValueError("receipt requires trusted central workflow")
    return {
        "run_id": int(env["GITHUB_RUN_ID"]),
        "attempt": int(env["GITHUB_RUN_ATTEMPT"]),
        "workflow_sha": env["GITHUB_SHA"],
        "workflow_ref": WORKFLOW_REF,
    }


def save_receipt(plan, head, digest, path):
    record = {
        **context(),
        "repository": writer.REPO,
        "pr": plan["pr"],
        "branch": plan["branch"],
        "main": plan["main"],
        "head": head,
        "plan_digest": digest,
        "created": int(time.time()),
    }
    raw = (json.dumps(record, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def load_receipt(path, digest):
    raw = path.read_bytes()
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("publication receipt digest mismatch")
    record = json.loads(raw)
    current = context()
    if (
        record["repository"] != writer.REPO
        or type(record["pr"]) is not int
        or record["pr"] < 1
        or not writer.BRANCH.fullmatch(record["branch"])
        or any(not writer.SHA.fullmatch(record[key]) for key in ["head", "main"])
        or not re.fullmatch(r"[0-9a-f]{64}", record["plan_digest"])
        or any(record[key] != current[key] for key in ["run_id", "workflow_sha", "workflow_ref"])
        or not 1 <= record["attempt"] <= current["attempt"] <= record["attempt"] + 2
        or not 0 <= time.time() - record["created"] <= 86400
    ):
        raise ValueError("publication receipt is stale or belongs to another run")
    return record


def protections(read):
    rules = read("rules/branches/main")
    queues = [r for r in rules if r["type"] == "merge_queue"]
    checks = [r for r in rules if r["type"] == "required_status_checks"]
    if not queues or not checks:
        raise ValueError("required checks and merge queue must be server-enforced")
    if any(r["parameters"].get("grouping_strategy") != "ALLGREEN" for r in queues):
        raise ValueError("merge queue must require fresh successful merge-group checks")
    required = set()
    for rule in checks:
        if rule["parameters"].get("strict_required_status_checks_policy") is not True:
            raise ValueError("strict required checks missing")
        required.update(
            (c["context"], c.get("integration_id"))
            for c in rule["parameters"]["required_status_checks"]
        )
    if not {(name, ACTIONS_APP) for name in WORKFLOWS.values()} <= required:
        raise ValueError("trusted required check contexts missing")
    for rule in queues + checks:
        # Missing bypass evidence is not an empty bypass list.
        detail = read(f"rulesets/{int(rule['ruleset_id'])}")
        if detail.get("enforcement") != "active" or detail.get("bypass_actors") != []:
            raise ValueError("active no-bypass ruleset evidence missing")


def successful_checks(record, read):
    head = record["head"]
    result = read(f"actions/runs?head_sha={head}&event=pull_request&per_page=100")
    runs = result["workflow_runs"]
    if result["total_count"] != len(runs) or len(runs) >= 100:
        raise ValueError("incomplete workflow evidence")
    result = read(f"commits/{head}/check-runs?filter=latest&per_page=100")
    checks = result["check_runs"]
    if result["total_count"] != len(checks) or len(checks) >= 100:
        raise ValueError("incomplete check evidence")
    for path, name in WORKFLOWS.items():
        matching = [r for r in runs if r.get("path") == path]
        if not matching:
            raise PendingEvidence("trusted workflow has not reported")
        if len(matching) != 1:
            raise ValueError("ambiguous trusted workflow run")
        run = matching[0]
        if run.get("status") in {"queued", "in_progress"} and run.get("conclusion") is None:
            raise PendingEvidence("trusted workflow still pending")
        if (
            run.get("head_sha") != head
            or run.get("event") != "pull_request"
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or run.get("repository", {}).get("full_name") != writer.REPO
            or run.get("head_repository", {}).get("full_name") != writer.REPO
            or not any(
                p.get("number") == record["pr"]
                and p.get("head", {}).get("sha") == head
                and p.get("base", {}).get("sha") == record["main"]
                for p in run.get("pull_requests", [])
            )
        ):
            raise ValueError("workflow did not successfully verify this head and base")
        matching = [c for c in checks if c.get("name") == name]
        if not matching:
            raise PendingEvidence("required check has not reported")
        if len(matching) != 1:
            raise ValueError("ambiguous required check")
        check = matching[0]
        if check.get("status") in {"queued", "in_progress"} and check.get("conclusion") is None:
            raise PendingEvidence("required check still pending")
        if (
            check.get("head_sha") != head
            or check.get("app", {}).get("id") != ACTIONS_APP
            or check.get("check_suite", {}).get("id") != run.get("check_suite_id")
            or not run.get("check_suite_id")
            or check.get("status") != "completed"
            or check.get("conclusion") != "success"
        ):
            raise ValueError("required check is not trusted successful exact-head evidence")
