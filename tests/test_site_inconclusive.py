"""tools/site.py surfaces an INCONCLUSIVE run integrity status (#165).

livingproof-evaluator#38 / PR 51, result format 1.2.0: a run where no check
applies is INCONCLUSIVE with no percentage. The fixture is PR 51's
examples/integrity/all-not-applicable/result.json. A 1.x minor may add run
statuses, and a consumer treats any status it does not recognise as not graded.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("site_tool_inconclusive", ROOT / "tools/site.py")
site_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_tool)

FIXTURE = ROOT / "tests/fixtures/evaluator-result-run-integrity-all-not-applicable.json"
INCONCLUSIVE = json.loads(FIXTURE.read_text(encoding="utf-8"))
COPY = "Not graded: no integrity check applied to this run."


def _report(document: dict, grade: str = "FAIL") -> dict:
    return {
        "source_bundle": {
            "assessment": {"integrity_results": {"livingproof-run-integrity": document}}
        },
        "summaries": [{"grade": grade}],
    }


def _with_status(status: str, **run) -> dict:
    document = copy.deepcopy(INCONCLUSIVE)
    document["integrity"]["run"].update({"test_system_status": status, **run})
    return document


def test_the_fixture_is_the_pr51_shape():
    run = INCONCLUSIVE["integrity"]["run"]
    assert INCONCLUSIVE["format"] == "livingproof-evaluator-result/1.2.0"
    assert run["test_system_status"] == "INCONCLUSIVE"
    assert "percentage" not in run


def test_an_inconclusive_run_is_surfaced_with_not_graded_copy():
    found = site_tool.failing_evaluators(_report(copy.deepcopy(INCONCLUSIVE)))
    assert found == [
        {
            "id": "livingproof-run-integrity",
            "title": "Run Integrity",
            "href": "#evaluator-livingproof-run-integrity",
            "summary": COPY,
        }
    ]


def test_an_unrecognised_status_is_not_surfaced():
    for status in ("PARTIAL", "SKIPPED"):
        document = _with_status(status, success=False)
        assert site_tool.failing_evaluators(_report(document)) == [], status


def test_error_still_speaks_and_completed_still_does_not():
    error = _with_status("ERROR", success=False, percentage=0)
    found = site_tool.failing_evaluators(_report(error))
    assert [item["id"] for item in found] == ["livingproof-run-integrity"]
    assert "summary" not in found[0]
    completed = _with_status("COMPLETED", success=True, percentage=100)
    assert site_tool.failing_evaluators(_report(completed)) == []
