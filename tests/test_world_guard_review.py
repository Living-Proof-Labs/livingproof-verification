"""Reviewer tests for #126: world_guard fails closed on the cases its own tests leave open.

Each case edits a copy of the unmodified run 44141446534 fixtures (see PROVENANCE.json).
"""

import fnmatch
import json
import re
import shutil
import subprocess
import sys

import pytest
import test_world_guard as wg

guard = wg.guard


def _copy(tmp_path, source=wg.HALLWAY):
    target = tmp_path / source.name
    shutil.copytree(source, target)
    return target


def _edit_json(path, edit):
    payload = json.loads(path.read_text())
    edit(payload)
    path.write_text(json.dumps(payload))


def _set_start(trial, x, y):
    columns = trial["trace"]["columns"]
    sample = trial["trace"]["samples"][0]
    sample[columns.index("x")] = x
    sample[columns.index("y")] = y


def test_a_run_that_measured_only_some_declared_walls_fails(tmp_path):
    evidence = _copy(tmp_path)
    _edit_json(
        evidence / "trials/trial-02.json",
        lambda t: t["wall_intrusion"]["walls"].pop("wall-left"),
    )
    problems = guard.violations(evidence, wg.world_for(evidence))
    assert any("trial-02.json wall_intrusion.walls is wall-right" in p for p in problems), problems


def test_a_run_whose_environment_recorded_no_walls_fails(tmp_path):
    evidence = _copy(tmp_path)
    _edit_json(evidence / "manifest.json", lambda m: m["environment"].update(walls=[]))
    problems = guard.violations(evidence, wg.world_for(evidence))
    assert any("environment.walls is []" in p for p in problems), problems


def test_a_walled_run_with_no_trials_fails_when_preconditions_were_met(tmp_path):
    evidence = _copy(tmp_path)
    _edit_json(evidence / "manifest.json", lambda m: m["trials"].update(results=[]))
    problems = guard.violations(evidence, wg.world_for(evidence))
    assert any("no trials were recorded" in p for p in problems), problems


def _unmet(manifest):
    manifest["preconditions"].append({"id": "reviewer-unmet", "met": False})


def test_an_unmet_precondition_with_no_trials_is_not_a_world_fallback(tmp_path):
    evidence = _copy(tmp_path)

    def edit(manifest):
        _unmet(manifest)
        manifest["trials"]["results"] = []

    _edit_json(evidence / "manifest.json", edit)
    assert guard.violations(evidence, wg.world_for(evidence)) == []


def test_an_unmet_precondition_does_not_excuse_recorded_trials(tmp_path):
    evidence = _copy(tmp_path)
    _edit_json(evidence / "manifest.json", _unmet)
    for trial in (evidence / "trials").glob("*.json"):
        _edit_json(trial, lambda t: t["wall_intrusion"].update(walls={}))
    problems = guard.violations(evidence, wg.world_for(evidence))
    assert any("wall_intrusion.walls is empty" in p for p in problems), problems


def test_a_boolean_start_coordinate_is_refused(tmp_path):
    evidence = _copy(tmp_path)
    _edit_json(evidence / "trials/trial-01.json", lambda t: _set_start(t, False, False))
    with pytest.raises(guard.WorldGuardError, match="start x is not a number"):
        guard.violations(evidence, wg.world_for(evidence))


@pytest.mark.parametrize("make", ["empty", "missing"])
def test_the_cli_fails_when_no_evidence_was_downloaded(tmp_path, make):
    evidence = tmp_path / "evidence"
    if make == "empty":
        evidence.mkdir()
    run = subprocess.run(
        [sys.executable, str(wg.ROOT / "tools/world_guard.py"), str(evidence)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 1
    assert "no evidence manifest.json found" in run.stderr


def test_the_download_pattern_matches_the_cited_evidence_artifacts():
    job = wg._verification_job_text()
    pattern = re.search(r"pattern: '([^']+)'", job).group(1)
    for name in ("g1-playground-hallway", "g1-stock-corner"):
        provenance = json.loads((wg.FIXTURES / name / "PROVENANCE.json").read_text())
        assert "/actions/runs/44141446534" in provenance["source"]
        assert fnmatch.fnmatchcase(provenance["artifact"], pattern), provenance["artifact"]
