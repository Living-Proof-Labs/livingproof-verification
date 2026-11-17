# SPDX-License-Identifier: Apache-2.0
"""Publication rejects stale/custom proposals without executing their code."""

import base64
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import bundle_proposal as writer  # noqa: E402

MAIN = "a" * 40
HEAD = "b" * 40
BRANCH = "renovate/living-proof-labs-livingproof-capability-report-3.x"


@pytest.fixture
def metadata():
    return {
        "state": "open",
        "draft": False,
        "user": {"login": "renovate[bot]"},
        "base": {"ref": "main", "repo": {"full_name": writer.REPO}},
        "head": {"sha": HEAD, "ref": BRANCH, "repo": {"full_name": writer.REPO}},
    }


@pytest.fixture
def remote(monkeypatch, metadata):
    state = {"main": MAIN, "author": "renovate[bot]", "parents": [MAIN]}

    def api(path, data=None):
        assert data is None
        if path.startswith("pulls/"):
            return metadata
        if path == "git/ref/heads/main":
            return {"object": {"sha": state["main"]}}
        if path.startswith("commits/"):
            return {
                "author": {"login": state["author"]},
                "parents": [{"sha": s} for s in state["parents"]],
            }
        raise AssertionError(path)

    monkeypatch.setattr(writer, "api", api)
    return state


@pytest.mark.parametrize("bad", ["head", "main", "fork", "branch", "author", "custom", "closed"])
def test_refuse_ineligible_proposal(metadata, remote, bad):
    if bad == "head":
        metadata["head"]["sha"] = "c" * 40
    elif bad == "main":
        remote["main"] = "c" * 40
    elif bad == "fork":
        metadata["head"]["repo"]["full_name"] = "attacker/repo"
    elif bad == "branch":
        metadata["head"]["ref"] = "main"
    elif bad == "author":
        remote["author"] = "custom-author"
    elif bad == "custom":
        remote["parents"] = ["c" * 40]
    else:
        metadata["state"] = "closed"
    with pytest.raises(ValueError):
        writer.proposal(107, HEAD, MAIN)


def test_accept_single_bot_commit(metadata, remote):
    assert writer.proposal(107, HEAD, MAIN) == metadata


@pytest.fixture
def checkout(tmp_path):
    for p in writer.bundle.paths(ROOT):
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / p, tmp_path / p)
    return tmp_path


@pytest.mark.parametrize("custom", [False, True])
def test_parse_reporter_as_data_only(checkout, monkeypatch, custom):
    path = "verifications/settings.toml"
    current = writer.bundle.lock.load_reporter_setting(checkout / path)["tag"]
    text = (checkout / path).read_text().replace(f'tag = "{current}"', 'tag = "v3.99.0"')
    if custom:
        text += "# custom work\n"

    def command(*args, **kwargs):
        if args[1] == "diff":
            return path
        if args[1] == "ls-tree":
            return "100644 blob hash file"
        if args[1] == "show":
            return text.rstrip("\n")
        raise AssertionError(args)

    monkeypatch.setattr(writer, "command", command)
    if custom:
        with pytest.raises(ValueError, match="custom edits"):
            writer.inputs(checkout, HEAD, BRANCH)
    else:
        assert writer.inputs(checkout, HEAD, BRANCH) == {"reporter": "v3.99.0"}


def plan():
    return {
        "repository": writer.REPO,
        "pr": 107,
        "main": MAIN,
        "head": HEAD,
        "branch": BRANCH,
        "selection": {"reporter": "v3.11.3"},
        "files": {"verifications/settings.toml": base64.b64encode(b"data").decode()},
    }


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        ".github/workflows/evil.yml",
        "tools/site.py",
        "verifications/unitree-g1-stock-mujoco-hallway.toml",
    ],
)
def test_publication_path_allowlist(checkout, path):
    candidate = plan()
    candidate["files"][path] = base64.b64encode(b"bad").decode()
    with pytest.raises(ValueError, match="unexpected files"):
        writer.validate_plan(checkout, candidate)


def test_artifact_tampering_stops_before_api(checkout, monkeypatch):
    path = checkout / "candidate.json"
    path.write_text(json.dumps(plan()))

    def fail(*args):
        raise AssertionError("must not call API")

    monkeypatch.setattr(writer, "api", fail)
    with pytest.raises(ValueError, match="digest"):
        writer.publish(checkout, path, "0" * 64)


def test_publication_is_exact_parent_and_expected_head_lease(checkout, monkeypatch, metadata):
    candidate = plan()
    path = checkout / "candidate.json"
    path.write_text(json.dumps(candidate))
    calls = []

    def api(route, data=None):
        calls.append((route, copy.deepcopy(data)))
        if route.startswith("git/commits/"):
            return {"tree": {"sha": "tree"}}
        return {"sha": "c" * 40}

    commands = []

    def command(*args, **kwargs):
        commands.append(args)
        return MAIN if args[1] == "rev-parse" else ""

    monkeypatch.setattr(writer, "api", api)
    monkeypatch.setattr(writer, "command", command)
    monkeypatch.setattr(writer, "proposal", lambda *args: metadata)
    writer.publish(checkout, path, hashlib.sha256(path.read_bytes()).hexdigest())
    assert dict(calls)["git/commits"]["parents"] == [HEAD]
    push = next(c for c in commands if "push" in c)
    assert f"--force-with-lease=refs/heads/{BRANCH}:{HEAD}" in push
    assert f"{'c' * 40}:refs/heads/{BRANCH}" in push
    assert not any("checkout" in c or "reset" in c for c in commands)


def test_discovery_skips_custom_proposals(monkeypatch):
    monkeypatch.setattr(
        writer,
        "api",
        lambda path: [{"number": 1, "head": {"sha": HEAD}}, {"number": 2, "head": {"sha": HEAD}}],
    )

    def proposal(number, head, main):
        if number == 1:
            raise ValueError("custom commit")
        return {"head": {"ref": BRANCH}}

    monkeypatch.setattr(writer, "command", lambda *args, **kwargs: MAIN)
    monkeypatch.setattr(writer, "inputs", lambda *args: {"reporter": "v3.99.0"})
    monkeypatch.setattr(writer, "proposal", proposal)
    assert writer.discover(MAIN) == {"pr": 2, "head": HEAD}


def test_dormant_template_separates_generation_and_write_credentials():
    import yaml

    path = ROOT / "tools/automation/bundle-completion.yml"
    template = yaml.safe_load(path.read_text())
    assert not (ROOT / ".github/workflows/bundle-completion.yml").exists()
    assert template["permissions"] == {"contents": "read"}
    assert template["concurrency"]["cancel-in-progress"] is False
    jobs = template["jobs"]
    assert set(jobs) == {"prepare", "publish"}
    assert "refs/heads/main" in jobs["prepare"]["if"]
    assert "secrets." not in json.dumps(jobs["prepare"])
    assert jobs["publish"]["needs"] == "prepare"
    for job in jobs.values():
        checkout = job["steps"][0]
        assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    writes = [s for s in jobs["publish"]["steps"] if "run" in s]
    assert len(writes) == 1
    assert "publish" in writes[0]["run"] and "prepare" not in writes[0]["run"]
    assert "pip" not in writes[0]["run"] and "pytest" not in writes[0]["run"]


@pytest.mark.parametrize("invalid", ["major", "malformed", "custom"])
def test_discovery_does_not_starve_valid_update(checkout, monkeypatch, invalid):
    later = "c" * 40
    candidates = [{"number": 1, "head": {"sha": HEAD}}, {"number": 2, "head": {"sha": later}}]
    monkeypatch.setattr(writer, "api", lambda path: candidates)
    monkeypatch.setattr(writer.bundle.lock, "repo_root", lambda: checkout)
    monkeypatch.setattr(writer, "proposal", lambda *args: {"head": {"ref": BRANCH}})
    path = "verifications/settings.toml"
    original = (checkout / path).read_text()
    current = writer.bundle.lock.load_reporter_setting(checkout / path)["tag"]
    good = original.replace(f'tag = "{current}"', 'tag = "v3.99.0"')
    bad = original.replace(
        f'tag = "{current}"', 'tag = "v4.0.0"' if invalid == "major" else 'tag = "main"'
    )
    if invalid == "custom":
        bad = good + "# custom change\n"
    calls = []

    def command(*args, **kwargs):
        calls.append(args)
        if args[1] == "rev-parse":
            return MAIN
        if args[1] == "fetch":
            return ""
        if args[1] == "diff":
            return path
        if args[1] == "ls-tree":
            return "100644 blob hash file"
        if args[1] == "show":
            return (bad if args[2].startswith(HEAD) else good).rstrip("\n")
        raise AssertionError(args)

    monkeypatch.setattr(writer, "command", command)
    assert writer.discover(MAIN) == {"pr": 2, "head": later}
    candidates.pop()
    assert writer.discover(MAIN) == {}
    assert not any("checkout" in call for call in calls)
