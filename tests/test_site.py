# SPDX-License-Identifier: Apache-2.0
"""Pages site assembly (tools/site.py) and the publishing jobs in verify.yml."""

import email.message
import hashlib
import html as html_module
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tomllib
import urllib.error
import urllib.request
import zipfile

import pytest
import yaml
from evaluated import bundle, evaluator_result

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("site_tool", ROOT / "tools/site.py")
site_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_tool)
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())
REPO = "Living-Proof-Labs/livingproof-verification"


def write_reports(directory: Path, passed: int, grade: str, html: str | None = None):
    directory.mkdir(parents=True)
    report = {
        "report_version": "1.0.0",
        "source_bundle": bundle(),
        "summaries": [
            {
                "capability": "walk-hallway",
                "system": {"id": "unitree-g1/stock"},
                "grade": grade,
                "k": passed,
                "n": 20,
                "required_passes": 18,
            }
        ],
    }
    (directory / "report.json").write_text(json.dumps(report))
    for name in ("report.html", "report.md", "report.txt"):
        (directory / name).write_text(f"{name} {passed}")
    if html is not None:
        (directory / "report.html").write_text(html)
    restamp(directory)


# Grading inputs a real report artifact carries beside the report (capability-verifier
# v1.21+). The scenario evaluator fixture cites this thresholds.json by sha256 (#65).
GRADING_INPUTS = ("thresholds.json",)
THRESHOLDS_FIXTURE = Path(__file__).parent / "fixtures/evaluator-result-scenario-thresholds.json"


def restamp(directory: Path) -> None:
    (directory / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((directory / n).read_bytes()).hexdigest()}  {n}\n"
            for n in (*site_tool.REPORT_FILES, *GRADING_INPUTS)
            if n in site_tool.REPORT_FILES or (directory / n).exists()
        )
    )


def run(run_id: str, created: str) -> dict:
    return {
        "id": run_id,
        "commit": "a" * 40,
        "created": created,
        "repository": REPO,
        "server": "https://github.com",
    }


def reports_for(tmp_path: Path, name: str, passed: int, grade: str) -> Path:
    """One run's report artifacts: every locked definition and configuration."""
    reports = tmp_path / name
    for target in site_tool.targets():
        write_reports(reports / target["artifact"], passed, grade)
    return reports


def local_links_resolve(site: Path):
    for page in site.rglob("*.html"):
        for href in re.findall(r"""(?:href|src)=["']([^"']+)["']""", page.read_text()):
            if href.startswith("https://"):
                continue
            href = href.split("#", 1)[0]
            if not href:
                continue
            target = (page.parent / href).resolve()
            if href.endswith("/"):
                target = target / "index.html"
            assert target.is_file(), (page.relative_to(site), href)


def test_targets_follow_the_locks_and_the_workflow_artifact_names():
    targets = site_tool.targets()
    assert ("unitree-g1", "stock-mujoco-hallway") in {(t["robot"], t["variant"]) for t in targets}
    assert len({(t["robot"], t["variant"]) for t in targets}) == len(targets)
    for target in targets:
        job = WORKFLOW["jobs"][target["id"]]
        assert target["artifact"] == (
            job["with"]["artifact-prefix"] + "capability-report-" + target["configuration"]
        )


def test_second_run_updates_latest_and_keeps_the_first(tmp_path):
    site = tmp_path / "site"
    site_tool.build(
        site, reports_for(tmp_path, "one", 12, "FAIL"), run("100", "2026-11-02T06:00:00Z")
    )
    site_tool.build(
        site, reports_for(tmp_path, "two", 19, "PASS"), run("200", "2026-11-03T06:00:00Z")
    )
    base = site / "unitree-g1/stock-mujoco-hallway"
    assert (base / "runs/100/report.md").read_text() == "report.md 12"
    assert (base / "runs/200/report.md").read_text() == "report.md 19"
    assert not (base / "latest/report.md").exists()
    names = sorted(path.name for path in (base / "latest").rglob("*") if path.is_file())
    assert names == ["index.html"]
    index = (site / "index.html").read_text()
    assert "PASS" in index and "History (2)" in index
    assert "19/20 passed" not in index
    history = (base / "index.html").read_text()
    assert history.index("runs/200/") < history.index("runs/100/")
    latest = (base / "latest/index.html").read_text()
    assert 'http-equiv="refresh"' in latest and "url=../runs/200/" in latest
    assert 'href="../runs/200/"' in latest
    lock = json.loads(
        (ROOT / "verifications/unitree-g1-stock-mujoco-hallway.lock.json").read_text()
    )
    releases = lock["releases"]
    for link in (
        f"{REPO}/commit/{'a' * 40}",
        f"capability-verifier/releases/tag/{releases['verifier']['tag']}",
        f"livingproof-capability-report/releases/tag/{releases['reporter']['tag']}",
        f"livingproof-unitree-g1/releases/tag/{lock['robot']['ref']}",
    ):
        assert link in history
    assert "/actions/" not in latest and "artifact" not in latest.lower()
    local_links_resolve(site)


def test_published_history_names_the_verifier_and_reporter_releases(tmp_path):
    """The public history and run.json name both pinned releases (issue 62)."""
    settings = tomllib.loads((ROOT / "verifications/settings.toml").read_text())
    reporter_tag = settings["reporter"]["tag"]
    assert reporter_tag == "v3.13.0"
    site = tmp_path / "site"
    site_tool.build(
        site, reports_for(tmp_path, "one", 12, "PASS"), run("100", "2026-11-02T06:00:00Z")
    )
    for target in site_tool.targets():
        lock = json.loads((ROOT / "verifications" / f"{target['id']}.lock.json").read_text())
        verifier = lock["releases"]["verifier"]
        reporter = lock["releases"]["reporter"]
        assert verifier["tag"] == "v2.8.0"
        assert verifier["commit"] == "7ebde8b4ca03d51cfac22a87bf2f49e4b25a6677"
        assert reporter["tag"] == reporter_tag
        assert reporter["repository"] == settings["reporter"]["repository"]
        history = (site / target["robot"] / target["variant"] / "index.html").read_text()
        record = json.loads(
            (site / target["robot"] / target["variant"] / "runs/100/run.json").read_text()
        )
        for release in (verifier, reporter):
            link = f"{release['repository']}/releases/tag/{release['tag']}"
            assert link in history
            assert f">{release['tag']}</a>" in history
        assert record["releases"]["verifier"]["tag"] == verifier["tag"]
        assert record["releases"]["reporter"]["tag"] == reporter["tag"]
        assert record["releases"]["verifier"]["commit"] == verifier["commit"]
        assert record["releases"]["reporter"]["commit"] == reporter["commit"]


def test_index_and_history_show_ci_runs_as_text(tmp_path):
    """lpv #23 / #129: no /actions/ links, no raw run ids or CI run links on index/history."""
    site = tmp_path / "site"
    site_tool.build(
        site, reports_for(tmp_path, "one", 12, "FAIL"), run("100", "2026-11-02T00:00:00Z")
    )
    site_tool.build(
        site, reports_for(tmp_path, "two", 19, "PASS"), run("200-2", "2026-11-03T00:00:00Z")
    )
    actions = f"https://github.com/{REPO}/actions/runs/"
    index = (site / "index.html").read_text()
    assert actions not in index
    assert "CI run" not in index
    history = (site / "unitree-g1/stock-mujoco-hallway/index.html").read_text()
    assert actions not in history
    assert "CI run" not in history
    for run_id in ("100", "200-2"):
        assert f'<a href="runs/{run_id}/">Report</a>' in history
    local_links_resolve(site)


def write_compared_reports(directory: Path, grade: str, statement: str) -> None:
    directory.mkdir(parents=True)
    report = {
        "report_version": "1.0.0",
        "source_bundle": bundle(),
        "summaries": [
            {
                "capability": "walk-hallway",
                "system": {"id": "unitree-g1/stock"},
                "grade": grade,
                "k": None,
                "n": None,
                "required_passes": None,
                "wilson95": [None, None],
                "reproducibility": {
                    "compared": True,
                    "identical": True,
                    "repeats": 3,
                    "finding": None,
                    "agreeing": 3,
                    "statement": statement,
                },
            }
        ],
    }
    (directory / "report.json").write_text(json.dumps(report))
    for name in ("report.html", "report.md", "report.txt"):
        (directory / name).write_text(f"{name} {statement}\n")
    restamp(directory)


def reports_for_compared(tmp_path: Path, name: str, grade: str, statement: str) -> Path:
    reports = tmp_path / name
    for target in site_tool.targets():
        write_compared_reports(reports / target["artifact"], grade, statement)
    return reports


def test_history_uses_the_reproducibility_statement_when_counts_are_null(tmp_path):
    """Compared repeats are one outcome plus the stored statement, not None/None."""
    statement = "3 of 3 repeats agree"
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for_compared(tmp_path, "compared", "PASS", statement),
        run("100", "2026-11-15T01:15:48Z"),
    )
    home = (site / "index.html").read_text()
    history = (site / "unitree-g1/stock-mujoco-hallway/index.html").read_text()
    for page in (home, history):
        assert "None/None passed (None needed)" not in page
        assert '<span class="badge badge-pass">PASS</span>' in page
    record = json.loads((site / "unitree-g1/stock-mujoco-hallway/runs/100/run.json").read_text())
    assert statement in site_tool.outcome_cells(record)


# A restored run.json from before this display fix has null counts and no statement.
# The history and index lines say this instead of None/None. Nothing is invented.
LEGACY_COUNTS = "counts not stored"
DISAGREEING_FINDING = (
    "Identical trials disagreed on trajectory hash or outcome. "
    "This is a test-system reproducibility finding, not a robot FAIL."
)


def write_summary_reports(directory: Path, summary: dict) -> None:
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(
        json.dumps({"report_version": "1.0.0", "source_bundle": bundle(), "summaries": [summary]})
    )
    for name in ("report.html", "report.md", "report.txt"):
        (directory / name).write_text(f"{name}\n")
    restamp(directory)


def reports_for_summary(tmp_path: Path, name: str, summary: dict) -> Path:
    reports = tmp_path / name
    for target in site_tool.targets():
        write_summary_reports(reports / target["artifact"], summary)
    return reports


def history_and_index(site: Path) -> tuple[str, str]:
    history = (site / "unitree-g1/stock-mujoco-hallway/index.html").read_text()
    return history, (site / "index.html").read_text()


def test_agreeing_repeats_preserve_the_statement_and_leave_counts_null(tmp_path):
    """An agreeing FAIL keeps the stored statement. k, n and required_passes stay null."""
    statement = "3 of 3 repeats agree"
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for_compared(tmp_path, "agree", "FAIL", statement),
        run("100", "2026-11-15T01:15:48Z"),
    )
    history, index = history_and_index(site)
    for page in (history, index):
        assert "None/None passed (None needed)" not in page
        assert " passed (" not in page
        assert '<span class="badge badge-fail">FAIL</span>' in page
    for target in site_tool.targets():
        folder = site / target["robot"] / target["variant"] / "runs" / "100"
        record = json.loads((folder / "run.json").read_text())
        item = record["outcome"][0]
        assert item["statement"] == statement
        assert item["passed"] is None and item["trials"] is None and item["required"] is None
        summary = json.loads((folder / "report.json").read_text())["summaries"][0]
        assert summary["k"] is None and summary["n"] is None
        assert summary["required_passes"] is None
        assert summary["reproducibility"]["statement"] == statement


def test_restored_history_reads_the_statement_from_report_json(tmp_path):
    """An older run.json dropped the statement. report.json still has it.

    Re-rendering copies that statement onto the history record and both pages.
    k, n and required_passes stay null.
    """
    statement = "3 of 3 repeats agree"
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for_compared(tmp_path, "restored", "FAIL", statement),
        run("100", "2026-11-15T01:15:48Z"),
    )
    for path in site.glob("*/*/runs/*/run.json"):
        record = json.loads(path.read_text())
        for item in record["outcome"]:
            item.pop("statement", None)
            item.pop("finding", None)
        path.write_text(json.dumps(record, indent=2) + "\n")
        assert "statement" not in json.loads(path.read_text())["outcome"][0]
    site_tool.render(site, now="2026-11-15T01:15:48Z")
    history, index = history_and_index(site)
    for page in (history, index):
        assert "None/None" not in page
        assert "counts not stored" not in page
        assert '<span class="badge badge-fail">FAIL</span>' in page
    item = json.loads((site / "unitree-g1/stock-mujoco-hallway/runs/100/run.json").read_text())[
        "outcome"
    ][0]
    assert item["statement"] == statement
    assert item["passed"] is None and item["trials"] is None and item["required"] is None


def test_disagreeing_repeats_show_the_finding_and_keep_null_counts(tmp_path):
    """No statement is stored. The line shows the finding, not a made-up passed/total."""
    summary = {
        "capability": "walk-hallway",
        "system": {"id": "unitree-g1/stock"},
        "grade": "INSUFFICIENT_EVIDENCE",
        "k": None,
        "n": None,
        "required_passes": None,
        "wilson95": [None, None],
        "reproducibility": {
            "compared": True,
            "identical": False,
            "repeats": 3,
            "finding": {
                "kind": "test-system",
                "name": "reproducibility",
                "detail": DISAGREEING_FINDING,
            },
        },
    }
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for_summary(tmp_path, "disagree", summary),
        run("100", "2026-11-15T01:15:48Z"),
    )
    history, index = history_and_index(site)
    assert DISAGREEING_FINDING in history
    for page in (history, index):
        assert "None/None passed (None needed)" not in page
        assert "repeats agree" not in page
        assert " passed (" not in page
        assert '<span class="badge badge-warn">Insufficient evidence</span>' in page
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/100"
    item = json.loads((folder / "run.json").read_text())["outcome"][0]
    assert item["grade"] == "INSUFFICIENT_EVIDENCE"
    assert item["passed"] is None and item["trials"] is None and item["required"] is None
    assert "statement" not in item
    assert item["finding"] == DISAGREEING_FINDING
    stored = json.loads((folder / "report.json").read_text())["summaries"][0]
    assert stored["k"] is None and stored["n"] is None and stored["required_passes"] is None
    assert "statement" not in stored["reproducibility"]
    record = json.loads((folder / "run.json").read_text())
    record["outcome"][0].pop("finding", None)
    (folder / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    site_tool.render(site, now="2026-11-15T01:15:48Z")
    history, index = history_and_index(site)
    assert DISAGREEING_FINDING in history
    restored = json.loads((folder / "run.json").read_text())["outcome"][0]
    assert restored["finding"] == DISAGREEING_FINDING
    assert restored["passed"] is None and "statement" not in restored


def test_legacy_null_counts_without_a_statement_say_counts_not_stored(tmp_path):
    """A record with null counts and no statement must not crash or show None/None.

    Fallback, on the history and index lines: ``counts not stored``.
    """
    summary = {
        "capability": "walk-hallway",
        "system": {"id": "unitree-g1/stock"},
        "grade": "PASS",
        "k": None,
        "n": None,
        "required_passes": None,
    }
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for_summary(tmp_path, "legacy", summary),
        run("100", "2026-11-15T01:15:48Z"),
    )
    for path in site.glob("*/*/runs/*/run.json"):
        record = json.loads(path.read_text())
        for item in record["outcome"]:
            item["passed"] = None
            item["trials"] = None
            item["required"] = None
            item.pop("statement", None)
            item.pop("finding", None)
        path.write_text(json.dumps(record, indent=2) + "\n")
    site_tool.render(site, now="2026-11-15T01:15:48Z")
    history, index = history_and_index(site)
    for page in (history, index):
        assert "None/None passed (None needed)" not in page
        assert "None/None" not in page
        assert '<span class="badge badge-pass">PASS</span>' in page
        assert " passed (" not in page
    record = json.loads((site / "unitree-g1/stock-mujoco-hallway/runs/100/run.json").read_text())
    assert LEGACY_COUNTS in site_tool.outcome_cells(record)
    item = record["outcome"][0]
    assert "statement" not in item and "finding" not in item
    assert item["passed"] is None and item["trials"] is None and item["required"] is None


def test_seeded_and_single_trial_counts_still_render(tmp_path):
    """Seeded and single-trial reports keep the stored k, n and required_passes."""
    single = {
        "capability": "walk-hallway",
        "system": {"id": "unitree-g1/stock"},
        "grade": "PASS",
        "k": 1,
        "n": 1,
        "required_passes": 1,
    }
    seeded = {
        "capability": "walk-hallway",
        "system": {"id": "unitree-g1/stock"},
        "grade": "FAIL",
        "k": 2,
        "n": 3,
        "required_passes": 3,
    }
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for_summary(tmp_path, "single", single),
        run("100", "2026-11-15T01:00:00Z"),
    )
    site_tool.build(
        site,
        reports_for_summary(tmp_path, "seeded", seeded),
        run("200", "2026-11-15T02:00:00Z"),
    )
    history, index = history_and_index(site)
    assert '<span class="badge badge-pass">PASS</span>' in history
    assert '<span class="badge badge-fail">FAIL</span>' in history
    assert '<span class="badge badge-fail">FAIL</span>' in index
    assert "counts not stored" not in history and "None/None" not in history
    single_run = json.loads(
        (site / "unitree-g1/stock-mujoco-hallway/runs/100/run.json").read_text()
    )
    seeded_run = json.loads(
        (site / "unitree-g1/stock-mujoco-hallway/runs/200/run.json").read_text()
    )
    assert (single_run["outcome"][0]["passed"], single_run["outcome"][0]["trials"]) == (1, 1)
    assert single_run["outcome"][0]["required"] == 1
    assert "statement" not in single_run["outcome"][0]
    assert (seeded_run["outcome"][0]["passed"], seeded_run["outcome"][0]["trials"]) == (2, 3)
    assert seeded_run["outcome"][0]["required"] == 3
    assert "statement" not in seeded_run["outcome"][0]


def test_reports_are_copied_unchanged_and_checked(tmp_path):
    reports = reports_for(tmp_path, "one", 12, "FAIL")
    artifact = reports / "unitree-g1-stock-mujoco-hallway-capability-report-stock"
    (artifact / "report.html").write_text("tampered")
    with pytest.raises(site_tool.SiteError, match="SHA256SUMS"):
        site_tool.build(tmp_path / "site", reports, run("1", "2026-11-02T00:00:00Z"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r.pop("report_version"), "report_version None"),
        (lambda r: r.update(report_version="2.0.0"), "report_version '2.0.0'"),
        (lambda r: r.pop("summaries"), "summaries list"),
        (lambda r: r["summaries"][0].pop("k"), r"summaries\[0\] lacks k"),
        (lambda r: r["summaries"][0].update(system="g1"), "lacks system.id"),
        (lambda r: r.update(report_version=["1.0.0"]), r"report_version \['1.0.0'\]"),
        (lambda r: r.update(report_version={"v": "1.0.0"}), r"report_version \{'v': '1.0.0'\}"),
        (lambda r: r["summaries"].__setitem__(0, "g1"), r"summaries\[0\] is str, not an object"),
    ],
)
def test_unknown_report_shape_fails_clearly(tmp_path, change, message):
    reports = reports_for(tmp_path, "one", 12, "FAIL")
    folder = reports / "unitree-g1-stock-mujoco-hallway-capability-report-stock"
    report = json.loads((folder / "report.json").read_text())
    change(report)
    (folder / "report.json").write_text(json.dumps(report))
    (folder / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((folder / n).read_bytes()).hexdigest()}  {n}\n"
            for n in site_tool.REPORT_FILES
        )
    )
    with pytest.raises(site_tool.SiteError, match=message):
        site_tool.build(tmp_path / "site", reports, run("1", "2026-11-02T00:00:00Z"))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[]", "expected a JSON object, found list"),
        ("{not json", "not valid JSON"),
    ],
)
def test_report_json_that_is_not_an_object_fails_clearly(tmp_path, text, message):
    reports = reports_for(tmp_path, "one", 12, "FAIL")
    folder = reports / "unitree-g1-stock-mujoco-hallway-capability-report-stock"
    (folder / "report.json").write_text(text)
    (folder / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((folder / n).read_bytes()).hexdigest()}  {n}\n"
            for n in site_tool.REPORT_FILES
        )
    )
    with pytest.raises(site_tool.SiteError, match=message):
        site_tool.build(tmp_path / "site", reports, run("1", "2026-11-02T00:00:00Z"))


@pytest.mark.parametrize("name", ["report.json", "report.html"])
def test_missing_report_file_is_a_site_error(tmp_path, name):
    reports = reports_for(tmp_path, "one", 12, "FAIL")
    (reports / "unitree-g1-stock-mujoco-hallway-capability-report-stock" / name).unlink()
    with pytest.raises(site_tool.SiteError, match=f"missing {name}"):
        site_tool.build(tmp_path / "site", reports, run("1", "2026-11-02T00:00:00Z"))


@pytest.mark.parametrize("name", ["report.json", "report.md"])
def test_missing_sha256sums_entry_is_a_site_error(tmp_path, name):
    reports = reports_for(tmp_path, "one", 12, "FAIL")
    sums = reports / "unitree-g1-stock-mujoco-hallway-capability-report-stock" / "SHA256SUMS"
    sums.write_text(
        "".join(line + "\n" for line in sums.read_text().splitlines() if not line.endswith(name))
    )
    with pytest.raises(site_tool.SiteError, match=f"no entry for {name}"):
        site_tool.build(tmp_path / "site", reports, run("1", "2026-11-02T00:00:00Z"))


def test_missing_sha256sums_is_a_site_error(tmp_path):
    reports = reports_for(tmp_path, "one", 12, "FAIL")
    (reports / "unitree-g1-stock-mujoco-hallway-capability-report-stock" / "SHA256SUMS").unlink()
    with pytest.raises(site_tool.SiteError, match="missing SHA256SUMS"):
        site_tool.build(tmp_path / "site", reports, run("1", "2026-11-02T00:00:00Z"))


def test_missing_report_artifact_fails(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(site_tool.SiteError, match="No report artifact"):
        site_tool.build(tmp_path / "site", tmp_path / "empty", run("1", "2026-11-02T00:00:00Z"))


def artifact(identifier, branch, run_id, created, expired=False, name="github-pages"):
    return {
        "id": identifier,
        "name": name,
        "created_at": created,
        "expired": expired,
        "archive_download_url": f"https://api.example/{identifier}/zip",
        "workflow_run": {"id": run_id, "head_branch": branch},
    }


def test_previous_artifact_is_the_newest_earlier_main_one():
    artifacts = [
        artifact(1, "main", 10, "2026-11-01T00:00:00Z"),
        artifact(2, "main", 20, "2026-11-02T00:00:00Z"),
        artifact(3, "feature", 30, "2026-11-03T00:00:00Z"),
        artifact(4, "main", 40, "2026-11-04T00:00:00Z"),
        artifact(5, "main", 50, "2026-11-05T00:00:00Z", name="other"),
    ]
    assert site_tool.latest_pages_artifact(artifacts, "main", 40)["id"] == 2
    assert site_tool.latest_pages_artifact(artifacts, "main", None)["id"] == 4
    assert site_tool.latest_pages_artifact([], "main", None) is None


def test_expired_history_fails_instead_of_dropping_runs():
    with pytest.raises(site_tool.SiteError, match="expired"):
        site_tool.latest_pages_artifact(
            [artifact(1, "main", 10, "2026-11-01T00:00:00Z", expired=True)], "main", None
        )


def pages_zip(site: Path) -> bytes:
    """What upload-pages-artifact stores: a zip holding artifact.tar of ./site."""
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        tar.add(site, arcname=".")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("artifact.tar", tar_buffer.getvalue())
    return zip_buffer.getvalue()


def test_restore_then_build_keeps_history(tmp_path, monkeypatch):
    first = tmp_path / "first"
    site_tool.build(
        first, reports_for(tmp_path, "one", 12, "FAIL"), run("100", "2026-11-02T06:00:00Z")
    )
    listing = {"artifacts": [artifact(7, "main", 100, "2026-11-02T06:10:00Z")]}
    archive = pages_zip(first)
    requested = []

    def fake(url, token):
        requested.append(url)
        return io.BytesIO(archive if url.endswith("/zip") else json.dumps(listing).encode())

    monkeypatch.setattr(site_tool, "_request", fake)
    # The download opens each hop itself (302, then the blob) instead of _request.
    monkeypatch.setattr(site_tool, "_open_once", fake)
    restored = tmp_path / "restored"
    assert site_tool.restore(restored, REPO, "main", 200, "t", dry_run=True)["id"] == 7
    assert not any(restored.iterdir()) and len(requested) == 1
    site_tool.restore(restored, REPO, "main", 200, "t")
    site_tool.build(
        restored, reports_for(tmp_path, "two", 19, "PASS"), run("200", "2026-11-03T06:00:00Z")
    )
    base = restored / "unitree-g1/stock-mujoco-hallway"
    assert sorted(p.name for p in (base / "runs").iterdir()) == ["100", "200"]
    latest = (base / "latest/index.html").read_text()
    assert "url=../runs/200/" in latest
    assert not (base / "latest/run.json").exists()


SITE_URL = "https://docs.example/livingproof-verification/"


def no_artifacts(monkeypatch, statuses):
    """Artifact listing is empty; the live site answers `statuses` per file name."""
    monkeypatch.setattr(site_tool, "_request", lambda url, token: io.BytesIO(b'{"artifacts": []}'))
    probed = []

    def status(url):
        probed.append(url)
        answer = statuses[url.rsplit("/", 1)[1]]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(site_tool, "_fetch_status", status)
    return probed


@pytest.mark.parametrize(
    "statuses",
    [{"site.json": 200, "index.html": 404}, {"site.json": 404, "index.html": 200}],
)
def test_restore_refuses_a_fresh_site_when_history_is_live(tmp_path, monkeypatch, statuses):
    no_artifacts(monkeypatch, statuses)
    with pytest.raises(site_tool.SiteError, match="reset-history"):
        site_tool.restore(tmp_path / "s", REPO, "main", 1, "t", site_url=SITE_URL)
    assert (
        site_tool.restore(
            tmp_path / "s", REPO, "main", 1, "t", site_url=SITE_URL, reset_history=True
        )
        is None
    )


@pytest.mark.parametrize(
    "statuses",
    [{"site.json": 500, "index.html": 404}, {"site.json": OSError("down"), "index.html": 404}],
)
def test_restore_fails_when_the_live_site_cannot_be_checked(tmp_path, monkeypatch, statuses):
    no_artifacts(monkeypatch, statuses)
    with pytest.raises(site_tool.SiteError, match="Cannot check"):
        site_tool.restore(tmp_path / "s", REPO, "main", 1, "t", site_url=SITE_URL)


def test_restore_starts_fresh_only_when_nothing_is_published(tmp_path, monkeypatch):
    probed = no_artifacts(monkeypatch, {"site.json": 404, "index.html": 404})
    assert site_tool.restore(tmp_path / "s", REPO, "main", 1, "t", site_url=SITE_URL) is None
    assert probed == [SITE_URL + "site.json", SITE_URL + "index.html"]
    with pytest.raises(site_tool.SiteError, match="--site-url"):
        site_tool.restore(tmp_path / "s", REPO, "main", 1, "t")


ARCHIVE_URL = "https://api.example/7/zip"
BLOB_URL = "https://objects.githubusercontent.example/github-pages/artifact"


def http_error(url, code, reason, location=None):
    headers = email.message.Message()
    if location:
        headers["Location"] = location
    return urllib.error.HTTPError(url, code, reason, headers, None)


def redirect_to_blob():
    return http_error(ARCHIVE_URL, 302, "Found", location=BLOB_URL)


def refuse_network(*args, **kwargs):
    raise AssertionError(f"unexpected network call: {args!r} {kwargs!r}")


def scripted_download(monkeypatch, outcomes):
    """Queue of exceptions or bodies for each hop. Sleep and HTTP stay in-process."""
    calls = []
    queue = list(outcomes)

    def open_once(url, token):
        calls.append((url, token))
        if not queue:
            raise AssertionError(f"unexpected GET {url}")
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return io.BytesIO(outcome)

    sleeps = []
    monkeypatch.setattr(site_tool, "_open_once", open_once)
    monkeypatch.setattr(site_tool.time, "sleep", sleeps.append)
    monkeypatch.setattr(site_tool.random, "uniform", lambda _low, _high: 1.0)
    monkeypatch.setattr(site_tool.urllib.request, "urlopen", refuse_network)
    monkeypatch.setattr(site_tool.urllib.request, "build_opener", refuse_network)
    return calls, sleeps, queue


def list_pages(monkeypatch, artifacts):
    def request(url, token):
        if isinstance(artifacts, BaseException):
            raise artifacts
        return io.BytesIO(json.dumps({"artifacts": artifacts}).encode())

    monkeypatch.setattr(site_tool, "_request", request)


def pages_artifact_listing():
    return [artifact(7, "main", 100, "2026-11-02T06:10:00Z")]


def test_redirect_handler_leaves_the_blob_hop_to_the_caller():
    """A 302 must surface here. urlopen following it would hide the blob 503."""
    handler = site_tool._NoRedirect()
    headers = email.message.Message()
    headers["Location"] = BLOB_URL
    request = urllib.request.Request(ARCHIVE_URL)
    with pytest.raises(urllib.error.HTTPError) as caught:
        handler.http_error_302(request, None, 302, "Found", headers)
    error = caught.value
    assert error.code == 302
    assert error.url == ARCHIVE_URL
    assert error.headers.get("Location") == BLOB_URL


def test_restore_succeeds_after_transient_503s(tmp_path, monkeypatch, capsys):
    """503s from the blob after the 302 are retried; so are 429 and a dropped connection."""
    first = tmp_path / "first"
    site_tool.build(
        first, reports_for(tmp_path, "one", 12, "FAIL"), run("100", "2026-11-02T06:00:00Z")
    )
    archive = pages_zip(first)
    failures = [
        http_error(BLOB_URL, 503, "Egress is over the account limit"),
        http_error(BLOB_URL, 503, "Egress is over the account limit"),
        http_error(BLOB_URL, 429, "too many requests"),
        urllib.error.URLError(TimeoutError("timed out")),
    ]
    outcomes = []
    for failure in failures:
        outcomes.extend((redirect_to_blob(), failure))
    outcomes.extend((redirect_to_blob(), archive))
    calls, sleeps, remaining = scripted_download(monkeypatch, outcomes)
    list_pages(monkeypatch, pages_artifact_listing())

    restored = tmp_path / "restored"
    found = site_tool.restore(restored, REPO, "main", 200, "t")

    assert remaining == []
    assert found["id"] == 7
    assert calls == [(ARCHIVE_URL, "t"), (BLOB_URL, None)] * site_tool.DOWNLOAD_ATTEMPTS
    # Jitter is pinned to 1, so the waits are the base schedule: 10, 20, 40, 80 seconds.
    assert sleeps == [site_tool.DOWNLOAD_BACKOFF_BASE * (2**n) for n in range(len(failures))]
    low, high = site_tool.DOWNLOAD_JITTER
    schedule = [
        site_tool.DOWNLOAD_BACKOFF_BASE * (2**n) for n in range(site_tool.DOWNLOAD_ATTEMPTS - 1)
    ]
    assert sum(schedule) * low >= 120
    assert sum(schedule) * high <= 180
    log = capsys.readouterr().out
    for attempt in range(1, len(failures) + 1):
        assert f"attempt {attempt}/{site_tool.DOWNLOAD_ATTEMPTS} failed" in log
        assert f"retrying in {float(sleeps[attempt - 1]):.1f}s" in log
    assert f"attempt {len(failures) + 1}/{site_tool.DOWNLOAD_ATTEMPTS} succeeded" in log
    assert "Egress is over the account limit" in log and BLOB_URL in log
    assert "connection error" in log and "429" in log
    report = restored / "unitree-g1/stock-mujoco-hallway/runs/100/report.md"
    assert report.read_text() == "report.md 12"


def test_restore_fails_when_download_503_persists(tmp_path, monkeypatch, capsys):
    outcomes = []
    for _attempt in range(site_tool.DOWNLOAD_ATTEMPTS):
        outcomes.extend(
            (
                redirect_to_blob(),
                http_error(BLOB_URL, 503, "Egress is over the account limit"),
            )
        )
    calls, sleeps, remaining = scripted_download(monkeypatch, outcomes)
    list_pages(monkeypatch, pages_artifact_listing())
    destination = tmp_path / "s"

    with pytest.raises(site_tool.SiteError, match="after 5 attempts") as caught:
        site_tool.restore(destination, REPO, "main", 200, "t", site_url=SITE_URL)

    message = str(caught.value)
    assert "Refusing to publish" in message
    assert "503" in message and "Egress is over the account limit" in message
    assert BLOB_URL in message
    assert isinstance(caught.value.__cause__, urllib.error.HTTPError)
    assert caught.value.__cause__.code == 503
    assert remaining == []
    assert sleeps == [site_tool.DOWNLOAD_BACKOFF_BASE * (2**n) for n in range(4)]
    assert calls == [(ARCHIVE_URL, "t"), (BLOB_URL, None)] * site_tool.DOWNLOAD_ATTEMPTS
    assert not any(destination.iterdir())
    log = capsys.readouterr().out
    assert f"attempt {site_tool.DOWNLOAD_ATTEMPTS}/{site_tool.DOWNLOAD_ATTEMPTS} failed" in log
    assert "succeeded" not in log
    assert "WARNING" not in log and "starting a new site" not in log and "Restored" not in log


@pytest.mark.parametrize(
    ("listing", "detail"),
    [
        ([], "none listed"),
        (http_error("https://api.example/artifacts", 404, "Not Found"), "HTTP 404"),
    ],
)
def test_restore_warns_and_continues_when_there_is_no_previous_artifact(
    tmp_path, monkeypatch, capsys, listing, detail
):
    """First publish: an empty list, or a 404 on the listing, continues with a warning."""
    calls, _sleeps, _remaining = scripted_download(monkeypatch, [])
    list_pages(monkeypatch, listing)
    probed = []

    def status(url):
        probed.append(url)
        return 404

    monkeypatch.setattr(site_tool, "_fetch_status", status)
    destination = tmp_path / "s"

    assert site_tool.restore(destination, REPO, "main", 1, "t", site_url=SITE_URL) is None

    assert calls == []
    assert probed == [SITE_URL + "site.json", SITE_URL + "index.html"]
    assert not any(destination.iterdir())
    log = capsys.readouterr().out
    assert "WARNING:" in log
    assert detail in log
    assert "without run history" in log
    assert "starting a new site" in log


def test_listing_404_refuses_when_the_published_site_has_history(tmp_path, monkeypatch, capsys):
    scripted_download(monkeypatch, [])
    list_pages(monkeypatch, http_error("https://api.example/artifacts", 404, "Not Found"))
    monkeypatch.setattr(site_tool, "_fetch_status", lambda url: 200)

    with pytest.raises(site_tool.SiteError, match="reset-history") as caught:
        site_tool.restore(tmp_path / "s", REPO, "main", 1, "t", site_url=SITE_URL)

    assert "HTTP 404" in str(caught.value)
    assert "Refusing to publish" in str(caught.value)
    log = capsys.readouterr().out
    assert "WARNING" not in log and "starting a new site" not in log


def test_restore_fails_on_a_corrupt_archive(tmp_path, monkeypatch, capsys):
    """A partial or corrupt body (not a zip) fails. It is not retried and not published."""
    partial = b"PK\x03\x04" + b"\x00" * 8
    calls, sleeps, remaining = scripted_download(monkeypatch, [redirect_to_blob(), partial])
    list_pages(monkeypatch, pages_artifact_listing())
    destination = tmp_path / "s"

    with pytest.raises(site_tool.SiteError, match="not a valid Pages archive") as caught:
        site_tool.restore(destination, REPO, "main", 200, "t", site_url=SITE_URL)

    message = str(caught.value)
    assert "BadZipFile" in message and "Refusing to publish" in message
    assert isinstance(caught.value.__cause__, zipfile.BadZipFile)
    assert sleeps == [] and remaining == []
    assert calls == [(ARCHIVE_URL, "t"), (BLOB_URL, None)]
    assert not any(destination.iterdir())
    log = capsys.readouterr().out
    assert "succeeded" in log
    assert "Restored" not in log and "starting a new site" not in log and "WARNING" not in log


def test_build_writes_the_history_marker(tmp_path):
    site = tmp_path / "site"
    site_tool.build(
        site, reports_for(tmp_path, "one", 12, "FAIL"), run("1", "2026-11-02T00:00:00Z")
    )
    site_tool.build(
        site, reports_for(tmp_path, "two", 19, "PASS"), run("2", "2026-11-03T00:00:00Z")
    )
    # One record per locked target per CI run.
    assert json.loads((site / "site.json").read_text()) == {
        "runs": 2 * len(site_tool.targets()),
        "updated": "2026-11-03T00:00:00Z",
    }


def test_workflow_checks_the_live_site_and_resets_only_on_request():
    on = WORKFLOW[True] if True in WORKFLOW else WORKFLOW["on"]
    reset = on["workflow_dispatch"]["inputs"]["reset-history"]
    assert reset["type"] == "boolean" and reset["default"] is False
    step = next(s for s in WORKFLOW["jobs"]["site"]["steps"] if "restore" in s.get("run", ""))
    assert step["env"]["SITE_URL"] == "https://docs.livingprooflabs.com/livingproof-verification/"
    assert "${{" not in step["run"]
    assert "--reset-history" in step["run"]


def test_reset_history_recovers_from_an_expired_artifact(tmp_path, monkeypatch):
    listing = {"artifacts": [artifact(7, "main", 100, "2026-08-01T00:00:00Z", expired=True)]}
    requested = []

    def fake(url, token):
        requested.append(url)
        return io.BytesIO(json.dumps(listing).encode())

    monkeypatch.setattr(site_tool, "_request", fake)
    with pytest.raises(site_tool.SiteError, match="expired"):
        site_tool.restore(tmp_path / "s", REPO, "main", 200, "t", site_url=SITE_URL)
    requested.clear()
    assert (
        site_tool.restore(
            tmp_path / "s", REPO, "main", 200, "t", site_url=SITE_URL, reset_history=True
        )
        is None
    )
    assert requested == [] and not any((tmp_path / "s").iterdir())


def test_restore_rejects_unsafe_archive_paths(tmp_path):
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        info = tarfile.TarInfo("../escape.html")
        tar.addfile(info, io.BytesIO(b""))
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("artifact.tar", tar_buffer.getvalue())
    with pytest.raises(site_tool.SiteError, match="Unsafe"):
        site_tool.extract_pages(zip_buffer.getvalue(), tmp_path)


def test_publishing_runs_only_on_push_to_main_with_scoped_permissions():
    jobs = WORKFLOW["jobs"]
    assert WORKFLOW["permissions"] == {"contents": "read"}
    verifications = {
        n
        for n, j in jobs.items()
        if any(f"/{name}@" in str(j.get("uses")) for name in ("walk-hallway.yml", "scenario.yml"))
    }
    assert len(verifications) == 10
    assert set(jobs["site"]["needs"]) == verifications | {"release"}  # ordering only, #131
    assert jobs["pages"]["if"] == (
        "github.ref == 'refs/heads/main' && (github.event_name == 'push' || "
        "(github.event_name == 'workflow_dispatch' && inputs.reset-history))"
    )
    assert str(jobs["site"]["if"]).startswith(
        "!cancelled() && github.ref == 'refs/heads/main' && (github.event_name == 'push' || "
        "(github.event_name == 'workflow_dispatch' && inputs.reset-history))"
    )  # test_bundle_release.py evaluates the rest
    assert jobs["pages"]["needs"] == "site"
    assert jobs["pages"]["permissions"] == {"pages": "write", "id-token": "write"}
    assert jobs["pages"]["environment"]["name"] == "github-pages"
    for name, job in jobs.items():
        if name != "pages":
            assert "pages" not in job.get("permissions", {})
            assert "id-token" not in job.get("permissions", {})
    assert jobs["site"]["permissions"] == {"contents": "read", "actions": "read"}


def test_publishing_actions_are_pinned_and_nothing_is_committed():
    uses = [
        s["uses"] for n in ("site", "pages") for s in WORKFLOW["jobs"][n]["steps"] if "uses" in s
    ]
    assert any(u.startswith("actions/upload-pages-artifact@") for u in uses)
    assert any(u.startswith("actions/deploy-pages@") for u in uses)
    assert all(re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}", u) for u in uses)
    scripts = " ".join(
        s.get("run", "") for n in ("site", "pages") for s in WORKFLOW["jobs"][n]["steps"]
    )
    assert "git " not in scripts


ART = "https://github.com/Living-Proof-Labs/livingproof-verification/actions/runs/7/artifacts"
PREFIX = "unitree-g1-stock-mujoco-hallway"


def evidence_for(tmp_path: Path, video: bytes = b"pass video") -> tuple[Path, list[dict]]:
    """Downloaded links, MCAP and one video artifact, plus the run's artifact metadata."""
    evidence = tmp_path / "evidence"
    links = {
        "run": "https://github.com/x",
        "links": [
            {"label": "MCAP, all 20 trials", "url": f"{ART}/101", "kind": "mcap"},
            {"label": "video", "url": f"{ART}/102", "kind": "video", "seed": 1, "status": "PASS"},
        ],
    }
    (evidence / f"{PREFIX}-links-stock").mkdir(parents=True)
    (evidence / f"{PREFIX}-links-stock" / "links.json").write_text(json.dumps(links))
    (evidence / f"{PREFIX}-mcap-stock").mkdir()
    (evidence / f"{PREFIX}-mcap-stock" / "trials.mcap").write_bytes(b"mcap bytes")
    (evidence / f"{PREFIX}-video-stock-seed-1-pass").mkdir()
    (evidence / f"{PREFIX}-video-stock-seed-1-pass" / "seed-01-pass.mp4").write_bytes(video)
    artifacts = [
        {
            "id": 101,
            "name": f"{PREFIX}-mcap-stock",
            "expires_at": "2026-12-02T08:54:02Z",
            "digest": "sha256:" + "1" * 64,
            "size_in_bytes": 10,
        },
        {
            "id": 102,
            "name": f"{PREFIX}-video-stock-seed-1-pass",
            "expires_at": "2026-12-02T08:54:03Z",
            "digest": "sha256:" + "2" * 64,
            "size_in_bytes": 10,
        },
    ]
    return evidence, artifacts


def test_linked_evidence_is_copied_with_its_identity(tmp_path):
    evidence, artifacts = evidence_for(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for(tmp_path, "r1", 12, "FAIL"),
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    record = json.loads((folder / "run.json").read_text())
    mcap, video = record["evidence"]
    assert mcap["preserved"] and mcap["artifact"]["id"] == "101"
    assert mcap["artifact"]["digest"] == "sha256:" + "1" * 64
    assert mcap["files"] == [
        {
            "path": f"evidence/{PREFIX}-mcap-stock/trials.mcap",
            "sha256": hashlib.sha256(b"mcap bytes").hexdigest(),
            "bytes": 10,
        }
    ]
    assert (video["seed"], video["status"]) == (1, "PASS")
    assert "index" not in video and "trial" not in video
    assert (folder / video["files"][0]["path"]).read_bytes() == b"pass video"
    page = (folder / "index.html").read_text()
    assert f'href="{ART}/101"' not in page
    assert "artifact" not in page.lower()
    assert not (site / "unitree-g1/stock-mujoco-hallway/latest" / "evidence").exists()
    local_links_resolve(site)


def mark_trial_model(reports: Path, result: dict) -> None:
    """Record one evidence 2.0.0 result on every report. The result id keeps its seed suffix."""
    for target in site_tool.targets():
        folder = reports / target["artifact"]
        report = json.loads((folder / "report.json").read_text())
        report["source_bundle"] = {
            "results": [{**result, "evaluator_results": [evaluator_result()]}]
        }
        (folder / "report.json").write_text(json.dumps(report))
        restamp(folder)


@pytest.mark.parametrize(
    ("report", "trial"),
    [
        ({"original_result": {"seed": 1, "status": "PASS"}}, False),
        ({"plan": {"trials": 20, "seeds": [1, 2]}}, False),
        ({"original_result": {"index": 1, "seed": 1, "status": "PASS"}}, True),
        ({"provenance": {"plan": {"trials": {"count": 3}}}}, True),
    ],
)
def test_trial_model_is_an_index_or_a_trials_object(report, trial):
    assert site_tool.trial_model(report) is trial


def test_a_2_0_0_trial_model_report_is_labelled_trial(tmp_path):
    """Evidence 2.0.0 shows trial N. The stored seed key and the result id stay."""
    evidence, artifacts = evidence_for(tmp_path)
    reports = reports_with_links(tmp_path, "r1")
    mark_trial_model(
        reports,
        {
            "id": "stock-seed-01",
            "original_result": {"status": "PASS", "index": 1, "seed": 1},
            "provenance": {"plan": {"trials": {"count": 3}, "command_speed": 0.5}},
        },
    )
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports,
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    record = json.loads((folder / "run.json").read_text())
    video = next(e for e in record["evidence"] if e.get("kind") == "video")
    assert (video["seed"], video["status"]) == (1, "PASS")
    assert "index" not in video and "trial" not in video
    stored = json.loads((folder / "report.json").read_text())
    assert stored["source_bundle"]["results"][0]["id"] == "stock-seed-01"
    page = (folder / "index.html").read_text()
    assert "trial 1 (PASS): video" in page
    assert "seed 1 (PASS)" not in page
    assert (
        f"<a href='evidence/{PREFIX}-video-stock-seed-1-pass/seed-01-pass.mp4'>"
        "trial 1 (PASS): video</a>" in page
    )
    assert "trial 4 (FAIL): video" in page and f'href="{ART}/103"' not in page
    assert "seed 4 (FAIL)" not in page
    assert "artifact" not in page.lower()
    assert not (folder / "report.html.orig").exists()
    latest = (site / "unitree-g1/stock-mujoco-hallway/latest/index.html").read_text()
    assert "url=../runs/7/" in latest and "trial 1" not in latest
    local_links_resolve(site)


def test_a_v3_3_report_that_already_says_trial_is_not_labelled_twice(tmp_path):
    """v3.3.0 writes trial N for evidence 2.0.0. Publishing must not write trial trial N."""
    evidence, artifacts = evidence_for(tmp_path)
    html_in = (
        "<html><body><ul>"
        f"<li><a href='{ART}/101'>MCAP, all 20 trials</a></li>"
        f"<li><a href='{ART}/102'>trial 1 (PASS): video</a></li>"
        f'<li><a href="{ART}/103">trial 4 (FAIL): video</a></li>'
        "</ul></body></html>"
    )
    reports = tmp_path / "r1"
    for target in site_tool.targets():
        write_reports(reports / target["artifact"], 12, "FAIL", html=html_in)
    mark_trial_model(
        reports,
        {
            "id": "stock-seed-01",
            "original_result": {"status": "PASS", "index": 1, "seed": 1},
            "provenance": {"plan": {"trials": {"count": 3}, "command_speed": 0.5}},
        },
    )
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports,
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    page = (folder / "index.html").read_text()
    assert "trial trial" not in page
    assert "seed 1 (PASS)" not in page and "seed 4 (FAIL)" not in page
    assert (
        f"<a href='evidence/{PREFIX}-video-stock-seed-1-pass/seed-01-pass.mp4'>"
        "trial 1 (PASS): video</a>" in page
    )
    assert page.count("trial 1 (PASS): video") == 1
    assert "trial 4 (FAIL): video" in page and "<a " not in page.split("trial 4")[1][:80]
    assert not (folder / "report.html.orig").exists()
    local_links_resolve(site)


def test_expired_actions_artifacts_are_labelled_not_linked(tmp_path):
    """A later publish drops copies past retention and does not link the old addresses."""
    evidence, artifacts = evidence_for(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for(tmp_path, "r1", 12, "FAIL"),
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    # Thirty-one days later the copies are past the retention setting.
    site_tool.build(site, reports_for(tmp_path, "r2", 15, "FAIL"), run("8", "2026-12-03T00:00:00Z"))
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    page = (folder / "index.html").read_text()
    assert ART not in page and "artifact" not in page.lower()
    assert f'href="evidence/{PREFIX}-mcap-stock/trials.mcap"' not in page
    assert not (folder / f"evidence/{PREFIX}-mcap-stock/trials.mcap").exists()
    assert (folder / "run.json").is_file() and (folder / "report.json").is_file()
    newer = site / "unitree-g1/stock-mujoco-hallway/runs/8"
    assert not (newer / "evidence").exists()
    local_links_resolve(site)


def test_evidence_not_downloaded_or_over_budget_is_listed_unpreserved(tmp_path, monkeypatch):
    evidence, artifacts = evidence_for(tmp_path, video=b"x" * 50)
    monkeypatch.setattr(site_tool, "MAX_RUN_EVIDENCE_BYTES", 20)
    del artifacts[0]  # metadata for the MCAP is missing, so it cannot be matched
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for(tmp_path, "r1", 12, "FAIL"),
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    mcap, video = json.loads((folder / "run.json").read_text())["evidence"]
    assert not mcap["preserved"] and mcap["reason"].startswith("not downloaded")
    assert not video["preserved"] and "budget" in video["reason"]
    assert not (folder / "evidence").exists()
    page = (folder / "index.html").read_text()
    assert f'href="{ART}/101"' not in page and "artifact" not in page.lower()


def test_publishing_downloads_the_linked_evidence():
    steps = WORKFLOW["jobs"]["site"]["steps"]
    patterns = [s["with"]["pattern"] for s in steps if "download-artifact" in s.get("uses", "")]
    assert patterns == ["*-capability-report-*", "*-links-*", "*-mcap-*", "*-video-*"]
    build_step = next(s for s in steps if "site.py build" in s.get("run", ""))
    assert "--evidence evidence" in build_step["run"]
    assert WORKFLOW["jobs"]["site"]["permissions"] == {"contents": "read", "actions": "read"}


def test_fetch_artifacts_pages_through_the_run(monkeypatch):
    calls = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def request(url, token):
        calls.append((url, token))
        page = int(url.rsplit("=", 1)[1])
        batch = [{"id": i} for i in range(100)] if page == 1 else [{"id": 100}]
        return Response(json.dumps({"artifacts": batch}).encode())

    monkeypatch.setattr(site_tool, "_request", request)
    found = site_tool.fetch_artifacts(REPO, "7-2", "t")
    assert len(found) == 101
    assert calls[0] == (
        f"https://api.github.com/repos/{REPO}/actions/runs/7/artifacts?per_page=100&page=1",
        "t",
    )


# The reporter's HTML: evidence anchors to Actions artifacts, and the lossless JSON in <pre>.
REPORTER_HTML = (
    "<html><body><ul>"
    f"<li><a href='{ART}/101'>MCAP, all 20 trials</a></li>"
    f"<li><a href='{ART}/102'>seed 1 (PASS): video</a></li>"
    f'<li><a href="{ART}/103">seed 4 (FAIL): video</a></li>'
    f"</ul><details><pre>{{&quot;url&quot;: &quot;{ART}/101&quot;}}</pre></details></body></html>"
)


def reports_with_links(tmp_path: Path, name: str) -> Path:
    reports = tmp_path / name
    for target in site_tool.targets():
        write_reports(reports / target["artifact"], 12, "FAIL", html=REPORTER_HTML)
    return reports


def artifact_anchors(text: str) -> list[tuple[str, str, str]]:
    """(href, anchor text, what follows) for every link to an Actions artifact."""
    return [
        (m.group(1), m.group(2), text[m.end() : m.end() + 120])
        for m in re.finditer(
            r"""<a href=["'](https://github\.com/[^"']+/actions/runs/[0-9]+/artifacts/[0-9]+)["']>"""
            r"(.*?)</a>",
            text,
        )
    ]


def publish_with_links(tmp_path: Path) -> Path:
    evidence, artifacts = evidence_for(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_with_links(tmp_path, "r1"),
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    return site


def test_published_report_links_evidence_to_the_site_copies(tmp_path):
    site = publish_with_links(tmp_path)
    source = tmp_path / "r1" / f"{PREFIX}-capability-report-stock"
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    html = (folder / "index.html").read_text()
    assert f"<a href='evidence/{PREFIX}-mcap-stock/trials.mcap'>MCAP, all 20 trials</a>" in html
    assert (
        f"<a href='evidence/{PREFIX}-video-stock-seed-1-pass/seed-01-pass.mp4'>"
        "seed 1 (PASS): video</a>" in html
    )
    assert f'href="{ART}/101"' not in html and f'href="{ART}/103"' not in html
    assert "seed 4 (FAIL): video" in html
    assert "artifact" not in html.lower() and "/actions/" not in html
    assert not (folder / "report.html.orig").exists()
    for name in ("report.json", "report.md", "report.txt"):
        assert (folder / name).read_bytes() == (source / name).read_bytes(), name
    sums = dict(
        reversed(line.split("  ")) for line in (folder / "SHA256SUMS").read_text().splitlines()
    )
    assert "report.html.orig" not in sums
    assert sums["report.html"] == hashlib.sha256((folder / "report.html").read_bytes()).hexdigest()
    latest = site / "unitree-g1/stock-mujoco-hallway/latest"
    assert not (latest / "report.html").exists()
    assert not (latest / "report.html.orig").exists()
    local_links_resolve(site)


def test_no_published_page_links_to_github_actions(tmp_path):
    """lpv #23 and #48: no artifact wording and no /actions/ href on any emitted page."""
    site = publish_with_links(tmp_path)
    pages = list(site.rglob("*.html"))
    assert any(p.name == "index.html" for p in pages)
    assert site_tool.public_page_problems(site) == []
    for page in pages:
        text = page.read_text()
        assert not artifact_anchors(text), page
        assert "artifact" not in text.lower()
    assert (site / "unitree-g1/stock-mujoco-hallway/runs/7/report.json").is_file()


def test_the_build_refuses_a_site_that_links_to_github_actions(tmp_path, monkeypatch):
    """The site-build check: a page that still links to Actions fails the build."""
    monkeypatch.setattr(
        site_tool,
        "_index_page_html",
        lambda history, now: f'<a href="https://github.com/{REPO}/actions/runs/7">CI run</a>',
    )
    site = tmp_path / "site"
    with pytest.raises(site_tool.SiteError, match=r"link to GitHub Actions \(#23\): .*index\.html"):
        site_tool.build(
            site, reports_for(tmp_path, "r1", 12, "FAIL"), run("7", "2026-11-02T00:00:00Z")
        )
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page.html").write_text("<p>CI run 101, commit abc, sha256 deadbeef</p>")
    assert site_tool.public_page_problems(pages) == []
    (pages / "page.html").write_text("<p>Actions artifact 101</p>")
    assert site_tool.public_page_problems(pages) == ["page.html mentions an artifact"]
    (pages / "page.html").write_text(f"<a href='{ART}/101'>x</a>")
    assert site_tool.actions_links(pages) == ["page.html"]


def test_published_report_drops_the_actions_link_once_expired(tmp_path):
    site = publish_with_links(tmp_path)
    site_tool.build(site, reports_for(tmp_path, "r2", 15, "FAIL"), run("8", "2026-12-03T00:00:00Z"))
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    html = (folder / "index.html").read_text()
    assert not artifact_anchors(html)
    assert f"<a href='evidence/{PREFIX}-mcap-stock/trials.mcap'>" not in html
    assert not (folder / f"evidence/{PREFIX}-mcap-stock/trials.mcap").exists()
    assert (folder / "report.json").is_file() and (folder / "run.json").is_file()
    assert not (folder / "report.html.orig").exists()
    for page in site.rglob("*.html"):
        assert not artifact_anchors(page.read_text()), page
        assert "artifact" not in page.read_text().lower()
    local_links_resolve(site)


def test_a_report_without_artifact_links_is_published_unchanged(tmp_path):
    site = tmp_path / "site"
    site_tool.build(site, reports_for(tmp_path, "r1", 12, "FAIL"), run("7", "2026-11-02T00:00:00Z"))
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    assert (folder / "report.html").read_text() == "report.html 12"
    assert not (folder / "report.html.orig").exists()
    assert "report.html.orig" not in (folder / "index.html").read_text()


def test_the_all_trials_mcap_link_opens_a_listing_of_every_trial(tmp_path):
    """'MCAP, all 20 trials' must not open only seed 1: it links a page listing all 20."""
    evidence, artifacts = evidence_for(tmp_path)
    mcap = evidence / f"{PREFIX}-mcap-stock"
    (mcap / "trials.mcap").unlink()
    for seed in range(1, 21):
        (mcap / "mcap").mkdir(exist_ok=True)
        (mcap / "mcap" / f"seed-{seed:02d}.mcap").write_bytes(f"seed {seed}".encode())
    (mcap / "media.json").write_text("{}")
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_with_links(tmp_path, "r1"),
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    report = (folder / "index.html").read_text()
    listing = f"evidence/{PREFIX}-mcap-stock.html"
    assert f"<a href='{listing}'>MCAP, all 20 trials</a>" in report
    assert "seed-01.mcap'>MCAP" not in report
    page = (folder / listing).read_text()
    for seed in range(1, 21):
        assert f'href="{PREFIX}-mcap-stock/mcap/seed-{seed:02d}.mcap"' in page, seed
    assert f'href="{PREFIX}-mcap-stock/media.json"' in page
    assert "artifact" not in page.lower()
    up = "../" * len(folder.relative_to(site).parts)
    assert f'<a href="../{up}index.html">← All verification reports</a>' in page
    assert f"<a href='evidence/{PREFIX}-video-stock-seed-1-pass/seed-01-pass.mp4'>" in report
    latest = site / "unitree-g1/stock-mujoco-hallway/latest"
    assert sorted(path.name for path in latest.rglob("*") if path.is_file()) == ["index.html"]
    local_links_resolve(site)


REAL_V303_HTML = Path(__file__).parent / "fixtures" / "reporter-v3.0.3-go1-report.html"


def test_a_real_reporter_v3_0_3_report_publishes_with_no_actions_links(tmp_path, monkeypatch):
    """PR #27 review: the reporter's Evidence "CI run" anchor must become text too."""
    real = REAL_V303_HTML.read_text()
    assert hashlib.sha256(real.encode()).hexdigest() == (
        "cd6149a0b882d5c6b0749c880b3745750a365b2aa86aaaae9c33df1b8982d88f"
    )
    assert real.count("/actions/runs/41703287431'>CI run</a>") == 1
    reports = tmp_path / "r"
    for target in site_tool.targets():
        write_reports(reports / target["artifact"], 15, "FAIL", html=real)
    site = tmp_path / "site"
    site_tool.build(site, reports, run("41703287431", "2026-11-03T19:40:00Z"))
    assert site_tool.public_page_problems(site) == []
    published = list(site.rglob("index.html"))
    assert published
    for page in site.rglob("*.html"):
        html = page.read_text()
        assert "/actions/" not in html
        assert "artifact" not in html.lower()
    assert list(site.rglob("report.html.orig")) == []
    # The guard stays strict: publishing the reporter file unchanged fails the build.
    monkeypatch.setattr(site_tool, "scrub_reporter_html", lambda text, record, **kwargs: text)
    with pytest.raises(site_tool.SiteError, match="index.html"):
        site_tool.build(tmp_path / "site2", reports, run("41703287431", "2026-11-03T19:40:00Z"))


def test_retention_lives_only_in_settings():
    """One setting, default 7. The site builder does not repeat that number."""
    assert site_tool.retention_days() == 7
    source = (ROOT / "tools/site.py").read_text()
    assert re.search(r"\b7\b", source) is None
    assert "Evidence kept for" not in source
    assigned = []
    for path in (ROOT / "verifications").glob("*.toml"):
        for line in path.read_text().splitlines():
            if "evidence_retention_days" in line and not line.strip().startswith("#"):
                assigned.append((path.name, line.strip()))
    assert assigned == [("settings.toml", "evidence_retention_days = 7")]


def test_evidence_links_are_only_relative_site_paths():
    """Vera's nit (a): an artifact URL or a plain actions/runs URL is never passed on."""
    run_url = "https://github.com/Living-Proof-Labs/livingproof-verification/actions/runs/99"
    artifact_url = run_url + "/artifacts/101"
    records = [
        {
            "label": "MCAP, all 20 trials",
            "kind": "mcap",
            "url": artifact_url,
            "preserved": True,
            "files": [{"path": f"evidence/{PREFIX}-mcap-stock/trials.mcap"}],
            "artifact": {"id": "101", "name": f"{PREFIX}-mcap-stock", "url": artifact_url},
        },
        {
            "label": "video",
            "kind": "video",
            "seed": 1,
            "status": "PASS",
            "url": run_url,
            "preserved": True,
            "files": [{"path": run_url}],
            "artifact": {"id": "102", "url": run_url},
        },
        {
            "label": "logs",
            "kind": "log",
            "url": artifact_url,
            "preserved": False,
            "files": [],
            "reason": "not downloaded when this run was published",
        },
    ]
    live = site_tool.evidence_links_document(records, expired=False)
    blob = json.dumps(live)
    assert "run" not in live
    assert "://" not in blob and "/actions/" not in blob and "artifact" not in blob.lower()
    assert live["links"] == [
        {
            "label": "MCAP, all 20 trials",
            "kind": "mcap",
            "path": f"evidence/{PREFIX}-mcap-stock/trials.mcap",
            "expired": False,
        }
    ]
    expired = site_tool.evidence_links_document(records[:1], expired=True)
    assert expired["links"] == [{"label": "MCAP, all 20 trials", "kind": "mcap", "expired": True}]
    assert "path" not in expired["links"][0] and "url" not in expired["links"][0]
    untouched = site_tool.evidence_links_document(records[2:], expired=False)
    assert untouched == {"links": []}


def _renderable_bundle(directory: Path) -> None:
    """A saved report the pinned reporter can render again. Observations stay beside it."""
    reporter = shutil.which("livingproof-capability-report")
    if reporter is None:
        raise AssertionError("livingproof-capability-report is not on PATH")
    observation = b'{"observed": true}\n'
    bundle = {
        "schema_version": "1.1.0",
        "producer": {"id": "capability-verifier", "version": "1.18.1", "commit": "c" * 40},
        "results": [
            {
                "id": "stock-seed-01",
                "spec": {
                    "repository": "Living-Proof-Labs/robot-capability-specs",
                    "tag": "v1.6.1",
                    "commit": "a" * 40,
                    "capability": "walk-hallway",
                    "revision": "1.0.0",
                },
                "system": {"id": "unitree-g1/stock", "version": "1.0.0"},
                "environment": {"id": "mujoco", "version": "1.0.0"},
                "evidence": [
                    {
                        "id": "obs",
                        "path": "evidence/obs.json",
                        "sha256": hashlib.sha256(observation).hexdigest(),
                    }
                ],
                "assessments": [
                    {
                        "id": "a1",
                        "evaluator": "hallway",
                        "kind": "deterministic",
                        "version": "1.0.0",
                        "status": "PASS",
                        "rationale": "walked",
                        "mandatory": True,
                        "evidence_ids": ["obs"],
                        "provenance": {},
                    }
                ],
                "provenance": {},
                "original_result": {"seed": 1, "status": "PASS"},
                # A published scenario evaluator result (G1 playground, run 43830552305).
                "evaluator_results": [
                    json.loads(
                        (
                            Path(__file__).parent / "fixtures/evaluator-result-scenario.json"
                        ).read_text()
                    )
                ],
                "decision": {
                    "status": "PASS",
                    "reason": "walked",
                    "policy": {"id": "walk", "version": "1.0.0"},
                    "failures": [],
                    "assessor_types": ["deterministic"],
                },
            }
        ],
        "assessment": {
            "format": "livingproof-run-assessment/0.1",
            "policy": {"id": "walk", "version": "1.0.0"},
            "aggregation": "all",
            "decisions": [{"result_id": "stock-seed-01", "status": "PASS", "reason": "walked"}],
            "runs": [
                {
                    "capability": "walk-hallway",
                    "spec": "walk-hallway@1.0.0",
                    "system": {"id": "unitree-g1/stock"},
                    "environment": {"id": "mujoco"},
                    "run_id": "run-1",
                    "k": 1,
                    "n": 1,
                    "required_passes": 1,
                    "grade": "PASS",
                    "result_ids": ["stock-seed-01"],
                    "failures": [],
                    "prototype": False,
                    "interval": {"method": "wilson", "level": 0.95, "lower": 0.2, "upper": 1.0},
                }
            ],
        },
    }
    root = directory.parent / "bundle"
    (root / "evidence").mkdir(parents=True, exist_ok=True)
    (root / "evidence/obs.json").write_bytes(observation)
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(bundle))
    directory.mkdir(parents=True, exist_ok=True)
    report_json = directory / "report.json"
    subprocess.run(
        [reporter, "render", str(manifest), "--format", "json", "-o", str(report_json)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            reporter,
            "render",
            str(report_json),
            "--format",
            "html",
            "-o",
            str(directory / "report.html"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    # Markdown and the terminal text are part of the checksummed set. The public
    # page is the HTML render; these two stay byte-for-byte with SHA256SUMS.
    (directory / "report.md").write_text("report.md\n")
    (directory / "report.txt").write_text("report.txt\n")
    # The embedded scenario evaluator result cites thresholds.json; ship the cited bytes.
    shutil.copyfile(THRESHOLDS_FIXTURE, directory / "thresholds.json")
    restamp(directory)


def _renderable_reports(tmp_path: Path) -> Path:
    reports = tmp_path / "renderable"
    sample = tmp_path / "sample-report"
    _renderable_bundle(sample)
    for target in site_tool.targets():
        destination = reports / target["artifact"]
        destination.mkdir(parents=True)
        for name in (*site_tool.REPORT_FILES, *GRADING_INPUTS, "SHA256SUMS"):
            shutil.copyfile(sample / name, destination / name)
    return reports


def _page(site: Path) -> str:
    return (site / "unitree-g1/stock-mujoco-hallway/runs/7/index.html").read_text()


def test_retention_changes_what_is_pruned_and_the_kept_for_text(tmp_path, monkeypatch):
    """The same setting prunes copies and is what the reporter prints."""
    evidence, artifacts = evidence_for(tmp_path)
    reports = _renderable_reports(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports,
        run("7", "2026-11-01T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    video = f"evidence/{PREFIX}-video-stock-seed-1-pass/seed-01-pass.mp4"
    mcap = f"evidence/{PREFIX}-mcap-stock/trials.mcap"
    assert (folder / video).is_file() and (folder / mcap).is_file()
    assert "Evidence kept for 7 days" in _page(site)
    assert f"src='{video}'" in _page(site)
    sums = dict(
        reversed(line.split("  ")) for line in (folder / "SHA256SUMS").read_text().splitlines()
    )
    for name in site_tool.REPORT_FILES:
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == sums[name]
    latest = site / "unitree-g1/stock-mujoco-hallway/latest"
    assert sorted(path.name for path in latest.rglob("*") if path.is_file()) == ["index.html"]
    assert "url=../runs/7/" in (latest / "index.html").read_text()
    # A run that never received copies is not backfilled.
    others = [path for path in (site).glob("*/*/runs/7") if path != folder]
    assert others and all(not (path / "evidence").exists() for path in others)
    assert site_tool.public_page_problems(site) == []

    # Four days later, 7 still keeps the copies and says 7.
    site_tool.render(site, now="2026-11-05T00:00:00Z")
    assert (folder / video).is_file()
    assert "Evidence kept for 7 days" in _page(site) and "Evidence kept for 3 days" not in _page(
        site
    )

    other = tmp_path / "settings.toml"
    other.write_text(
        (ROOT / "verifications/settings.toml")
        .read_text()
        .replace("evidence_retention_days = 7", "evidence_retention_days = 3")
    )
    monkeypatch.setattr(site_tool, "SETTINGS", other)
    assert site_tool.retention_days() == 3
    site_tool.render(site, now="2026-11-05T00:00:00Z")
    assert not (folder / video).exists() and not (folder / mcap).exists()
    assert (folder / "run.json").is_file()
    assert (folder / "report.json").is_file() and (folder / "index.html").is_file()
    assert (folder / "report.html").is_file()
    page = _page(site)
    # Reporter v3.11.1 (livingproof-capability-report#77) says so once the copies are gone.
    assert "Evidence expired after 3 days" in page and "Evidence kept for" not in page
    assert f"src='{video}'" not in page and f"href='{video}'" not in page
    assert "artifact" not in page.lower() and "/actions/" not in page
    record = json.loads((folder / "run.json").read_text())
    assert record["evidence"][0]["artifact"]["id"] == "101"
    local_links_resolve(site)


def test_a_pruned_run_page_says_its_evidence_expired(tmp_path):
    """Issue 54: once retention prunes a run's copies, its page says so, not "kept for".

    Vera found 40 pruned run pages still reading "Evidence kept for 7 days" (class
    is-kept) with no file behind them. The number is the same retention setting.
    """
    evidence, artifacts = evidence_for(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        _renderable_reports(tmp_path),
        run("7", "2026-11-01T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    assert "Evidence kept for 7 days" in _page(site)
    assert "Evidence expired" not in _page(site)

    # Eight days on, the default 7 has pruned the copies.
    assert site_tool.retention_days() == 7
    site_tool.render(site, now="2026-11-09T00:00:00Z")
    video = f"evidence/{PREFIX}-video-stock-seed-1-pass/seed-01-pass.mp4"
    mcap = f"evidence/{PREFIX}-mcap-stock/trials.mcap"
    assert not (folder / video).exists() and not (folder / mcap).exists()
    page = _page(site)
    assert "Evidence expired after 7 days" in page
    # Each Evidence item is an is-expired entry that carries the wording.
    expired = re.findall(r"<li class='[^']*\bis-expired\b[^']*'>(?:(?!</li>).)*</li>", page)
    assert len(expired) == 2
    assert all("Evidence expired after 7 days" in item for item in expired)
    assert "Evidence kept for" not in page
    # The stylesheet still defines li.is-kept; no element on this page carries it.
    assert not re.search(r"class='[^']*\bis-kept\b", page)
    assert site_tool.public_page_problems(site) == []
    local_links_resolve(site)


RUN_URL = "https://github.com/Living-Proof-Labs/livingproof-verification/actions/runs/7"


def stamp_actions_urls(reports: Path) -> None:
    """Put Actions run and artifact URLs into the reporter files, then restamp the incoming sums."""
    for folder in reports.iterdir():
        if not (folder / "report.json").is_file():
            continue
        report = json.loads((folder / "report.json").read_text())
        report["evidence_links"] = {
            "run": RUN_URL,
            "links": [
                {"label": "MCAP", "kind": "mcap", "url": f"{ART}/101"},
                {"label": "clip", "kind": "note", "url": "https://example.com/clip"},
            ],
        }
        (folder / "report.json").write_text(json.dumps(report))
        (folder / "report.md").write_text(f"Evidence: {RUN_URL} and {ART}/101\n")
        (folder / "report.txt").write_text("report.txt\n")
        restamp(folder)


def published_checksums(folder: Path) -> dict[str, str]:
    return dict(
        reversed(line.split("  ")) for line in (folder / "SHA256SUMS").read_text().splitlines()
    )


def test_published_files_except_run_json_have_no_actions_urls(tmp_path):
    """Every published file other than run.json is free of artifact wording and /actions/ URLs."""
    reports = reports_with_links(tmp_path, "r1")
    stamp_actions_urls(reports)
    evidence, artifacts = evidence_for(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports,
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    assert site_tool.public_page_problems(site) == []
    scanned = []
    for path in site.rglob("*"):
        if not path.is_file() or path.name == "run.json":
            continue
        text = path.read_text(errors="replace")
        scanned.append(path)
        assert "artifact" not in text.lower(), path
        assert "/actions/" not in text.lower(), path
    assert scanned


def test_published_report_json_drops_actions_urls_and_run_json_keeps_them(tmp_path):
    reports = reports_with_links(tmp_path, "r1")
    stamp_actions_urls(reports)
    evidence, artifacts = evidence_for(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports,
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    published = json.loads((folder / "report.json").read_text())
    blob = json.dumps(published)
    assert "/actions/" not in blob.lower()
    assert "artifact" not in blob.lower()
    links = published["evidence_links"]
    assert "run" not in links
    assert all(entry.get("url") != f"{ART}/101" for entry in links["links"])
    assert any(entry.get("url") == "https://example.com/clip" for entry in links["links"])
    record = json.loads((folder / "run.json").read_text())
    assert record["run"]["url"] == (
        "https://github.com/Living-Proof-Labs/livingproof-verification/actions/runs/7"
    )
    assert record["evidence"][0]["artifact"]["url"].endswith("/artifacts/101")
    assert "artifact" in json.dumps(record).lower()


def test_report_html_orig_is_not_published(tmp_path):
    evidence, artifacts = evidence_for(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_with_links(tmp_path, "r1"),
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    assert list(site.rglob("report.html.orig")) == []
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    # A restored copy is removed on the next render and is not checksummed.
    (folder / "report.html.orig").write_text(REPORTER_HTML)
    site_tool.render(site, now="2026-11-02T00:00:00Z")
    assert not (folder / "report.html.orig").exists()
    assert "report.html.orig" not in (folder / "SHA256SUMS").read_text()
    assert list(site.rglob("*.orig")) == []


def test_sha256sums_verifies_exactly_the_published_files(tmp_path):
    evidence, artifacts = evidence_for(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_with_links(tmp_path, "r1"),
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    folders = list(site.glob("*/*/runs/*"))
    assert folders
    for folder in folders:
        sums = published_checksums(folder)
        files = {
            path.relative_to(folder).as_posix()
            for path in folder.rglob("*")
            if path.is_file() and path.name != "SHA256SUMS"
        }
        assert set(sums) == files
        assert "report.html.orig" not in sums
        for name, digest in sums.items():
            assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest


def test_build_fails_when_a_published_report_keeps_an_actions_url(tmp_path, monkeypatch):
    """The lint covers json and markdown, not only html, and build stops on it."""
    reports = reports_for(tmp_path, "r1", 12, "FAIL")
    stamp_actions_urls(reports)
    monkeypatch.setattr(site_tool, "strip_actions_links", lambda report: False)
    monkeypatch.setattr(site_tool, "strip_actions_text", lambda text: text)
    with pytest.raises(site_tool.SiteError, match=r"report\.(json|md)"):
        site_tool.build(
            site=tmp_path / "site", reports=reports, run=run("7", "2026-11-02T00:00:00Z")
        )


def test_sha256sum_c_accepts_the_published_run_folder(tmp_path):
    """Hand check: from the run folder, ``sha256sum -c SHA256SUMS`` exits 0."""
    evidence, artifacts = evidence_for(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_with_links(tmp_path, "r1"),
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    folders = list(site.glob("*/*/runs/*"))
    assert folders
    for folder in folders:
        completed = subprocess.run(
            ["sha256sum", "-c", "SHA256SUMS"],
            cwd=folder,
            check=True,
            capture_output=True,
            text=True,
        )
        assert "FAILED" not in completed.stdout
        assert "report.html.orig" not in completed.stdout
        assert "report.html.orig" not in (folder / "SHA256SUMS").read_text()


def test_checksums_cover_the_scrubbed_report_json_and_the_bundle_digest_stays(tmp_path):
    """SHA256SUMS hashes the scrubbed report.json, not the incoming reporter file.

    ``source_bundle_sha256`` still matches the bundle, which this publish does not change.
    """
    from livingproof_capability_report.core import digest as bundle_digest

    reports = _renderable_reports(tmp_path)
    stamp_actions_urls(reports)
    incoming = {}
    for path in reports.glob("*/report.json"):
        payload = json.loads(path.read_text())
        incoming[path.parent.name] = {
            "hash": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bundle_sha": payload["source_bundle_sha256"],
        }
        assert "/actions/" in path.read_text().lower()
    site = tmp_path / "site"
    site_tool.build(site, reports, run("7", "2026-11-02T00:00:00Z"))
    for target in site_tool.targets():
        folder = site / target["robot"] / target["variant"] / "runs" / "7"
        published = folder / "report.json"
        report = json.loads(published.read_text())
        sums = published_checksums(folder)
        published_hash = hashlib.sha256(published.read_bytes()).hexdigest()
        original = incoming[target["artifact"]]
        assert sums["report.json"] == published_hash
        assert published_hash != original["hash"]
        assert "/actions/" not in published.read_text().lower()
        assert "artifact" not in published.read_text().lower()
        assert report["source_bundle_sha256"] == original["bundle_sha"]
        assert bundle_digest(report["source_bundle"]) == report["source_bundle_sha256"]


def _rerender(report_json: Path, tmp_path: Path, retention: int) -> subprocess.CompletedProcess:
    """The pinned reporter renders the published report.json on its own."""
    return subprocess.run(
        [
            site_tool.reporter_bin(),
            "render",
            str(report_json),
            "--format",
            "html",
            "-o",
            str(tmp_path / f"rerendered-{len(list(tmp_path.glob('rerendered-*')))}.html"),
            "--evidence-retention",
            str(retention),
        ],
        capture_output=True,
        text=True,
    )


def _published_links(folder: Path) -> list[dict]:
    return json.loads((folder / "report.json").read_text())["evidence_links"]["links"]


def test_published_report_json_links_point_at_site_copies_or_are_expired(tmp_path):
    """No evidence_links entry is left with neither a url nor a path (#54)."""
    reports = _renderable_reports(tmp_path)
    stamp_actions_urls(reports)
    evidence, artifacts = evidence_for(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports,
        run("7", "2026-11-01T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    record = json.loads((folder / "run.json").read_text())
    page_links = site_tool.evidence_links_document(record["evidence"], expired=False)["links"]
    links = _published_links(folder)
    assert "run" not in json.loads((folder / "report.json").read_text())["evidence_links"]
    copied = [entry for entry in links if "path" in entry]
    assert copied == page_links and copied
    for entry in links:
        assert entry.get("path") or entry.get("expired") is True or entry.get("url"), entry
        if entry.get("path"):
            assert (folder / entry["path"]).is_file(), entry
        if entry.get("url"):
            assert "/actions/" not in entry["url"] and "artifact" not in entry["url"].lower()
    assert [entry["url"] for entry in links if "url" in entry] == ["https://example.com/clip"]
    assert _rerender(folder / "report.json", tmp_path, 7).returncode == 0
    sums = published_checksums(folder)
    assert hashlib.sha256((folder / "report.json").read_bytes()).hexdigest() == sums["report.json"]

    # Pruned: the same entries, now expired with no path, exactly as the page has them.
    site_tool.render(site, now="2026-12-01T00:00:00Z")
    links = _published_links(folder)
    expired = site_tool.evidence_links_document(record["evidence"], expired=True)["links"]
    assert [entry for entry in links if "url" not in entry] == expired and expired
    assert all(entry["expired"] is True and "path" not in entry for entry in expired)
    rendered = _rerender(folder / "report.json", tmp_path, 7)
    assert rendered.returncode == 0, rendered.stderr
    sums = published_checksums(folder)
    assert hashlib.sha256((folder / "report.json").read_bytes()).hexdigest() == sums["report.json"]


def test_an_already_stripped_report_json_is_repaired_on_the_next_publish(tmp_path):
    """Older publishes left label-and-kind entries; rendering the site again fixes them."""
    reports = _renderable_reports(tmp_path)
    evidence, artifacts = evidence_for(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports,
        run("7", "2026-11-01T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    folder = site / "unitree-g1/stock-mujoco-hallway/runs/7"
    report = json.loads((folder / "report.json").read_text())
    report["evidence_links"] = {"links": [{"label": "MCAP", "kind": "mcap"}]}
    (folder / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    assert _rerender(folder / "report.json", tmp_path, 7).returncode != 0
    site_tool.render(site, now="2026-11-02T00:00:00Z")
    links = _published_links(folder)
    assert links and all(entry.get("path") for entry in links)
    assert _rerender(folder / "report.json", tmp_path, 7).returncode == 0


def test_renderable_reports_carry_the_thresholds_their_evaluator_results_cite(tmp_path):
    """The test artifact is complete: each cited thresholds.json is there with its digest (#65)."""
    reports = _renderable_reports(tmp_path)
    for target in site_tool.targets():
        folder = reports / target["artifact"]
        cited = {
            item["sha256"]
            for result in json.loads((folder / "report.json").read_text())["source_bundle"][
                "results"
            ]
            for evaluated in result.get("evaluator_results", [])
            for item in evaluated.get("inputs", [])
            if item.get("role") == "thresholds"
        }
        assert cited, "the renderable report should cite a thresholds input"
        digest = hashlib.sha256((folder / "thresholds.json").read_bytes()).hexdigest()
        assert cited == {digest}
        assert f"{digest}  thresholds.json" in (folder / "SHA256SUMS").read_text()


def test_left_right_adoption_keeps_retired_corner_evidence(tmp_path):
    """Retiring a definition never renames or deletes its published run."""
    historical = ROOT / "tests/fixtures/historical-corner"
    site = tmp_path / "site"
    reports = tmp_path / "historical-reports"
    for target in site_tool.targets(historical):
        write_reports(reports / target["artifact"], 1, "PASS")
    site_tool.build(site, reports, run("1000", "2026-11-17T06:00:00Z"), historical)
    immutable = {
        path: path.read_bytes()
        for path in site.rglob("*")
        if path.is_file() and "runs/1000/" in path.as_posix()
    }
    assert immutable
    site_tool.build(
        site,
        reports_for(tmp_path, "current-reports", 1, "PASS"),
        run("2000", "2026-11-18T06:00:00Z"),
    )
    for path, original in immutable.items():
        assert path.read_bytes() == original
    for robot, variant in (
        ("unitree-g1", "stock"),
        ("booster-t1", "stock"),
        ("unitree-go1", "playground"),
    ):
        assert (site / robot / f"{variant}-mujoco-corner/runs/1000/report.json").exists()
    local_links_resolve(site)


def test_failing_evaluators_chips_and_clean_history_view(tmp_path):
    """Issue #129: failing evaluators speak via chips and one-line finding; passing rows stay quiet."""
    site = tmp_path / "site"
    failing_bundle = {
        "results": [
            {
                "id": "trial-01",
                "evaluator_results": [
                    {
                        "format": "livingproof-evaluator-result/1.1.0",
                        "evaluator": {"id": "livingproof-scenario", "display_name": "Scenario"},
                        "descriptor": {
                            "title": "Scenario",
                            "purpose": "Checks scenario steps.",
                            "findings": [
                                {
                                    "text": "Drifted 0.66 m from the centreline, then touched the wall."
                                }
                            ],
                        },
                    }
                ],
            },
            {
                "id": "trial-02",
                "evaluator_results": [
                    {
                        "format": "livingproof-evaluator-result/1.1.0",
                        "evaluator": {"id": "livingproof-scenario", "display_name": "Scenario"},
                        "descriptor": {
                            "title": "Scenario",
                            "purpose": "Checks scenario steps.",
                            "findings": [{"text": "Second trial finding."}],
                        },
                    }
                ],
            },
        ]
    }
    reports = tmp_path / "fail-reports"
    for target in site_tool.targets():
        folder = reports / target["artifact"]
        folder.mkdir(parents=True)
        (folder / "report.json").write_text(
            json.dumps(
                {
                    "report_version": "1.0.0",
                    "source_bundle": failing_bundle,
                    "summaries": [
                        {
                            "capability": "walk-hallway",
                            "system": {"id": f"{target['robot']}/{target['configuration']}"},
                            "grade": "FAIL",
                            "k": 0,
                            "n": 2,
                            "required_passes": 2,
                        }
                    ],
                }
            )
        )
        for name in ("report.html", "report.md", "report.txt"):
            (folder / name).write_text(f"{name} fail\n")
        restamp(folder)
    site_tool.build(site, reports, run("100", "2026-11-18T05:00:00Z"))
    # Second run passes with a bundle tag; passing row must stay quiet even if bundle has findings.
    pass_run = {**run("200", "2026-11-18T05:20:00Z"), "tag": "v1.0.0"}
    site_tool.build(site, reports_for(tmp_path, "pass-reports", 3, "PASS"), pass_run)

    base = site / "booster-t1/stock-mujoco-hallway"
    fail_record = json.loads((base / "runs/100/run.json").read_text())
    assert fail_record["failing_evaluators"] == [
        {
            "id": "livingproof-scenario",
            "title": "Scenario",
            "href": "#run-evaluators-tab-1",
            "summary": "Drifted 0.66 m from the centreline, then touched the wall.",
        }
    ]
    assert fail_record["poster"] == {"type": "ci", "name": "CI"}
    assert fail_record["robot"]["tag"] == "v2.3.5"
    assert fail_record["robot"]["software_version"] == "1.0.0"

    pass_record = json.loads((base / "runs/200/run.json").read_text())
    assert "failing_evaluators" not in pass_record
    assert pass_record["commit"]["tag"] == "v1.0.0"

    history = (base / "index.html").read_text()
    assert "Booster T1 · stock · MuJoCo" in history
    assert "Walk the hallway" in history
    assert "livingproof-booster-t1 v2.3.5" in history
    assert "livingproof-booster-t1 @" not in history
    assert "software stock 1.0.0" in history
    assert 'href="runs/100/#run-evaluators-tab-1"' in history
    assert ">Scenario</a>" in history
    assert 'aria-label="Open the Scenario finding in the report"' in history
    assert "Drifted 0.66 m from the centreline, then touched the wall." in history
    assert ">commit</a>" in history
    assert 'class="release is-fallback"' in history
    assert ">v1.0.0</a>" in history
    assert '<time datetime="2026-11-18T05:00:00Z"' in history
    assert 'title="18 Nov 2026, 16:00 AEDT"' in history
    assert "20 minutes ago</time>" in history
    assert "newest first. Older runs are trimmed over time.</p>" in history
    assert "pagination" not in history
    assert "holistically" not in history
    assert "SHA256SUMS" not in history
    assert "CI run" not in history
    assert "/actions/" not in history

    index = (site / "index.html").read_text()
    assert "All 10 latest results pass." in index
    assert "History (2)" in index
    assert "SHA256SUMS" not in index
    assert "/actions/" not in index
    assert (site / "styles.css").is_file()
    local_links_resolve(site)


def test_old_run_json_records_fall_back_without_new_fields(tmp_path):
    """Pre-#129 run.json: no tag shows the short SHA, no poster is CI, no chips."""
    site = tmp_path / "site"
    site_tool.build(
        site, reports_for(tmp_path, "old", 0, "FAIL"), run("100", "2026-11-18T05:00:00Z")
    )
    base = site / "booster-t1/stock-mujoco-hallway"
    path = base / "runs/100/run.json"
    record = json.loads(path.read_text())
    record["commit"].pop("tag", None)
    record.pop("poster", None)
    record.pop("failing_evaluators", None)
    path.write_text(json.dumps(record, indent=2) + "\n")
    site_tool.render(site, now="2026-11-18T05:20:00Z")
    history = (base / "index.html").read_text()
    assert 'class="release is-fallback"' in history
    assert ">commit</a>" in history
    assert record["commit"]["sha"][:8] not in visible_text(history)
    assert 'class="poster">CI</span>' in history
    assert 'class="chip"' not in history and "class='chip'" not in history
    assert "SHA256SUMS" not in history
    local_links_resolve(site)


def test_time_tooltip_uses_australia_sydney(tmp_path):
    """#129: the <time> tooltip is Australia/Sydney local time, not UTC."""
    site = tmp_path / "site"
    site_tool.build(
        site, reports_for(tmp_path, "one", 19, "PASS"), run("100", "2026-11-18T05:00:00Z")
    )
    history = (site / "booster-t1/stock-mujoco-hallway/index.html").read_text()
    assert '<time datetime="2026-11-18T05:00:00Z"' in history
    assert 'title="18 Nov 2026, 16:00 AEDT"' in history
    assert "UTC" not in history
    path = site / "booster-t1/stock-mujoco-hallway/runs/100/run.json"
    record = json.loads(path.read_text())
    record["run"]["created"] = "2026-06-18T05:00:00Z"
    path.write_text(json.dumps(record, indent=2) + "\n")
    site_tool.render(site, now="2026-06-18T05:20:00Z")
    winter = (site / "booster-t1/stock-mujoco-hallway/index.html").read_text()
    assert '<time datetime="2026-06-18T05:00:00Z"' in winter
    assert 'title="18 Jun 2026, 15:00 AEST"' in winter


def test_human_reviewer_chip_renders_when_a_finding_was_stored(tmp_path):
    """#129: each human reviewer who reports a problem is their own chip."""
    site = tmp_path / "site"
    poster = {"type": "human", "name": "Aaron Roller"}
    site_tool.build(
        site,
        reports_for(tmp_path, "fail", 0, "FAIL"),
        {**run("150", "2026-11-18T05:10:00Z"), "poster": poster},
    )
    base = site / "booster-t1/stock-mujoco-hallway"
    path = base / "runs/150/run.json"
    record = json.loads(path.read_text())
    assert record["poster"] == poster
    reviewers = list(record.get("failing_evaluators") or [])
    reviewers.append(
        {
            "id": "aaron-roller",
            "title": "Aaron Roller",
            "href": "#evaluator-aaron-roller",
            "summary": "The robot stepped through the wall.",
        }
    )
    record["failing_evaluators"] = reviewers
    path.write_text(json.dumps(record, indent=2) + "\n")
    site_tool.render(site, now="2026-11-18T05:20:00Z")
    history = (base / "index.html").read_text()
    assert 'aria-label="Open the Aaron Roller finding in the report"' in history
    assert 'href="runs/150/#run-evaluators-tab-1"' in history
    assert ">Aaron Roller</a>" in history
    assert 'class="poster">Aaron Roller</span>' in history
    local_links_resolve(site)


def test_history_styles_match_the_requested_rhythm():
    """Art's #135 review: 16px row padding, 8px gaps, mono SHA, stronger group headings."""
    css = site_tool.SITE_STYLES
    assert "padding: 1rem;" in css
    assert "gap: 0.5rem;" in css
    assert ".finding {\n  margin: 0.5rem 0 0;" in css
    assert ".row-meta" in css and "margin-top: 0.5rem;" in css
    assert ".release.is-fallback" in css
    fallback = css.split(".release.is-fallback {", 1)[1].split("}", 1)[0]
    assert "monospace" not in fallback  # no hash is shown, so nothing is set in mono (#146)
    assert "code.sha" not in css
    assert ".needs h2, .robots-label" in css
    assert "font-size: 0.88rem;" in css
    assert "font-weight: 700;" in css
    assert ".robots-label" in css


def _parse_index_cards(index_html: str) -> list[dict]:
    from html.parser import HTMLParser

    class CardParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.cards: list[dict] = []
            self.current: dict | None = None
            self.anchor_depth = 0
            self.current_anchor: dict | None = None
            self.in_title = False

        def handle_starttag(self, tag, attrs):
            attr_map = dict(attrs)
            classes = (attr_map.get("class") or "").split()
            if tag == "article" and "card" in classes:
                self.current = {"anchors": [], "chips": [], "releases": [], "has_nested_a": False}
                self.cards.append(self.current)
                return
            if self.current is None:
                return
            if tag == "h3" and "title" in classes:
                self.in_title = True
            elif tag == "a":
                self.anchor_depth += 1
                if self.anchor_depth > 1:
                    self.current["has_nested_a"] = True
                self.current_anchor = {
                    "href": attr_map.get("href"),
                    "class": classes,
                    "aria-label": attr_map.get("aria-label"),
                    "in_title": self.in_title,
                    "text": "",
                }
                self.current["anchors"].append(self.current_anchor)
            elif "chip" in classes:
                self.current["chips"].append(tag)
            elif "release" in classes:
                self.current["releases"].append(tag)

        def handle_endtag(self, tag):
            if self.current is None:
                return
            if tag == "article":
                self.current = None
                self.in_title = False
            elif tag == "h3":
                self.in_title = False
            elif tag == "a":
                self.anchor_depth = max(0, self.anchor_depth - 1)
                self.current_anchor = None

        def handle_data(self, data):
            if self.current_anchor is not None:
                self.current_anchor["text"] += data

    parser = CardParser()
    parser.feed(index_html)
    return parser.cards


def test_index_cards_use_stretched_latest_link_and_keep_history_only_explicit_link(tmp_path):
    """Issue #138: whole card opens latest report; History is the only explicit link."""
    site = tmp_path / "site"
    site_tool.build(
        site, reports_for(tmp_path, "r1", 0, "FAIL"), run("100", "2026-11-18T05:00:00Z")
    )
    # Give one FAIL record a failing evaluator chip and keep another target PASS.
    fail_run = site / "unitree-g1/stock-mujoco-hallway/runs/100/run.json"
    record = json.loads(fail_run.read_text())
    record["failing_evaluators"] = [
        {
            "id": "livingproof-scenario",
            "title": "Scenario",
            "href": "#evaluator-livingproof-scenario",
            "summary": "Stopped short of the goal.",
        }
    ]
    fail_run.write_text(json.dumps(record, indent=2) + "\n")

    site_tool.build(
        site,
        reports_for(tmp_path, "r2", 19, "PASS"),
        {**run("200", "2026-11-18T05:20:00Z"), "tag": "v1.0.0"},
    )
    # Force one card back to FAIL with a chip and one card to UNSUPPORTED / INSUFFICIENT_EVIDENCE.
    pass_hallway = site / "booster-t1/stock-mujoco-hallway/runs/200/run.json"
    r_fail = json.loads(fail_run.read_text())
    r_fail["run"]["created"] = "2026-11-18T05:30:00Z"
    fail_run.write_text(json.dumps(r_fail, indent=2) + "\n")

    unsupported_run = site / "unitree-g1/stock-mujoco-corner-left/runs/200/run.json"
    r_unsup = json.loads(unsupported_run.read_text())
    r_unsup["outcome"][0]["grade"] = "UNSUPPORTED"
    unsupported_run.write_text(json.dumps(r_unsup, indent=2) + "\n")

    insuff_run = site / "unitree-g1/stock-mujoco-corner-right/runs/200/run.json"
    r_insuff = json.loads(insuff_run.read_text())
    r_insuff["outcome"][0]["grade"] = "INSUFFICIENT_EVIDENCE"
    insuff_run.write_text(json.dumps(r_insuff, indent=2) + "\n")

    assert pass_hallway.is_file()
    site_tool.render(site, now="2026-11-18T05:35:00Z")

    index = (site / "index.html").read_text()
    assert "Latest report" not in index
    assert '<script src="open.js"></script>' in index
    assert (site / "open.js").is_file()
    assert "onclick=" not in index.lower()

    needs_part, robots_part = index.split('<h2 class="robots-label"', 1)
    needs_cards = _parse_index_cards(needs_part)
    robot_cards = _parse_index_cards(robots_part)
    assert len(needs_cards) == 1
    assert len(robot_cards) == len(site_tool.targets())
    labels = set()
    for card in needs_cards + robot_cards:
        assert not card["has_nested_a"]
        assert all(tag == "span" for tag in card["chips"])
        assert card["releases"] == ["span"]
        assert len(card["anchors"]) == 2
        card_link, history_link = card["anchors"]
        assert "card-link" in card_link["class"]
        assert card_link["in_title"] is True
        assert card_link["href"].endswith("/latest/")
        assert "history-link" in history_link["class"]
        assert history_link["href"] == card_link["href"].removesuffix("latest/")
        assert history_link["text"] == "History (2)"
        labels.add(card_link["aria-label"])

    assert "Walk the hallway, latest report (PASS)" in labels
    assert "Walk the hallway, latest report (FAIL)" in labels
    assert "Walk around the corner (left), latest report (Not supported)" in labels
    assert "Walk around the corner (right), latest report (Insufficient evidence)" in labels

    # History pages still keep linked chips and release links.
    history = (site / "unitree-g1/stock-mujoco-hallway/index.html").read_text()
    assert '<a class="chip"' in history
    assert '<a class="release' in history
    local_links_resolve(site)


def test_index_card_styles_support_stretched_link_hover_focus_and_mobile_touch_target():
    """Issue #138: stretched ::after link, --bg-muted/--border-strong hover, card ring, 44px mobile."""
    css = site_tool.SITE_STYLES
    assert ".card {\n  position: relative;" in css
    assert "cursor: pointer;" in css
    assert (
        ".card:hover, .card:focus-within {\n  background: var(--bg-muted);\n  border-color: var(--border-strong);\n}"
        in css
    )
    assert (
        ".card:has(.card-link:focus-visible) {\n  outline: 3px solid var(--focus);\n  outline-offset: 3px;\n}"
        in css
    )
    assert ".card-link:focus-visible { outline: none; }" in css
    assert (
        '.card-link::after {\n  content: "";\n  position: absolute;\n  inset: 0;\n  z-index: 1;'
        in css
    )
    assert ".history-link {\n  position: relative;\n  z-index: 2;" in css
    assert "a.chip:hover {" in css
    assert "min-height: 44px;" in css


def test_spec_identities_survive_publication_and_history_restoration(tmp_path):
    fixture = json.loads((ROOT / "tests/fixtures/historical-spec-provenance.json").read_text())
    target = next(t for t in site_tool.targets() if t["id"] == "booster-t1-stock-mujoco-hallway")
    target["lock"]["releases"]["specs"] = fixture["generation"]
    reports = tmp_path / "reports"
    source = reports / target["artifact"]
    write_reports(source, 0, fixture["grade"], "<html><body>Recorded FAIL</body></html>")
    report = json.loads((source / "report.json").read_text())
    report["summaries"][0]["spec"] = fixture["assessment"]
    (source / "report.json").write_text(json.dumps(report))
    restamp(source)
    first = tmp_path / "first"
    folder = site_tool.add_run(first, reports, target, run("43954868926", "2026-11-16T21:04:33Z"))
    evidence = folder / "evidence/observations.json"
    evidence.parent.mkdir()
    evidence.write_bytes(b'{"recorded": "unchanged"}\n')
    preserved = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    site_tool.render(first, now="2026-11-16T21:04:33Z")
    # Initial publication has always restamped SHA256SUMS to include run.json.
    for relative, original in preserved.items():
        if relative.name != "SHA256SUMS":
            assert (folder / relative).read_bytes() == original, relative
    preserved = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    restored = tmp_path / "restored"
    site_tool.extract_pages(pages_zip(first), restored)
    # A later publish uses today's locks, which must not replace historical pins.
    site_tool.build(
        restored, reports_for(tmp_path, "later", 19, "PASS"), run("500", "2026-11-17T00:00:00Z")
    )
    old = restored / folder.relative_to(first)
    for relative, original in preserved.items():
        assert (old / relative).read_bytes() == original, relative
    # Both recorded identities stay on the report page (#142), now in the collapsed block (#146).
    # The history page no longer repeats them.
    page = (old / "index.html").read_text()
    block = provenance_block(page)
    assert "Generation" in block and "Assessment" in block
    for role in ("generation", "assessment"):
        assert fixture[role]["tag"] in visible_text(block)
        assert fixture[role]["commit"] in block  # the full SHA, in a link or title
        assert not visible_hex(visible_text(block))
    assert page.count('id="spec-provenance"') == 1
    assert "FAIL" in page
    history = (old.parent.parent / "index.html").read_text()
    assert "spec-provenance" not in history and "Spec provenance" not in history
    for role in ("generation", "assessment"):
        assert fixture[role]["commit"] not in history
    local_links_resolve(restored)


def visible_text(markup: str) -> str:
    """The text a reader sees: tags, and so attribute values such as titles, are dropped."""
    return re.sub(r"<[^>]+>", " ", markup)


def visible_page_text(markup: str) -> str:
    """The text a reader sees on a whole page: no head, script, style, comment or attribute."""
    markup = re.sub(r"(?is)<(head|script|style|title)\b.*?</\1\s*>|<!--.*?-->", " ", markup)
    return html_module.unescape(re.sub(r"<[^>]+>", " ", markup))


def visible_hex(text: str) -> list[str]:
    """Commit-id-like words: 7 to 40 hex characters with at least one a-f letter."""
    return re.findall(r"\b(?=[0-9]*[a-f])[0-9a-f]{7,40}\b", text)


def provenance_block(page: str) -> str:
    blocks = re.findall(r'<details class="spec-provenance".*?</details>', page, re.S)
    assert len(blocks) == 1, blocks
    return blocks[0]


SPECS_REPOSITORY = "https://github.com/Living-Proof-Labs/robot-capability-specs"


def spec_identity(tag: str, revision: str | None = None, capability: str = "walk-hallway") -> dict:
    return {
        "repository": SPECS_REPOSITORY,
        "tag": tag,
        "commit": hashlib.sha1(tag.encode()).hexdigest(),
        "capability": capability,
        **({"revision": revision} if revision else {}),
    }


def build_spec_history(tmp_path: Path, specs: list[tuple[str, dict | None]]) -> Path:
    """One run per entry, oldest first. Each is (generation tag, assessment identity or None).

    Returns the booster-t1 hallway variant's folder in the built site.
    """
    verifications = tmp_path / "verifications"
    verifications.mkdir(parents=True)
    site = tmp_path / "site"
    for index, (generation, assessment) in enumerate(specs):
        for lock_path in (ROOT / "verifications").glob("*.lock.json"):
            lock = json.loads(lock_path.read_text())
            lock["releases"]["specs"]["tag"] = generation
            lock["releases"]["specs"]["commit"] = hashlib.sha1(generation.encode()).hexdigest()
            (verifications / lock_path.name).write_text(json.dumps(lock))
        reports = tmp_path / f"reports-{index}"
        for target in site_tool.targets(verifications):
            folder = reports / target["artifact"]
            write_reports(folder, 19, "PASS")
            if assessment is not None:
                report = json.loads((folder / "report.json").read_text())
                report["summaries"][0]["spec"] = assessment
                (folder / "report.json").write_text(json.dumps(report))
                restamp(folder)
        site_tool.build(
            site,
            reports,
            run(str(100 + index), f"2026-11-18T0{index}:00:00Z"),
            verifications,
        )
    return site / "booster-t1/stock-mujoco-hallway"


def history_rows(base: Path) -> dict[str, str]:
    """Each history row's markup, keyed by the run folder its Report link opens."""
    page = (base / "index.html").read_text()
    rows = re.findall(r'<article class="row[^"]*">(.*?)</article>', page, re.S)
    return {re.search(r'href="runs/([^/"]+)/"', row).group(1): row for row in rows}


def test_history_rows_drop_the_spec_provenance_block(tmp_path):
    """#146: a row is badge, title, chips, release, time, poster and Report; nothing else."""
    base = build_spec_history(
        tmp_path, [("v2.3.0", spec_identity("v2.3.0", "0.1.0")) for _ in range(3)]
    )
    page = (base / "index.html").read_text()
    rows = history_rows(base)
    assert list(rows) == ["102", "101", "100"]
    for row in rows.values():
        assert "Spec provenance" not in row
        assert "spec-provenance" not in row
        assert "<dl" not in row and "<details" not in row
        assert not re.search(r"[0-9a-f]{40}", visible_text(row))
        assert "run.json" not in row and "report.json" not in row
        assert "Generation source" not in row and "do not reassess" not in row
    assert "Spec provenance" not in page and "spec-provenance" not in page
    assert page.count("<article") == 3


def test_history_header_shows_the_spec_version_and_the_robot_tag(tmp_path):
    """#146: `specs v2.3.0` links to the spec release; the robot pin is its tag."""
    base = build_spec_history(tmp_path, [("v2.3.0", spec_identity("v2.3.0", "0.1.0"))])
    header = (base / "index.html").read_text().split("<main", 1)[0]
    assert (
        'specs <a href="https://github.com/Living-Proof-Labs/robot-capability-specs/'
        'releases/tag/v2.3.0">v2.3.0</a>'
    ) in header
    assert "runner <a" in header and "verifier <a" in header and "reporter <a" in header
    assert "livingproof-booster-t1 v2.3.5" in header
    assert "@" not in visible_text(header)
    assert "releases/tag/v2.3.5" in header
    assert not re.search(r"[0-9a-f]{40}", visible_text(header))


def test_history_header_resolves_the_robot_tag_of_a_record_stored_without_one(tmp_path):
    """The retired corner histories hold pre-#129 records with only a robot commit.

    The reviewed locks name that commit by tag, so the header shows the tag; a commit no lock
    knows falls back to the link text "commit", never a hash. The stored run.json is left alone.
    """
    base = build_spec_history(tmp_path, [("v2.3.0", None)])
    path = base / "runs/100/run.json"
    record = json.loads(path.read_text())
    record["robot"].pop("tag")
    record["robot"]["url"] = (
        f"https://github.com/{record['robot']['repository']}/tree/" + (record["robot"]["commit"])
    )
    path.write_text(json.dumps(record, indent=2) + "\n")
    stored = path.read_bytes()
    site_tool.render(tmp_path / "site", now="2026-11-18T05:00:00Z")
    header = (base / "index.html").read_text().split("<main", 1)[0]
    assert "livingproof-booster-t1 v2.3.5" in header
    assert "@" not in visible_text(header)
    assert 'releases/tag/v2.3.5"' in header
    assert path.read_bytes() == stored

    record["robot"]["commit"] = "c" * 40
    path.write_text(json.dumps(record, indent=2) + "\n")
    site_tool.render(tmp_path / "site", now="2026-11-18T05:00:00Z")
    header = (base / "index.html").read_text().split("<main", 1)[0]
    assert re.search(r">livingproof-booster-t1 commit</a>", header)
    assert "@" not in visible_text(header)
    assert "cccccccc" not in visible_text(header)
    assert "c" * 40 in header  # the full SHA stays in the link and its title
    assert "<code" not in header.split("meta-line", 1)[-1]
    assert "v2.3.5" not in header


def test_a_record_at_a_commit_no_lock_pins_any_more_shows_commit_not_the_new_tag(tmp_path):
    """#145: the locks moved booster-t1 from v2.2.10 (a00c7d88) to v2.3.5, so no lock names a00c7d88.

    A run stored before #129 at that commit has no tag of its own. The header must not claim the
    new tag for the old commit, and does not invent the old one: it reads `commit`, linked to
    the commit, with the full SHA in the title. The stored run.json is left alone.
    """
    retired = "a00c7d884b764c89404dc91be9232c0d55eb392c"
    pinned = {
        json.loads(path.read_text())["robot"]["commit"]
        for path in (ROOT / "verifications").glob("*.lock.json")
    }
    assert retired not in pinned
    base = build_spec_history(tmp_path, [("v2.3.0", None)])
    path = base / "runs/100/run.json"
    record = json.loads(path.read_text())
    record["robot"].pop("tag")
    record["robot"]["commit"] = retired
    record["robot"]["url"] = f"https://github.com/{record['robot']['repository']}/tree/{retired}"
    path.write_text(json.dumps(record, indent=2) + "\n")
    stored = path.read_bytes()
    site_tool.render(tmp_path / "site", now="2026-11-18T05:00:00Z")
    header = (base / "index.html").read_text().split("<main", 1)[0]
    assert re.search(r">livingproof-booster-t1 commit</a>", header)
    assert f'href="https://github.com/{record["robot"]["repository"]}/tree/{retired}"' in header
    assert f'title="Commit {retired}"' in header
    assert "v2.3.5" not in header and "v2.2.10" not in header
    assert "@" not in visible_text(header)
    assert "a00c7d88" not in visible_text(header)
    assert path.read_bytes() == stored


def test_a_spec_bump_flags_exactly_one_row(tmp_path):
    """#146: `Spec changed: v2.3.0 → v2.4.0` on the first run of the new spec, nowhere else."""
    base = build_spec_history(
        tmp_path,
        [
            ("v2.3.0", spec_identity("v2.3.0", "0.1.0")),
            ("v2.3.0", spec_identity("v2.3.0", "0.1.0")),
            ("v2.4.0", spec_identity("v2.4.0", "0.1.0")),
            ("v2.4.0", spec_identity("v2.4.0", "0.1.0")),
        ],
    )
    rows = history_rows(base)
    assert list(rows) == ["103", "102", "101", "100"]
    flagged = {run_id for run_id, row in rows.items() if "Spec changed" in row}
    assert flagged == {"102"}
    assert rows["102"].count("Spec changed") == 1
    assert '<span class="chip chip-spec">Spec changed: v2.3.0 → v2.4.0</span>' in rows["102"]
    page = (base / "index.html").read_text()
    assert page.count("Spec changed") == 1
    assert ".chip-spec" in site_tool.SITE_STYLES


def test_an_assessment_revision_change_flags_the_row(tmp_path):
    base = build_spec_history(
        tmp_path,
        [
            ("v2.3.0", spec_identity("v2.3.0", "0.1.0")),
            ("v2.3.0", spec_identity("v2.3.0", "0.2.0")),
        ],
    )
    rows = history_rows(base)
    assert "Spec changed: revision 0.1.0 → 0.2.0" in rows["101"]
    assert "Spec changed" not in rows["100"]


def test_a_row_is_not_flagged_when_nothing_changed_or_nothing_was_recorded(tmp_path):
    same = build_spec_history(
        tmp_path / "same", [("v2.3.0", spec_identity("v2.3.0", "0.1.0")) for _ in range(3)]
    )
    assert "Spec changed" not in (same / "index.html").read_text()
    # No assessment recorded on either run: an unknown is not a change.
    unknown = build_spec_history(tmp_path / "unknown", [("v2.3.0", None), ("v2.3.0", None)])
    assert "Spec changed" not in (unknown / "index.html").read_text()
    # A single run has nothing below it.
    single = build_spec_history(tmp_path / "single", [("v2.4.0", spec_identity("v2.4.0", "0.1.0"))])
    assert "Spec changed" not in (single / "index.html").read_text()


def test_a_missing_assessment_revision_is_not_a_change_but_a_known_one_is(tmp_path):
    base = build_spec_history(
        tmp_path,
        [("v2.3.0", spec_identity("v2.3.0")), ("v2.3.0", spec_identity("v2.3.0", "0.1.0"))],
    )
    assert "Spec changed" not in (base / "index.html").read_text()


@pytest.mark.parametrize(
    ("markup", "closing"),
    [
        (
            "<html><body><header>Report</header><main class='wrap'><section class='verdict'>"
            "PASS</section><section id='evaluators'>Evaluators</section></main></body></html>",
            "</main>",
        ),
        # Older reporters wrote no <main>: the block goes at the end of the body.
        (
            "<html><body><section class='verdict'>PASS</section><p>Evaluators</p></body></html>",
            "</body>",
        ),
        ("plain text report Evaluators", None),
    ],
)
def test_report_provenance_follows_the_evaluators_and_is_collapsed(markup, closing):
    """#146: results first. The block goes before </main>, never parsed out of the reporter."""
    record = {"releases": {"specs": spec_identity("v2.3.0")}}
    public = site_tool.with_spec_provenance(markup, record, {"summaries": []})
    block = provenance_block(public)
    assert public.index("Evaluators") < public.index(block)
    if closing:
        assert public.index(block) < public.index(closing)
        assert public[public.index(block) + len(block) :].startswith(closing)
    else:
        assert public.endswith(block)
    assert not re.search(r"<details[^>]* open", block)
    assert "Spec provenance" not in public
    # The reporter's own markup is untouched.
    assert public.replace(block, "") == markup


def test_report_provenance_on_the_current_reporter_output(tmp_path):
    """The pinned reporter's real page: the block is last in <main>, after every evaluator."""
    sample = tmp_path / "sample"
    _renderable_bundle(sample)
    report = json.loads((sample / "report.json").read_text())
    reporter_page = site_tool.render_with_reporter(
        report,
        {"links": []},
        history_href="../../index.html",
        history_label="Run history",
        retention=None,
    )
    record = {"releases": {"specs": spec_identity("v2.3.0", "0.1.0")}}
    public = site_tool.with_spec_provenance(reporter_page, record, report)
    block = provenance_block(public)
    assert public.index("evaluator-") < public.index(block)
    assert public.index("class='verdict'") < public.index(block)
    assert public.index(block) < public.rindex("</main>")
    assert public.replace(block, "") == reporter_page
    # No mid-page position: nothing but the closing tags follows the block.
    assert public[public.index(block) + len(block) :].strip().startswith("</main>")


def test_report_provenance_summary_is_one_line_and_hides_the_commit(tmp_path):
    spec = spec_identity("v2.3.0", "0.1.0")
    record = {
        "releases": {"specs": {**spec, "repository": "Living-Proof-Labs/robot-capability-specs"}}
    }
    block = site_tool.spec_provenance_html(record, {"summaries": [{"spec": spec}]})
    assert block.startswith('<details class="spec-provenance" id="spec-provenance"')
    summary = re.search(r"<summary[^>]*>(.*?)</summary>", block, re.S).group(1)
    assert "\n" not in summary
    assert re.sub(r"\s+", " ", visible_text(summary)).strip() == (
        "Spec v2.3.0 · walk-hallway · revision 0.1.0"
    )
    assert "<code" not in summary  # the version is body text, not monospace


def test_report_provenance_shows_one_line_when_generation_and_assessment_match():
    spec = spec_identity("v2.3.0", "0.1.0")
    # The generation pin is recorded as owner/name; the assessment as a URL. Same repository.
    generation = {
        "repository": "Living-Proof-Labs/robot-capability-specs",
        "tag": "v2.3.0",
        "commit": spec["commit"],
        "url": f"{SPECS_REPOSITORY}/releases/tag/v2.3.0",
    }
    block = site_tool.spec_provenance_html(
        {"releases": {"specs": generation}}, {"summaries": [{"spec": spec}, {"spec": spec}]}
    )
    assert len(re.findall(r"<dt[ >]", block)) == 1
    assert re.search(r"<dt[^>]*>Generation and assessment</dt>", block)
    text = re.sub(r"\s+", " ", visible_text(block))
    assert (
        "v2.3.0 · Living-Proof-Labs/robot-capability-specs · walk-hallway · revision 0.1.0" in text
    )
    assert spec["commit"][:8] not in text
    assert f'href="{SPECS_REPOSITORY}/releases/tag/v2.3.0"' in block
    assert spec["commit"] in block  # the full SHA lives in the link's title, not in the text
    assert f'title="Release v2.3.0, commit {spec["commit"]}"' in block
    assert spec["commit"] not in text
    assert "https://" not in text
    assert "Recorded at run time; not reassessed." in text
    for old in ("Generation source", "saved caller lock", "summaries[].spec", "run.json"):
        assert old not in block


def test_report_provenance_shows_both_lines_only_when_they_differ():
    generation = spec_identity("v2.0.2")
    assessment = spec_identity("v2.3.0", "0.1.0")
    block = site_tool.spec_provenance_html(
        {"releases": {"specs": {**generation, "url": f"{SPECS_REPOSITORY}/releases/tag/v2.0.2"}}},
        {"summaries": [{"spec": assessment}]},
    )
    assert len(re.findall(r"<dt[ >]", block)) == 2
    assert re.search(r"<dt[^>]*>Generation</dt>", block) and re.search(
        r"<dt[^>]*>Assessment</dt>", block
    )
    text = re.sub(r"\s+", " ", visible_text(block))
    assert generation["commit"][:8] not in text and assessment["commit"][:8] not in text
    assert generation["commit"] not in text and assessment["commit"] not in text
    assert generation["commit"] in block and assessment["commit"] in block
    # The summary names the assessed spec, the one the report's Spec field shows.
    summary = re.search(r"<summary[^>]*>(.*?)</summary>", block, re.S).group(1)
    assert "v2.3.0" in summary and "v2.0.2" not in summary
    # Several distinct assessments each keep a line.
    other = spec_identity("v2.4.0", "0.2.0")
    many = site_tool.spec_provenance_html(
        {"releases": {"specs": generation}},
        {"summaries": [{"spec": assessment}, {"spec": other}, {"spec": assessment}]},
    )
    assert len(re.findall(r"<dt[ >]", many)) == 3
    assert not visible_hex(visible_text(many))
    assert other["commit"] in many


def test_report_provenance_keeps_missing_fields_explicit_and_escapes_values():
    empty = site_tool.spec_provenance_html({}, {})
    assert empty.count("Not recorded") >= 1 and "<details" in empty
    hostile = {"tag": "<script>alert(1)</script>", "commit": "d" * 40}
    block = site_tool.spec_provenance_html({"releases": {"specs": hostile}}, {})
    assert "<script>" not in block


def test_published_report_page_puts_the_result_first(tmp_path):
    """End to end through build: no provenance before the reporter's own content."""
    reports = _renderable_reports(tmp_path)
    site = tmp_path / "site"
    site_tool.build(site, reports, run("7", "2026-11-01T00:00:00Z"))
    page = _page(site)
    block = provenance_block(page)
    assert page.index("<main") < page.index("class='verdict'") < page.index(block)
    assert "Spec provenance" not in page
    assert not re.search(r"<details[^>]* open", block)
    assert page.count('class="spec-provenance"') == 1
    local_links_resolve(site)


def test_no_commit_id_is_visible_on_the_index_or_history_page(tmp_path):
    """#146 (Aaron): commit ids are links, never visible text. 7 or more hex characters is a hash.

    Runs are built with a commit and no tag, so every row takes the fallback, and the robot
    commit is one no lock knows by tag, so the header takes its fallback too.
    """
    base = build_spec_history(
        tmp_path,
        [
            ("v2.3.0", spec_identity("v2.3.0", "0.1.0")),
            ("v2.4.0", spec_identity("v2.4.0", "0.1.0")),
        ],
    )
    for number in ("100", "101"):
        path = base / f"runs/{number}/run.json"
        record = json.loads(path.read_text())
        record["commit"].pop("tag", None)
        record["robot"].pop("tag", None)
        record["robot"]["commit"] = "c" * 40
        path.write_text(json.dumps(record, indent=2) + "\n")
    site_tool.render(tmp_path / "site", now="2026-11-18T05:00:00Z")
    history = (base / "index.html").read_text()
    assert history.count(">commit</a>") == 2  # a link whose text is "commit", one per row
    assert re.search(r'<a [^>]*href="[^"]*/commit/a{40}"[^>]*>commit</a>', history)
    assert visible_hex(visible_page_text(history)) == []
    index = (tmp_path / "site/index.html").read_text()
    assert visible_hex(visible_page_text(index)) == []
    for page in (history, index):
        assert not re.search(r"\b[0-9a-f]{40}\b", visible_page_text(page))
    # The id is not lost: the full SHA is the link and its title.
    assert "a" * 40 in history


def test_the_robot_pin_is_written_without_an_at_sign(tmp_path):
    base = build_spec_history(tmp_path, [("v2.3.0", spec_identity("v2.3.0", "0.1.0"))])
    page = (base / "index.html").read_text()
    pin = re.search(r'<a href="[^"]*/releases/tag/v2\.3\.5"[^>]*>([^<]*)</a>', page)
    assert pin is not None
    assert pin.group(1) == "livingproof-booster-t1 v2.3.5"
    assert "@" not in visible_text(page.split("<main", 1)[0])


def test_report_page_shows_no_commit_id_that_the_site_adds(tmp_path):
    """The reporter's own sections are its own; the block this site adds shows no hash."""
    reports = _renderable_reports(tmp_path)
    site = tmp_path / "site"
    site_tool.build(site, reports, run("7", "2026-11-01T00:00:00Z"))
    page = _page(site)
    block = provenance_block(page)
    assert visible_hex(visible_page_text(block)) == []
    assert not re.search(r"\b[0-9a-f]{40}\b", visible_page_text(block))
    assert "@" not in visible_page_text(block)
    # Nothing outside the block changed: the site adds no visible hash to the reporter's page.
    assert sorted(visible_hex(visible_page_text(page.replace(block, "")))) == sorted(
        visible_hex(visible_page_text(page))
    )
    # An identity with no release tag is the link text "commit", not a hash.
    spec = spec_identity("v2.3.0", "0.1.0")
    untagged = {k: v for k, v in spec.items() if k != "tag"}
    text = visible_page_text(
        site_tool.spec_provenance_html(
            {"releases": {"specs": untagged}}, {"summaries": [{"spec": untagged}]}
        )
    )
    assert visible_hex(text) == []
    text = re.sub(r"\s+", " ", text)
    assert text.startswith(" Spec commit · walk-hallway · revision 0.1.0")
    assert (
        "commit · Living-Proof-Labs/robot-capability-specs · walk-hallway · revision 0.1.0" in text
    )
    # ... and the link still works: it is the commit on GitHub.
    html_block = site_tool.spec_provenance_html(
        {"releases": {"specs": untagged}}, {"summaries": [{"spec": untagged}]}
    )
    assert f'href="{SPECS_REPOSITORY}/commit/{spec["commit"]}"' in html_block
    assert ">commit</a>" in html_block


def test_report_provenance_summary_is_not_letter_spaced_or_uppercased():
    """#146 (Art): the reporter styles every <summary> as small letter-spaced capitals."""
    spec = spec_identity("v2.3.0", "0.1.0")
    block = site_tool.spec_provenance_html(
        {"releases": {"specs": spec}}, {"summaries": [{"spec": spec}]}
    )
    opening = re.search(r"<summary([^>]*)>", block).group(1)
    style = re.search(r'style="([^"]*)"', opening).group(1)
    assert "text-transform:none" in style
    assert "letter-spacing:normal" in style
    assert "uppercase" not in block.lower().replace("text-transform:none", "")
    assert "Spec" in re.search(r"<summary[^>]*>(.*?)</summary>", block, re.S).group(1)
    # "Show" / "Hide" follow the line, and are not the reporter's capitalised chrome.
    assert 'content:"Show"' in block and 'content:"Hide"' in block


def test_the_commit_fallback_link_is_styled_like_the_report_link():
    """#146 (Art): underlined, link colour, same weight as the Report link in `.row-meta`."""
    css = site_tool.SITE_STYLES
    rule = re.search(r"a\.release\.is-fallback\s*\{([^}]*)\}", css)
    assert rule, "no `a.release.is-fallback` rule"
    body = rule.group(1)
    assert "text-decoration: underline" in body
    assert "color: var(--link)" in body
    assert "font-weight: 550" in body  # `.row-meta a`, the Report link's weight
    assert ".row-meta a { font-weight: 550; }" in css


def test_history_header_robot_tag_follows_the_reviewed_locks_not_a_constant(tmp_path):
    """#146 (Vera): change the robot tag in the locks and the header's pin follows it."""
    verifications = tmp_path / "verifications"
    verifications.mkdir()
    for lock_path in (ROOT / "verifications").glob("*.lock.json"):
        lock = json.loads(lock_path.read_text())
        lock["robot"]["tag"] = "v9.9.9"
        (verifications / lock_path.name).write_text(json.dumps(lock))
    reports = tmp_path / "reports"
    for target in site_tool.targets(verifications):
        write_reports(reports / target["artifact"], 19, "PASS")
    site = tmp_path / "site"
    site_tool.build(site, reports, run("100", "2026-11-18T00:00:00Z"), verifications)
    header = (site / "booster-t1/stock-mujoco-hallway/index.html").read_text().split("<main", 1)[0]
    assert "livingproof-booster-t1 v9.9.9" in header
    assert "v2.2.10" not in header and "@" not in visible_text(header)


def test_spec_chip_edge_cases_single_run_revision_only_and_unrecorded(tmp_path):
    """#146 (Vera): a lone run and an unrecorded assessment never flag; a revision change does."""
    single = build_spec_history(tmp_path / "single", [("v2.3.0", spec_identity("v2.3.0", "0.1.0"))])
    assert "Spec changed" not in (single / "index.html").read_text()
    unknown = build_spec_history(
        tmp_path / "unknown", [("v2.3.0", None), ("v2.3.0", spec_identity("v2.3.0", "0.1.0"))]
    )
    assert "Spec changed" not in (unknown / "index.html").read_text()
    revised = build_spec_history(
        tmp_path / "revised",
        [
            ("v2.3.0", spec_identity("v2.3.0", "0.1.0")),
            ("v2.3.0", spec_identity("v2.3.0", "0.2.0")),
        ],
    )
    flagged = {k for k, row in history_rows(revised).items() if "Spec changed" in row}
    assert flagged == {"101"}
    assert "Spec changed: revision 0.1.0 → 0.2.0" in (revised / "index.html").read_text()


@pytest.mark.parametrize("suffix", ["", "/", ".git", ".git/"])
def test_repository_labels_match_without_changing_stored_provenance(suffix):
    generation = {
        **spec_identity("v2.0.2"),
        "repository": "Living-Proof-Labs/robot-capability-specs",
    }
    assessment = {**spec_identity("v2.3.0"), "repository": SPECS_REPOSITORY + suffix}
    record = {"releases": {"specs": generation}}
    report = {"summaries": [{"spec": assessment}]}
    before = json.dumps([record, report], sort_keys=True)
    markup = "<main><p>Reporter-owned content</p></main>"
    public = site_tool.with_spec_provenance(markup, record, report)
    block = provenance_block(public)
    rows = re.findall(r"<dd[^>]*>(.*?)</dd>", block, re.S)
    assert len(rows) == 2
    for row, tag in zip(rows, ("v2.0.2", "v2.3.0")):
        assert "Living-Proof-Labs/robot-capability-specs" in visible_text(row)
        assert "https://" not in visible_text(row)
        assert f'href="{SPECS_REPOSITORY}/releases/tag/{tag}"' in row
    assert public.replace(block, "") == markup
    assert json.dumps([record, report], sort_keys=True) == before


@pytest.mark.parametrize(
    "repository",
    [
        "javascript:alert(1)",
        "//evil.example/owner/repo",
        "https://user@evil.example/owner/repo",
        "https://github.com/owner/repo?redirect=evil",
        "https://github.com/owner/repo#fragment",
        "https://github.com/owner/<script>",
        "https:///owner/repo",
        "https://[invalid/owner/repo",
        "owner/../repo",
    ],
)
def test_spec_repository_links_reject_unsafe_or_ambiguous_values(repository):
    spec = {**spec_identity("v2.3.0"), "repository": repository}
    rendered = site_tool._spec_line_html(spec, assessed=True)
    assert "href=" not in rendered
    assert "<script>" not in rendered


def test_index_needs_attention_newest_first_chips_and_finding(tmp_path):
    """#139: Needs attention lists latest FAILs across robots, newest first, with chips & finding."""
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for(tmp_path, "pass", 19, "PASS"),
        {**run("200", "2026-11-18T05:20:00Z"), "tag": "v1.1.0"},
    )
    booster_fail = site / "booster-t1/stock-mujoco-hallway/runs/200/run.json"
    b_rec = json.loads(booster_fail.read_text())
    b_rec["outcome"][0]["grade"] = "FAIL"
    b_rec["run"]["created"] = "2026-11-18T05:10:00Z"
    b_rec["failing_evaluators"] = [
        {
            "id": "livingproof-scenario",
            "title": "Scenario",
            "href": "#evaluator-livingproof-scenario",
            "summary": "Left the hallway through the right wall.",
        },
        {
            "id": "aaron-roller",
            "title": "Aaron Roller",
            "href": "#evaluator-aaron-roller",
        },
    ]
    booster_fail.write_text(json.dumps(b_rec, indent=2) + "\n")

    site_tool.render(site, now="2026-11-18T05:40:00Z")
    one_fail = (site / "index.html").read_text()
    assert "1 latest result failed, newest first. Each card opens the latest report." in one_fail

    g1_fail = site / "unitree-g1/stock-mujoco-hallway/runs/200/run.json"
    g_rec = json.loads(g1_fail.read_text())
    g_rec["outcome"][0]["grade"] = "FAIL"
    g_rec["run"]["created"] = "2026-11-18T05:30:00Z"
    g_rec["failing_evaluators"] = [
        {
            "id": "livingproof-scenario",
            "title": "Scenario",
            "href": "#evaluator-livingproof-scenario",
            "summary": "Drifted 0.66 m from the centreline.",
        }
    ]
    g1_fail.write_text(json.dumps(g_rec, indent=2) + "\n")

    site_tool.render(site, now="2026-11-18T05:40:00Z")
    index = (site / "index.html").read_text()
    needs_part, robots_part = index.split('<h2 class="robots-label"', 1)

    assert '<h2 id="needs-attention">Needs attention</h2>' in needs_part
    assert "2 latest results failed, newest first. Each card opens the latest report." in needs_part
    assert "Unitree G1 · stock · MuJoCo" in needs_part
    assert "Booster T1 · stock · MuJoCo" in needs_part
    assert needs_part.index("Unitree G1 · stock · MuJoCo") < needs_part.index(
        "Booster T1 · stock · MuJoCo"
    )
    assert '<span class="chip">Scenario</span>' in needs_part
    assert '<span class="chip">Aaron Roller</span>' in needs_part
    assert '<p class="finding">Drifted 0.66 m from the centreline.</p>' in needs_part
    assert '<p class="finding">Left the hallway through the right wall.</p>' in needs_part
    assert len(_parse_index_cards(needs_part)) == 2
    # Both failing scenarios also appear inside their robot sections, without repeating robot name.
    assert "Unitree G1 · stock · MuJoCo" not in robots_part
    assert "Booster T1 · stock · MuJoCo" not in robots_part
    local_links_resolve(site)


def test_index_robot_sections_collapsed_with_counts_dots_and_failing_first_order(tmp_path):
    """#139: robot sections use native collapsed <details>, accessible counts, and fail-first dots."""
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for(tmp_path, "pass", 19, "PASS"),
        {**run("200", "2026-11-18T05:20:00Z"), "tag": "v1.1.0"},
    )
    booster_fail = site / "booster-t1/stock-mujoco-hallway/runs/200/run.json"
    b_rec = json.loads(booster_fail.read_text())
    b_rec["outcome"][0]["grade"] = "FAIL"
    b_rec["run"]["created"] = "2026-11-18T05:30:00Z"
    b_rec["failing_evaluators"] = [
        {
            "id": "livingproof-scenario",
            "title": "Scenario",
            "href": "#evaluator-livingproof-scenario",
            "summary": "Stopped early.",
        }
    ]
    booster_fail.write_text(json.dumps(b_rec, indent=2) + "\n")

    site_tool.render(site, now="2026-11-18T05:40:00Z")
    index = (site / "index.html").read_text()

    assert not re.search(r'<details class="robot"[^>]*\bopen\b', index)
    for robot_id in ("booster-t1", "unitree-g1", "unitree-go1"):
        assert f'<details class="robot" id="{robot_id}">' in index

    booster_block = index.split('<details class="robot" id="booster-t1">', 1)[1].split(
        "</details>", 1
    )[0]
    assert (
        '<span class="visually-hidden">'
        "Booster T1, 3 scenarios, 1 failed, 2 passed, latest v1.1.0, 10 minutes ago"
        "</span>"
    ) in booster_block
    assert '<div class="summary-row" aria-hidden="true">' in booster_block
    assert '<span class="fail-count">1 failed</span>' in booster_block
    assert '<span class="pass-count">2 passed</span>' in booster_block
    assert (
        '<span class="dots">'
        '<span class="pip pip-fail" title="Walk the hallway: failed"></span>'
        '<span class="pip pip-pass" title="Walk around the corner (left): passed"></span>'
        '<span class="pip pip-pass" title="Walk around the corner (right): passed"></span>'
        '<span class="pip-more" hidden></span>'
        "</span>"
    ) in booster_block
    # Expanded cards: FAIL first, then alphabetical.
    cards = _parse_index_cards(booster_block)
    assert [c["anchors"][0]["aria-label"] for c in cards] == [
        "Walk the hallway, latest report (FAIL)",
        "Walk around the corner (left), latest report (PASS)",
        "Walk around the corner (right), latest report (PASS)",
    ]
    assert (site / "open.js").is_file()
    local_links_resolve(site)


def test_index_all_pass_shows_single_quiet_line(tmp_path):
    """#139: only an entirely passing catalog gets the all-pass message."""
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for(tmp_path, "pass", 19, "PASS"),
        {**run("200", "2026-11-18T05:20:00Z"), "tag": "v1.1.0"},
    )
    site_tool.render(site, now="2026-11-18T05:40:00Z")
    index = (site / "index.html").read_text()
    assert 'id="needs-attention"' not in index
    assert '<p class="all-pass">All 10 latest results pass.</p>' in index
    assert "failed" not in index.split('<h2 class="robots-label"', 1)[0]
    assert index.index("All 10 latest results pass.") < index.index('id="robots"')
    local_links_resolve(site)


@pytest.mark.parametrize(
    ("grade", "label", "robot_label"),
    [
        ("INSUFFICIENT_EVIDENCE", "insufficient evidence", "insufficient"),
        ("UNSUPPORTED", "not supported", "not supported"),
        ("UNKNOWN", "unknown", "unknown"),
        ("UNRECOGNIZED", "unknown", "unknown"),
        ("", "unknown", "unknown"),
        (None, "unknown", "unknown"),
    ],
)
def test_index_nonpassing_results_do_not_claim_all_pass(tmp_path, grade, label, robot_label):
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for(tmp_path, "pass", 19, "PASS"),
        run("200", "2026-11-18T05:20:00Z"),
    )
    history = site_tool.runs(site)
    records = history[("booster-t1", "stock-mujoco-hallway")]
    if grade is None:
        records[0]["outcome"] = []
    else:
        records[0]["outcome"][0]["grade"] = grade
    index = site_tool._index_page_html(history, "2026-11-19T08:00:00Z")
    assert 'class="all-pass"' not in index
    assert f'<p class="status">10 latest results: 9 passed, 1 {label}.</p>' in index
    assert "Booster T1, 3 scenarios, " in index
    assert f"1 {robot_label}" in index
    assert 'id="needs-attention"' not in index


def test_index_empty_catalog_does_not_claim_all_pass():
    index = site_tool._index_page_html({}, "2026-11-19T08:00:00Z")
    assert '<p class="status">No verification reports published yet.</p>' in index
    assert 'class="all-pass"' not in index
    assert 'id="robots"' not in index
