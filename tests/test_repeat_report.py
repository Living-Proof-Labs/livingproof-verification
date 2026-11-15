# SPDX-License-Identifier: Apache-2.0
"""Repeat-aware rendering from the reporter pinned in verifications/settings.toml.

The bundles in tests/fixtures/repeats are livingproof-capability-report v3.7.0's
own fixtures. This renders them with that release's command, the same one CI
installs before pytest. A compared run is one stored outcome plus its
reproducibility statement. Seeded and single-trial runs keep their rate.

Reporter issue 65 is still open on this release: a disagreeing run's Actual line
says "No trial failed." beside INSUFFICIENT_EVIDENCE, and the terminal table
omits the stored finding. Those lines are not asserted here.
"""

import json
from pathlib import Path
import re
import shutil
import subprocess
from html import unescape

import pytest

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests/fixtures/repeats"
RATE = re.compile(r"\d+ successes?, \d+%")
NOT_STORED = "Success rate not stored"
RANGE = "95% confidence range"
FINDING = (
    "Identical trials disagreed on trajectory hash or outcome. "
    "This is a test-system reproducibility finding, not a robot FAIL."
)


def _reporter() -> str:
    found = shutil.which("livingproof-capability-report")
    if found is None:
        pytest.fail("livingproof-capability-report is not on PATH; checks installs the pin first")
    return found


def _render(tmp_path: Path, name: str, fmt: str) -> str:
    destination = tmp_path / f"{name}.{fmt}"
    subprocess.run(
        [
            _reporter(),
            "render",
            str(FIXTURES / name / "manifest.json"),
            "--format",
            fmt,
            "-o",
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return destination.read_text()


def _visible(page: str) -> str:
    """Drop the lossless JSON and tags so assertions see the page a reader sees."""
    page = page[: page.index("<details class='lossless'>")]
    page = re.sub(r"<style\b[^>]*>.*?</style>", "", page, flags=re.DOTALL)
    text = unescape(re.sub(r"<[^>]+>", "\n", page))
    return re.sub(r"[ \t]+", " ", text)


def test_compared_repeats_show_one_outcome_and_the_statement(tmp_path):
    statement = "3 of 3 repeats agree"
    html = _render(tmp_path, "identical", "html")
    markdown = _render(tmp_path, "identical", "markdown")
    table = _render(tmp_path, "identical", "table")
    report = json.loads(_render(tmp_path, "identical", "json"))
    summary = report["summaries"][0]
    visible = _visible(html)
    assert summary["grade"] == "PASS"
    assert summary["k"] is None and summary["n"] is None
    assert summary["wilson95"] == [None, None]
    assert summary["reproducibility"]["statement"] == statement
    assert summary["reproducibility"]["compared"] is True
    for text in (visible, markdown, table):
        assert statement in text
    assert "Stored overall status: PASS" in visible
    assert "PASS. 3 of 3 repeats agree." in table
    assert "3 repeats" in markdown and "N=" not in markdown
    assert "success-ring" not in html
    for banned in (NOT_STORED, RANGE, "successes,", "% success"):
        assert banned not in visible, banned
        assert banned not in markdown, banned
    assert RATE.search(visible) is None
    assert RATE.search(markdown) is None


def test_disagreeing_repeats_show_the_finding_and_no_rate(tmp_path):
    html = _render(tmp_path, "disagreeing", "html")
    markdown = _render(tmp_path, "disagreeing", "markdown")
    report = json.loads(_render(tmp_path, "disagreeing", "json"))
    summary = report["summaries"][0]
    visible = _visible(html)
    assert summary["grade"] == "INSUFFICIENT_EVIDENCE"
    assert summary["k"] is None and summary["wilson95"] == [None, None]
    assert summary["reproducibility"]["finding"]["detail"] == FINDING
    assert "statement" not in summary["reproducibility"]
    assert f"Overall INSUFFICIENT_EVIDENCE. {FINDING}" in visible
    assert FINDING in markdown
    assert "success-ring" not in html
    for banned in (NOT_STORED, RANGE, "successes,", "% success", "repeats agree"):
        assert banned not in visible, banned
        assert banned not in markdown, banned
    assert RATE.search(visible) is None


def test_a_seeded_run_still_shows_its_rate(tmp_path):
    html = _render(tmp_path, "seeded", "html")
    markdown = _render(tmp_path, "seeded", "markdown")
    report = json.loads(_render(tmp_path, "seeded", "json"))
    summary = report["summaries"][0]
    visible = _visible(html)
    assert "reproducibility" not in summary
    assert (summary["k"], summary["n"], summary["grade"]) == (2, 3, "FAIL")
    assert summary["wilson95"][0] is not None
    assert "2 successes, 67%" in visible
    assert RANGE in visible
    assert "success-ring" in html
    assert "2 successes, 67%" in markdown
    assert "N=3" in markdown
    assert "repeats agree" not in visible and "repeats agree" not in markdown
    assert NOT_STORED not in visible


def test_a_single_trial_still_shows_its_rate(tmp_path):
    html = _render(tmp_path, "single", "html")
    markdown = _render(tmp_path, "single", "markdown")
    report = json.loads(_render(tmp_path, "single", "json"))
    summary = report["summaries"][0]
    visible = _visible(html)
    assert "reproducibility" not in summary
    assert (summary["k"], summary["n"], summary["grade"]) == (1, 1, "PASS")
    assert "1 success, 100%" in visible
    assert RANGE in visible
    assert "success-ring" in html
    assert "1 success, 100%" in markdown
    assert "N=1" in markdown
    assert "repeats agree" not in visible and "repeats agree" not in markdown
    assert NOT_STORED not in visible
