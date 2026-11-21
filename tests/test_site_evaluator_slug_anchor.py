"""Evaluator chips link to the reporter's own `#evaluator-<slug>` tab anchor (#176).

Reporter v3.14.0 (capability-report#109) gives every evaluator tab the id `evaluator-<slug>`,
which opens and focuses that tab with or without a Scenario result. The slug is the evaluator id
with every character outside `A-Za-z0-9._-` turned into `-` (`evaluator` when nothing is left);
two ids with the same slug would get `-2`, `-3` in the reporter's own tab order.

Which pages have the anchor is decided from the reporter recorded for the run
(`releases.reporter.tag`): v3.14.0 or newer has it. A page built by an older reporter, or a record
that does not say, keeps the `#summary` fallback. The site cannot know the reporter's tab order, so
an id whose slug is shared with another evaluator's on the same page also keeps the fallback.
"""

from __future__ import annotations

import copy
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from test_site_evaluator_anchor import (
    BASE,
    BUNDLE,
    ROOT,
    SCENARIO,
    _document,
    _ids,
    site_tool,
)

PIN = "v3.14.0"
FALLBACK = "#summary"


def _scenario(grade: int = 0) -> dict:
    result = copy.deepcopy(SCENARIO)
    result["descriptor"]["grade"] = grade
    return result


def _manifest_report(document: dict, scenario: bool) -> dict:
    """What the site stores for the run: the report with the evaluator documents in it."""
    report = {
        "source_bundle": {
            "assessment": {"integrity_results": {"livingproof-run-integrity": document}},
        },
        "summaries": [{"grade": "FAIL"}],
    }
    if scenario:
        report["source_bundle"]["results"] = [{"evaluator_results": [_scenario()]}]
    return report


def _record(report: dict, tag: str | None = PIN) -> dict:
    record = {"grade": "FAIL", "outcome": [{"grade": "FAIL"}]}
    entries = site_tool.failing_evaluators(report)
    assert entries, "the run should have evaluators that spoke"
    record["failing_evaluators"] = entries
    if tag is not None:
        record["releases"] = {"reporter": {"tag": tag}}
    return record


def _fragments(chips: str) -> list[str]:
    return [h.split("#", 1)[1] for h in re.findall(r'href="([^"]*#[^"]*)"', chips)]


def _tab_name(page: str, fragment: str) -> str | None:
    found = re.search(rf"id=['\"]{re.escape(fragment)}['\"].*?eval-name-full'>([^<]*)", page, re.S)
    return found.group(1) if found else None


def _page(tmp_path: Path, document: dict, scenario: bool) -> str:
    import json
    import shutil

    exe = shutil.which("livingproof-capability-report")
    if exe is None:
        pytest.skip("livingproof-capability-report is not installed")
    work = tmp_path / "bundle"
    shutil.copytree(BUNDLE, work)
    manifest = json.loads((work / "manifest.json").read_text())
    for run in manifest["assessment"]["runs"]:
        run["run_id"] = document["integrity"]["run"]["id"]
    if scenario:
        manifest["results"][0]["evaluator_results"] = [_scenario()]
    manifest["assessment"]["integrity_results"] = {"livingproof-run-integrity": document}
    (work / "manifest.json").write_text(json.dumps(manifest))
    out = work / "report.html"
    subprocess.run(
        [exe, "render", str(work / "manifest.json"), "--format", "html", "-o", str(out)],
        check=True,
        capture_output=True,
    )
    return out.read_text(encoding="utf-8")


def test_the_reporter_pin_is_v3_14_0_and_the_installed_reporter_is_the_pin() -> None:
    tag = tomllib.loads((ROOT / "verifications/settings.toml").read_text())["reporter"]["tag"]
    assert tag == PIN
    out = subprocess.run(
        ["livingproof-capability-report", "--version"], capture_output=True, text=True
    )
    assert out.returncode == 0 and "version 3.14.0" in out.stdout, out.stdout


@pytest.mark.parametrize(
    ("ev_id", "slug"),
    [
        ("livingproof-run-integrity", "livingproof-run-integrity"),
        ("livingproof-scenario", "livingproof-scenario"),
        ("a.b_c-d", "a.b_c-d"),
        ("acme/check one", "acme-check-one"),
        ("é", "-"),
        ("", "evaluator"),
    ],
)
def test_the_slug_is_the_reporters_rule(ev_id: str, slug: str) -> None:
    assert site_tool.evaluator_slug(ev_id) == slug


@pytest.mark.parametrize("scenario", [False, True], ids=["no scenario", "with scenario"])
def test_each_chip_resolves_to_its_own_evaluators_tab_in_the_v3_14_0_page(
    scenario: bool, tmp_path: Path
) -> None:
    document = _document("ERROR", success=False, percentage=0)
    report = _manifest_report(document, scenario)
    record = _record(report)
    chips = site_tool._chips_html(record, "../", report=report)
    fragments = _fragments(chips)
    expected = ["evaluator-livingproof-run-integrity"]
    if scenario:
        expected = ["evaluator-livingproof-scenario", *expected]
    assert fragments == expected
    page = _page(tmp_path, document, scenario)
    names = {
        "evaluator-livingproof-run-integrity": "Run Integrity",
        "evaluator-livingproof-scenario": "Scenario",
    }
    assert {f"#{f}" for f in fragments}.isdisjoint({FALLBACK})
    for fragment in fragments:
        assert fragment in _ids(page), f"#{fragment} is not an id on the page"
        assert _tab_name(page, fragment) == names[fragment]


@pytest.mark.parametrize("scenario", [False, True], ids=["no scenario", "with scenario"])
def test_a_not_graded_marker_on_a_passing_run_links_to_the_run_integrity_tab(
    scenario: bool, tmp_path: Path
) -> None:
    document = _document("INCONCLUSIVE")
    report = {
        "source_bundle": {
            "assessment": {"integrity_results": {"livingproof-run-integrity": document}}
        },
        "summaries": [{"grade": "PASS"}],
    }
    record = {"grade": "PASS", "outcome": [{"grade": "PASS"}]}
    record["integrity_status"] = site_tool.integrity_markers(report)
    record["releases"] = {"reporter": {"tag": PIN}}
    chips = site_tool._chips_html(record, "../", report=report)
    (fragment,) = _fragments(chips)
    assert fragment == "evaluator-livingproof-run-integrity"
    page = _page(tmp_path, document, scenario)
    assert fragment in _ids(page)
    assert _tab_name(page, fragment) == "Run Integrity"


@pytest.mark.parametrize("tag", ["v3.13.2", "v3.13.9", "v3.9.0", "v2.99.0", None, "main", ""])
def test_a_page_from_an_older_or_unrecorded_reporter_keeps_the_summary_fallback(tag) -> None:
    document = _document("ERROR", success=False, percentage=0)
    report = _manifest_report(document, scenario=True)
    chips = site_tool._chips_html(_record(report, tag), "runs/1/", report=report)
    assert _fragments(chips) == ["summary", "summary"]
    assert "#evaluator-" not in chips


@pytest.mark.parametrize("tag", ["v3.14.0", "v3.14.1", "v3.15.0", "v4.0.0"])
def test_a_reporter_at_or_after_v3_14_0_gets_the_anchor(tag: str) -> None:
    document = _document("ERROR", success=False, percentage=0)
    report = _manifest_report(document, scenario=True)
    chips = site_tool._chips_html(_record(report, tag), "runs/1/", report=report)
    assert _fragments(chips) == [
        "evaluator-livingproof-scenario",
        "evaluator-livingproof-run-integrity",
    ]


def test_without_the_report_the_chip_keeps_the_fallback() -> None:
    """The collision check needs every evaluator id on the page, so no report, no anchor."""
    document = _document("ERROR", success=False, percentage=0)
    report = _manifest_report(document, scenario=False)
    chips = site_tool._chips_html(_record(report), "../")
    assert _fragments(chips) == ["summary"]


def test_two_evaluators_with_one_slug_keep_the_fallback_not_a_guessed_suffix() -> None:
    """`a b` and `a-b` share the slug a-b; which tab gets `-2` is the reporter's private order."""
    one = _document("ERROR", success=False, percentage=0)
    one["evaluator"]["id"] = "a b"
    two = copy.deepcopy(BASE)
    two["evaluator"]["id"] = "a-b"
    report = {
        "source_bundle": {"assessment": {"integrity_results": {"one": one, "two": two}}},
        "summaries": [{"grade": "FAIL"}],
    }
    record = _record(report)
    chips = site_tool._chips_html(record, "../", report=report)
    assert _fragments(chips) == ["summary", "summary"]
    # An id that merely looks like it has a twin on another page is not affected.
    solo = {
        "source_bundle": {"assessment": {"integrity_results": {"one": one}}},
        "summaries": [{"grade": "FAIL"}],
    }
    assert _fragments(site_tool._chips_html(_record(solo), "../", report=solo)) == ["evaluator-a-b"]


def test_a_stored_dead_href_is_still_ignored() -> None:
    record = {
        "grade": "FAIL",
        "releases": {"reporter": {"tag": PIN}},
        "failing_evaluators": [{"id": "e", "title": "E", "href": "#evaluator-x"}],
    }
    report = {
        "source_bundle": {
            "assessment": {
                "integrity_results": {"e": {**copy.deepcopy(BASE), "evaluator": {"id": "e"}}}
            }
        }
    }
    chips = site_tool._chips_html(record, "runs/1/", report=report)
    assert 'href="runs/1/#evaluator-e"' in chips and "#evaluator-x" not in chips


@pytest.mark.parametrize(
    "ev_id",
    [
        "Acme.Check_1",
        "UPPER-Case",
        "a b",
        "a/b",
        "--x--",
        "x" * 80,
        "é-ü",
        "livingproof-run-integrity",
        "",
    ],
)
def test_the_slug_matches_the_installed_reporters_anchor_id(ev_id: str) -> None:
    """Parity with the reporter's own `_anchor_id` (case is kept, edge characters become `-`)."""
    from livingproof_capability_report import evaluators as reporter

    anchor = reporter._anchor_id(ev_id, set())
    assert anchor == f"evaluator-{site_tool.evaluator_slug(ev_id)}"
