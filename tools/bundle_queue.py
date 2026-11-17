#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepared exact-head queue coordinator; no workflow calls this command yet.

Only reviewed main code may run this with the head supplied by the same trusted
publisher job. Never discover completion commits by message/author or execute PR code.
"""

import argparse
import json

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


def enqueue(number, head, branch):
    # The trusted publisher has already validated the coherent bundle. Optional CI
    # is informational: GitHub's normal queue enforces the configured protections.
    pr = target(number, head, branch)
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
