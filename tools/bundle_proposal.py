#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Trusted prepare/publish boundary for reporter/verifier Renovate proposals.

Run this code only from reviewed main. Prepare needs read-only access; publish accepts
only that trusted job's sealed JSON, never a PR artifact. No proposal code is executed.
Activation and normal-queue entry are intentionally separate (docs/bundle-automation.md).
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import update_bundle as bundle

REPO = "Living-Proof-Labs/livingproof-verification"
URL = f"https://github.com/{REPO}.git"
SHA = re.compile(r"[0-9a-f]{40}")
BRANCH = re.compile(
    r"renovate/(capability-verifier-evidence|living-proof-labs-livingproof-capability-report-\d+\.x)"
)


def command(*args, cwd=None, data=None):
    return subprocess.run(
        args, cwd=cwd, input=data, text=True, check=True, stdout=subprocess.PIPE
    ).stdout.strip()


def api(path, data=None):
    args = ["gh", "api", f"repos/{REPO}/{path}"]
    if data is not None:
        args += ["--method", "POST", "--input", "-"]
    return json.loads(command(*args, data=json.dumps(data) if data is not None else None))


def proposal(number, head, main):
    if not SHA.fullmatch(head) or not SHA.fullmatch(main):
        raise ValueError("expected immutable main/head SHA")
    pr = api(f"pulls/{number}")
    if (
        pr["state"] != "open"
        or pr["draft"]
        or pr["user"]["login"] != "renovate[bot]"
        or pr["base"]["ref"] != "main"
        or pr["head"]["repo"]["full_name"] != REPO
        or pr["base"]["repo"]["full_name"] != REPO
        or pr["head"]["sha"] != head
        or not BRANCH.fullmatch(pr["head"]["ref"])
    ):
        raise ValueError("not an eligible exact-head same-repository Renovate proposal")
    if api("git/ref/heads/main")["object"]["sha"] != main:
        raise ValueError("main moved; regenerate")
    # A bot-only single-commit proposal directly on current main. Never overwrite custom
    # completion, merged main, other contributors, or a stale Renovate base.
    commit = api(f"commits/{head}")
    if commit.get("author", {}).get("login") != "renovate[bot]" or [
        p["sha"] for p in commit["parents"]
    ] != [main]:
        raise ValueError("proposal contains custom commits or a stale base; leave it alone")
    return pr


def inputs(root, head, branch):
    """Treat proposal files as inert data; compare all other bytes with trusted main."""
    changed = command("git", "diff", "--name-only", "HEAD", head, cwd=root).splitlines()
    reporter = "capability-report" in branch
    allowed = (
        {"verifications/settings.toml"}
        if reporter
        else {".github/workflows/verify.yml", ".devcontainer/devcontainer.json"}
    )
    if not changed or not set(changed) <= allowed:
        raise ValueError("proposal has custom files")
    tags = set()
    for path in changed:
        # Reject symlinks and other special modes before inspecting text.
        entry = command("git", "ls-tree", head, "--", path, cwd=root).split()
        if not entry:
            raise ValueError(f"proposal deletes required input: {path}")
        if entry[0] != "100644":
            raise ValueError("proposal is not a regular file")
        old = (root / path).read_text()
        new = command("git", "show", f"{head}:{path}", cwd=root) + "\n"
        if reporter:
            pattern = r'(?m)(^\[reporter\]\r?\n(?:^(?!\[)[^\r\n]*\r?\n)*?^tag = ")(v\d+\.\d+\.\d+)(")(?=\r?$)'
            matches = [match.group(2) for match in re.finditer(pattern, new)]
            if len(matches) != 1:
                raise ValueError("expected one reporter tag")
            tags.update(matches)

            def normalize(s):
                return re.sub(pattern, r"\g<1>VERSION\g<3>", s)
        elif path.endswith("verify.yml"):
            matches = list(bundle.update_verifier.CALL.finditer(new))
            if len(matches) != len(bundle.lock.definition_paths(root)):
                raise ValueError("expected all verifier calls")
            tags.update(re.findall(r"# (v\d+\.\d+\.\d+)", m[0])[0] for m in matches)

            def normalize(s):
                return bundle.update_verifier.CALL.sub(lambda m: m["prefix"] + "PIN", s)
        else:
            matches = bundle.update_verifier.IMAGE.findall(new)
            if len(matches) != 1:
                raise ValueError("expected one image")
            tag = re.search(r":(\d+\.\d+\.\d+)@", matches[0])[1]
            tags.add("v" + tag)

            def normalize(s):
                return bundle.update_verifier.IMAGE.sub("IMAGE", s)

        if normalize(old) != normalize(new):
            raise ValueError("proposal contains custom edits")
    if len(tags) != 1:
        raise ValueError("proposal does not identify one coherent release")
    tag = tags.pop()
    if reporter:
        current = bundle.lock.load_reporter_setting(root / bundle.SETTINGS)["tag"]
    else:
        first = bundle.lock.definition_paths(root)[0]
        current = bundle.lock.read_lock(bundle.lock.lock_path(first))["releases"]["verifier"]["tag"]
    selected_version = tuple(map(int, tag[1:].split(".")))
    current_version = tuple(map(int, current[1:].split(".")))
    if selected_version[0] != current_version[0] or selected_version < current_version:
        raise ValueError("major releases and downgrades require explicit review")
    return {"reporter" if reporter else "verifier": tag}


def prepare(root, number, head, main, output):
    if command("git", "rev-parse", "HEAD", cwd=root) != main:
        raise ValueError("prepare must use the exact trusted main checkout")
    if command("git", "status", "--porcelain", "--untracked-files=no", cwd=root):
        raise ValueError("prepare needs a clean checkout")
    pr = proposal(number, head, main)
    command("git", "fetch", "--no-tags", URL, head, cwd=root)
    selected = inputs(root, head, pr["head"]["ref"])
    proposed_paths = command("git", "diff", "--name-only", "HEAD", head, cwd=root).splitlines()
    changed = bundle.update(root, **selected)
    # Include discovery files even when generated bytes equal main (digest-only repair).
    changed = sorted(set(changed) | {Path(p) for p in proposed_paths})
    snapshot = {str(p): (root / p).read_bytes() for p in bundle.paths(root)}
    if bundle.update(root, **selected):
        raise ValueError("generation is not byte-stable")
    # Full CI runs on the resulting PR head; prepare additionally checks all generated
    # locks with the offline guards. No wheel/dependency execution in the publisher.
    command(
        os.sys.executable, "-m", "pytest", "tests/test_locks.py", "tests/test_corner.py", cwd=root
    )
    if any((root / p).read_bytes() != value for p, value in snapshot.items()):
        raise ValueError("tests modified the candidate")
    files = {str(p): base64.b64encode((root / p).read_bytes()).decode() for p in changed}
    plan = {
        "repository": REPO,
        "pr": number,
        "head": head,
        "main": main,
        "branch": pr["head"]["ref"],
        "files": files,
        "selection": selected,
    }
    output.write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n")
    print(hashlib.sha256(output.read_bytes()).hexdigest())


def validate_plan(root, plan):
    if plan["repository"] != REPO or not BRANCH.fullmatch(plan["branch"]):
        raise ValueError("unexpected publication target")
    allowed = {str(p) for p in bundle.paths(root)}
    # Definitions are frozen in this slice; the updater never changes them.
    allowed -= {str(p.relative_to(root)) for p in bundle.lock.definition_paths(root)}
    if not plan["files"] or not set(plan["files"]) <= allowed:
        raise ValueError("candidate contains unexpected files")
    if not set(plan["selection"]) <= {"reporter", "verifier"} or not plan["selection"]:
        raise ValueError("unexpected selected input")
    if any(not bundle.lock.TAG.fullmatch(tag) for tag in plan["selection"].values()):
        raise ValueError("unexpected release")
    for encoded in plan["files"].values():
        base64.b64decode(encoded, validate=True).decode("utf-8")


def publish(root, path, digest):
    # The digest must come from the trusted prepare job output, never from the artifact.
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("candidate artifact digest mismatch")
    plan = json.loads(path.read_text())
    validate_plan(root, plan)
    if command("git", "rev-parse", "HEAD", cwd=root) != plan["main"]:
        raise ValueError("publisher code must be the same trusted main revision")
    pr = proposal(plan["pr"], plan["head"], plan["main"])
    if pr["head"]["ref"] != plan["branch"]:
        raise ValueError("proposal branch changed")
    parent = api(f"git/commits/{plan['head']}")
    entries = []
    for name, content in sorted(plan["files"].items()):
        blob = api("git/blobs", {"content": content, "encoding": "base64"})
        entries.append({"path": name, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = api("git/trees", {"base_tree": parent["tree"]["sha"], "tree": entries})
    commit = api(
        "git/commits",
        {
            "message": "chore: complete verified dependency bundle",
            "tree": tree["sha"],
            "parents": [plan["head"]],
        },
    )
    proposal(plan["pr"], plan["head"], plan["main"])
    # A lease is an atomic compare-and-swap against the expected branch head. The new
    # commit has that exact head as parent: no custom commit can be discarded. No checkout,
    # hooks, filters or proposal scripts run in this empty publication repository.
    with tempfile.TemporaryDirectory(prefix="bundle-publish-") as directory:
        command("git", "init", "--bare", directory)
        command("git", "fetch", "--no-tags", URL, commit["sha"], cwd=directory)
        command(
            "git",
            "-c",
            "credential.helper=!gh auth git-credential",
            "push",
            f"--force-with-lease=refs/heads/{plan['branch']}:{plan['head']}",
            URL,
            f"{commit['sha']}:refs/heads/{plan['branch']}",
            cwd=directory,
        )
    print(commit["sha"])


def discover(main):
    """Select one eligible proposal; a single repository concurrency group serializes it."""
    root = bundle.lock.repo_root()
    if command("git", "rev-parse", "HEAD", cwd=root) != main:
        raise ValueError("discovery must use the exact trusted main checkout")
    for pr in api("pulls?state=open&base=main&per_page=100"):
        try:
            eligible = proposal(pr["number"], pr["head"]["sha"], main)
            command("git", "fetch", "--no-tags", URL, pr["head"]["sha"], cwd=root)
            # Inspect inert blobs before choosing: a major/malformed/custom proposal
            # must not monopolize every scheduled run. Prepare rechecks these inputs.
            inputs(root, pr["head"]["sha"], eligible["head"]["ref"])
        except ValueError:
            continue
        return {"pr": pr["number"], "head": pr["head"]["sha"]}
    return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    discovery = sub.add_parser("discover")
    discovery.add_argument("--main", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--pr", type=int, required=True)
    prep.add_argument("--head", required=True)
    prep.add_argument("--main", required=True)
    prep.add_argument("--output", type=Path, required=True)
    pub = sub.add_parser("publish")
    pub.add_argument("--plan", type=Path, required=True)
    pub.add_argument("--sha256", required=True)
    args = parser.parse_args()
    root = bundle.lock.repo_root()
    if args.action == "discover":
        print(json.dumps(discover(args.main)))
    elif args.action == "prepare":
        prepare(root, args.pr, args.head, args.main, args.output)
    else:
        publish(root, args.plan, args.sha256)


if __name__ == "__main__":
    main()
