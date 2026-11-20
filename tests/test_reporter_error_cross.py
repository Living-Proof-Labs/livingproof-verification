"""A rendered reporter v3.13.2 page shows the ERROR cross for an ERROR integrity run (#106).

capability-report #106 (PR 108) stops a Run Integrity ERROR from showing the passing tick: the
collapsed mark carries a failing cross pip, class `is-error`, and the label `<title>: error`.
That ships in v3.13.2, so this runs against the reporter on PATH (the pinned release).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_site_evaluator_anchor import _document, _page


@pytest.mark.parametrize("scenario", [False, True], ids=["no scenario", "with scenario"])
def test_an_error_integrity_run_shows_the_cross_not_the_tick(scenario: bool, tmp_path: Path):
    page = _page(tmp_path, _document("ERROR", success=False, percentage=0), scenario)
    marks = re.findall(r"<summary class='eval-mark[^']*'.*?</summary>", page, re.S)
    mark = next(m for m in marks if "Run Integrity</span>" in m)
    assert "is-error" in mark
    assert "pip-fail" in mark
    assert "aria-label='Run Integrity: error'" in mark


def test_a_completed_integrity_run_keeps_its_tick(tmp_path: Path):
    page = _page(tmp_path, _document("COMPLETED", success=True, percentage=100))
    mark = next(
        m
        for m in re.findall(r"<summary class='eval-mark[^']*'.*?</summary>", page, re.S)
        if "Run Integrity</span>" in m
    )
    assert "is-error" not in mark and "pip-fail" not in mark
