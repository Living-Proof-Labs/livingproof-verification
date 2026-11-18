"""Reviewer tests for #121: the ``sources`` bindings in generated locks (#119).

``v2_problems`` must refuse a binding that does not match the recorded world and
parameters digests or leaves the robot root, ``--update`` must keep the bindings,
and a scenario without a ``parameters.json`` digest must not bind one.
"""

import copy
import json

import pytest
import test_locks as tl

lock_tool = tl.lock_tool
OTHER_SHA = "0" * 64


def _committed():
    return [tl.lock_of(definition) for definition in tl.DEFINITIONS]


def test_committed_locks_bind_what_source_bindings_derives():
    for lock in _committed():
        assert lock["sources"] == lock_tool.source_bindings(lock)
        assert lock_tool.v2_problems(lock) == []


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        pytest.param(
            lambda s: s["world"].update(sha256=OTHER_SHA),
            "sources.world sha256 must match the recorded digest",
            id="world-sha-mismatch",
        ),
        pytest.param(
            lambda s: s["parameters"].update(sha256=OTHER_SHA),
            "sources.parameters sha256 must match the recorded digest",
            id="parameters-sha-mismatch",
        ),
        pytest.param(
            lambda s: s["world"].update(sha256="abc"),
            "sources.world sha256 must be 64 hex characters",
            id="world-sha-shape",
        ),
        pytest.param(
            lambda s: s["world"].update(path="worlds/other/0.1.0/world.json"),
            "sources.world path must be worlds/",
            id="world-path-mismatch",
        ),
        pytest.param(
            lambda s: s["parameters"].update(path="../parameters.json"),
            "sources.parameters path must stay inside the robot repository",
            id="parameters-path-escape",
        ),
        pytest.param(
            lambda s: s.update(extra={"path": "x.json", "sha256": OTHER_SHA}),
            "sources names unknown kinds ['extra']",
            id="unknown-kind",
        ),
        pytest.param(
            lambda s: s.update(world="worlds/x/world.json"),
            "sources.world must be an object with path and sha256",
            id="world-not-object",
        ),
    ],
)
def test_a_wrong_source_binding_is_a_lock_problem(edit, message):
    for lock in _committed():
        tampered = copy.deepcopy(lock)
        edit(tampered["sources"])
        problems = lock_tool.v2_problems(tampered)
        assert any(message in problem for problem in problems), problems


def test_sources_that_is_not_an_object_is_a_lock_problem():
    lock = copy.deepcopy(_committed()[0])
    lock["sources"] = []
    assert "sources must be an object" in lock_tool.v2_problems(lock)


def test_a_scenario_without_a_parameters_digest_binds_only_the_world():
    for lock in _committed():
        absent = copy.deepcopy(lock)
        param_path = absent["sources"]["parameters"]["path"]
        absent["releases"]["specs"]["files"][param_path] = None
        assert lock_tool.source_bindings(absent) == {"world": lock["sources"]["world"]}


def test_update_records_the_source_bindings(tmp_path, monkeypatch):
    root = tl._copy_verification_repo(tmp_path)
    before = tl._original_locks(root)
    for name in before:
        path = lock_tool.lock_path(root / "verifications" / name)
        stripped = json.loads(path.read_text())
        stripped.pop("sources")
        path.write_text(json.dumps(stripped, indent=2) + "\n")
    first = next(iter(before.values()))
    verifier = first["releases"]["verifier"]
    evaluator = first["evaluators"]["livingproof-evaluator-scenario"]
    monkeypatch.setattr(lock_tool, "tag_of_commit", lambda repository, commit: verifier["tag"])
    monkeypatch.setattr(lock_tool, "image_digest", lambda image, tag: verifier["digest"])
    monkeypatch.setattr(
        lock_tool,
        "_verifier_pyproject",
        lambda repository, commit, work: tl._declared_evaluator_pyproject(
            evaluator["repository"], evaluator["tag"]
        ),
    )
    monkeypatch.setattr(lock_tool, "resolve_tag", lambda repository, tag: evaluator["commit"])
    assert lock_tool.update_locks(root)
    for name, prior in before.items():
        written = json.loads(lock_tool.lock_path(root / "verifications" / name).read_text())
        assert written["sources"] == prior["sources"]
