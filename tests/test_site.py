# SPDX-License-Identifier: Apache-2.0
"""Pages site assembly (tools/site.py) and the publishing jobs in verify.yml."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import tarfile
import zipfile

import pytest
import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("site_tool", ROOT / "tools/site.py")
site_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_tool)
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())
REPO = "Living-Proof-Labs/livingproof-verification"


def write_reports(directory: Path, passed: int, grade: str):
    directory.mkdir(parents=True)
    report = {
        "summaries": [
            {
                "capability": "walk-hallway",
                "system": {"id": "unitree-g1/stock"},
                "grade": grade,
                "k": passed,
                "n": 20,
                "required_passes": 18,
            }
        ]
    }
    (directory / "report.json").write_text(json.dumps(report))
    for name in ("report.html", "report.md", "report.txt"):
        (directory / name).write_text(f"{name} {passed}")
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
    reports = tmp_path / name
    write_reports(
        reports / "unitree-g1-stock-mujoco-hallway-capability-report-stock", passed, grade
    )
    return reports


def local_links_resolve(site: Path):
    for page in site.rglob("*.html"):
        if page.name == "report.html":
            continue
        for href in re.findall(r'(?:href|src)="([^"]+)"', page.read_text()):
            if href.startswith("https://"):
                continue
            target = (page.parent / href).resolve()
            if href.endswith("/"):
                target = target / "index.html"
            assert target.is_file(), (page.relative_to(site), href)


def test_targets_follow_the_locks_and_the_workflow_artifact_names():
    (target,) = site_tool.targets()
    assert (target["robot"], target["variant"]) == ("unitree-g1", "stock-mujoco-hallway")
    job = WORKFLOW["jobs"][target["id"]]
    assert target["artifact"] == job["with"]["artifact-prefix"] + "capability-report-stock"


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
    assert (base / "latest/report.md").read_text() == "report.md 19"
    index = (site / "index.html").read_text()
    assert "19/20 passed" in index and "history (2 runs)" in index
    history = (base / "index.html").read_text()
    assert history.index("runs/200/") < history.index("runs/100/")
    latest = (base / "latest/index.html").read_text()
    for link in (
        "actions/runs/200",
        f"{REPO}/commit/{'a' * 40}",
        "capability-verifier/releases/tag/v1.9.0",
        "livingproof-capability-report/releases/tag/v2.0.0",
        "livingproof-unitree-g1/tree/f3447e14ab7f70828d0cf769c7b6f7e512f89150",
    ):
        assert link in latest
    local_links_resolve(site)


def test_index_and_history_link_runs_to_ci(tmp_path):
    site = tmp_path / "site"
    site_tool.build(
        site, reports_for(tmp_path, "one", 12, "FAIL"), run("100", "2026-11-02T00:00:00Z")
    )
    site_tool.build(
        site, reports_for(tmp_path, "two", 19, "PASS"), run("200-2", "2026-11-03T00:00:00Z")
    )
    actions = f"https://github.com/{REPO}/actions/runs/"
    index = (site / "index.html").read_text()
    assert f'<a href="{actions}200">CI run 200-2</a>' in index
    assert f"{actions}100" not in index
    history = (site / "unitree-g1/stock-mujoco-hallway/index.html").read_text()
    for run_id, ci in (("100", "100"), ("200-2", "200")):
        assert f'<a href="runs/{run_id}/">{run_id}</a>' in history
        assert f'<a href="{actions}{ci}">CI run {run_id}</a>' in history
    local_links_resolve(site)


def test_reports_are_copied_unchanged_and_checked(tmp_path):
    reports = reports_for(tmp_path, "one", 12, "FAIL")
    artifact = reports / "unitree-g1-stock-mujoco-hallway-capability-report-stock"
    (artifact / "report.html").write_text("tampered")
    with pytest.raises(site_tool.SiteError, match="SHA256SUMS"):
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
    restored = tmp_path / "restored"
    assert site_tool.restore(restored, REPO, "main", 200, "t", dry_run=True)["id"] == 7
    assert not any(restored.iterdir()) and len(requested) == 1
    site_tool.restore(restored, REPO, "main", 200, "t")
    site_tool.build(
        restored, reports_for(tmp_path, "two", 19, "PASS"), run("200", "2026-11-03T06:00:00Z")
    )
    base = restored / "unitree-g1/stock-mujoco-hallway"
    assert sorted(p.name for p in (base / "runs").iterdir()) == ["100", "200"]
    assert json.loads((base / "latest/run.json").read_text())["run"]["id"] == "200"


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


def test_build_writes_the_history_marker(tmp_path):
    site = tmp_path / "site"
    site_tool.build(
        site, reports_for(tmp_path, "one", 12, "FAIL"), run("1", "2026-11-02T00:00:00Z")
    )
    site_tool.build(
        site, reports_for(tmp_path, "two", 19, "PASS"), run("2", "2026-11-03T00:00:00Z")
    )
    assert json.loads((site / "site.json").read_text()) == {
        "runs": 2,
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
    assert (folder / video["files"][0]["path"]).read_bytes() == b"pass video"
    page = (folder / "index.html").read_text()
    assert f'href="{ART}/101"' in page and "available until 2026-12-02T08:54:02Z" in page
    assert f'href="evidence/{PREFIX}-mcap-stock/trials.mcap"' in page
    assert (site / "unitree-g1/stock-mujoco-hallway/latest" / video["files"][0]["path"]).is_file()
    local_links_resolve(site)


def test_expired_actions_artifacts_are_labelled_not_linked(tmp_path):
    """#11: after expiry no page keeps a live-looking link to the Actions artifact."""
    evidence, artifacts = evidence_for(tmp_path)
    site = tmp_path / "site"
    site_tool.build(
        site,
        reports_for(tmp_path, "r1", 12, "FAIL"),
        run("7", "2026-11-02T00:00:00Z"),
        evidence=evidence,
        artifacts=artifacts,
    )
    # A later publish (after both artifacts expired) re-renders the restored run.
    site_tool.build(site, reports_for(tmp_path, "r2", 15, "FAIL"), run("8", "2026-12-03T00:00:00Z"))
    page = (site / "unitree-g1/stock-mujoco-hallway/runs/7/index.html").read_text()
    assert ART not in page
    assert page.count("no longer available") == 2
    assert "expired 2026-12-02T08:54:02Z" in page and "sha256:" + "1" * 64 in page
    assert f'href="evidence/{PREFIX}-mcap-stock/trials.mcap"' in page  # the copy remains
    newer = (site / "unitree-g1/stock-mujoco-hallway/runs/8/index.html").read_text()
    assert "was not copied to this site" in newer
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
    assert f'href="{ART}/101"' not in page and "expiry unknown, not linked" in page


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
