"""Evaluator chip links must land on an element of the reporter page (#168, #129).

Reporter v3.13.0 has no `evaluator-<id>` element: its ids are `run-evaluators-tab-N`,
`run-evaluators-panel-N`, `summary` and `theme-*`. The tab number of an evaluator depends on the
reporter's own ordering (Run Integrity is tab 2 when a Scenario result exists), which the site
cannot derive reliably, so the chips link to `#summary`, a stable id inside the first evaluators panel (a stopgap until the reporter emits a per-evaluator anchor). A chip for a not-graded run on a FAIL run uses the same
neutral dashed style as the marker on a PASS run, not the chip-spec "Spec changed" style.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("site_tool_anchor", ROOT / "tools/site.py")
site_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_tool)

FIXTURE = ROOT / "tests/fixtures/evaluator-result-run-integrity-all-not-applicable.json"
BASE = json.loads(FIXTURE.read_text(encoding="utf-8"))
BUNDLE = ROOT / "tests/fixtures/repeats/identical"
TARGET = "#summary"


def _document(status: str, **run) -> dict:
    document = copy.deepcopy(BASE)
    document["integrity"]["run"].update({"test_system_status": status, **run})
    return document


def _report(document: dict, grade: str) -> dict:
    return {
        "source_bundle": {
            "assessment": {"integrity_results": {"livingproof-run-integrity": document}}
        },
        "summaries": [{"grade": grade}],
    }


SCENARIO = json.loads((ROOT / "tests/fixtures/evaluator-result-scenario.json").read_text())


def _page(tmp_path: Path, document: dict, scenario: bool = False) -> str:
    """The v3.13.0 reporter's page for a bundle carrying this integrity result.

    With `scenario`, the run also has a Scenario result, which the reporter orders before Run
    Integrity, so Run Integrity is tab 2 (as on a real run page)."""
    exe = shutil.which("livingproof-capability-report")
    if exe is None:
        pytest.skip("livingproof-capability-report is not installed")
    work = tmp_path / "bundle"
    shutil.copytree(BUNDLE, work)
    manifest = json.loads((work / "manifest.json").read_text())
    for run in manifest["assessment"]["runs"]:
        run["run_id"] = document["integrity"]["run"]["id"]
    if scenario:
        manifest["results"][0]["evaluator_results"] = [copy.deepcopy(SCENARIO)]
    manifest["assessment"]["integrity_results"] = {"livingproof-run-integrity": document}
    (work / "manifest.json").write_text(json.dumps(manifest))
    out = work / "report.html"
    subprocess.run(
        [exe, "render", str(work / "manifest.json"), "--format", "html", "-o", str(out)],
        check=True,
        capture_output=True,
    )
    return out.read_text(encoding="utf-8")


def _ids(page: str) -> set[str]:
    return set(re.findall(r"""\bid=['"]([^'"]+)['"]""", page))


def _fragments(chips: str) -> list[str]:
    return [h.split("#", 1)[1] for h in re.findall(r'href="([^"]*#[^"]*)"', chips)]


CASES = {
    "failing run, ERROR": ("FAIL", _document("ERROR", success=False, percentage=0)),
    "failing run, INCONCLUSIVE": ("FAIL", _document("INCONCLUSIVE")),
    "passing run, ERROR": ("PASS", _document("ERROR", success=False, percentage=0)),
    "passing run, INCONCLUSIVE": ("PASS", _document("INCONCLUSIVE")),
}


def _chips(grade: str, document: dict) -> str:
    report = _report(document, grade)
    record = {"outcome": [{"grade": grade}], "grade": grade}
    entries = site_tool.failing_evaluators(report)
    if entries:
        record["failing_evaluators"] = entries
    markers = site_tool.integrity_markers(report)
    if markers:
        record["integrity_status"] = markers
    return site_tool._chips_html(record, "../")


def _tab_label(page: str, fragment: str) -> str | None:
    """The evaluator name on the tab with this id, or None when the id is not a tab."""
    found = re.search(rf"id='{re.escape(fragment)}'[^>]*>.*?eval-name-full'>([^<]*)", page, re.S)
    return found.group(1) if found and fragment.startswith("run-evaluators-tab-") else None


@pytest.mark.parametrize("scenario", [False, True], ids=["no scenario", "with scenario"])
@pytest.mark.parametrize("case", CASES)
def test_every_chip_fragment_resolves_in_the_v3_13_0_page(
    case: str, scenario: bool, tmp_path: Path
):
    grade, document = CASES[case]
    chips = _chips(grade, document)
    fragments = _fragments(chips)
    assert fragments, "the chip should be linked"
    page = _page(tmp_path, document, scenario)
    ids = _ids(page)
    for fragment in fragments:
        assert fragment in ids, f"#{fragment} is not an id on the reporter page: {sorted(ids)}"
        # A link to a tab must land on that evaluator's own tab: with a Scenario result,
        # tab 1 is Scenario and Run Integrity is tab 2.
        label = _tab_label(page, fragment)
        assert label in (None, "Run Integrity"), f"the chip links to the {label} tab"


def test_a_stored_dead_href_is_not_rendered():
    """Records written before this change carry `#evaluator-<id>`; the page never has it."""
    record = {
        "grade": "FAIL",
        "failing_evaluators": [{"id": "e", "title": "E", "href": "#evaluator-e"}],
    }
    chips = site_tool._chips_html(record, "runs/1/")
    assert f'href="runs/1/{TARGET}"' in chips
    assert "#evaluator-" not in chips


def test_the_entry_href_is_the_working_target():
    entries = site_tool.failing_evaluators(_report(_document("INCONCLUSIVE"), "FAIL"))
    assert entries[0]["href"] == TARGET


def test_an_inconclusive_run_on_a_fail_run_uses_the_not_graded_chip():
    chips = _chips("FAIL", _document("INCONCLUSIVE"))
    tip = "Run Integrity: integrity status INCONCLUSIVE (not graded)"
    assert (
        f'<a class="chip chip-integrity chip-integrity-inconclusive" href="../{TARGET}" '
        f'title="{tip}"><span aria-hidden="true">?</span> Run Integrity not graded</a>'
    ) in chips
    assert "chip-spec" not in chips
    plain = site_tool._chips_html(
        {
            "grade": "FAIL",
            "failing_evaluators": site_tool.failing_evaluators(
                _report(_document("INCONCLUSIVE"), "FAIL")
            ),
        },
        "../",
        linked=False,
    )
    assert (
        f'<span class="chip chip-integrity chip-integrity-inconclusive" title="{tip}">'
        '<span aria-hidden="true">?</span> Run Integrity not graded</span>'
    ) in plain
    assert "chip-spec" not in plain


def test_a_failing_chip_keeps_the_plain_failing_style():
    chips = _chips("FAIL", _document("ERROR", success=False, percentage=0))
    assert 'class="chip"' in chips
    assert "chip-integrity" not in chips
