# SPDX-License-Identifier: Apache-2.0
"""Offline review guards: every definition has a current lock, and the thin workflow call
passes exactly the locked values. The full check (robot lock re-read at the locked commit)
is `livingproof capability verify lock --check --workflow .github/workflows/verify.yml`."""

import hashlib
import json
from pathlib import Path
import tomllib

import pytest

ROOT = Path(__file__).parents[1]
DEFINITIONS = sorted((ROOT / "verifications").glob("*.toml"))
WORKFLOW = (ROOT / ".github/workflows/verify.yml").read_text()


def test_there_is_at_least_one_definition():
    assert DEFINITIONS


@pytest.mark.parametrize("definition", DEFINITIONS, ids=lambda p: p.stem)
def test_lock_is_current_for_its_definition(definition):
    lock = json.loads(definition.with_name(definition.stem + ".lock.json").read_text())
    d = tomllib.loads(definition.read_text())
    assert lock["definition"]["sha256"] == hashlib.sha256(definition.read_bytes()).hexdigest()
    assert lock["robot"]["repository"] == d["robot"]["repository"]
    assert lock["robot"]["configurations"] == d["robot"]["configurations"]
    assert len(lock["robot"]["commit"]) == 40


@pytest.mark.parametrize("definition", DEFINITIONS, ids=lambda p: p.stem)
def test_workflow_calls_the_locked_verifier_robot_and_configurations(definition):
    lock = json.loads(definition.with_name(definition.stem + ".lock.json").read_text())
    v = lock["releases"]["verifier"]
    job = WORKFLOW.split(f"  {definition.stem}:\n", 1)[1]
    assert f"uses: {v['repository']}/.github/workflows/walk-hallway.yml@{v['commit']}" in job
    assert f"repository: {lock['robot']['repository']}" in job
    assert f"ref: {lock['robot']['commit']}" in job
    assert f"configurations: '{json.dumps(lock['robot']['configurations'])}'" in job
