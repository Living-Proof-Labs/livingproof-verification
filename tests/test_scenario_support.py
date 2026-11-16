# SPDX-License-Identifier: Apache-2.0
"""Robot manifest ``scenarios`` (robot-capability-specs v2.3.0, issue #52).

A scenario that lists the precondition ``scenario_supported`` is checked against
the manifest list before any trial. walk-corner lists it. walk-hallway does not,
so a manifest with no list keeps showing that run's trial grade. An undeclared
walk-corner is ``UNSUPPORTED`` and the report says "not supported", never a failure.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
import urllib.error

import pytest

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures/manifests"
MARKER = "GRADE FAIL from the planted trial report"


def _load(name: str, path: Path):
    if not path.is_file():
        pytest.fail(f"{path} is missing")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


support = _load("scenario_support", ROOT / "tools/scenario_support.py")
site_tool = _load("site_tool", ROOT / "tools/site.py")


def _manifest(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _reports_for(directory: Path, verifications: Path, text: str, grade: str) -> None:
    for target in site_tool.targets(verifications):
        folder = directory / target["artifact"]
        folder.mkdir(parents=True)
        report = {
            "report_version": "1.0.0",
            "summaries": [
                {
                    "capability": "walk-hallway",
                    "system": {"id": f"{target['robot']}/{target['configuration']}"},
                    "grade": grade,
                    "k": 0,
                    "n": 1,
                    "required_passes": 1,
                }
            ],
        }
        (folder / "report.json").write_text(json.dumps(report))
        for filename in ("report.html", "report.md", "report.txt"):
            (folder / filename).write_text(text + "\n")
        (folder / "SHA256SUMS").write_text(
            "".join(
                f"{hashlib.sha256((folder / filename).read_bytes()).hexdigest()}  {filename}\n"
                for filename in site_tool.REPORT_FILES
            )
        )


def _lock_dir(tmp_path: Path, scenario: str, filename: str) -> Path:
    source = json.loads(
        (ROOT / "verifications/unitree-g1-stock-mujoco-hallway.lock.json").read_text()
    )
    source["scenario"] = scenario
    directory = tmp_path / "verifications"
    directory.mkdir(parents=True)
    (directory / filename).write_text(json.dumps(source))
    return directory


def _publish(tmp_path: Path, verifications: Path, manifest: dict | None, reports_name: str):
    reports = tmp_path / reports_name
    _reports_for(reports, verifications, MARKER, "FAIL")
    repository = "Living-Proof-Labs/livingproof-unitree-g1"
    site = tmp_path / f"site-{reports_name}"
    site_tool.build(
        site,
        reports,
        {
            "id": "1",
            "commit": "a" * 40,
            "created": "2026-11-16T00:00:00Z",
            "repository": "Living-Proof-Labs/livingproof-verification",
            "server": "https://github.com",
        },
        verifications=verifications,
        manifests={repository: manifest},
    )
    return site


def _visible(site: Path, variant: str) -> str:
    history = (site / "unitree-g1" / variant / "index.html").read_text()
    run = next((site / "unitree-g1" / variant / "runs").glob("*/report.md")).read_text()
    page = next((site / "unitree-g1" / variant / "runs").glob("*/index.html")).read_text()
    output = json.loads(
        next((site / "unitree-g1" / variant / "runs").glob("*/verification.json")).read_text()
    )
    return history, run, page, output


def test_an_undeclared_walk_corner_is_not_supported_and_not_a_failure():
    manifest = _manifest("declares-walk-hallway.json")
    assert manifest["scenarios"] == ["walk-hallway"]
    assert "walk-corner" not in manifest["scenarios"]
    assert support.grade(manifest, "walk-corner", "FAIL") == "UNSUPPORTED"
    assert support.grade(manifest, "walk-corner", "PASS") == "UNSUPPORTED"
    document = support.verification(manifest, "walk-corner@0.1.0", "FAIL")
    assert document["grade"] == "UNSUPPORTED"
    assert document["precondition"] == "scenario_supported"
    assert document["trials"] == []
    assert document["statement"] == "not supported"
    rendered = support.render(document)
    for text in rendered.values():
        assert "not supported" in text
        assert "FAIL" not in text
    assert "UNSUPPORTED: precondition scenario_supported unmet" in rendered["md"]
    assert "no trials run" in rendered["md"].lower()


def test_a_declared_walk_hallway_keeps_its_trial_grade():
    manifest = _manifest("declares-walk-hallway.json")
    assert support.grade(manifest, "walk-hallway", "FAIL") == "FAIL"
    assert support.grade(manifest, "walk-hallway", "PASS") == "PASS"
    document = support.verification(manifest, "walk-hallway@0.1.0", "FAIL")
    assert document["grade"] == "FAIL"
    assert document["precondition"] is None
    assert document["trials"] is None
    rendered = support.render(document)
    for text in rendered.values():
        assert "not supported" not in text
        assert "FAIL" in text


def test_a_missing_scenarios_list_follows_the_spec():
    """No list names nothing. walk-corner is unsupported; walk-hallway still runs."""
    manifest = _manifest("no-scenarios.json")
    assert "scenarios" not in manifest
    assert support.grade(manifest, "walk-corner", "FAIL") == "UNSUPPORTED"
    assert support.grade(manifest, "walk-corner", "PASS") == "UNSUPPORTED"
    assert support.grade(manifest, "walk-hallway", "FAIL") == "FAIL"
    assert support.grade(manifest, "walk-hallway", "PASS") == "PASS"
    # A pinned robot that has not released a manifest file is the same absence.
    assert support.grade(None, "walk-corner", "FAIL") == "UNSUPPORTED"
    assert support.grade(None, "walk-hallway", "PASS") == "PASS"
    corner = support.verification(manifest, "walk-corner@0.1.0", "PASS")
    hallway = support.verification(None, "walk-hallway@0.1.0", "PASS")
    assert corner["grade"] == "UNSUPPORTED"
    assert corner["declared_scenarios"] is None
    assert "not supported" in support.render(corner)["txt"]
    assert "FAIL" not in support.render(corner)["txt"]
    assert hallway["grade"] == "PASS"
    assert "not supported" not in support.render(hallway)["md"]


def test_a_declared_walk_corner_keeps_a_trial_failure():
    manifest = _manifest("declares-walk-corner.json")
    assert support.grade(manifest, "walk-corner", "FAIL") == "FAIL"
    document = support.verification(manifest, "walk-corner@0.1.0", "FAIL")
    assert document["grade"] == "FAIL"
    assert "not supported" not in support.render(document)["md"]


def test_an_invalid_scenarios_list_is_not_a_robot_grade():
    manifest = _manifest("invalid-scenarios.json")
    assert support.grade(manifest, "walk-corner", "FAIL") == "ERROR"
    assert support.grade(manifest, "walk-hallway", "PASS") == "ERROR"
    document = support.verification(manifest, "walk-corner@0.1.0", "FAIL")
    assert document["grade"] == "ERROR"
    rendered = support.render(document)
    for text in rendered.values():
        assert "FAIL" not in text
        assert "not supported" not in text


def test_published_walk_corner_on_an_undeclared_robot_says_not_supported(tmp_path):
    verifications = _lock_dir(
        tmp_path, "walk-corner@0.1.0", "unitree-g1-stock-mujoco-corner.lock.json"
    )
    site = _publish(tmp_path, verifications, _manifest("declares-walk-hallway.json"), "corner")
    history, report, page, output = _visible(site, "stock-mujoco-corner")
    assert output["grade"] == "UNSUPPORTED"
    assert output["trials"] == []
    assert output["precondition"] == "scenario_supported"
    for text in (report, page):
        assert "not supported" in text
        assert "FAIL" not in text
        assert MARKER not in text
    assert "not supported" in history
    assert ">FAIL<" not in history
    assert MARKER not in history


def test_published_walk_hallway_stays_the_planted_failure_when_declared(tmp_path):
    verifications = _lock_dir(
        tmp_path, "walk-hallway@0.1.0", "unitree-g1-stock-mujoco-hallway.lock.json"
    )
    site = _publish(tmp_path, verifications, _manifest("declares-walk-hallway.json"), "hallway")
    history, report, page, output = _visible(site, "stock-mujoco-hallway")
    assert output["grade"] == "FAIL"
    assert MARKER in report
    assert MARKER in page
    assert "not supported" not in history
    assert "not supported" not in report
    assert "not supported" not in page
    assert "FAIL" in history


def test_published_absent_scenarios_match_the_spec(tmp_path):
    absent = _manifest("no-scenarios.json")
    corner_dir = _lock_dir(
        tmp_path / "corner", "walk-corner@0.1.0", "unitree-g1-stock-mujoco-corner.lock.json"
    )
    hallway_dir = _lock_dir(
        tmp_path / "hallway", "walk-hallway@0.1.0", "unitree-g1-stock-mujoco-hallway.lock.json"
    )
    corner = _publish(tmp_path, corner_dir, absent, "absent-corner")
    hallway = _publish(tmp_path, hallway_dir, None, "absent-hallway")
    corner_history, corner_report, _, corner_output = _visible(corner, "stock-mujoco-corner")
    hallway_history, hallway_report, _, hallway_output = _visible(hallway, "stock-mujoco-hallway")
    assert corner_output["grade"] == "UNSUPPORTED"
    assert corner_output["declared_scenarios"] is None
    assert "not supported" in corner_history
    assert ">FAIL<" not in corner_history
    assert "not supported" in corner_report
    assert "FAIL" not in corner_report
    assert hallway_output["grade"] == "FAIL"
    assert MARKER in hallway_report
    assert "not supported" not in hallway_history


def test_the_current_hallway_locks_stay_failures_when_no_manifest_is_released(tmp_path):
    verifications = ROOT / "verifications"
    repositories = {
        target["lock"]["robot"]["repository"]: None for target in site_tool.targets(verifications)
    }
    reports = tmp_path / "reports"
    _reports_for(reports, verifications, MARKER, "FAIL")
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports,
        {
            "id": "1",
            "commit": "b" * 40,
            "created": "2026-11-16T00:00:00Z",
            "repository": "Living-Proof-Labs/livingproof-verification",
            "server": "https://github.com",
        },
        verifications=verifications,
        manifests=repositories,
    )
    index = (site / "index.html").read_text()
    assert "not supported" not in index
    assert (
        MARKER
        in (site / "unitree-g1" / "stock-mujoco-hallway" / "runs" / "1" / "report.md").read_text()
    )
    output = json.loads(
        (
            site / "unitree-g1" / "stock-mujoco-hallway" / "runs" / "1" / "verification.json"
        ).read_text()
    )
    assert output["grade"] == "FAIL"
    assert output["declared_scenarios"] is None


def test_a_pr_shaped_hallway_manifest_shows_walk_corner_as_not_supported(tmp_path):
    """The G1, Go1 and T1 manifests declare only walk-hallway (G1#40, Go1#26, T1#31)."""
    manifest = _manifest("unitree-g1-manifest.json")
    assert list(manifest) == [
        "$comment",
        "format",
        "robot",
        "source",
        "profile",
        "adapter",
        "configurations",
        "assets",
        "features",
        "scenarios",
    ]
    assert manifest["format"] == "livingproof-robot-manifest/0.1"
    assert manifest["robot"] == "unitree-g1"
    assert manifest["scenarios"] == ["walk-hallway"]
    assert manifest["features"] == {}
    assert isinstance(manifest["source"], dict) and manifest["source"]["repository"]
    assert isinstance(manifest["assets"], list) and manifest["assets"]
    assert support.grade(manifest, "walk-hallway", "FAIL") == "FAIL"
    verifications = _lock_dir(
        tmp_path, "walk-corner@0.1.0", "unitree-g1-stock-mujoco-corner.lock.json"
    )
    site = _publish(tmp_path, verifications, manifest, "pr-shaped-corner")
    history, report, page, output = _visible(site, "stock-mujoco-corner")
    assert output["grade"] == "UNSUPPORTED"
    assert output["declared_scenarios"] == ["walk-hallway"]
    assert output["trials"] == []
    assert output["precondition"] == "scenario_supported"
    for text in (report, page):
        assert "not supported" in text
        assert "FAIL" not in text
        assert MARKER not in text
    assert "not supported" in history
    assert ">FAIL<" not in history


def test_pinned_manifest_read_uses_the_commit_and_a_missing_file_names_nothing():
    seen = []

    def opener(url, timeout):
        seen.append((url, timeout))
        raise urllib.error.HTTPError(url, 404, "missing", hdrs=None, fp=None)

    manifest = support.pinned_manifest(
        "Living-Proof-Labs/livingproof-unitree-g1",
        "da7d9504cb5d4deaa509b92a1973490e43888f90",
        opener=opener,
    )
    assert manifest is None
    assert seen[0][0].endswith(
        "Living-Proof-Labs/livingproof-unitree-g1/"
        "da7d9504cb5d4deaa509b92a1973490e43888f90/manifest.json"
    )
    assert support.grade(manifest, "walk-hallway", "PASS") == "PASS"
    assert support.grade(manifest, "walk-corner", "FAIL") == "UNSUPPORTED"


def test_a_pinned_manifest_that_cannot_be_read_is_not_treated_as_empty():
    def opener(url, timeout):
        raise urllib.error.HTTPError(url, 500, "down", hdrs=None, fp=None)

    with pytest.raises(support.ScenarioSupportError):
        support.pinned_manifest("Org/robot", "a" * 40, opener=opener)
