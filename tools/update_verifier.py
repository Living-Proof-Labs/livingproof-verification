#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Complete either kind of Renovate proposal with one reviewed verifier release."""

import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile

import lock

REPOSITORY = "Living-Proof-Labs/capability-verifier"
CALL = re.compile(
    r"(?m)^(?P<prefix>[ \t]+uses: "
    + re.escape(REPOSITORY + "/" + lock.PROCEDURE)
    + r"@)[0-9a-f]{40}[ \t]+# v\d+\.\d+\.\d+[ \t]*$"
)
IMAGE = re.compile(re.escape(lock.VERIFIER_IMAGE) + r":\d+\.\d+\.\d+@sha256:[0-9a-f]{64}")


def update(root: Path, tag: str) -> list[Path]:
    """Stage resolution first: network/validation failures leave tracked files untouched."""
    commit = lock.resolve_tag(REPOSITORY, tag)
    if lock.tag_of_commit(REPOSITORY, commit) != tag:
        raise lock.LockError("requested release does not uniquely identify the verifier commit")
    paths = [
        Path(".github/workflows/verify.yml"),
        Path(".devcontainer/devcontainer.json"),
        Path("README.md"),
        Path(".github/requirements/lock-check-git.txt"),
        Path(".github/requirements/lock-check.in"),
    ]
    paths += [p.relative_to(root) for p in lock.definition_paths(root)]
    paths += [
        lock.lock_path(root / p).relative_to(root) for p in paths.copy() if p.suffix == ".toml"
    ]
    original = {p: (root / p).read_bytes() for p in paths}
    with tempfile.TemporaryDirectory(prefix="verifier-update-") as directory:
        stage = Path(directory)
        for path in paths:
            (stage / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / path, stage / path)
        workflow = stage / paths[0]
        text, count = CALL.subn(lambda m: f"{m['prefix']}{commit}  # {tag}", workflow.read_text())
        if count != len(lock.definition_paths(stage)):
            raise lock.LockError("expected one immutable walk-hallway call per definition")
        workflow.write_text(text)
        # Definition comments identify the release; the generator retains their hashes,
        # so leave definition bytes untouched rather than inventing new evidence hashes.
        written = lock.update_locks(stage)
        verifier = lock.read_lock(written[0])["releases"]["verifier"]
        if any(lock.read_lock(path)["releases"]["verifier"] != verifier for path in written):
            raise lock.LockError("verifier resolution changed between configurations; retry")
        if verifier["tag"] != tag or verifier["commit"] != commit:
            raise lock.LockError("resolved verifier differs from requested release")
        pinned = f"{verifier['image']}:{tag.removeprefix('v')}@{verifier['digest']}"
        for path in paths[1:3]:
            target = stage / path
            text, count = IMAGE.subn(pinned, target.read_text())
            if count != 1:
                raise lock.LockError(f"{path}: expected exactly one immutable hallway image")
            target.write_text(text)
        if json.loads((stage / paths[1]).read_text())["image"] != pinned:
            raise lock.LockError("devcontainer image did not match the resolved release")
        changed = [p for p in paths if (stage / p).read_bytes() != original[p]]
        if any((root / p).read_bytes() != original[p] for p in paths):
            raise lock.LockError("checkout changed during resolution; retry on a stable branch")
        for path in changed:
            (root / path).write_bytes((stage / path).read_bytes())
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="reviewed published verifier tag, vX.Y.Z")
    args = parser.parse_args()
    changed = update(lock.repo_root(), args.tag)
    print("Updated: " + ", ".join(map(str, changed)))
    print("Review the diff, run exact-head CI, and commit with a fix: or chore: prefix.")


if __name__ == "__main__":
    main()
