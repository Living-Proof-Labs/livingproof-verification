# SPDX-License-Identifier: Apache-2.0
"""A walled run with no measured wall geoms, or an origin start, fails the guard.

Fixtures under tests/fixtures/world-guard come from verify run 44141446534.
g1-playground-hallway and g1-stock-corner are the artifact bytes. no-walls and
origin-start are those hallway trials with one edit each, labeled in
PROVENANCE.json. The playground run never touches a wall (wall_contacts is
empty) and must still pass: the measured geoms are wall_intrusion.walls.
"""

import hashlib
import importlib.util
import json
import re
import shutil
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


# sha256 of the evidence files in verify run 44141446534, as PROVENANCE.json cites.
# g1-playground-hallway and g1-stock-corner must stay those artifact bytes.
_SOURCE_DIGESTS = {
    "g1-playground-hallway": {
        "manifest.json": "6a024d9fc4cd8e3884a12d8cc6de0794fa535d38e3b6ee0ea167752d19becc3b",
        "run-manifest.json": "fdf3dd3756822c8180a73843c408c1365d69d5a8d82cf5aacefc7ecda5666b02",
        "trials/trial-01.json": "48b6078f3deb763cd1595d727bff52baac95b335de96e57db1402ee6ece9840b",
        "trials/trial-02.json": "48b6078f3deb763cd1595d727bff52baac95b335de96e57db1402ee6ece9840b",
        "trials/trial-03.json": "48b6078f3deb763cd1595d727bff52baac95b335de96e57db1402ee6ece9840b",
    },
    "g1-stock-corner": {
        "manifest.json": "d5a18b8cd0372f3dcb083c4ad000deda4a7243813349b6f3e563b37cde6941c8",
        "run-manifest.json": "8038512b5914ddbc0e4bef5e604703e93e6d3eac21f898680972c8a395fb3a81",
        "trials/trial-01.json": "8337886b0509110df270dc34c6a78d6c3632dad46c05aac733f55a2e813f5424",
    },
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _changed_fields(source, edited, path="$"):
    """JSON paths whose value was replaced. A replaced object or list counts as one field."""
    if source == edited:
        return []
    if isinstance(source, dict) and isinstance(edited, dict) and set(source) == set(edited):
        changed = []
        for key in source:
            changed.extend(_changed_fields(source[key], edited[key], f"{path}.{key}"))
        return changed
    if isinstance(source, list) and isinstance(edited, list) and len(source) == len(edited):
        changed = []
        for index, (left, right) in enumerate(zip(source, edited)):
            changed.extend(_changed_fields(left, right, f"{path}[{index}]"))
        return changed
    return [path]


def test_unmodified_fixtures_match_cited_source_digests():
    for name, files in _SOURCE_DIGESTS.items():
        root = FIXTURES / name
        for rel, digest in files.items():
            path = root / rel
            assert path.is_file(), path
            assert _sha256(path) == digest, rel


def test_derived_fixtures_differ_from_source_by_one_field():
    for name in ("trial-01.json", "trial-02.json", "trial-03.json"):
        source = json.loads((HALLWAY / "trials" / name).read_text())
        no_walls = json.loads((NO_WALLS / "trials" / name).read_text())
        origin = json.loads((ORIGIN / "trials" / name).read_text())
        assert _changed_fields(source, no_walls) == ["$.wall_intrusion.walls"]
        assert no_walls["wall_intrusion"]["walls"] == {}
        assert _changed_fields(source, origin) == ["$.trace.samples[0][1]"]
        assert origin["trace"]["samples"][0][1] == 0.0
    for rel in ("manifest.json", "run-manifest.json"):
        assert _sha256(NO_WALLS / rel) == _SOURCE_DIGESTS["g1-playground-hallway"][rel]
        assert _sha256(ORIGIN / rel) == _SOURCE_DIGESTS["g1-playground-hallway"][rel]


def test_world_fixtures_hash_to_their_filenames_and_locks():
    for evidence in (HALLWAY, CORNER):
        pin = json.loads((evidence / "manifest.json").read_text())["lock"]["content"]["world"]
        path = WORLDS / f"{pin['sha256']}.json"
        assert _sha256(path) == pin["sha256"]
        assert path.name == f"{pin['sha256']}.json"


def test_cli_exit_codes_on_independently_edited_copies(tmp_path):
    good = tmp_path / "good"
    shutil.copytree(HALLWAY, good)
    assert _cli(good).returncode == 0

    empty = tmp_path / "empty-walls"
    shutil.copytree(HALLWAY, empty)
    for trial in (empty / "trials").glob("*.json"):
        payload = json.loads(trial.read_text())
        payload["wall_intrusion"]["walls"] = {}
        trial.write_text(json.dumps(payload))
    missing = _cli(empty)
    assert missing.returncode == 1, missing.stderr
    assert "wall_intrusion.walls" in missing.stderr

    origin = tmp_path / "origin-start"
    shutil.copytree(HALLWAY, origin)
    for trial in (origin / "trials").glob("*.json"):
        payload = json.loads(trial.read_text())
        columns = payload["trace"]["columns"]
        sample = payload["trace"]["samples"][0]
        sample[columns.index("x")] = 0.0
        sample[columns.index("y")] = 0.0
        trial.write_text(json.dumps(payload))
    started = _cli(origin)
    assert started.returncode == 1, started.stderr
    assert "not the origin" in started.stderr


def _verification_job_text() -> str:
    lines = (ROOT / ".github/workflows/verify.yml").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line == "  verification:")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.fullmatch(r"  [A-Za-z0-9_.-]+:", lines[index]):
            end = index
            break
    return "\n".join(lines[start:end])


def test_verification_job_invokes_world_guard_without_masking():
    job = _verification_job_text()
    assert re.search(r"^    timeout-minutes: 10$", job, re.M)
    checkout = re.search(
        r"uses: actions/checkout@([0-9a-f]{40})\n        with:\n          persist-credentials: false",
        job,
    )
    download = re.search(r"uses: actions/download-artifact@([0-9a-f]{40})\n", job)
    assert checkout, job
    assert download, job
    assert "run: python3 tools/world_guard.py evidence" in job
    guard_line = next(line for line in job.splitlines() if "world_guard.py" in line)
    assert "||" not in guard_line
    assert "|" not in guard_line.replace("world_guard.py", "")
    assert "continue-on-error" not in job
    assert "secrets:" not in job
    assert "permissions:" not in job
    assert "deploy" not in job.lower()
