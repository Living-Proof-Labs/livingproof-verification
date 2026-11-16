# SPDX-License-Identifier: Apache-2.0
"""The pinned reporter names contributing evaluators in Markdown and the PR comment.

capability-report v3.9.0 (issue 55 / pull 74) added a **Evaluators:** line.
This checks the pin this repository actually installs, not a frozen tag.
"""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
IDENTICAL = ROOT / "tests/fixtures/repeats/identical"
SCENARIO = ROOT / "tests/fixtures/evaluator-result-scenario.json"


def _reporter() -> str:
    found = shutil.which("livingproof-capability-report")
    if found is None:
        pytest.fail("livingproof-capability-report is not on PATH; checks installs the pin first")
    return found


def test_pinned_reporter_markdown_and_comment_name_contributing_evaluators(tmp_path):
    """A report built with the pinned reporter names the scenario evaluator."""
    work = tmp_path / "bundle"
    shutil.copytree(IDENTICAL, work)
    bundle = json.loads((work / "manifest.json").read_text())
    result = json.loads(SCENARIO.read_text())
    for item in bundle["results"]:
        item["evaluator_results"] = [result]
    (work / "manifest.json").write_text(json.dumps(bundle, indent=2) + "\n")

    markdown = tmp_path / "report.md"
    subprocess.run(
        [
            _reporter(),
            "render",
            str(work / "manifest.json"),
            "--format",
            "markdown",
            "-o",
            str(markdown),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    text = markdown.read_text()
    assert "**Evaluators:**" in text
    assert "Scenario" in text

    comments = tmp_path / "comments.json"
    comments.write_text("[]\n")
    posted = subprocess.run(
        [
            _reporter(),
            "comment",
            "--repo",
            "Living-Proof-Labs/livingproof-verification",
            "--pr",
            "1",
            "--dry-run",
            "--comments",
            str(comments),
            str(markdown),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "**Evaluators:**" in posted.stdout
    assert "Scenario" in posted.stdout
    assert "would POST" in posted.stdout
