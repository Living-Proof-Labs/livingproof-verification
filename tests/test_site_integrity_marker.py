"""A passing run whose Run Integrity is ERROR or INCONCLUSIVE gets a marker (#168).

Today the site shows chips only on a non-passing run, so a PASS or UNSUPPORTED run whose
integrity result is ERROR or INCONCLUSIVE looks clean. The marker has its own style (not the
"Spec changed" chip-spec) and an accessible label. A status the site does not know shows nothing.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("site_tool_marker", ROOT / "tools/site.py")
site_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_tool)

FIXTURE = ROOT / "tests/fixtures/evaluator-result-run-integrity-all-not-applicable.json"
BASE = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _report(status: str, grade: str = "PASS", **run) -> dict:
    document = copy.deepcopy(BASE)
    document["integrity"]["run"].update({"test_system_status": status, **run})
    return {
        "source_bundle": {
            "assessment": {"integrity_results": {"livingproof-run-integrity": document}}
        },
        "summaries": [{"grade": grade}],
    }


def _record(report: dict, grade: str) -> dict:
    markers = site_tool.integrity_markers(report)
    record = {"outcome": [{"grade": grade}]}
    if markers:
        record["integrity_status"] = markers
    return record


@pytest.mark.parametrize("grade", ["PASS", "UNSUPPORTED"])
@pytest.mark.parametrize("status", ["ERROR", "INCONCLUSIVE"])
def test_a_passing_run_shows_a_distinct_labelled_marker(status: str, grade: str):
    extra = {"success": False, "percentage": 0} if status == "ERROR" else {}
    record = _record(_report(status, grade, **extra), grade)
    assert record["integrity_status"] == [
        {"id": "livingproof-run-integrity", "title": "Run Integrity", "status": status}
    ]
    html = site_tool._chips_html(record, "../")
    kind = status.lower()
    assert f'class="chip chip-integrity chip-integrity-{kind}"' in html
    assert f'aria-label="Run Integrity: integrity status {status}' in html
    assert f"Run Integrity: {status}" in html
    assert "chip-spec" not in html


def test_the_marker_has_its_own_style_not_chip_spec():
    css = site_tool.SITE_STYLES
    for name in (".chip-integrity-error", ".chip-integrity-inconclusive"):
        assert name in css
        block = css[css.index(name) :].split("}")[0]
        assert "chip-spec" not in block


@pytest.mark.parametrize("status", ["COMPLETED", "PARTIAL", "SKIPPED"])
def test_completed_and_unknown_statuses_show_nothing(status: str):
    record = _record(_report(status, success=True), "PASS")
    assert "integrity_status" not in record
    assert site_tool._chips_html(record, "../") == ""


def test_a_failing_run_keeps_its_own_chips_and_no_second_marker():
    report = _report("ERROR", "FAIL", success=False, percentage=0)
    record = _record(report, "FAIL")
    record["failing_evaluators"] = site_tool.failing_evaluators(report)
    html = site_tool._chips_html(record, "../")
    assert "chip-integrity" not in html
    assert 'class="chip"' in html
