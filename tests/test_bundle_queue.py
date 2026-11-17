# SPDX-License-Identifier: Apache-2.0
"""Queue refusals are local simulations, never real-release automerge evidence."""

import copy
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
    rules = [
        {"type": "merge_queue"},
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [
                    {"context": name, "integration_id": queue.ACTIONS_APP} for name in queue.GATES
                ]
            },
        },
    ]
    runs = [
        {
            "id": i,
            "path": path,
            "head_sha": HEAD,
            "event": "pull_request",
            "head_repository": {"full_name": queue.writer.REPO},
            "pull_requests": [{"number": 1}],
            "status": "completed",
            "conclusion": "success",
        }
        for i, path in enumerate(sorted(queue.WORKFLOWS), 1)
    ]
    jobs = {
        i: [
            {"name": name, "head_sha": HEAD, "status": "completed", "conclusion": "success"}
            for name in names
        ]
        for i, names in [(1, ["checks", "renovate"]), (2, ["verification"])]
    }
    state = {"pr": pr, "rules": rules, "runs": runs, "jobs": jobs, "writes": []}

    def api(path):
        if path == "pulls/1":
            return pr
        if path == "rules/branches/main":
            return rules
        if path.startswith("actions/runs?"):
            return {"total_count": len(runs), "workflow_runs": runs}
        if "/jobs?" in path:
            selected = jobs[int(path.split("/")[2])]
            return {"total_count": len(selected), "jobs": selected}
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
    queue.enqueue(1, HEAD, branch, wait_seconds=0)
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
        queue.enqueue(1, HEAD, BRANCHES[0], wait_seconds=0)
    assert not remote["writes"]


@pytest.mark.parametrize(
    "bad", ["queue", "gate", "app", "missing", "skipped", "failed", "stale", "pending", "newer"]
)
def test_missing_or_unsuccessful_gates_refuse(remote, bad):
    if bad == "queue":
        remote["rules"].pop(0)
    if bad == "gate":
        remote["rules"][1]["parameters"]["required_status_checks"].pop()
    if bad == "app":
        remote["rules"][1]["parameters"]["required_status_checks"][0]["integration_id"] = None
    if bad == "missing":
        remote["jobs"][2].clear()
    if bad == "skipped":
        remote["jobs"][2][0]["conclusion"] = "skipped"
    if bad == "failed":
        remote["runs"][0]["conclusion"] = "failure"
    if bad == "stale":
        remote["runs"][0]["head_sha"] = "b" * 40
    if bad == "pending":
        remote["runs"][0]["status"] = "in_progress"
    if bad == "newer":
        run = copy.deepcopy(remote["runs"][0])
        run.update(id=3, conclusion="failure")
        remote["runs"].append(run)
    with pytest.raises(ValueError):
        queue.enqueue(1, HEAD, BRANCHES[0], wait_seconds=0)
    assert not remote["writes"]


def test_head_race_after_green_stops_before_mutation(remote, monkeypatch):
    original = queue.ready

    def ready(*args):
        result = original(*args)
        remote["pr"]["head"]["sha"] = "b" * 40
        return result

    monkeypatch.setattr(queue, "ready", ready)
    with pytest.raises(ValueError, match="head/target changed"):
        queue.enqueue(1, HEAD, BRANCHES[0], wait_seconds=0)
    assert not remote["writes"]


@pytest.mark.parametrize("result", ["success", "failure", "skipped", "cancelled"])
def test_aggregate_gate_runs_and_refuses_incomplete_pipeline(result):
    verify = yaml.safe_load((ROOT / ".github/workflows/verify.yml").read_text())
    gate = verify["jobs"]["verification"]
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {"locks"} | {
        name for name, job in verify["jobs"].items() if "uses" in job
    }
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
