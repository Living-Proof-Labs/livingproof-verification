# SPDX-License-Identifier: Apache-2.0
"""#145: each corner run records the digest of the parameters file it loaded, and it is the lock's.

livingproof-sim-mujoco#104 (released in v3.5.0) records ``sources.parameters`` (path and sha256 of
the one read of the scenario parameters file) in the evidence ``manifest.json``.
``tools/parameters_digest.py`` compares that record with the verification lock, so the check is
not done by hand. The fixtures are trimmed real manifests (tests/fixtures/README.md); a run
before v3.5.0 records nothing and must fail.
"""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location(
    "parameters_digest", ROOT / "tools/parameters_digest.py"
)
digest_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(digest_tool)

FIXTURES = ROOT / "tests/fixtures/corner-evidence"
CORNERS = sorted(
    path.name.removesuffix(".lock.json")
    for path in (ROOT / "verifications").glob("*-mujoco-corner-*.lock.json")
)
OTHER = "0" * 64


def _lock(name: str) -> dict:
    return json.loads((ROOT / "verifications" / f"{name}.lock.json").read_text())


def _manifest_for(lock: dict, runner: str | None = None) -> dict:
    """A manifest that records what the lock declares, as v3.5.0 and later do."""
    runner = runner or lock["releases"]["runner"]["tag"]
    return {
        "format": "livingproof-hallway-evidence/2.0.0",
        "sources": {"parameters": dict(lock["sources"]["parameters"])},
        "environment": {"runner": {"tag": runner, "commit": lock["releases"]["runner"]["commit"]}},
    }


def test_six_corner_locks_are_checked():
    assert len(CORNERS) == 6
    assert {name.rsplit("-", 1)[1] for name in CORNERS} == {"left", "right"}


@pytest.mark.parametrize("name", CORNERS)
def test_a_manifest_that_records_the_locks_parameters_digest_passes(name):
    lock = _lock(name)
    assert digest_tool.problems(lock, _manifest_for(lock)) == []
    assert (
        digest_tool.recorded_digest(_manifest_for(lock)) == lock["sources"]["parameters"]["sha256"]
    )


def test_a_manifest_with_no_recorded_parameters_digest_fails_naming_the_runner():
    """A run before livingproof-sim-mujoco v3.5.0 (for example v3.4.1) records no digest."""
    lock = _lock("unitree-g1-stock-mujoco-corner-left")
    manifest = _manifest_for(lock, runner="v3.4.1")
    del manifest["sources"]
    found = digest_tool.problems(lock, manifest)
    assert len(found) == 1
    assert "records no sources.parameters" in found[0] and "v3.4.1" in found[0]


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        pytest.param(
            lambda m: m["sources"]["parameters"].update(sha256=OTHER),
            "recorded sha256",
            id="digest-differs",
        ),
        pytest.param(
            lambda m: m["sources"]["parameters"].update(
                path="specs/scenarios/other/parameters.json"
            ),
            "recorded path",
            id="path-differs",
        ),
        pytest.param(
            lambda m: m["sources"]["parameters"].update(sha256="abc"),
            "must be 64 hex characters",
            id="malformed-digest",
        ),
        pytest.param(
            lambda m: m["environment"]["runner"].update(commit="1" * 40),
            "runner commit",
            id="runner-differs",
        ),
    ],
)
def test_a_manifest_that_disagrees_with_the_lock_fails(edit, message):
    lock = _lock("booster-t1-stock-mujoco-corner-right")
    manifest = _manifest_for(lock)
    edit(manifest)
    found = digest_tool.problems(lock, manifest)
    assert found and any(message in item for item in found), found


def test_a_lock_whose_specs_files_disagree_with_its_parameters_source_fails():
    lock = _lock("booster-t1-stock-mujoco-corner-left")
    path = lock["sources"]["parameters"]["path"]
    lock["releases"]["specs"]["files"][path] = OTHER
    found = digest_tool.problems(lock, _manifest_for(_lock("booster-t1-stock-mujoco-corner-left")))
    assert any("releases.specs.files" in item for item in found), found


def test_a_lock_without_a_parameters_source_fails():
    lock = _lock("booster-t1-stock-mujoco-corner-left")
    manifest = _manifest_for(lock)
    del lock["sources"]["parameters"]
    assert any(
        "lock has no sources.parameters" in item for item in digest_tool.problems(lock, manifest)
    )


def test_the_command_exits_non_zero_on_a_mismatch(tmp_path, capsys):
    name = "unitree-go1-playground-mujoco-corner-left"
    lock_path = ROOT / "verifications" / f"{name}.lock.json"
    manifest = _manifest_for(_lock(name))
    good = tmp_path / "good.json"
    good.write_text(json.dumps(manifest))
    assert digest_tool.main([str(lock_path), str(good)]) == 0
    manifest = copy.deepcopy(manifest)
    manifest["sources"]["parameters"]["sha256"] = OTHER
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(manifest))
    assert digest_tool.main([str(lock_path), str(bad)]) == 1
    assert "recorded sha256" in capsys.readouterr().out


@pytest.mark.parametrize("name", CORNERS)
def test_each_corner_run_on_ci_recorded_the_digest_in_its_lock(name):
    """Historical CI evidence matches the original lock at its recorded head, not today's."""
    fixture = json.loads((FIXTURES / f"{name}.json").read_text())
    assert isinstance(fixture["workflow_run"], int)
    assert fixture["head_sha"] == "797c1f9958ef5f606afe026aa3aa74db5036d765"
    lock = json.loads((FIXTURES / "locks" / f"{name}.lock.json").read_text())
    assert fixture["artifact"] == f"{name}-evidence-{lock['robot']['configurations'][0]}"
    manifest = fixture["manifest"]
    assert digest_tool.problems(lock, manifest) == []
    assert manifest["environment"]["runner"]["tag"] == lock["releases"]["runner"]["tag"]
    assert manifest["sources"]["parameters"] == lock["sources"]["parameters"]


@pytest.mark.parametrize(
    "name", ["unitree-g1-stock-mujoco-corner-left", "unitree-g1-stock-mujoco-corner-right"]
)
def test_historical_g1_evidence_cannot_verify_the_updated_runner(name):
    """The new robot release requires fresh hosted evidence; old manifests cannot pass."""
    fixture = json.loads((FIXTURES / f"{name}.json").read_text())
    found = digest_tool.problems(_lock(name), fixture["manifest"])
    assert len(found) == 1
    assert "runner commit" in found[0]


@pytest.mark.parametrize(
    "name", ["unitree-g1-stock-mujoco-corner-left", "unitree-g1-stock-mujoco-corner-right"]
)
def test_a_real_v3_4_1_run_records_no_digest_and_is_refused(name):
    """The pre-v3.5.0 evidence these locks used to produce (verify run 44423756063)."""
    fixture = json.loads((FIXTURES / "v3.4.1" / f"{name}.json").read_text())
    assert fixture["manifest"]["environment"]["runner"]["tag"] == "v3.4.1"
    assert "sources" not in fixture["manifest"]
    found = digest_tool.problems(_lock(name), fixture["manifest"])
    assert any("records no sources.parameters" in item for item in found), found


def _artifact_dir(root: Path, definition: str, manifest: dict) -> None:
    folder = root / f"{definition}-evidence-stock"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps(manifest))


def test_the_evidence_directory_check_covers_every_run_and_names_the_bad_one(tmp_path, capsys):
    """The `verification` job downloads every `*-evidence-*` artifact and runs this over it."""
    evidence = tmp_path / "evidence"
    for name in CORNERS:
        _artifact_dir(evidence, name, _manifest_for(_lock(name)))
    assert (
        digest_tool.main(["--evidence", str(evidence), "--locks", str(ROOT / "verifications")]) == 0
    )
    assert capsys.readouterr().out.count("OK ") == len(CORNERS)
    bad = _manifest_for(_lock(CORNERS[0]))
    bad["sources"]["parameters"]["sha256"] = OTHER
    (evidence / f"{CORNERS[0]}-evidence-stock" / "manifest.json").write_text(json.dumps(bad))
    assert (
        digest_tool.main(["--evidence", str(evidence), "--locks", str(ROOT / "verifications")]) == 1
    )
    out = capsys.readouterr().out
    assert f"FAIL {evidence / (CORNERS[0] + '-evidence-stock') / 'manifest.json'}" in out


def test_the_evidence_directory_check_refuses_an_empty_directory(tmp_path):
    """No evidence at all must not read as a pass."""
    assert (
        digest_tool.main(["--evidence", str(tmp_path), "--locks", str(ROOT / "verifications")]) == 1
    )


def test_the_verify_workflow_runs_the_digest_check_over_the_downloaded_evidence():
    workflow = (ROOT / ".github/workflows/verify.yml").read_text()
    assert (
        "python3 tools/parameters_digest.py --evidence evidence --locks verifications" in workflow
    )
    assert workflow.index("tools/world_guard.py evidence") < workflow.index(
        "tools/parameters_digest.py"
    )


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("{not json", id="invalid-json"),
        pytest.param("", id="empty-file"),
        pytest.param("[1, 2]", id="not-an-object"),
    ],
)
def test_an_unreadable_manifest_prints_a_fail_line_and_exits_1_without_a_traceback(
    tmp_path, capsys, content
):
    name = "booster-t1-stock-mujoco-corner-left"
    lock_path = ROOT / "verifications" / f"{name}.lock.json"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(content)
    assert digest_tool.main([str(lock_path), str(manifest)]) == 1
    out = capsys.readouterr().out
    expected = "not a JSON object" if content.startswith("[") else "invalid JSON"
    assert f"FAIL {manifest}: {expected}" in out
    assert "Traceback" not in out


def test_an_unreadable_manifest_in_the_evidence_directory_fails_and_the_rest_are_still_checked(
    tmp_path, capsys
):
    evidence = tmp_path / "evidence"
    for name in CORNERS[:2]:
        _artifact_dir(evidence, name, _manifest_for(_lock(name)))
    (evidence / f"{CORNERS[0]}-evidence-stock" / "manifest.json").write_text("{broken")
    assert (
        digest_tool.main(["--evidence", str(evidence), "--locks", str(ROOT / "verifications")]) == 1
    )
    out = capsys.readouterr().out
    assert (
        f"FAIL {evidence / (CORNERS[0] + '-evidence-stock') / 'manifest.json'}: invalid JSON" in out
    )
    assert f"OK   {evidence / (CORNERS[1] + '-evidence-stock') / 'manifest.json'}" in out


def test_a_missing_manifest_or_lock_file_fails_cleanly(tmp_path, capsys):
    lock_path = ROOT / "verifications/booster-t1-stock-mujoco-corner-left.lock.json"
    assert digest_tool.main([str(lock_path), str(tmp_path / "absent.json")]) == 1
    assert "FAIL" in capsys.readouterr().out
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    assert digest_tool.main([str(tmp_path / "no-lock.json"), str(manifest)]) == 1
    assert "FAIL" in capsys.readouterr().out
