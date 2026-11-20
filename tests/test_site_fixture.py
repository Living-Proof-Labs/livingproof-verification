# SPDX-License-Identifier: Apache-2.0
"""The PR checks job's fixture site build (tools/site_fixture.py) works end to end."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("site_fixture", ROOT / "tools/site_fixture.py")
site_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(site_fixture)
site_tool = site_fixture.site_tool


def test_fixture_reports_build_a_site_with_no_broken_links(tmp_path):
    reports = tmp_path / "reports"
    written = site_fixture.write(reports)
    assert len(written) == len(site_tool.targets())
    site = tmp_path / "site"
    run = {
        "id": "1",
        "commit": "0" * 40,
        "created": "2026-11-02T00:00:00Z",
        "repository": "Living-Proof-Labs/livingproof-verification",
        "server": "https://github.com",
    }
    assert len(site_tool.build(site, reports, run)) == len(written)
    (site / "index.html").write_text("<!doctype html><title>Overview</title>")
    assert site_fixture.broken_links(site) == []
    assert site_fixture.main(["check", str(site)]) == 0


def test_check_reports_a_broken_link(tmp_path):
    (tmp_path / "index.html").write_text('<a href="missing/">x</a>')
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "index.html").write_text("ok")
    assert site_fixture.broken_links(tmp_path) == ["index.html: missing/"]


def test_fixture_history_has_no_human_reviewer_finding(tmp_path):
    """Art's #135 point 2: the official fixture has no human-reviewer finding to chip."""
    reports = tmp_path / "reports"
    site_fixture.write(reports)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports,
        {
            "id": "1",
            "commit": "0" * 40,
            "created": "2026-11-02T00:00:00Z",
            "repository": "Living-Proof-Labs/livingproof-verification",
            "server": "https://github.com",
        },
    )
    for page in site.glob("reports/*/*/index.html"):
        html = page.read_text()
        assert 'class="poster">CI</span>' in html
        assert "Aaron Roller" not in html
        assert 'class="chip"' not in html
    for record_path in site.glob("reports/*/*/runs/*/run.json"):
        record = json.loads(record_path.read_text())
        assert "failing_evaluators" not in record
        assert record.get("poster", {}).get("type") == "ci"
