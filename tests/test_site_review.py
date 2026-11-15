# SPDX-License-Identifier: Apache-2.0
"""Review checks from Vera's review of #70.

These cover hand checks that are not in tests/test_site.py: reconstructing the
history line from the published run.json and report.json for 43683639865
(statement dropped from the record, still in the report) and 43656730997
(legacy, no reproducibility block), plus HTML escaping and the rule that a
stored statement wins over a finding.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("site_tool_review", ROOT / "tools/site.py")
site_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_tool)


# Compact extracts from the public pages at
# docs.livingprooflabs.com/livingproof-verification/unitree-g1/stock-mujoco-hallway/
# opened 16 Nov 2026, 7:42am Australia/Sydney. Only outcome and the report
# summary are kept; k, n and required_passes stay null, as published.

PUBLISHED_COMPARED = {
    "run_id": "43683639865",
    "outcome": {
        "capability": "walk-hallway",
        "system": "unitree-g1/stock",
        "grade": "FAIL",
        "passed": None,
        "trials": None,
        "required": None,
    },
    "summary": {
        "capability": "walk-hallway",
        "system": {"id": "unitree-g1/stock"},
        "grade": "FAIL",
        "k": None,
        "n": None,
        "required_passes": None,
        "reproducibility": {
            "compared": True,
            "identical": True,
            "repeats": 3,
            "finding": None,
            "agreeing": 3,
            "statement": "3 of 3 repeats agree",
        },
    },
}

PUBLISHED_LEGACY = {
    "run_id": "43656730997",
    "outcome": {
        "capability": "walk-hallway",
        "system": "unitree-g1/stock",
        "grade": "FAIL",
        "passed": None,
        "trials": None,
        "required": None,
    },
    "summary": {
        "capability": "walk-hallway",
        "system": {"id": "unitree-g1/stock"},
        "grade": "FAIL",
        "k": None,
        "n": None,
        "required_passes": None,
    },
}


def _restore_published_wording(tmp_path: Path, published: dict) -> dict:
    folder = tmp_path / published["run_id"]
    folder.mkdir()
    (folder / "run.json").write_text(
        json.dumps({"outcome": [dict(published["outcome"])]}, indent=2) + "\n"
    )
    (folder / "report.json").write_text(
        json.dumps({"report_version": "1.0.0", "summaries": [published["summary"]]}) + "\n"
    )
    record = json.loads((folder / "run.json").read_text())
    site_tool.remember_stored_wording(folder, record)
    return json.loads((folder / "run.json").read_text())["outcome"][0]


def test_published_compared_run_restores_the_statement_and_keeps_counts_null(tmp_path):
    """Run 43683639865: the live record dropped the statement; the report still has it."""
    item = _restore_published_wording(tmp_path, PUBLISHED_COMPARED)
    assert item["grade"] == "FAIL"
    assert item["statement"] == "3 of 3 repeats agree"
    assert item["passed"] is None and item["trials"] is None and item["required"] is None
    cell = site_tool.outcome_cells({"outcome": [item]})
    assert "3 of 3 repeats agree" in cell
    assert "None/None" not in cell
    assert " passed (" not in cell
    assert '<span class="FAIL">FAIL</span>' in cell


def test_published_legacy_run_says_counts_not_stored(tmp_path):
    """Run 43656730997: null counts and no reproducibility block stay 'counts not stored'."""
    item = _restore_published_wording(tmp_path, PUBLISHED_LEGACY)
    assert item["grade"] == "FAIL"
    assert "statement" not in item and "finding" not in item
    assert item["passed"] is None and item["trials"] is None and item["required"] is None
    cell = site_tool.outcome_cells({"outcome": [item]})
    assert "counts not stored" in cell
    assert "None/None" not in cell
    assert "repeats agree" not in cell
    assert '<span class="FAIL">FAIL</span>' in cell


def test_statement_and_finding_are_html_escaped():
    statement = "3 of 3 <script>alert(1)</script> agree"
    finding = "A & B disagreed"
    statement_cell = site_tool.outcome_cells(
        {
            "outcome": [
                {
                    "grade": "PASS",
                    "capability": "walk-hallway",
                    "statement": statement,
                    "passed": None,
                    "trials": None,
                    "required": None,
                }
            ]
        }
    )
    assert statement not in statement_cell
    assert "3 of 3 &lt;script&gt;alert(1)&lt;/script&gt; agree" in statement_cell
    finding_cell = site_tool.outcome_cells(
        {
            "outcome": [
                {
                    "grade": "INSUFFICIENT_EVIDENCE",
                    "capability": "walk-hallway",
                    "finding": finding,
                    "passed": None,
                    "trials": None,
                    "required": None,
                }
            ]
        }
    )
    assert finding not in finding_cell
    assert "A &amp; B disagreed" in finding_cell
    assert '<span class="INSUFFICIENT_EVIDENCE">INSUFFICIENT_EVIDENCE</span>' in finding_cell


def test_a_stored_statement_is_preferred_over_a_finding():
    cell = site_tool.outcome_cells(
        {
            "outcome": [
                {
                    "grade": "INSUFFICIENT_EVIDENCE",
                    "capability": "walk-hallway",
                    "statement": "3 of 3 repeats agree",
                    "finding": "Identical trials disagreed on trajectory hash or outcome.",
                    "passed": None,
                    "trials": None,
                    "required": None,
                }
            ]
        }
    )
    assert "3 of 3 repeats agree" in cell
    assert "disagreed" not in cell
    assert "None/None" not in cell
