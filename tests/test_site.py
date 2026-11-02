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
            "github.event_name == 'push' && github.ref == 'refs/heads/main'"
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
