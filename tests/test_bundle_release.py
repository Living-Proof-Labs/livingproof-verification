# SPDX-License-Identifier: Apache-2.0
"""Bundle releases (#131): the `release` job in verify.yml and the tag a run records.

Gating is checked three ways, none of which needs a hosted run:

* the workflow is parsed, so a pin, `needs`, permission or `if` that drifts fails here;
* the `if:` expressions are evaluated for every event and verification result, and the
  `verification` job's own check script is executed for every result a job can end with;
* the shared semantic-release engine is run in tests/bundle_release_rerun.test.mjs
  (CI job `bundle-release-engine`): a re-run on an already tagged commit, including an
  old tagged commit after main has moved on, publishes nothing and does not move or
  recreate the tag. docs/bundle-releases.md describes the cases; with
  LIVINGPROOF_NETWORK=1 the call and tag format are also compared with the pinned tag.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

import pytest
import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW_FILE = ROOT / ".github/workflows/verify.yml"
WORKFLOW = yaml.safe_load(WORKFLOW_FILE.read_text())
JOBS = WORKFLOW["jobs"]
SHARED = "Living-Proof-Labs/collaboration-guidelines/.github/workflows/semantic-release.yml"
REPO = "Living-Proof-Labs/livingproof-verification"
STATUS_FUNCTIONS = ("always()", "success()", "failure()", "cancelled()")

spec = importlib.util.spec_from_file_location("site_tool", ROOT / "tools/site.py")
site_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_tool)


def verification_jobs() -> list[str]:
    """Every job that calls a capability-verifier workflow (hallway and corner runs)."""
    return [
        name
        for name, job in JOBS.items()
        if any(f"/{file}@" in str(job.get("uses")) for file in ("walk-hallway.yml", "scenario.yml"))
    ]


def evaluate(expression: str, *, event: str, ref: str, needs: dict | None = None, **inputs):
    """Evaluate the small subset of GitHub expressions these `if:` lines use.

    Anything outside that subset raises, so a new construct needs this model extended
    on purpose rather than being silently treated as true.
    """
    text = " ".join(str(expression).split())
    text = re.sub(r"needs\.([\w-]+)\.result", lambda m: f"NEEDS[{m.group(1)!r}]", text)
    text = text.replace("github.event_name", "EVENT").replace("github.ref", "REF")
    text = text.replace("inputs.reset-history", "RESET")
    text = text.replace("!cancelled()", "True").replace("&&", " and ").replace("||", " or ")
    leftover = re.sub(
        r"NEEDS\[[^\]]+\]|EVENT|REF|RESET|True|and|or|==|!=|\(|\)|'[^']*'|\s", "", text
    )
    assert leftover == "", f"unsupported expression {expression!r}: {leftover!r}"
    scope = {
        "NEEDS": needs or {},
        "EVENT": event,
        "REF": ref,
        "RESET": inputs.get("reset_history", False),
    }
    return bool(eval(text, {"__builtins__": {}}, scope))  # noqa: S307 - whitelisted above


MAIN = "refs/heads/main"
EVENTS = {
    "push to main": ("push", MAIN, True),
    "push to another branch": ("push", "refs/heads/feature", False),
    "pull request": ("pull_request", "refs/pull/7/merge", False),
    "merge group": ("merge_group", "refs/heads/gh-readonly-queue/main/pr-7-abc", False),
    "manual run on main": ("workflow_dispatch", MAIN, False),
    "manual run on a branch": ("workflow_dispatch", "refs/heads/feature", False),
}


def release_runs(event: str, ref: str, verification_result: str) -> bool:
    """Whether GitHub starts `release`: its `if` holds and `verification` succeeded.

    With no status function in the `if`, GitHub adds success(), which needs every job in
    `needs` to have succeeded. That is why the status functions are banned below.
    """
    job = JOBS["release"]
    return evaluate(job["if"], event=event, ref=ref) and verification_result == "success"


def test_release_calls_one_published_shared_tag():
    job = JOBS["release"]
    name, _, ref = job["uses"].partition("@")
    assert name == SHARED
    # A reviewed published tag: never a branch, never a bare commit, never a moving name.
    assert re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", ref), ref
    assert set(job) <= {"needs", "if", "permissions", "uses", "with", "secrets"}
    assert not job.get("with"), "the call uses the shared defaults: no dry-run, no draft"
    assert not job.get("secrets"), "the release uses the caller's GITHUB_TOKEN only"
    assert [name for name, j in JOBS.items() if SHARED in str(j.get("uses"))] == ["release"]


def test_release_waits_for_the_verification_job_only_and_writes_contents_only_there():
    assert WORKFLOW["permissions"] == {"contents": "read"}
    assert JOBS["release"]["needs"] == "verification"
    assert JOBS["release"]["permissions"] == {"contents": "write"}
    for name, job in JOBS.items():
        if name != "release":
            assert job.get("permissions", {}).get("contents") != "write", name


def test_release_condition_has_no_status_function():
    condition = str(JOBS["release"]["if"])
    assert condition == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    for function in STATUS_FUNCTIONS:
        assert function not in condition
    assert "needs." not in condition


@pytest.mark.parametrize("label", EVENTS)
def test_only_a_push_to_main_with_verification_passed_publishes(label):
    event, ref, on_main = EVENTS[label]
    for result in ("success", "failure", "cancelled", "skipped"):
        assert release_runs(event, ref, result) is (on_main and result == "success"), (
            label,
            result,
        )


def test_pull_requests_never_reach_the_release_job():
    for label in ("pull request", "merge group", "push to another branch"):
        event, ref, _ = EVENTS[label]
        assert evaluate(JOBS["release"]["if"], event=event, ref=ref) is False
    assert "pull_request" in WORKFLOW[True]  # PyYAML reads the key `on` as True
    assert "pull_request_target" not in WORKFLOW[True]


def verification_check_script() -> str:
    """The Python the `verification` job runs on its needs; YAML already removed the indent."""
    step = JOBS["verification"]["steps"][0]
    assert step["env"] == {"RESULTS": "${{ toJSON(needs) }}"}
    found = re.fullmatch(r"python3 - <<'PYCODE'\n(.*)\nPYCODE\n?", step["run"], re.S)
    assert found, step["run"]
    return found.group(1)


def run_check(results: dict) -> int:
    done = subprocess.run(
        [sys.executable, "-c", verification_check_script()],
        env={**os.environ, "RESULTS": json.dumps(results)},
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode


def test_the_verification_job_gates_on_every_job_it_needs():
    needed = JOBS["verification"]["needs"]
    assert set(verification_jobs()) <= set(needed)
    assert "locks" in needed
    assert JOBS["verification"]["if"] == "always()"  # it must run to fail on a failed need
    healthy = {name: {"result": "success", "outputs": {}} for name in needed}
    assert run_check(healthy) == 0
    for name in needed:
        for result in ("failure", "cancelled", "skipped"):
            broken = {**healthy, name: {"result": result, "outputs": {}}}
            assert run_check(broken) != 0, (name, result)
    assert run_check({}) != 0


@pytest.mark.parametrize("event", ["pull_request", "merge_group"])
def test_queue_gate_requires_exact_complete_evidence(event):
    # Execute the workflow's real check; this does not simulate GitHub queue admission.
    assert event in WORKFLOW[True]
    needed = JOBS["verification"]["needs"]
    healthy = {name: {"result": "success"} for name in needed}
    assert run_check(healthy) == 0
    for name in needed:
        assert run_check({key: value for key, value in healthy.items() if key != name}) != 0
        for bad in (
            {},
            None,
            {"result": "neutral"},
            {"result": "failure"},
            {"result": "cancelled"},
            {"result": "skipped"},
        ):
            assert run_check({**healthy, name: bad}) != 0
    assert run_check({**healthy, "unexpected": {"result": "success"}}) != 0
    for bad in (None, [], {}, "success"):
        assert run_check(bad) != 0


def test_a_failed_or_cancelled_verification_never_releases_for_any_failing_job():
    needed = JOBS["verification"]["needs"]
    healthy = {name: {"result": "success", "outputs": {}} for name in needed}
    for name in needed:
        for result in ("failure", "cancelled"):
            ended = "failure" if run_check({**healthy, name: {"result": result}}) else "success"
            assert release_runs("push", MAIN, ended) is False, (name, result)
    assert release_runs("push", MAIN, "success" if run_check(healthy) == 0 else "failure")


def site_if(event: str, ref: str, results: dict, release: str, reset: bool = False) -> bool:
    needs = {**results, "release": release}  # job name -> its result
    return evaluate(JOBS["site"]["if"], event=event, ref=ref, needs=needs, reset_history=reset)


def _release_rerun_step() -> dict:
    return next(
        step
        for step in JOBS["site"]["steps"]
        if step.get("name") == "Re-run reports and the site together for a release"
    )


def test_rolling_runs_are_not_tied_to_a_release_and_deploy_follows_reports():
    """Rolling report publication does not read the release outcome (#186).

    The site job still waits for `release` so a tag can be recorded, but its `if`
    does not. Deploy runs only with that publication: `pages` needs `site` and
    requires the site job to have succeeded, which is what #189 kept.
    """
    verifications = set(verification_jobs())
    assert len(verifications) >= 7
    assert set(JOBS["site"]["needs"]) == verifications | {"release"}
    condition = " ".join(str(JOBS["site"]["if"]).split())
    assert condition.startswith("!cancelled() && ")
    assert "needs.release" not in condition
    for name in verifications:
        assert f"needs.{name}.result == 'success'" in condition, name
    ordinary = next(
        step
        for step in JOBS["site"]["steps"]
        if step.get("name") == "Add this run and write the index pages"
    )
    assert "if" not in ordinary
    assert "tools/site.py build" in ordinary["run"]
    assert JOBS["pages"]["needs"] == "site"
    pages = " ".join(str(JOBS["pages"]["if"]).split())
    assert pages.startswith("!cancelled() && needs.site.result == 'success' && ")
    assert "always()" not in pages
    # The release re-run is a step, not a second way to publish without the reports.
    assert _release_rerun_step()["if"] == "needs.release.outputs.published == 'true'"


def test_a_release_reruns_reports_and_the_site_together():
    """A published release re-runs report publication in the site job, then pages deploys it.

    Both steps are the same publication. The re-run is skipped when the release
    did not publish, so a rolling run is not blocked and is not rebuilt twice.
    """
    step = _release_rerun_step()
    assert step["env"]["RELEASE_TAG"] == "${{ needs.release.outputs.git-tag }}"
    assert step["env"]["RELEASE_COMMIT"] == "${{ needs.release.outputs.commit }}"
    assert step["env"]["CREATED"] == "${{ steps.publish-reports.outputs.created }}"
    script = step["run"]
    assert 'test "$RELEASE_COMMIT" = "$GITHUB_SHA"' in script
    assert "tools/site.py snapshot-publication" in script
    assert "tools/site.py build" in script
    assert "tools/site.py require-preserved" in script
    publishers = [
        name
        for name, job in JOBS.items()
        if any(
            str(item.get("uses", "")).startswith("actions/deploy-pages@")
            for item in job.get("steps") or []
        )
    ]
    assert publishers == ["pages"]
    results = {name: "success" for name in verification_jobs()}
    assert site_if("push", MAIN, results, "success") is True
    assert pages_runs("push", MAIN, "success") is True
    for release in ("failure", "skipped", "cancelled"):
        assert site_if("push", MAIN, results, release) is True


def _pages_archive(site: Path) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        tar.add(site, arcname=".")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("artifact.tar", tar_buffer.getvalue())
    return zip_buffer.getvalue()


def _marker_site(root: Path, marker: bytes) -> None:
    report = root / "reports" / "unitree-g1" / "stock-mujoco-hallway" / "runs" / "9"
    report.mkdir(parents=True)
    (report / "report.md").write_bytes(marker)
    (root / "site.json").write_bytes(b'{"runs": 1, "marker": "' + marker + b'"}\n')


def _pages_artifact(identifier: int, run_id: int, created: str, url: str) -> dict:
    return {
        "id": identifier,
        "name": "github-pages",
        "created_at": created,
        "expired": False,
        "archive_download_url": url,
        "workflow_run": {"id": run_id, "head_branch": "main"},
    }


def test_rebuild_uses_the_last_successful_deploy_when_the_newest_failed(tmp_path, monkeypatch):
    """A newer upload whose pages job failed is not the snapshot.

    Restore unpacks the last successful deploy, and every byte under reports/
    and in site.json matches that deploy rather than the failed upload.
    """
    good = tmp_path / "good"
    failed = tmp_path / "failed"
    _marker_site(good, b"good-report-bytes")
    _marker_site(failed, b"failed-upload-bytes")
    archives = {
        "https://api.example/good/zip": _pages_archive(good),
        "https://api.example/failed/zip": _pages_archive(failed),
    }
    artifacts = [
        _pages_artifact(1, 10, "2026-11-01T00:00:00Z", "https://api.example/good/zip"),
        _pages_artifact(2, 20, "2026-11-02T00:00:00Z", "https://api.example/failed/zip"),
    ]
    conclusions = {10: "success", 20: "failure"}

    def request(url, token):
        if "/jobs" in url:
            run_id = int(url.split("/runs/")[1].split("/")[0])
            body = {"jobs": [{"name": "pages", "conclusion": conclusions[run_id]}]}
            return io.BytesIO(json.dumps(body).encode())
        return io.BytesIO(json.dumps({"artifacts": artifacts}).encode())

    def open_once(url, token):
        if url not in archives:
            raise AssertionError(url)
        return io.BytesIO(archives[url])

    monkeypatch.setattr(site_tool, "_request", request)
    monkeypatch.setattr(site_tool, "_open_once", open_once)
    restored = tmp_path / "restored"
    found = site_tool.restore(restored, REPO, "main", 30, "t")
    assert found["id"] == 1
    assert site_tool.publication_bytes(restored) == site_tool.publication_bytes(good)
    assert site_tool.publication_bytes(restored) != site_tool.publication_bytes(failed)
    blob = b"".join(site_tool.publication_bytes(restored).values())
    assert b"good-report-bytes" in blob
    assert b"failed-upload-bytes" not in blob


def test_an_expired_successful_deploy_is_not_replaced_by_an_older_upload():
    artifacts = [
        _pages_artifact(1, 10, "2026-11-01T00:00:00Z", "https://api.example/old/zip"),
        _pages_artifact(2, 20, "2026-11-02T00:00:00Z", "https://api.example/new/zip"),
    ]
    artifacts[1]["expired"] = True
    with pytest.raises(site_tool.SiteError, match="expired"):
        site_tool.deployed_pages_artifact(artifacts, "main", None, lambda _run: "success")


def test_release_rerun_preserves_every_byte_under_reports_and_site_json(tmp_path):
    """Re-running report publication and assembling docs must not change those bytes.

    The check is the one the release step runs (`require-preserved`): site.json
    and every file under reports/ are compared, and a single flipped byte fails.
    """
    import test_site

    site = tmp_path / "site"
    reports = test_site.reports_for(tmp_path, "reports", 12, "FAIL")
    record = test_site.run("100", "2026-11-02T00:00:00Z")
    site_tool.build(site, reports, record)
    before = site_tool.publication_bytes(site)
    assert "site.json" in before
    assert any(path.startswith("reports/") for path in before)

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.html").write_text("<p>manual</p>\n")
    assembly_spec = importlib.util.spec_from_file_location(
        "docs_assembly", ROOT / "tools/docs/assemble.py"
    )
    assembly = importlib.util.module_from_spec(assembly_spec)
    assembly_spec.loader.exec_module(assembly)
    assembly.assemble(docs, site)
    site_tool.require_publication_bytes(site, before)

    snapshot = tmp_path / "publication.json"
    site_tool.snapshot_publication(site, snapshot)
    site_tool.build(site, reports, record)
    site_tool.require_publication_bytes(site, before)
    site_tool.require_preserved_snapshot(site, snapshot)

    report = next(path for path in before if path.endswith("report.md"))
    (site / report).write_bytes(before[report] + b"\n")
    with pytest.raises(site_tool.SiteError, match="report.md"):
        site_tool.require_publication_bytes(site, before)
    (site / report).write_bytes(before[report])
    marker = site / "site.json"
    marker.write_bytes(before["site.json"] + b" ")
    with pytest.raises(site_tool.SiteError, match="site.json"):
        site_tool.require_publication_bytes(site, before)


def pages_runs(event: str, ref: str, site: str, reset: bool = False) -> bool:
    """Whether `pages` starts: not cancelled, the site job succeeded, and its own `if` holds.

    `!cancelled()` is modelled as true here. The condition must name it so a cancelled
    run does not deploy, and must name `needs.site.result` so a failed release earlier
    in the chain does not skip a site that already succeeded.
    """
    condition = " ".join(str(JOBS["pages"]["if"]).split())
    assert condition.startswith("!cancelled() && needs.site.result == 'success' &&")
    return evaluate(condition, event=event, ref=ref, needs={"site": site}, reset_history=reset)


def test_pages_deploys_a_successful_site_on_main_even_when_release_failed():
    assert pages_runs("push", MAIN, "success") is True
    assert pages_runs("workflow_dispatch", MAIN, "success", reset=True) is True
    assert pages_runs("workflow_dispatch", MAIN, "success", reset=False) is False
    for event, ref, _ in (
        EVENTS["pull request"],
        EVENTS["merge group"],
        EVENTS["push to another branch"],
    ):
        assert pages_runs(event, ref, "success") is False
    for site in ("failure", "skipped", "cancelled"):
        assert pages_runs("push", MAIN, site) is False, site


@pytest.mark.parametrize("release", ["success", "failure", "skipped", "cancelled"])
def test_evidence_publishes_after_any_release_outcome_when_verification_jobs_passed(release):
    results = {name: "success" for name in verification_jobs()}
    assert site_if("push", MAIN, results, release) is True


def test_evidence_is_unchanged_for_failed_cancelled_or_skipped_verification_jobs():
    for name in verification_jobs():
        for result in ("failure", "cancelled", "skipped"):
            results = {n: "success" for n in verification_jobs()} | {name: result}
            assert site_if("push", MAIN, results, "skipped") is False, (name, result)


def test_evidence_still_publishes_only_on_main_and_the_reset_run_has_no_release():
    results = {name: "success" for name in verification_jobs()}
    assert site_if("pull_request", "refs/pull/7/merge", results, "skipped") is False
    assert site_if("push", "refs/heads/feature", results, "skipped") is False
    assert site_if("workflow_dispatch", MAIN, results, "skipped", reset=False) is False
    assert site_if("workflow_dispatch", MAIN, results, "skipped", reset=True) is True
    assert release_runs("workflow_dispatch", MAIN, "success") is False


def test_cancelling_the_run_stops_both_the_release_and_the_publication():
    # `cancelled()` is run-level, so the model above cannot show it: assert the structure.
    # The release has no status function, so a cancelled verification skips it; the site job
    # opts out of success() only through `!cancelled()`, which is false once the run is cancelled.
    assert "cancelled" not in str(JOBS["release"]["if"])
    assert str(JOBS["site"]["if"]).startswith("!cancelled() && ")
    assert "always()" not in str(JOBS["site"]["if"])


def test_the_site_checkout_fetches_tags_so_the_run_can_resolve_its_bundle_tag():
    checkout = next(
        step
        for step in JOBS["site"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-tags"] is True
    assert checkout["with"]["persist-credentials"] is False
    build = next(s for s in JOBS["site"]["steps"] if "tools/site.py build" in s.get("run", ""))
    assert "--git-dir" not in build["run"]  # the checkout is the default repository


# --- the tag a run records ---------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "PATH": os.environ["PATH"],
        "HOME": str(cwd),
    }
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    git(root, "init", "-q", "--initial-branch=main")
    return root


def commit(root: Path, message: str) -> str:
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


def test_a_tag_on_the_runs_own_commit_is_recorded(repo):
    sha = commit(repo, "feat: first bundle")
    git(repo, "tag", "v1.0.0")
    assert site_tool.bundle_tag(sha, repo) == "v1.0.0"


def test_an_annotated_tag_resolves_to_its_commit(repo):
    sha = commit(repo, "feat: first bundle")
    git(repo, "tag", "-a", "v1.0.0", "-m", "release")
    assert git(repo, "rev-parse", "v1.0.0") != sha  # the tag object, not the commit
    assert site_tool.bundle_tag(sha, repo) == "v1.0.0"


def test_a_run_never_shows_the_tag_of_another_commit(repo):
    tagged = commit(repo, "feat: first bundle")
    git(repo, "tag", "v1.0.0")
    later = commit(repo, "docs: no release needed")
    assert site_tool.bundle_tag(later, repo) is None  # newest tag is not this run's
    assert site_tool.bundle_tag(tagged, repo) == "v1.0.0"
    earlier = git(repo, "rev-parse", "HEAD~1")
    assert earlier == tagged


def test_the_tag_that_appears_after_a_run_is_not_added_to_it(repo):
    sha = commit(repo, "feat: first bundle")
    assert site_tool.bundle_tag(sha, repo) is None
    git(repo, "tag", "v1.0.0")
    assert site_tool.bundle_tag(sha, repo) == "v1.0.0"  # only a later resolution sees it


def test_no_tags_and_unknown_commits_keep_the_commit_alone(repo):
    sha = commit(repo, "chore: nothing yet")
    assert site_tool.bundle_tag(sha, repo) is None
    assert site_tool.bundle_tag("f" * 40, repo) is None  # not in this checkout
    assert site_tool.bundle_tag("HEAD", repo) is None  # only a full commit hash names a run
    assert site_tool.bundle_tag(sha[:12], repo) is None
    assert site_tool.bundle_tag(sha, repo / "missing") is None


@pytest.mark.parametrize(
    "name", ["v1.0", "1.0.0", "v1.0.0-rc.1", "v01.0.0", "release-1", "v1.0.0+build", "vx.y.z"]
)
def test_only_plain_semver_tags_name_a_bundle(repo, name):
    sha = commit(repo, "feat: first bundle")
    git(repo, "tag", name)
    assert site_tool.bundle_tag(sha, repo) is None


def test_the_highest_version_wins_when_a_commit_has_several_tags(repo):
    sha = commit(repo, "feat: first bundle")
    for name in ("v1.9.0", "v1.10.0", "v1.2.0", "v1.0.0-rc.1"):
        git(repo, "tag", name)
    assert site_tool.bundle_tag(sha, repo) == "v1.10.0"


def test_a_branch_with_a_tag_like_name_is_not_a_tag(repo):
    sha = commit(repo, "feat: first bundle")
    git(repo, "branch", "v9.9.9")
    assert site_tool.bundle_tag(sha, repo) is None


def site_job_checkout(origin: Path, sha: str, dest: Path) -> Path:
    """What the `site` job has: actions/checkout at depth 1 with `fetch-tags: true`, detached."""
    dest.mkdir()
    git(dest, "init", "-q")
    git(dest, "remote", "add", "origin", origin.as_uri())
    git(
        dest, "fetch", "-q", "--no-tags", "--prune", "--depth=1", "origin",
        f"+{sha}:refs/remotes/origin/main", "+refs/tags/*:refs/tags/*",
    )  # fmt: skip
    git(dest, "checkout", "-q", "--detach", sha)
    assert git(dest, "rev-parse", "--is-shallow-repository") == "true"
    return dest


def test_the_shallow_detached_checkout_of_the_site_job_resolves_each_runs_own_tag(tmp_path, repo):
    """bundle_tag() must work in the checkout the hosted job really has, not only in a clone.

    One run per commit: a released commit, an untagged commit between two releases, an
    annotated release carrying a prerelease and a non-bundle tag, and a commit after the last
    release. The untagged commit must not borrow the later release's tag.
    """
    first = commit(repo, "feat: first bundle")
    git(repo, "tag", "v1.0.0")
    between = commit(repo, "chore: nothing released for this run")
    second = commit(repo, "fix: second bundle")
    git(repo, "tag", "-a", "v1.0.1", "-m", "release")
    git(repo, "tag", "v1.0.1-rc.1")
    git(repo, "tag", "latest")
    after = commit(repo, "Merge branch 'odd'")
    expected = {first: "v1.0.0", between: None, second: "v1.0.1", after: None}
    for sha, tag in expected.items():  # full history: a later release is not an earlier run's
        assert site_tool.bundle_tag(sha, repo) == tag, (sha[:7], tag)
    for number, (sha, tag) in enumerate(expected.items()):
        checkout = site_job_checkout(repo, sha, tmp_path / f"site-job-{number}")
        assert site_tool.bundle_tag(sha, checkout) == tag, (sha[:7], tag)
        assert site_tool.bundle_tag(git(checkout, "rev-parse", "HEAD"), checkout) == tag


def test_a_detached_head_is_judged_by_its_commit_not_by_a_nearby_tag(repo):
    tagged = commit(repo, "feat: first bundle")
    git(repo, "tag", "v1.0.0")
    git(repo, "checkout", "-q", "--detach")
    assert site_tool.bundle_tag(git(repo, "rev-parse", "HEAD"), repo) == "v1.0.0"
    child = commit(repo, "fix: on a detached head")
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert site_tool.bundle_tag(child, repo) is None  # the parent's tag is not the child's
    assert site_tool.bundle_tag(tagged, repo) == "v1.0.0"


def target() -> dict:
    return site_tool.targets()[0]


def run_record(tag: str | None) -> dict:
    return {
        "id": "100",
        "commit": "a" * 40,
        "created": "2026-11-18T06:00:00Z",
        "repository": REPO,
        "server": "https://github.com",
        "tag": tag,
    }


def test_provenance_names_the_tag_with_the_commit_only_when_given():
    tagged = site_tool.provenance(target(), run_record("v1.0.0"))
    assert tagged["commit"] == {
        "sha": "a" * 40,
        "url": f"https://github.com/{REPO}/commit/{'a' * 40}",
        "tag": "v1.0.0",
    }
    untagged = site_tool.provenance(target(), run_record(None))
    assert untagged["commit"] == {"sha": "a" * 40, "url": tagged["commit"]["url"]}
    assert "tag" not in untagged["commit"]
    legacy = {k: v for k, v in run_record(None).items() if k != "tag"}
    assert site_tool.provenance(target(), legacy) == untagged  # callers that never pass one


@pytest.mark.parametrize("bad", ["v1.0", "main", "v1.0.0-rc.1", "../v1.0.0", "v1.0.0 "])
def test_provenance_refuses_anything_but_a_plain_release_tag(bad):
    with pytest.raises(site_tool.SiteError, match="bundle tag"):
        site_tool.provenance(target(), run_record(bad))


def test_the_field_names_match_the_history_design_in_129():
    # #129 proposes run.json `commit: {sha, url, tag}`; the mock's README says the same.
    record = site_tool.provenance(target(), run_record("v1.2.3"))
    assert list(record["commit"]) == ["sha", "url", "tag"]
    assert record["run"]["id"] == "100"  # every run keeps its own unique id
    assert set(record) >= {"run", "commit", "robot", "releases", "definition"}


def test_history_shows_the_tag_or_the_short_sha_fallback():
    """#129: a recorded bundle tag is the release; otherwise the link text "commit" (#146)."""
    commit_url = f"https://github.com/{REPO}/commit/{'b' * 40}"
    plain = site_tool._release_html({"commit": {"sha": "b" * 40, "url": commit_url}})
    assert ">commit</a>" in plain
    assert f'href="{commit_url}"' in plain
    assert "b" * 8 not in re.sub(r"<[^>]+>", " ", plain)  # visible text only
    assert "is-fallback" in plain
    tagged = site_tool._release_html(
        {"commit": {"sha": "b" * 40, "url": commit_url, "tag": "v1.2.3"}}
    )
    assert ">v1.2.3</a>" in tagged
    assert "releases/tag/v1.2.3" in tagged
    assert "is-fallback" not in tagged
    assert "/actions/" not in tagged


def test_a_published_site_keeps_old_runs_untouched_when_a_later_run_has_a_tag(tmp_path):
    import test_site

    site = tmp_path / "site"
    old = test_site.run("1000", "2026-11-17T06:00:00Z")
    site_tool.build(site, test_site.reports_for(tmp_path, "r1", 1, "PASS"), old)
    first = {p: p.read_bytes() for p in site.rglob("run.json") if "/runs/1000/" in p.as_posix()}
    assert first and all("tag" not in json.loads(b)["commit"] for b in first.values())
    new = {**test_site.run("2000", "2026-11-18T06:00:00Z"), "tag": "v1.0.0"}
    site_tool.build(site, test_site.reports_for(tmp_path, "r2", 1, "PASS"), new)
    for path, original in first.items():
        assert path.read_bytes() == original  # no retro-tagging, no rewritten record
    tagged = [
        json.loads(p.read_text()) for p in site.rglob("run.json") if "/runs/2000/" in p.as_posix()
    ]
    assert tagged and all(r["commit"]["tag"] == "v1.0.0" for r in tagged)
    assert all(r["run"]["id"] == "2000" for r in tagged)
    history = next(site.glob("reports/*/*/index.html")).read_text()
    assert ">v1.0.0</a>" in history  # the new row shows the tag, not tag-and-SHA
    assert history.count("releases/tag/v1.0.0") >= 1
    assert 'class="release is-fallback"' in history
    assert ">commit</a>" in history  # the untagged older row says "commit", never a hash


def test_the_build_command_records_a_tag_only_from_the_checkout(tmp_path, repo):
    import test_site

    sha = commit(repo, "feat: first bundle")
    reports = test_site.reports_for(tmp_path, "r", 1, "PASS")

    def build(name: str, run_id: str, git_dir: Path) -> list[dict]:
        out = tmp_path / name
        done = subprocess.run(
            [
                sys.executable, str(ROOT / "tools/site.py"), "build", str(out),
                "--reports", str(reports), "--run-id", run_id, "--commit", sha,
                "--created", "2026-11-18T06:00:00Z", "--repository", REPO,
                "--git-dir", str(git_dir),
            ],
            capture_output=True, text=True, check=False, cwd=ROOT,
        )  # fmt: skip
        assert done.returncode == 0, done.stderr
        return [json.loads(p.read_text()) for p in out.rglob("run.json")]

    assert all("tag" not in r["commit"] for r in build("untagged", "1", repo))
    git(repo, "tag", "v1.0.0")
    records = build("tagged", "2", repo)
    assert records and all(r["commit"]["tag"] == "v1.0.0" for r in records)
    assert all(r["commit"]["sha"] == sha for r in records)
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "--initial-branch=main")
    assert all("tag" not in r["commit"] for r in build("elsewhere", "3", other))


# --- the shared workflow, checked against the pinned tag (network) ----------------------------


def pinned_ref() -> str:
    return JOBS["release"]["uses"].partition("@")[2]


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read().decode()


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
def test_the_pinned_tag_has_the_serialised_never_cancelled_main_only_job():
    base = (
        "https://raw.githubusercontent.com/Living-Proof-Labs/collaboration-guidelines/"
        f"{pinned_ref()}/.github"
    )
    shared = yaml.safe_load(fetch(f"{base}/workflows/semantic-release.yml"))
    job = shared["jobs"]["release"]
    assert job["if"] == "github.ref == 'refs/heads/main'"  # the shared job refuses other refs
    assert job["concurrency"]["cancel-in-progress"] is False  # one release at a time, no cancel
    assert job["permissions"] == {"contents": "write"}
    config = fetch(f"{base}/actions/semantic-release/release.config.mjs")
    assert "tagFormat: 'v${version}'" in config  # what site.BUNDLE_TAG accepts
