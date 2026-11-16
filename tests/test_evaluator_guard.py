# SPDX-License-Identifier: Apache-2.0
"""A report is not published without its evaluator results (issue #46).

Every assessed trial in the report's source bundle must carry at least one
evaluator result, and each result's descriptor needs a title and a purpose,
or the page shows the placeholder "Evaluator" with "No evaluator descriptor
was stored." A report graded UNSUPPORTED with no trials run (scenario not
declared, since #75) has nothing to describe and still publishes.
"""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("site_tool_guard", ROOT / "tools/site.py")
site_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_tool)

DESCRIPTOR = {
    "title": "Scenario",
    "short_title": "Scenario",
    "purpose": "Checks each step of the scenario against the limits in its spec.",
    "version": "1.2.1",
    "tier": "verdict",
    "findings": [],
}


def _report(grade="PASS", trials=3) -> dict:
    return {
        "report_version": "1.0.0",
        "source_bundle": {
            "results": [
                {
                    "id": f"stock-seed-{i:02d}",
                    "evaluator_results": [
                        {
                            "format": "livingproof-evaluator-result/1.1.0",
                            "evaluator": {"id": "livingproof-scenario"},
                            "descriptor": copy.deepcopy(DESCRIPTOR),
                        }
                    ],
                }
                for i in range(1, trials + 1)
            ]
        },
        "summaries": [
            {
                "capability": "walk-hallway",
                "system": {"id": "unitree-g1/stock"},
                "grade": grade,
                "k": trials,
                "n": trials,
                "required_passes": trials,
            }
        ],
    }


def _artifact(directory: Path, report: dict) -> Path:
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(json.dumps(report))
    for name in ("report.html", "report.md", "report.txt"):
        (directory / name).write_text(name)
    (directory / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((directory / n).read_bytes()).hexdigest()}  {n}\n"
            for n in site_tool.REPORT_FILES
        )
    )
    return directory


def _publish(tmp_path: Path, report: dict) -> Path:
    target = site_tool.targets()[0]
    reports = tmp_path / "reports"
    _artifact(reports / target["artifact"], report)
    run = {
        "id": "100",
        "commit": "a" * 40,
        "created": "2026-11-16T08:00:00Z",
        "repository": "Living-Proof-Labs/livingproof-verification",
        "server": "https://github.com",
    }
    return site_tool.add_run(tmp_path / "site", reports, target, run)


def test_a_complete_report_publishes(tmp_path):
    assert (_publish(tmp_path, _report()) / "report.json").is_file()


def _without_results(report):
    del report["source_bundle"]["results"][-1]["evaluator_results"]


def _empty_results(report):
    report["source_bundle"]["results"][0]["evaluator_results"] = []


def _no_title(report):
    del report["source_bundle"]["results"][1]["evaluator_results"][0]["descriptor"]["title"]


def _blank_purpose(report):
    report["source_bundle"]["results"][2]["evaluator_results"][0]["descriptor"]["purpose"] = " "


def _no_descriptor(report):
    del report["source_bundle"]["results"][0]["evaluator_results"][0]["descriptor"]


def _no_trials(report):
    report["source_bundle"]["results"] = []


def _no_bundle(report):
    del report["source_bundle"]


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (_without_results, "stock-seed-03 has no evaluator result"),
        (_empty_results, "stock-seed-01 has no evaluator result"),
        (_no_title, "stock-seed-02 evaluator livingproof-scenario descriptor has no title"),
        (_blank_purpose, "stock-seed-03 evaluator livingproof-scenario descriptor has no purpose"),
        (_no_descriptor, "stock-seed-01 evaluator livingproof-scenario has no descriptor"),
        (_no_trials, "no assessed trials"),
        (_no_bundle, "no assessed trials"),
    ],
)
def test_a_report_missing_evaluator_results_or_descriptor_is_not_published(tmp_path, edit, message):
    report = _report()
    edit(report)
    with pytest.raises(site_tool.SiteError, match=message):
        _publish(tmp_path, report)
    assert not (tmp_path / "site").exists() or not any((tmp_path / "site").rglob("report.json"))


def test_unsupported_with_no_trials_still_publishes(tmp_path):
    report = _report(grade="UNSUPPORTED", trials=0)
    report["summaries"][0]["reason"] = "precondition scenario_supported unmet"
    assert (_publish(tmp_path, report) / "report.json").is_file()
    del report["source_bundle"]
    assert (_publish(tmp_path / "again", report) / "report.json").is_file()


def test_already_published_pages_without_evaluators_are_left_in_place(tmp_path):
    """The guard checks only the incoming report. Restored history is not re-checked."""
    target = site_tool.targets()[0]
    historical = tmp_path / "site" / target["robot"] / target["variant"] / "runs" / "50"
    historical.mkdir(parents=True)
    old = _report()
    _without_results(old)
    (historical / "report.json").write_text(json.dumps(old))
    (historical / "report.html").write_text("No evaluator descriptor was stored.")
    published = _publish(tmp_path, _report())
    assert (published / "report.json").is_file()
    assert published != historical
    assert (historical / "report.html").read_text() == "No evaluator descriptor was stored."
    stored = json.loads((historical / "report.json").read_text())
    assert "evaluator_results" not in stored["source_bundle"]["results"][-1]
    with pytest.raises(site_tool.SiteError, match="stock-seed-03 has no evaluator result"):
        _publish(tmp_path / "resubmit", old)
