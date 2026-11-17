# SPDX-License-Identifier: Apache-2.0
"""Reviewer coverage for releases.specs.files (issue 100 step 3).

``v2_problems`` refuses a lock whose specs pin does not record well-formed digests for
every scenario file, a published "missing" file that now exists is refused, and a specs
pin that checks out as a different commit is refused.
"""

import copy
import json
from pathlib import Path

import pytest

from test_lock_files import (
    DESCRIPTION,
    HALLWAY_DESCRIPTION,
    HALLWAY_PARAMETERS,
    PARAMETERS,
    _digest,
    _generate,
    _project,
    _specs_files,
    lock_tool,
)

ROOT = Path(__file__).parents[1]

CORNER = "unitree-g1-stock-mujoco-corner"
CORNER_PARAMETERS = "specs/scenarios/walk-corner/parameters.json"


def _committed(name: str) -> dict:
    return json.loads((ROOT / "verifications" / f"{name}.lock.json").read_text())


def _without_files(lock: dict) -> None:
    del lock["releases"]["specs"]["files"]


def _files_not_object(lock: dict) -> None:
    lock["releases"]["specs"]["files"] = list(lock["releases"]["specs"]["files"])


def _short_digest(lock: dict) -> None:
    lock["releases"]["specs"]["files"][CORNER_PARAMETERS] = "abc123"


def _escaping_path(lock: dict) -> None:
    lock["releases"]["specs"]["files"]["../outside.json"] = None


def _absolute_path(lock: dict) -> None:
    lock["releases"]["specs"]["files"]["/etc/passwd"] = None


def _missing_parameters_path(lock: dict) -> None:
    del lock["releases"]["specs"]["files"][CORNER_PARAMETERS]


def test_committed_lock_has_no_v2_problems():
    assert lock_tool.v2_problems(_committed(CORNER)) == []


@pytest.mark.parametrize(
    "weaken",
    [
        _without_files,
        _files_not_object,
        _short_digest,
        _escaping_path,
        _absolute_path,
        _missing_parameters_path,
    ],
)
def test_v2_problems_refuses_incomplete_spec_file_digests(weaken):
    lock = copy.deepcopy(_committed(CORNER))
    weaken(lock)
    problems = lock_tool.v2_problems(lock)
    assert any("releases.specs.files" in problem for problem in problems), problems


def test_a_published_missing_file_that_now_exists_is_refused(tmp_path, monkeypatch):
    definition, existing, _commit = _project(
        tmp_path, monkeypatch, specs_files=_specs_files(parameters=PARAMETERS), specs_tag="v2.3.0"
    )
    existing["releases"]["specs"]["files"] = {
        HALLWAY_PARAMETERS: None,
        HALLWAY_DESCRIPTION: _digest(DESCRIPTION),
    }
    with pytest.raises(lock_tool.LockError, match="not the published missing"):
        _generate(tmp_path, definition, existing)


def test_a_specs_pin_fetched_as_another_commit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(lock_tool, "checkout", lambda repository, ref, dest: "e" * 40)
    with pytest.raises(lock_tool.LockError, match="fetched as"):
        lock_tool.spec_file_digests(
            "Living-Proof-Labs/robot-capability-specs",
            "a" * 40,
            "walk-hallway@0.1.0",
            tmp_path / "work",
        )
