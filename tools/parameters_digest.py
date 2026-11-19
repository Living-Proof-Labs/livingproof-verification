#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check that an evidence manifest records the lock's scenario parameters digest (#145).

livingproof-sim-mujoco v3.5.0 (livingproof-sim-mujoco#104) writes the path and sha256 of the one
read of the scenario parameters file to ``sources.parameters`` in the evidence ``manifest.json``.
The verification lock records the digest it expects as ``sources.parameters`` (and
``releases.specs.files``). A run is accepted only when the three agree. A manifest that records
nothing (a runner before v3.5.0) is refused, never skipped.

    python3 tools/parameters_digest.py verifications/<name>.lock.json evidence/manifest.json ...
    python3 tools/parameters_digest.py --evidence evidence --locks verifications
    python3 tools/parameters_digest.py --fixture RUN_ID HEAD_SHA ARTIFACT evidence/manifest.json

The second form checks every downloaded ``<definition>-evidence-<configuration>/manifest.json``
against ``<definition>.lock.json`` (the verification job runs it) and fails on an empty directory.
The third form prints the trimmed manifest that tests/fixtures/corner-evidence keeps.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HEX64 = re.compile(r"[0-9a-f]{64}")


def _object(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def recorded_source(manifest: dict) -> dict | None:
    """The manifest's ``sources.parameters`` record, or None when it records none."""
    record = _object(_object(manifest.get("sources")).get("parameters"))
    return record or None


def recorded_digest(manifest: dict) -> str | None:
    record = recorded_source(manifest)
    return record.get("sha256") if record else None


def _runner(manifest: dict) -> dict:
    return _object(_object(manifest.get("environment")).get("runner"))


def problems(lock: dict, manifest: dict) -> list[str]:
    """Every way the manifest's recorded parameters digest fails to match the lock's."""
    found: list[str] = []
    expected = _object(_object(lock.get("sources")).get("parameters"))
    if not expected:
        return ["lock has no sources.parameters"]
    path, sha = expected.get("path"), expected.get("sha256")
    if not isinstance(sha, str) or not HEX64.fullmatch(sha):
        found.append(f"lock sources.parameters.sha256 {sha!r} must be 64 hex characters")
    listed = _object(_object(_object(lock.get("releases")).get("specs")).get("files")).get(path)
    if listed != sha:
        found.append(
            f"lock sources.parameters.sha256 {sha} differs from releases.specs.files[{path}] {listed}"
        )
    runner = _runner(manifest)
    record = recorded_source(manifest)
    if record is None:
        found.append(
            "evidence manifest records no sources.parameters "
            f"(runner {runner.get('tag', 'unknown')}; recorded from livingproof-sim-mujoco v3.5.0)"
        )
        return found
    recorded_sha = record.get("sha256")
    if not isinstance(recorded_sha, str) or not HEX64.fullmatch(recorded_sha):
        found.append(f"recorded sha256 {recorded_sha!r} must be 64 hex characters")
    elif recorded_sha != sha:
        found.append(f"recorded sha256 {recorded_sha} differs from the lock's {sha}")
    if record.get("path") != path:
        found.append(f"recorded path {record.get('path')!r} differs from the lock's {path!r}")
    wanted = _object(_object(lock.get("releases")).get("runner")).get("commit")
    if runner.get("commit") != wanted:
        found.append(f"runner commit {runner.get('commit')!r} differs from the lock's {wanted!r}")
    return found


def trimmed(manifest: dict, workflow_run: int, head_sha: str, artifact: str) -> dict:
    """The few manifest fields this check reads, with where they came from."""
    keep = {
        "format": manifest.get("format"),
        "run_id": manifest.get("run_id"),
        "created": manifest.get("created"),
        "scenario": _object(_object(manifest.get("lock")).get("content")).get("scenario"),
        "environment": {"runner": _runner(manifest)},
    }
    if manifest.get("sources"):
        keep["sources"] = manifest["sources"]
    return {
        "workflow_run": workflow_run,
        "head_sha": head_sha,
        "artifact": artifact,
        "manifest": keep,
    }


def load_object(path: Path) -> tuple[dict | None, str | None]:
    """The JSON object in a file, or (None, why) when it is missing, not JSON or not an object."""
    try:
        value = json.loads(Path(path).read_text())
    except OSError as exc:
        return None, f"cannot read: {exc.strerror or exc}"
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(value, dict):
        return None, "not a JSON object"
    return value, None


def check_directory(evidence: Path, locks: Path) -> int:
    """Check each downloaded run against its definition's lock; no runs at all is a failure."""
    manifests = sorted(evidence.glob("*-evidence-*/manifest.json"))
    if not manifests:
        print(f"FAIL {evidence}: no */manifest.json evidence to check")
        return 1
    status = 0
    for path in manifests:
        lock_path = locks / f"{path.parent.name.split('-evidence-')[0]}.lock.json"
        if not lock_path.is_file():
            print(f"FAIL {path}: no lock {lock_path}")
            status = 1
            continue
        status |= main([str(lock_path), str(path)])
    return status


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--evidence":
        if len(argv) != 4 or argv[2] != "--locks":
            print(__doc__, file=sys.stderr)
            return 2
        return check_directory(Path(argv[1]), Path(argv[3]))
    if argv and argv[0] == "--fixture":
        if len(argv) != 5:
            print(__doc__, file=sys.stderr)
            return 2
        manifest, why = load_object(Path(argv[4]))
        if manifest is None:
            print(f"FAIL {argv[4]}: {why}")
            return 1
        print(json.dumps(trimmed(manifest, int(argv[1]), argv[2], argv[3]), indent=2))
        return 0
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    lock, why = load_object(Path(argv[0]))
    if lock is None:
        print(f"FAIL {argv[0]}: {why}")
        return 1
    status = 0
    for name in argv[1:]:
        manifest, why = load_object(Path(name))
        if manifest is None:
            print(f"FAIL {name}: {why}")
            status = 1
            continue
        found = problems(lock, manifest)
        if found:
            status = 1
            for item in found:
                print(f"FAIL {name}: {item}")
        else:
            print(f"OK   {name}: sources.parameters {recorded_digest(manifest)} == lock")
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
