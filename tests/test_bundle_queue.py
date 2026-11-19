# SPDX-License-Identifier: Apache-2.0
"""Offline admission/refusal and recovery simulations; no hosted activation evidence."""

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

HEAD, MAIN = "a" * 40, "b" * 40
BRANCHES = [
    "renovate/capability-verifier-evidence",
    "renovate/living-proof-labs-livingproof-capability-report-3.x",
]


@pytest.fixture
def remote(monkeypatch):
    record = {"pr": 1, "head": HEAD, "main": MAIN, "branch": BRANCHES[0]}
    pr = {
        "state": "open",
        "draft": False,
        "node_id": "PR_example",
        "user": {"login": "renovate[bot]"},
        "base": {"ref": "main", "repo": {"full_name": queue.writer.REPO}},
        "head": {"sha": HEAD, "ref": BRANCHES[0], "repo": {"full_name": queue.writer.REPO}},
    }
    rules = [
        {"type": "merge_queue", "ruleset_id": 1, "parameters": {"grouping_strategy": "ALLGREEN"}},
        {
            "type": "required_status_checks",
            "ruleset_id": 1,
            "parameters": {
                "strict_required_status_checks_policy": True,
                "do_not_enforce_on_create": False,
                "required_status_checks": [
                    {"context": name, "integration_id": 15368}
                    for name in ["checks", "bundle-release-engine", "renovate", "verification"]
                ],
            },
        },
    ]
    runs, checks = [], []
    # Independent fixture contract: do not derive evidence from the implementation.
    workflows = {
        ".github/workflows/checks.yml": ("checks", "bundle-release-engine", "renovate"),
        ".github/workflows/verify.yml": ("verification",),
    }
    for index, (path, names) in enumerate(workflows.items(), 1):
        runs.append(
            {
                "id": index,
                "run_attempt": 1,
                "path": path,
                "head_sha": HEAD,
                "event": "pull_request",
                "status": "completed",
                "conclusion": "success",
                "check_suite_id": index,
                "repository": {"full_name": queue.writer.REPO},
                "head_repository": {"full_name": queue.writer.REPO},
                "pull_requests": [{"number": 1, "head": {"sha": HEAD}, "base": {"sha": MAIN}}],
            }
        )
        for name in names:
            checks.append(
                {
                    "id": len(checks) + 101,
                    "name": name,
                    "head_sha": HEAD,
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"id": 15368},
                    "check_suite": {"id": index},
                }
            )
    state = {
        "pr": pr,
        "record": record,
        "writes": [],
        "rules": rules,
        "runs": runs,
        "checks": checks,
        "main": MAIN,
        "queued": None,
        "uncertain": False,
        "detail": {"enforcement": "active", "bypass_actors": []},
    }

    def api(path):
        if path == "pulls/1":
            return pr
        if path == "git/ref/heads/main":
            return {"object": {"sha": state["main"]}}
        raise AssertionError(path)

    def read(path):
        if path == "rules/branches/main":
            return state["rules"]
        if path == "rulesets/1":
            return state["detail"]
        if path.startswith("actions/runs?"):
            return {
                "total_count": state.get("total_runs", len(state["runs"])),
                "workflow_runs": state["runs"],
            }
        if path.startswith("actions/runs/"):
            run_id = int(path.split("/")[-1])
            return next(run for run in state["runs"] if run["id"] == run_id)
        if path.startswith("commits/"):
            return {"total_count": len(state["checks"]), "check_runs": state["checks"]}
        raise AssertionError(path)

    def command(*args, data=None):
        request = json.loads(data)
        if request["query"].startswith("query"):
            return json.dumps(
                {
                    "data": {
                        "node": {
                            "headRefOid": pr["head"]["sha"],
                            "mergeQueueEntry": state["queued"],
                        }
                    }
                }
            )
        state["writes"].append(request)
        state["queued"] = {"id": "entry"}
        if state["uncertain"]:
            raise TimeoutError("response lost after accepted enqueue")
        return json.dumps({"data": {"enqueuePullRequest": {"mergeQueueEntry": state["queued"]}}})

    monkeypatch.setattr(queue.writer, "api", api)
    monkeypatch.setattr(queue.writer, "command", command)
    state["read"] = read
    return state


def enqueue(remote):
    queue.enqueue(remote["record"], remote["read"])


@pytest.mark.parametrize("branch", BRANCHES)
def test_success_uses_atomic_head_and_normal_queue(remote, branch):
    remote["pr"]["head"]["ref"] = remote["record"]["branch"] = branch
    enqueue(remote)
    assert len(remote["writes"]) == 1
    request = remote["writes"][0]
    assert request["variables"] == {"id": "PR_example", "head": HEAD}
    assert "expectedHeadOid: $head" in request["query"] and "jump: false" in request["query"]
    assert "mergePullRequest" not in request["query"]


@pytest.mark.parametrize("status", ["failure", "skipped", "cancelled", "neutral", None])
@pytest.mark.parametrize("kind", ["runs", "checks"])
def test_unsuccessful_evidence_never_writes(remote, status, kind):
    remote[kind][0]["conclusion"] = status
    with pytest.raises(ValueError):
        enqueue(remote)
    assert not remote["writes"]


@pytest.mark.parametrize(
    "bad",
    ["missing", "duplicate", "pending", "stale", "base", "fork", "event", "path", "truncated"],
)
def test_workflow_evidence_refuses(remote, bad):
    run = remote["runs"][0]
    if bad == "missing":
        remote["runs"].clear()
    if bad == "duplicate":
        remote["runs"].append(copy.deepcopy(run))
    if bad == "pending":
        run["status"] = "in_progress"
    if bad == "stale":
        run["head_sha"] = MAIN
    if bad == "base":
        run["pull_requests"][0]["base"]["sha"] = HEAD
    if bad == "fork":
        run["head_repository"]["full_name"] = "other/repo"
    if bad == "event":
        run["event"] = "workflow_dispatch"
    if bad == "path":
        run["path"] = ".github/workflows/impostor.yml"
    if bad == "truncated":
        remote["total_runs"] = 101
    with pytest.raises(ValueError):
        enqueue(remote)
    assert not remote["writes"]


@pytest.mark.parametrize("bad", ["missing", "duplicate", "app", "suite", "stale", "pending"])
def test_check_provenance_refuses(remote, bad):
    check = remote["checks"][0]
    if bad == "missing":
        remote["checks"].clear()
    if bad == "duplicate":
        remote["checks"].append(copy.deepcopy(check))
    if bad == "app":
        check["app"]["id"] = 999
    if bad == "suite":
        check["check_suite"]["id"] = 999
    if bad == "stale":
        check["head_sha"] = MAIN
    if bad == "pending":
        check["status"] = "queued"
    with pytest.raises(ValueError):
        enqueue(remote)
    assert not remote["writes"]


@pytest.mark.parametrize(
    "bad",
    [
        "absent",
        "queue",
        "strict",
        "integration",
        "context",
        "bypass",
        "unknown_bypass",
        "inactive",
        "not_allgreen",
    ],
)
def test_missing_enforcement_never_writes(remote, bad):
    if bad == "absent":
        remote["rules"].pop()
    if bad == "queue":
        remote["rules"].pop(0)
    if bad == "strict":
        remote["rules"][1]["parameters"]["strict_required_status_checks_policy"] = False
    if bad == "integration":
        remote["rules"][1]["parameters"]["required_status_checks"][0]["integration_id"] = None
    if bad == "context":
        remote["rules"][1]["parameters"]["required_status_checks"].pop()
    if bad == "bypass":
        remote["detail"]["bypass_actors"] = [{"actor_id": 1}]
    if bad == "unknown_bypass":
        del remote["detail"]["bypass_actors"]
    if bad == "inactive":
        remote["detail"]["enforcement"] = "evaluate"
    if bad == "not_allgreen":
        remote["rules"][0]["parameters"]["grouping_strategy"] = "HEADGREEN"
    with pytest.raises(ValueError):
        enqueue(remote)
    assert not remote["writes"]


def test_inaccessible_evidence_never_writes(remote):
    def denied(path):
        raise PermissionError("denied")

    remote["read"] = denied
    with pytest.raises(PermissionError):
        enqueue(remote)
    assert not remote["writes"]


@pytest.mark.parametrize("race", ["head", "base", "protections"])
def test_race_during_evidence_collection_refuses(remote, race):
    read = remote["read"]

    def racing(path):
        result = read(path)
        if path.startswith("commits/"):
            if race == "head":
                remote["pr"]["head"]["sha"] = MAIN
            if race == "base":
                remote["main"] = HEAD
            if race == "protections":
                remote["rules"].clear()
        return result

    remote["read"] = racing
    with pytest.raises(ValueError):
        enqueue(remote)
    assert not remote["writes"]


def test_failed_ci_then_successful_retry_uses_same_completion(remote):
    remote["runs"][0]["conclusion"] = "failure"
    with pytest.raises(ValueError):
        enqueue(remote)
    remote["runs"][0]["conclusion"] = "success"
    enqueue(remote)
    assert len(remote["writes"]) == 1


def test_interrupted_enqueue_is_inspected_before_retry(remote):
    remote["uncertain"] = True
    with pytest.raises(TimeoutError):
        enqueue(remote)
    enqueue(remote)
    assert len(remote["writes"]) == 1


def test_merge_group_failure_server_refusal_has_no_fallback(remote, monkeypatch):
    original = queue.writer.command

    def refused(*args, data=None):
        if json.loads(data)["query"].startswith("query"):
            return original(*args, data=data)
        remote["writes"].append(json.loads(data))
        return json.dumps({"errors": [{"message": "required merge-group check failed"}]})

    monkeypatch.setattr(queue.writer, "command", refused)
    with pytest.raises(ValueError, match="queue refused"):
        enqueue(remote)
    assert len(remote["writes"]) == 1


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
        "behind-base-branch",
        None,
    ]
    assert not (ROOT / ".github/workflows/bundle-completion.yml").exists()


@pytest.mark.parametrize("bad", ["head", "fork", "draft", "branch", "base", "closed"])
def test_target_changes_refuse_before_mutation(remote, bad):
    pr = remote["pr"]
    if bad == "head":
        pr["head"]["sha"] = MAIN
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
        enqueue(remote)
    assert not remote["writes"]


def test_unknown_queue_state_refuses(remote, monkeypatch):
    monkeypatch.setattr(
        queue.writer,
        "command",
        lambda *args, **kwargs: json.dumps({"data": {"node": {"headRefOid": HEAD}}}),
    )
    with pytest.raises(ValueError, match="uncertain"):
        enqueue(remote)
    assert not remote["writes"]


def test_pending_checks_poll_without_republication(remote, monkeypatch):
    monkeypatch.setattr(queue.admission, "load_receipt", lambda *args: remote["record"])
    monkeypatch.setattr(queue.admission, "public_read", remote["read"])
    remote["runs"][0].update(status="in_progress", conclusion=None)
    waits = []

    def finish(seconds):
        assert not remote["writes"]
        waits.append(seconds)
        remote["runs"][0].update(status="completed", conclusion="success")

    monkeypatch.setattr(queue.time, "sleep", finish)
    queue.wait_for_checks(Path("receipt"), "digest")
    assert waits == [60] and len(remote["writes"]) == 1


def test_polling_is_bounded_and_never_retries_unknown_mutation(remote, monkeypatch):
    monkeypatch.setattr(queue.admission, "load_receipt", lambda *args: remote["record"])
    monkeypatch.setattr(queue.admission, "public_read", remote["read"])
    saved = remote["runs"]
    remote["runs"] = []
    waits = []
    monkeypatch.setattr(queue.time, "sleep", lambda seconds: waits.append(seconds))
    with pytest.raises(ValueError, match="wait exhausted"):
        queue.wait_for_checks(Path("receipt"), "digest")
    assert len(waits) == 24 and not remote["writes"]
    remote["runs"] = saved
    remote["uncertain"] = True
    with pytest.raises(TimeoutError):
        queue.wait_for_checks(Path("receipt"), "digest")
    assert len(remote["writes"]) == 1 and len(waits) == 24


@pytest.mark.parametrize("value", [True, None])
def test_checks_must_explicitly_enforce_on_create(remote, value):
    parameters = remote["rules"][1]["parameters"]
    if value is None:
        del parameters["do_not_enforce_on_create"]
    else:
        parameters["do_not_enforce_on_create"] = value
    with pytest.raises(ValueError):
        enqueue(remote)
    assert not remote["writes"]


@pytest.mark.parametrize(
    "status,conclusion", [("in_progress", None), ("completed", "failure"), ("completed", "success")]
)
@pytest.mark.parametrize("replacement", ["attempt", "run"])
def test_superseding_ci_after_snapshot_never_enqueues(remote, status, conclusion, replacement):
    read = remote["read"]
    changed = False

    def superseding(path):
        nonlocal changed
        if path == "rules/branches/main" and not changed:
            changed = True
            run = remote["runs"][0]
            run.update(status=status, conclusion=conclusion)
            if replacement == "attempt":
                run["run_attempt"] += 1
            else:
                run["id"] += 10
        return read(path)

    remote["read"] = superseding
    with pytest.raises(ValueError):
        enqueue(remote)
    assert changed and not remote["writes"]


@pytest.mark.parametrize(
    "status,conclusion", [("in_progress", None), ("completed", "failure"), ("completed", "success")]
)
def test_current_run_read_detects_attempt_superseding_final_collection(remote, status, conclusion):
    read = remote["read"]

    def superseding(path):
        if path == "actions/runs/1":
            remote["runs"][0].update(run_attempt=2, status=status, conclusion=conclusion)
        return read(path)

    remote["read"] = superseding
    with pytest.raises(ValueError):
        enqueue(remote)
    assert not remote["writes"]


@pytest.mark.parametrize("field", ["id", "run_attempt", "check_suite_id"])
def test_missing_run_identity_refuses(remote, field):
    del remote["runs"][0][field]
    with pytest.raises(ValueError):
        enqueue(remote)
    assert not remote["writes"]


def test_replaced_check_identity_after_snapshot_refuses(remote):
    read = remote["read"]

    def replaced(path):
        if path == "rules/branches/main":
            remote["checks"][0]["id"] += 1
        return read(path)

    remote["read"] = replaced
    with pytest.raises(ValueError):
        enqueue(remote)
    assert not remote["writes"]


def test_inaccessible_current_attempt_refuses(remote):
    read = remote["read"]

    def denied(path):
        if path.startswith("actions/runs/"):
            raise PermissionError("current attempt inaccessible")
        return read(path)

    remote["read"] = denied
    with pytest.raises(PermissionError):
        enqueue(remote)
    assert not remote["writes"]


@pytest.mark.parametrize("context", ["bundle-release-engine", "renovate"])
@pytest.mark.parametrize("fault", ["missing", "wrong-app"])
def test_independent_context_must_be_server_enforced_from_actions(remote, context, fault):
    checks = remote["rules"][1]["parameters"]["required_status_checks"]
    if fault == "missing":
        checks[:] = [check for check in checks if check["context"] != context]
    else:
        next(check for check in checks if check["context"] == context)["integration_id"] = 999
    with pytest.raises(ValueError, match="trusted required check contexts missing"):
        enqueue(remote)
    assert not remote["writes"]


@pytest.mark.parametrize("context", ["bundle-release-engine", "renovate"])
@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "skipped",
        "neutral",
        "failure",
        "pending",
        "wrong-app",
        "wrong-suite",
        "wrong-head",
        "duplicate",
        "missing-id",
        "shared-id",
    ],
)
def test_each_independent_check_requires_its_own_successful_trusted_evidence(
    remote, context, fault
):
    check = next(check for check in remote["checks"] if check["name"] == context)
    if fault == "missing":
        remote["checks"].remove(check)
    elif fault in {"skipped", "neutral", "failure"}:
        check["conclusion"] = fault
    elif fault == "pending":
        check.update(status="in_progress", conclusion=None)
    elif fault == "wrong-app":
        check["app"]["id"] = 999
    elif fault == "wrong-suite":
        check["check_suite"]["id"] = 999
    elif fault == "wrong-head":
        check["head_sha"] = MAIN
    elif fault == "duplicate":
        remote["checks"].append(copy.deepcopy(check))
    elif fault == "missing-id":
        del check["id"]
    elif fault == "shared-id":
        check["id"] = remote["checks"][0]["id"]
    # Both workflow summaries and all four server requirements remain successful.
    with pytest.raises(ValueError):
        enqueue(remote)
    assert not remote["writes"]


@pytest.mark.parametrize("context", ["bundle-release-engine", "renovate"])
@pytest.mark.parametrize("replacement", ["successful-id", "pending", "skipped", "missing"])
def test_independent_check_replacement_after_snapshot_refuses(remote, context, replacement):
    read = remote["read"]
    changed = False

    def replace(path):
        nonlocal changed
        if path == "rules/branches/main" and not changed:
            changed = True
            check = next(check for check in remote["checks"] if check["name"] == context)
            if replacement == "missing":
                remote["checks"].remove(check)
            else:
                check["id"] += 1000
                if replacement == "pending":
                    check.update(status="in_progress", conclusion=None)
                elif replacement == "skipped":
                    check["conclusion"] = "skipped"
        return read(path)

    remote["read"] = replace
    with pytest.raises(ValueError):
        enqueue(remote)
    assert changed and not remote["writes"]


def test_snapshot_contains_all_four_individual_check_identities(remote):
    snapshot = queue.admission.successful_checks(remote["record"], remote["read"])
    assert snapshot == {
        ".github/workflows/checks.yml": (
            1,
            1,
            1,
            (("checks", 101), ("bundle-release-engine", 102), ("renovate", 103)),
        ),
        ".github/workflows/verify.yml": (2, 1, 2, (("verification", 104),)),
    }
