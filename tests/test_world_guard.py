# SPDX-License-Identifier: Apache-2.0
"""A walled run with no measured wall geoms, or an origin start, fails the guard.

Fixtures under tests/fixtures/world-guard come from verify run 44141446534.
g1-playground-hallway and g1-stock-corner are the artifact bytes. no-walls and
origin-start are those hallway trials with one edit each, labeled in
PROVENANCE.json. The playground run never touches a wall (wall_contacts is
empty) and must still pass: the measured geoms are wall_intrusion.walls.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures/world-guard"
HALLWAY = FIXTURES / "g1-playground-hallway"
CORNER = FIXTURES / "g1-stock-corner"
NO_WALLS = FIXTURES / "no-walls"
ORIGIN = FIXTURES / "origin-start"
WORLDS = FIXTURES / "worlds"

spec = importlib.util.spec_from_file_location("world_guard", ROOT / "tools/world_guard.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def world_for(evidence: Path) -> dict:
    manifest = json.loads((evidence / "manifest.json").read_text())
    pin = manifest["lock"]["content"]["world"]
    body = (WORLDS / f"{pin['sha256']}.json").read_bytes()
    return guard.load_world(pin, lambda _url, payload=body: payload)


def test_real_playground_hallway_passes_with_zero_wall_contacts():
    """The current G1 playground run records no contacts and still measured both walls."""
    world = world_for(HALLWAY)
    for trial in sorted((HALLWAY / "trials").glob("*.json")):
        observation = json.loads(trial.read_text())
        assert observation["wall_contacts"] == []
        assert set(observation["wall_intrusion"]["walls"]) == {"wall-left", "wall-right"}
    assert guard.violations(HALLWAY, world) == []


def test_real_corner_evidence_passes():
    assert guard.violations(CORNER, world_for(CORNER)) == []


def test_no_walls_fixture_is_a_minimal_edit_and_fails():
    for name in ("trial-01.json", "trial-02.json", "trial-03.json"):
        real = json.loads((HALLWAY / "trials" / name).read_text())
        edited = json.loads((NO_WALLS / "trials" / name).read_text())
        assert real["wall_intrusion"]["walls"]
        real["wall_intrusion"]["walls"] = {}
        assert edited == real
    assert (NO_WALLS / "manifest.json").read_bytes() == (HALLWAY / "manifest.json").read_bytes()
    label = json.loads((NO_WALLS / "PROVENANCE.json").read_text())
    assert "wall_intrusion.walls" in label["edit"]
    problems = guard.violations(NO_WALLS, world_for(NO_WALLS))
    assert problems
    assert all("wall_intrusion.walls" in problem and "empty" in problem for problem in problems)


def test_origin_start_fixture_is_a_minimal_edit_and_fails():
    for name in ("trial-01.json", "trial-02.json", "trial-03.json"):
        real = json.loads((HALLWAY / "trials" / name).read_text())
        edited = json.loads((ORIGIN / "trials" / name).read_text())
        assert real["trace"]["samples"][0][1] == -2.5
        real["trace"]["samples"][0][1] = 0.0
        real["trace"]["samples"][0][2] = 0.0
        assert edited == real
    assert (ORIGIN / "manifest.json").read_bytes() == (HALLWAY / "manifest.json").read_bytes()
    label = json.loads((ORIGIN / "PROVENANCE.json").read_text())
    assert "0.0" in label["edit"]
    problems = guard.violations(ORIGIN, world_for(ORIGIN))
    assert len(problems) == 3
    assert all(
        "spawn-entrance" in problem and "starts at (0.0, 0.0)" in problem for problem in problems
    )


def test_origin_place_allows_an_origin_start(tmp_path):
    evidence = tmp_path / "origin-place"
    evidence.mkdir()
    manifest = json.loads((ORIGIN / "manifest.json").read_text())
    manifest["start"]["spawn"] = "spawn-centre"
    (evidence / "manifest.json").write_text(json.dumps(manifest))
    trials = evidence / "trials"
    trials.mkdir()
    for trial in sorted((ORIGIN / "trials").glob("*.json")):
        (trials / trial.name).write_text(trial.read_text())
    world = world_for(HALLWAY)
    centre = next(place for place in world["places"] if place["name"] == "spawn-centre")
    assert centre["position"][:2] == [0.0, 0.0]
    assert guard.violations(evidence, world) == []


def test_sha256_mismatch_fails_closed():
    pin = json.loads((HALLWAY / "manifest.json").read_text())["lock"]["content"]["world"]
    with pytest.raises(guard.WorldGuardError, match="does not match"):
        guard.load_world(pin, lambda _url: b"{}")


def _cli(evidence: Path) -> subprocess.CompletedProcess:
    pin = json.loads((evidence / "manifest.json").read_text())["lock"]["content"]["world"]
    world = WORLDS / f"{pin['sha256']}.json"
    return subprocess.run(
        [sys.executable, str(ROOT / "tools/world_guard.py"), str(evidence), "--world", str(world)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_passes_real_evidence_and_fails_the_derived_fixtures():
    passed = _cli(HALLWAY)
    assert passed.returncode == 0, passed.stderr
    assert "warning" not in passed.stderr.lower()
    corner = _cli(CORNER)
    assert corner.returncode == 0, corner.stderr
    missing = _cli(NO_WALLS)
    assert missing.returncode == 1
    assert "wall_intrusion.walls" in missing.stderr
    assert "warning" not in missing.stderr.lower()
    origin = _cli(ORIGIN)
    assert origin.returncode == 1
    assert "not the origin" in origin.stderr
    assert "warning" not in origin.stderr.lower()
