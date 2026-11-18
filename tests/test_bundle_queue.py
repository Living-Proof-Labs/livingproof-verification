# SPDX-License-Identifier: Apache-2.0
"""Queue refusals are local simulations, never real-release automerge evidence."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import bundle_queue as queue  # noqa: E402

HEAD = "a" * 40
BRANCHES = [
    "renovate/capability-verifier-evidence",
    "renovate/living-proof-labs-livingproof-capability-report-3.x",
]


@pytest.fixture
def remote(monkeypatch):
    pr = {
        "state": "open",
        "draft": False,
        "node_id": "PR_example",
        "user": {"login": "renovate[bot]"},
        "base": {"ref": "main", "repo": {"full_name": queue.writer.REPO}},
        "head": {"sha": HEAD, "ref": BRANCHES[0], "repo": {"full_name": queue.writer.REPO}},
    }
    state = {"pr": pr, "writes": [], "reads": [], "rules": [], "runs": []}

    def api(path):
        state["reads"].append(path)
        if path == "pulls/1":
            return pr
        if path == "rules/branches/main":
            return state["rules"]
        if path.startswith("actions/runs?"):
            return {"total_count": len(state["runs"]), "workflow_runs": state["runs"]}
        raise AssertionError(path)

    def command(*args, data=None):
        assert args == ("gh", "api", "graphql", "--input", "-")
        state["writes"].append(json.loads(data))
        return json.dumps({"data": {"enqueuePullRequest": {"mergeQueueEntry": {"id": "entry"}}}})

    monkeypatch.setattr(queue.writer, "api", api)
    monkeypatch.setattr(queue.writer, "command", command)
    return state


@pytest.mark.parametrize("branch", BRANCHES, ids=["verifier", "reporter"])
def test_each_release_path_requests_exact_head_normal_queue(remote, branch):
    remote["pr"]["head"]["ref"] = branch
    queue.enqueue(1, HEAD, branch)
    assert len(remote["writes"]) == 1
    request = remote["writes"][0]
    assert request["variables"] == {"id": "PR_example", "head": HEAD}
    assert "expectedHeadOid: $head" in request["query"]
    assert "jump: false" in request["query"]
    assert "enablePullRequestAutoMerge" not in request["query"]


@pytest.mark.parametrize("bad", ["head", "fork", "draft", "branch", "base", "closed"])
def test_target_changes_refuse_before_write(remote, bad):
    pr = remote["pr"]
    if bad == "head":
        pr["head"]["sha"] = "b" * 40
    if bad == "fork":
        pr["head"]["repo"]["full_name"] = "other/repo"
    if bad == "draft":
        pr["draft"] = True
    if bad == "branch":
        pr["head"]["ref"] = "custom"
    if bad == "base":
        pr["base"]["ref"] = "other"
    if bad == "closed":
        pr["state"] = "closed"
    with pytest.raises(ValueError):
        queue.enqueue(1, HEAD, BRANCHES[0])
    assert not remote["writes"]


@pytest.mark.parametrize("branch", BRANCHES, ids=["verifier", "reporter"])
@pytest.mark.parametrize("status", ["absent", "queued", "in_progress", "failure", "skipped"])
def test_no_required_checks_and_informational_runs_do_not_block(remote, branch, status):
    remote["pr"]["head"]["ref"] = branch
    remote["rules"] = [{"type": "merge_queue"}]
    remote["runs"] = [] if status == "absent" else [{"status": status}]
    queue.enqueue(1, HEAD, branch)
    assert len(remote["writes"]) == 1
    # No hidden status polling, rule enrollment prerequisite, or wait for optional CI.
    assert remote["reads"] == ["pulls/1"]


@pytest.mark.parametrize("reason", ["head moved", "required check pending", "queue unavailable"])
def test_server_refusal_never_falls_back_to_merge_or_retries(remote, monkeypatch, reason):
    calls = []

    def command(*args, data=None):
        request = json.loads(data)
        assert request["variables"]["head"] == HEAD
        assert "expectedHeadOid: $head" in request["query"]
        calls.append(request)
        return json.dumps({"errors": [{"message": reason}]})

    monkeypatch.setattr(queue.writer, "command", command)
    with pytest.raises(ValueError, match="queue refused"):
        queue.enqueue(1, HEAD, BRANCHES[0])
    assert len(calls) == 1
    assert "mergePullRequest" not in calls[0]["query"]


@pytest.mark.parametrize("head", ["main", "", "a" * 39])
def test_invalid_publication_head_refused(remote, head):
    with pytest.raises(ValueError, match="invalid trusted publication output"):
        queue.enqueue(1, head, BRANCHES[0])
    assert not remote["reads"] and not remote["writes"]


@pytest.mark.parametrize("result", ["success", "failure", "skipped", "cancelled"])
def test_aggregate_gate_runs_and_refuses_incomplete_pipeline(result):
    verify = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())
    gate = verify["jobs"]["verification"]
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {"locks"} | {
        name for name, job in verify["jobs"].items() if "uses" in job and name != "release"
    }  # the release job (#131) runs after this gate and cannot be one of its needs
    outcomes = {name: {"result": "success"} for name in gate["needs"]}
    outcomes["locks"]["result"] = result
    process = subprocess.run(
        ["bash", "-c", gate["steps"][0]["run"]],
        env={**os.environ, "RESULTS": json.dumps(outcomes)},
        capture_output=True,
    )
    assert (process.returncode == 0) == (result == "success")


def test_merge_group_and_scoped_refresh_preserve_boundaries():
    for name in ["checks", "verify"]:
        workflow = yaml.safe_load((ROOT / f".github/workflows/{name}.yml").read_text())
        assert workflow[True]["merge_group"] == {"types": ["checks_requested"]}
        assert "pull_request" in workflow[True]
    config = json.loads((ROOT / "renovate.json").read_text())
    assert config["automerge"] is False and config["platformAutomerge"] is False
    assert [r.get("rebaseWhen") for r in config["packageRules"]] == [
        "behind-base-branch",
        "behind-base-branch",
        None,
    ]
    assert not (ROOT / ".github/workflows/bundle-completion.yml").exists()
