# SPDX-License-Identifier: Apache-2.0
"""The PR checks job's fixture site build (tools/site_fixture.py) works end to end."""

import importlib.util
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
    assert site_fixture.broken_links(site) == []
    assert site_fixture.main(["check", str(site)]) == 0


def test_check_reports_a_broken_link(tmp_path):
    (tmp_path / "index.html").write_text('<a href="missing/">x</a>')
    assert site_fixture.broken_links(tmp_path) == ["index.html: missing/"]
