#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Admit a receipt-bound completion only with trusted checks and server protections.

Only reviewed main code may run this with the head supplied by the same trusted
publisher job. Never discover completion commits by message/author or execute PR code.
"""

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import bundle_admission as admission

import bundle_proposal as writer


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


def verify_receipt_handoff(path, expected_digest, run_id, run_attempt):
    """The receipt downloaded for this run id and attempt, or a refusal.

    The file has to be the artifact publish uploaded for this run, and its
    sha256 has to be the receipt digest publish output, not the plan digest.
    """
    receipt = Path(path)
    if not receipt.is_file():
        raise ValueError("receipt artifact is missing")
    if type(run_id) is not int or type(run_attempt) is not int or run_id < 1 or run_attempt < 1:
        raise ValueError("receipt run attempt is wrong or stale")
    raw = receipt.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected_digest)):
        raise ValueError("receipt digest does not match")
    if actual != expected_digest:
        raise ValueError("receipt digest does not match")
    record = json.loads(raw)
    if record.get("run_id") != run_id or record.get("attempt") != run_attempt:
        raise ValueError("receipt run attempt is wrong or stale")
    return record


def refuse_github_token_enqueue():
    """Stop before enqueuePullRequest when the only token is GITHUB_TOKEN.

    https://docs.github.com/en/actions/using-workflows/triggering-a-workflow#triggering-a-workflow-from-a-workflow
    says events triggered by GITHUB_TOKEN do not create a workflow run, except
    workflow_dispatch, repository_dispatch, and some pull_request activity.
    merge_group is not one of those exceptions, so a queue entry made with
    GITHUB_TOKEN would not run the required merge-group checks.
    """
    raise ValueError(
        "Refusing to enqueue with GITHUB_TOKEN: events triggered by the "
        "GITHUB_TOKEN will not create a merge_group workflow run "
        "(https://docs.github.com/en/actions/using-workflows/triggering-a-workflow"
        "#triggering-a-workflow-from-a-workflow)"
    )


def enqueue(record, read=None):
    admission.require_created(record)
    admission.arm_job_deadline()
    read = read or admission.public_read
    number, head, branch = record["pr"], record["head"], record["branch"]
    pr = target(number, head, branch)
    # Inspect remote queue state before every attempt, including recovery from an
    # uncertain mutation. Already queued is a no-op, never another queue write.
    query = """query($id: ID!) {
      node(id: $id) { ... on PullRequest { headRefOid mergeQueueEntry { id } } }
    }"""
    response = json.loads(
        writer.command(
            "gh",
            "api",
            "graphql",
            "--input",
            "-",
            data=json.dumps({"query": query, "variables": {"id": pr["node_id"]}}),
        )
    )
    node = (response.get("data") or {}).get("node")
    if (
        response.get("errors")
        or not node
        or node.get("headRefOid") != head
        or "mergeQueueEntry" not in node
    ):
        raise ValueError("queue state is uncertain; inspect before retry")
    if node["mergeQueueEntry"] is not None:
        if not node["mergeQueueEntry"].get("id"):
            raise ValueError("queue state is uncertain; inspect before retry")
        return
    snapshot = admission.successful_checks(record, read)
    admission.protections(read)
    # Re-read after evidence collection. A base race after this read is covered by
    # the server's required fresh merge-group checks, not a client-side base lease.
    target(number, head, branch)
    if writer.api("git/ref/heads/main")["object"]["sha"] != record["main"]:
        raise ValueError("base moved since publication; no queue write")
    admission.protections(read)
    admission.revalidate_checks(record, snapshot, read)
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


# Unauthenticated GitHub allows 60 requests an hour. These reads stay
# unauthenticated. Poll every 15 minutes. A head with 108 check runs is two
# pages, and the workflow-run listing is about two pages as well, so a poll
# is about 4 requests: 16 an hour. A worst-case walk of 20 pages for each
# listing is 40 requests a poll, 160 an hour, which exceeds 60. Then
# X-RateLimit-Remaining 0 waits until X-RateLimit-Reset, and a 403 or 429
# waits for Retry-After. Every one of those waits counts against the same
# 5.5h deadline. A wait that would pass it refuses. The next scheduled or
# triggered run retries. This process does not start another cron.
POLL_INTERVAL = 15 * 60
# Backstop if the clock does not move. The 5.5h deadline is what stops a wait.
_MAX_POLLS = 30


def wait_for_checks(path, digest):
    # Bounded read-only polling, never retry a mutation or an unknown outcome.
    # Public API denial and terminal CI failures fail immediately. Recovery is
    # the next scheduled or triggered run, never a cron this process starts.
    record = admission.load_receipt(path, digest)
    admission.require_created(record)
    deadline = time.time() + admission.EVIDENCE_DEADLINE
    admission.arm_deadline(deadline)
    try:
        for attempt in range(_MAX_POLLS):
            try:
                return enqueue(record)
            except admission.PendingEvidence:
                if attempt == _MAX_POLLS - 1 or time.time() + POLL_INTERVAL > deadline:
                    raise ValueError(admission.DEADLINE_MESSAGE) from None
                time.sleep(POLL_INTERVAL)
                record = admission.load_receipt(path, digest)
                admission.require_created(record)
    finally:
        admission.arm_deadline(None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--run-attempt", type=int)
    parser.add_argument(
        "--refuse-github-token",
        action="store_true",
        help="check the receipt handoff, then refuse before enqueue",
    )
    args = parser.parse_args()
    if args.refuse_github_token:
        if (
            args.receipt is None
            or args.sha256 is None
            or args.run_id is None
            or args.run_attempt is None
        ):
            raise ValueError("receipt handoff requires the receipt, its digest, and this run")
        verify_receipt_handoff(args.receipt, args.sha256, args.run_id, args.run_attempt)
        refuse_github_token_enqueue()
    if args.receipt is None or args.sha256 is None:
        parser.error("--receipt and --sha256 are required")
    wait_for_checks(args.receipt, args.sha256)


if __name__ == "__main__":
    main()
