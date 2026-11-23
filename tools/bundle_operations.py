#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verification adapter for the dormant central bundle operations workflow.

Emit only validated same-run publication outputs. Execute from the captured trusted
main checkout, with no dependency installation or candidate execution.
"""

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path

import bundle_admission as admission
import bundle_proposal as writer


def publish(root, path, digest, receipt=None):
    if receipt is not None:
        admission.context()
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("candidate artifact digest mismatch")
    plan = json.loads(raw)
    writer.validate_plan(root, plan)
    if type(plan["pr"]) is not int or plan["pr"] < 1:
        raise ValueError("invalid publication PR number")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        writer.publish(root, path, digest)
    head = output.getvalue().strip()
    if not writer.SHA.fullmatch(head):
        raise ValueError("uncertain publication output; inspect remote before retry")
    if receipt is not None:
        receipt_digest = admission.save_receipt(plan, head, digest, receipt)
        print(f"receipt_digest={receipt_digest}")
    # The branch and number are from the sealed plan, never a second PR lookup.
    print(f"head={head}\npr={plan['pr']}\nbranch={plan['branch']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["publish"])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    publish(writer.bundle.lock.repo_root(), args.plan, args.sha256, args.receipt)


if __name__ == "__main__":
    main()
