# SPDX-License-Identifier: Apache-2.0
"""The checks job that runs tests/bundle_release_rerun.test.mjs stays on the release pin.

The re-run cases themselves live in that Node test (the shared engine, git-only, no
GitHub plugin). This file only checks that CI actually runs it, on the same reviewed
collaboration-guidelines tag and commit as verify.yml, with the same pinned Node as
the renovate job and no extra secrets.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).parents[1]
CHECKS = yaml.safe_load((ROOT / ".github/workflows/checks.yml").read_text())
VERIFY = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())
JOB = CHECKS["jobs"]["bundle-release-engine"]
# Reviewed tag v9.6.10 of Living-Proof-Labs/collaboration-guidelines.
SHARED_RELEASE_SHA = "54dcb297c628a4853c79bb31b5155e859afb7e93"


def test_the_engine_job_is_read_only_and_has_no_secrets():
    assert JOB["permissions"] == {"contents": "read"}
    assert JOB.get("secrets") is None
    assert JOB.get("environment") is None
    dumped = yaml.safe_dump(JOB)
    assert "secrets." not in dumped
    assert "GITHUB_TOKEN" not in dumped
    assert (ROOT / "tests/bundle_release_rerun.test.mjs").is_file()


def test_the_engine_job_uses_the_same_node_pin_as_renovate():
    renovate = CHECKS["jobs"]["renovate"]
    assert JOB["env"]["NODE_VERSION"] == renovate["env"]["NODE_VERSION"]
    assert JOB["env"]["NODE_SHA256"] == renovate["env"]["NODE_SHA256"]
    install = next(step for step in JOB["steps"] if step.get("name") == "Install Node.js")
    assert "sha256sum -c" in install["run"]
    assert "nodejs.org/dist" in install["run"]


def test_the_engine_job_runs_the_rerun_test_on_the_pinned_shared_release():
    release_tag = VERIFY["jobs"]["release"]["uses"].partition("@")[2]
    assert JOB["env"]["SHARED_RELEASE_TAG"] == release_tag
    assert JOB["env"]["SHARED_RELEASE_SHA"] == SHARED_RELEASE_SHA
    checkouts = [
        step for step in JOB["steps"] if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert len(checkouts) == 2
    shared = checkouts[1]["with"]
    assert shared["repository"] == "Living-Proof-Labs/collaboration-guidelines"
    assert shared["ref"] == SHARED_RELEASE_SHA  # the commit, not a moving tag
    assert shared["persist-credentials"] is False
    assert shared["path"] == "collaboration-guidelines"
    install = next(
        step
        for step in JOB["steps"]
        if step.get("name") == "Install the pinned shared release engine"
    )
    assert "npm ci" in install["run"]
    assert "--omit=dev" in install["run"]
    assert "--ignore-scripts" in install["run"]
    assert 'rev-parse HEAD)" = "$SHARED_RELEASE_SHA"' in install["run"]
    run = next(
        step
        for step in JOB["steps"]
        if step.get("name") == "Re-run of an already tagged commit publishes nothing"
    )
    assert run["run"] == "node --test tests/bundle_release_rerun.test.mjs"
    assert (
        "collaboration-guidelines/.github/actions/semantic-release"
        in run["env"]["SHARED_RELEASE_ACTION"]
    )


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
def test_the_pinned_tag_still_resolves_to_the_recorded_commit():
    tag = JOB["env"]["SHARED_RELEASE_TAG"]
    listing = subprocess.check_output(
        [
            "git",
            "ls-remote",
            "https://github.com/Living-Proof-Labs/collaboration-guidelines.git",
            f"refs/tags/{tag}",
        ],
        text=True,
        timeout=30,
    ).strip()
    # A tag points at the annotated-tag object; peel it with ^{} when present.
    peeled = None
    tagged = None
    for line in listing.splitlines():
        sha, ref = line.split()
        if ref.endswith("^{}"):
            peeled = sha
        else:
            tagged = sha
    assert (peeled or tagged) == SHARED_RELEASE_SHA, listing
