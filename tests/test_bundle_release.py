# SPDX-License-Identifier: Apache-2.0
"""Bundle releases (#131): the `release` job in verify.yml and the tag a run records.

Gating is checked three ways, none of which needs a hosted run:

* the workflow is parsed, so a pin, `needs`, permission or `if` that drifts fails here;
* the `if:` expressions are evaluated for every event and verification result, and the
  `verification` job's own check script is executed for every result a job can end with;
* the shared semantic-release workflow's side of the contract (it never moves or recreates
  a tag) is documented in docs/bundle-releases.md and, with LIVINGPROOF_NETWORK=1, compared
  with the pinned tag.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request

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


def test_site_waits_for_the_release_but_never_depends_on_its_outcome():
    verifications = set(verification_jobs())
    assert len(verifications) >= 7
    assert set(JOBS["site"]["needs"]) == verifications | {"release"}
    condition = str(JOBS["site"]["if"])
    assert "!cancelled()" in condition
    assert "needs.release" not in condition  # a failed, skipped or empty release never blocks
    for name in verifications:
        assert f"needs.{name}.result == 'success'" in condition, name
    assert JOBS["pages"]["needs"] == "site"
    assert not JOBS["pages"].get("if", "").count("always")


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


def test_history_shows_the_tag_then_the_commit_and_only_the_commit_without_one():
    commit_url = f"https://github.com/{REPO}/commit/{'b' * 40}"
    plain = site_tool.commit_cell({"sha": "b" * 40, "url": commit_url})
    assert plain == f'<a href="{commit_url}">{"b" * 12}</a>'
    tagged = site_tool.commit_cell({"sha": "b" * 40, "url": commit_url, "tag": "v1.2.3"})
    assert tagged == (
        f'<a href="https://github.com/{REPO}/releases/tag/v1.2.3">v1.2.3</a> · '
        f'<a href="{commit_url}">{"b" * 12}</a>'
    )
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
    history = next(site.glob("*/*/index.html")).read_text()
    assert "v1.0.0</a> · " in history  # the new row names the tag and its commit
    assert history.count("releases/tag/v1.0.0") >= 1
    assert ("a" * 12) in history  # old and new rows still show their commit


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
