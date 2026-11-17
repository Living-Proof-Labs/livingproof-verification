#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepared exact-head queue coordinator; no workflow calls this command yet.

Only reviewed main code may run this with the head supplied by the same trusted
publisher job. Never discover completion commits by message/author or execute PR code.
"""

import argparse
import json
import time

import bundle_proposal as writer

GATES = {"checks", "renovate", "verification"}
ACTIONS_APP = 15368
WORKFLOWS = {".github/workflows/checks.yml", ".github/workflows/verify.yml"}


def target(number, head, branch):
    if not writer.SHA.fullmatch(head) or not writer.BRANCH.fullmatch(branch):
        raise ValueError("invalid trusted publication output")
    pr = writer.api(f"pulls/{number}")
    if (
        pr["state"] != "open"
        or pr["draft"]
        or pr["user"]["login"] != "renovate[bot]"
        or pr["base"]["ref"] != "main"
        or pr["base"]["repo"]["full_name"] != writer.REPO
        or pr["head"]["repo"]["full_name"] != writer.REPO
        or pr["head"]["ref"] != branch
        or pr["head"]["sha"] != head
    ):
        raise ValueError("publication head/target changed; do not enqueue")
    return pr


def gates_enforced():
    # Active rules applying to main, including inherited rules. Additional classic
    # protection requirements remain GitHub's responsibility at queue entry.
    rules = writer.api("rules/branches/main")
    required = {
        check["context"]
        for rule in rules
        if rule["type"] == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
        if check.get("integration_id") == ACTIONS_APP
    }
    if not any(r["type"] == "merge_queue" for r in rules) or not GATES <= required:
        raise ValueError("normal queue must enforce checks, renovate and verification from Actions")


def ready(number, head):
    response = writer.api(f"actions/runs?event=pull_request&head_sha={head}&per_page=100")
    if response["total_count"] > 100:
        raise ValueError("too many runs; cannot establish latest final-head checks")
    latest = {}
    for run in sorted(response["workflow_runs"], key=lambda r: r["id"], reverse=True):
        if (
            run["head_sha"] == head
            and run["event"] == "pull_request"
            and run["head_repository"]["full_name"] == writer.REPO
            and any(pr["number"] == number for pr in run["pull_requests"])
            and run["path"] in WORKFLOWS
        ):
            latest.setdefault(run["path"], run)
    if set(latest) != WORKFLOWS:
        return False
    for path, run in latest.items():
        if run["status"] != "completed":
            return False
        if run["conclusion"] != "success":
            raise ValueError(f"final-head workflow failed: {path}")
        response = writer.api(f"actions/runs/{run['id']}/jobs?filter=latest&per_page=100")
        if response["total_count"] > 100:
            raise ValueError("too many jobs; cannot establish final-head gates")
        jobs = {job["name"]: job for job in response["jobs"]}
        expected = {"verification"} if path.endswith("verify.yml") else {"checks", "renovate"}
        if any(
            name not in jobs
            or jobs[name]["head_sha"] != head
            or jobs[name]["status"] != "completed"
            or jobs[name]["conclusion"] != "success"
            for name in expected
        ):
            raise ValueError("missing, skipped or unsuccessful final-head gate")
    return True


def enqueue(number, head, branch, *, wait_seconds=3600):
    deadline = time.monotonic() + wait_seconds
    while True:
        target(number, head, branch)
        gates_enforced()
        if ready(number, head):
            break
        if time.monotonic() >= deadline:
            raise ValueError(
                "final-head checks timed out; retain completion, retry trusted queue job"
            )
        time.sleep(min(30, max(0, deadline - time.monotonic())))
    pr = target(number, head, branch)
    gates_enforced()
    # Direct queue request, never deferred auto-merge, bypass or queue jumping.
    query = """mutation($id: ID!, $head: GitObjectID!) {
      enqueuePullRequest(input: {pullRequestId: $id, expectedHeadOid: $head, jump: false}) {
        mergeQueueEntry { id }
      }
    }"""
    response = json.loads(
        writer.command(
            "gh",
            "api",
            "graphql",
            "--input",
            "-",
            data=json.dumps({"query": query, "variables": {"id": pr["node_id"], "head": head}}),
        )
    )
    entry = (response.get("data") or {}).get("enqueuePullRequest") or {}
    if response.get("errors") or not (entry.get("mergeQueueEntry") or {}).get("id"):
        raise ValueError("queue refused or returned an uncertain response; inspect before retry")
    print(json.dumps(response["data"]["enqueuePullRequest"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args()
    enqueue(args.pr, args.head, args.branch)


if __name__ == "__main__":
    main()
