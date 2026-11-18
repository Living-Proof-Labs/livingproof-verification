# SPDX-License-Identifier: Apache-2.0
"""The pinned reporter shows the stored Run Integrity result as an evaluator card.

The run JSON for a published run carries ``assessment.integrity_results`` with a
``livingproof-run-integrity`` result. livingproof-capability-report v3.11.3 stored it
but never rendered it, so the Evaluators section of the public report listed only
Scenario. v3.11.5 (capability-report pull 88) binds the result to the run whose id
equals ``integrity.run.id`` and renders it next to Scenario. This renders a fixture
bundle with the reporter this repository installs from verifications/settings.toml
(the same command CI runs before pytest) and checks three things: the card renders
when the result exists, no card is implied when it does not, and the stored grades are
the same with or without it. The reporter never grades.

tests/fixtures/evaluator-result-run-integrity.json is the real result of CI run
44220477144 (verify on ca169ae), artifact
``booster-t1-stock-mujoco-corner-evaluator-results-livingproof-run-integrity-stock``.
"""

from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
IDENTICAL = ROOT / "tests/fixtures/repeats/identical"
INTEGRITY = ROOT / "tests/fixtures/evaluator-result-run-integrity.json"
PINS_INTEGRITY = ROOT / "tests/fixtures/evaluator-result-run-integrity-pins.json"
EVALUATOR = "livingproof-run-integrity"


def _reporter() -> str:
    found = shutil.which("livingproof-capability-report")
    if found is None:
        pytest.fail("livingproof-capability-report is not on PATH; checks installs the pin first")
    return found


def _manifest(directory: Path, *, integrity: bool, fixture: Path = INTEGRITY) -> Path:
    """The identical-repeats bundle with a run id, and optionally the stored integrity result."""
    work = directory / ("with-integrity" if integrity else "without-integrity")
    shutil.copytree(IDENTICAL, work)
    bundle = json.loads((work / "manifest.json").read_text())
    result = json.loads(fixture.read_text())
    run_id = result["integrity"]["run"]["id"]
    for run in bundle["assessment"]["runs"]:
        run["run_id"] = run_id
    if integrity:
        bundle["assessment"]["integrity_results"] = {EVALUATOR: result}
    (work / "manifest.json").write_text(json.dumps(bundle, indent=2) + "\n")
    return work / "manifest.json"


def _render(manifest: Path, fmt: str) -> str:
    destination = manifest.parent / f"report.{fmt}"
    subprocess.run(
        [_reporter(), "render", str(manifest), "--format", fmt, "-o", str(destination)],
        check=True,
        capture_output=True,
        text=True,
    )
    return destination.read_text()


class _EvaluatorCards(HTMLParser):
    """Collect the <h4> title of every evaluator panel inside the Evaluators section."""

    def __init__(self):
        super().__init__()
        self.cards: list[str] = []
        self._in_section = 0
        self._depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if tag == "div" and "evaluators" in classes:
            self._in_section = 1
        elif self._in_section and tag == "div":
            self._in_section += 1
        elif self._in_section and tag == "h4":
            self._in_title = True
            self.cards.append("")

    def handle_endtag(self, tag):
        if self._in_section and tag == "div":
            self._in_section -= 1
        elif tag == "h4":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.cards[-1] += data


def _cards(page: str) -> list[str]:
    parser = _EvaluatorCards()
    parser.feed(page)
    return parser.cards


def test_run_integrity_card_renders_in_the_evaluators_section(tmp_path):
    page = _render(_manifest(tmp_path, integrity=True), "html")
    assert "Run Integrity" in _cards(page)
    # The stored status and percentage are shown, as stored.
    assert "Status: COMPLETED" in page
    assert "Judges whether the run and the test system can be trusted." in page


def test_go1_corner_left_pins_result_renders_with_pins_at_100(tmp_path):
    """A v1.1.1 Go1 corner-left integrity result surfaces pins at 100 in rendered reports."""
    stored = json.loads(PINS_INTEGRITY.read_text())
    assert stored["evaluator"]["version"] == "1.1.1"
    checks = {check["id"]: check for check in stored["integrity"]["checks"]}
    assert checks["pins"] == {
        "id": "pins",
        "version": "1.0.0",
        "applicable": True,
        "success": True,
        "percentage": 100,
        "findings": [],
    }
    manifest = _manifest(tmp_path, integrity=True, fixture=PINS_INTEGRITY)
    page = _render(manifest, "html")
    assert "Run Integrity" in _cards(page)
    assert "1.1.1" in page
    assert "Status: COMPLETED" in page
    assert "100%" in page
    assert "Matched the verification lock: runner, world, configuration." in page
    assert "Not checked: specs, verifier." in page
    markdown = _render(manifest, "markdown")
    assert "Run Integrity 1.1.1" in markdown
    rendered_json = json.loads(_render(manifest, "json"))
    integrity = rendered_json["source_bundle"]["assessment"]["integrity_results"][EVALUATOR]
    rendered_checks = {check["id"]: check for check in integrity["integrity"]["checks"]}
    assert rendered_checks["pins"]["percentage"] == 100
    assert rendered_checks["pins"]["success"] is True


def test_no_run_integrity_card_when_no_integrity_result_exists(tmp_path):
    """A report with no integrity result does not imply that one ran."""
    page = _render(_manifest(tmp_path, integrity=False), "html")
    assert "Run Integrity" not in _cards(page)
    assert "Status: COMPLETED" not in page


def test_run_integrity_does_not_change_any_stored_grade(tmp_path):
    with_result = json.loads(_render(_manifest(tmp_path, integrity=True), "json"))
    without = json.loads(_render(_manifest(tmp_path, integrity=False), "json"))
    assert with_result["summaries"], "the fixture must carry at least one summary"
    grades = [s["grade"] for s in with_result["summaries"]]
    assert grades == [s["grade"] for s in without["summaries"]]
    statuses = [(d["result_id"], d["status"]) for d in with_result["decisions"]]
    assert statuses == [(d["result_id"], d["status"]) for d in without["decisions"]]
    assert with_result["summaries"] == without["summaries"]
    assert with_result["decisions"] == without["decisions"]
