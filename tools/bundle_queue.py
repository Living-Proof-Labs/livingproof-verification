#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Admit a receipt-bound completion only with trusted checks and server protections.

Only reviewed main code may run this with the head supplied by the same trusted
publisher job. Never discover completion commits by message/author or execute PR code.
"""

import argparse
import json
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


def enqueue(record, read=None):
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


def wait_for_checks(path, digest):
    # Bounded read-only polling, never retry a mutation or an unknown outcome.
    # Public API denial and terminal CI failures fail immediately. Recovery is a
    # failed-job rerun using the original receipt, never another publication.
    for attempt in range(25):
        record = admission.load_receipt(path, digest)
        try:
            return enqueue(record)
        except admission.PendingEvidence:
            if attempt == 24:
                raise ValueError(
                    "CI evidence wait exhausted; rerun failed queue job only"
                ) from None
            time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    wait_for_checks(args.receipt, args.sha256)


if __name__ == "__main__":
    main()
