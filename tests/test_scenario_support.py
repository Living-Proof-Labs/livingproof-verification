# SPDX-License-Identifier: Apache-2.0
"""The site renders the verifier's grade. It does not re-grade.

A verifier FAIL stays FAIL. A verifier UNSUPPORTED for scenario_supported is
labelled "not supported" on the history page, and the report files stay the
verifier's bytes. sim-mujoco records the unmet precondition and capability-verifier
grades it. Refs Living-Proof-Labs/robot-capability-specs#52.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
MARKER = "GRADE FAIL from the planted trial report"
UNSUPPORTED_TEXT = "UNSUPPORTED: precondition scenario_supported unmet (no trials assessed)\n"


def _load(name: str, path: Path):
    if not path.is_file():
        pytest.fail(f"{path} is missing")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


site_tool = _load("site_tool", ROOT / "tools/site.py")


def _reports_for(directory: Path, verifications: Path, text: str, grade: str) -> None:
    for target in site_tool.targets(verifications):
        folder = directory / target["artifact"]
        folder.mkdir(parents=True)
        report = {
            "report_version": "1.0.0",
            "summaries": [
                {
                    "capability": "walk-corner" if grade == "UNSUPPORTED" else "walk-hallway",
                    "system": {"id": f"{target['robot']}/{target['configuration']}"},
                    "grade": grade,
                    "k": 0,
                    "n": 1,
                    "required_passes": 1,
                    "reason": (
                        "precondition scenario_supported unmet"
                        if grade == "UNSUPPORTED"
                        else "a trial failed"
                    ),
                }
            ],
        }
        (folder / "report.json").write_text(json.dumps(report))
        for filename in ("report.html", "report.md", "report.txt"):
            (folder / filename).write_text(text)
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


def _publish(tmp_path: Path, verifications: Path, text: str, grade: str, name: str) -> Path:
    reports = tmp_path / name
    _reports_for(reports, verifications, text, grade)
    site = tmp_path / f"site-{name}"
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
    )
    return site


def test_the_publisher_does_not_regrade_from_a_robot_manifest():
    assert not (ROOT / "tools/scenario_support.py").exists()


def test_a_planted_verifier_fail_still_shows_fail(tmp_path):
    verifications = _lock_dir(
        tmp_path, "walk-corner@0.1.0", "unitree-g1-stock-mujoco-corner.lock.json"
    )
    site = _publish(tmp_path, verifications, MARKER + "\n", "FAIL", "fail")
    report = (site / "unitree-g1" / "stock-mujoco-corner" / "runs" / "1" / "report.md").read_text()
    page = (site / "unitree-g1" / "stock-mujoco-corner" / "runs" / "1" / "index.html").read_text()
    history = (site / "unitree-g1" / "stock-mujoco-corner" / "index.html").read_text()
    assert report == MARKER + "\n"
    assert MARKER in page
    assert "FAIL" in history
    assert "not supported" not in history
    assert not (
        site / "unitree-g1" / "stock-mujoco-corner" / "runs" / "1" / "verification.json"
    ).exists()


def test_a_verifier_unsupported_report_is_labelled_not_supported_and_not_rewritten(tmp_path):
    verifications = _lock_dir(
        tmp_path / "corner", "walk-corner@0.1.0", "unitree-g1-stock-mujoco-corner.lock.json"
    )
    site = _publish(tmp_path, verifications, UNSUPPORTED_TEXT, "UNSUPPORTED", "unsupported")
    run = site / "unitree-g1" / "stock-mujoco-corner" / "runs" / "1"
    assert (run / "report.md").read_text() == UNSUPPORTED_TEXT
    assert (run / "report.txt").read_text() == UNSUPPORTED_TEXT
    stored = json.loads((run / "report.json").read_text())
    assert stored["summaries"][0]["grade"] == "UNSUPPORTED"
    assert "scenario_supported" in stored["summaries"][0]["reason"]
    history = (site / "unitree-g1" / "stock-mujoco-corner" / "index.html").read_text()
    index = (site / "index.html").read_text()
    for text in (history, index):
        assert "not supported" in text
        assert "no trials run" in text
        assert ">FAIL<" not in text
    assert not (run / "verification.json").exists()
