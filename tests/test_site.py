# SPDX-License-Identifier: Apache-2.0
"""Pages site assembly (tools/site.py) and the publishing jobs in verify.yml."""

import email.message
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import urllib.error
import urllib.request
import zipfile

import pytest
import yaml

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


def restamp(directory: Path) -> None:
    (directory / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((directory / n).read_bytes()).hexdigest()}  {n}\n"
            for n in site_tool.REPORT_FILES
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
    assert "19/20 passed" in index and "history (2 runs)" in index
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
        f"livingproof-unitree-g1/tree/{lock['robot']['commit']}",
    ):
        assert link in history
    assert "/actions/" not in latest and "artifact" not in latest.lower()
    local_links_resolve(site)


def test_index_and_history_show_ci_runs_as_text(tmp_path):
    """lpv #23: Actions pages 404 for logged-out evaluators, so the run id is text."""
    site = tmp_path / "site"
    site_tool.build(
        site, reports_for(tmp_path, "one", 12, "FAIL"), run("100", "2026-11-02T00:00:00Z")
    )
    site_tool.build(
        site, reports_for(tmp_path, "two", 19, "PASS"), run("200-2", "2026-11-03T00:00:00Z")
    )
    actions = f"https://github.com/{REPO}/actions/runs/"
    index = (site / "index.html").read_text()
    assert "CI run 200-2</td>" in index and actions not in index
    assert "CI run 100" not in index
    history = (site / "unitree-g1/stock-mujoco-hallway/index.html").read_text()
    for run_id, ci in (("100", "100"), ("200-2", "200")):
        assert f'<a href="runs/{run_id}/">{run_id}</a>' in history
        assert f"<td>CI run {run_id}</td>" in history and actions not in history
    local_links_resolve(site)


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
    verifications = {n for n, j in jobs.items() if "walk-hallway.yml" in str(j.get("uses"))}
    assert set(jobs["site"]["needs"]) == verifications
    for name in ("site", "pages"):
        assert jobs[name]["if"] == (
            "github.ref == 'refs/heads/main' && (github.event_name == 'push' || "
            "(github.event_name == 'workflow_dispatch' && inputs.reset-history))"
        )
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
        report["source_bundle"] = {"results": [result]}
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
    monkeypatch.setattr(site_tool, "ci_run", lambda r: f'<a href="{r["url"]}">CI run</a>')
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
    restamp(directory)


def _renderable_reports(tmp_path: Path) -> Path:
    reports = tmp_path / "renderable"
    sample = tmp_path / "sample-report"
    _renderable_bundle(sample)
    for target in site_tool.targets():
        destination = reports / target["artifact"]
        destination.mkdir(parents=True)
        for name in (*site_tool.REPORT_FILES, "SHA256SUMS"):
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
    assert "Evidence kept for 3 days" in page and "Evidence kept for 7 days" not in page
    assert f"src='{video}'" not in page and f"href='{video}'" not in page
    assert "artifact" not in page.lower() and "/actions/" not in page
    record = json.loads((folder / "run.json").read_text())
    assert record["evidence"][0]["artifact"]["id"] == "101"
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
