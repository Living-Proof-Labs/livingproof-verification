# SPDX-License-Identifier: Apache-2.0
"""Real released inputs and generator; all proposal edits stay in disposable copies."""

import hashlib
import json
import re
import tomllib

import pytest
import yaml

from test_locks import ROOT, _copy_verification_repo, call_problems, lock_tool
from test_locks import test_lock_is_current_for_its_definition as assert_current_definition


def _ref(definition, tag):
    text = definition.read_text()
    text, count = re.subn(r'^ref = "v[0-9]+\.[0-9]+\.[0-9]+"$', f'ref = "{tag}"', text, flags=re.M)
    assert count == 1
    definition.write_text(text)


def _generate(definition, work):
    # No injected resolver/digester: git and the registry resolve the real releases.
    lock, notes = lock_tool.generate(definition, work)
    lock_tool.lock_path(definition).write_text(json.dumps(lock, indent=2) + "\n")
    assert lock_tool.v2_problems(lock) == []
    assert lock["definition"]["sha256"] == hashlib.sha256(definition.read_bytes()).hexdigest()
    robot = tomllib.loads(definition.read_text())["robot"]
    assert lock["robot"]["commit"] == lock_tool.resolve_tag(robot["repository"], robot["ref"])
    return lock, notes


def _sync_workflow(workflow, definition, lock):
    # Copy only this job's generated robot commit, preserving all surrounding YAML.
    text = workflow.read_text()
    pattern = rf"(  {re.escape(definition.stem)}:\n[\s\S]*?      ref: )[0-9a-f]{{40}}"
    text, count = re.subn(pattern, lambda m: m[1] + lock["robot"]["commit"], text, count=1)
    assert count == 1
    workflow.write_text(text)
    return yaml.safe_load(text)["jobs"][definition.stem]


@pytest.mark.parametrize("ownership", ["definition", "legacy"])
def test_released_robot_update_is_coherent_and_rejects_partial_completion(tmp_path, ownership):
    original = {p: p.read_bytes() for p in (ROOT / "verifications").iterdir() if p.is_file()}
    root = _copy_verification_repo(tmp_path)
    suffix = "corner-left" if ownership == "definition" else "hallway"
    definition = root / "verifications" / f"booster-t1-stock-mujoco-{suffix}.toml"
    workflow = root / ".github/workflows/verify.yml"
    untouched = {
        p: p.read_bytes()
        for p in (root / "verifications").iterdir()
        if p not in (definition, lock_tool.lock_path(definition))
    }
    if ownership == "legacy":
        # A historical compatibility fixture, not an eligible hallway proposal.
        # Let the released robot-owned lock supply all scenario inputs.
        text = definition.read_text().split("\n[world]", 1)[0]
        definition.write_text(re.sub(r"^scenario = .*\n", "", text, flags=re.M))
        old_tag, new_tag = "v1.0.0", "v1.0.1"
    else:
        old_tag, new_tag = "v2.3.4", "v2.3.5"
    _ref(definition, old_tag)
    before, _ = _generate(definition, tmp_path / "before")
    assert call_problems(before, _sync_workflow(workflow, definition, before)) == []

    _ref(definition, new_tag)
    with pytest.raises(AssertionError):
        assert_current_definition(definition)
    after, notes = _generate(definition, tmp_path / "after")
    assert_current_definition(definition)
    job = yaml.safe_load(workflow.read_text())["jobs"][definition.stem]
    assert any("with.ref" in p for p in call_problems(after, job))
    assert call_problems(after, _sync_workflow(workflow, definition, after)) == []
    assert before["robot"]["commit"] != after["robot"]["commit"]

    robot_tree = tmp_path / "after/robot"
    if ownership == "definition":
        assert not (robot_tree / "verification.lock.json").exists()
        assert after["releases"]["runner"] == lock_tool._runner_from_uv_lock(robot_tree, "test")
        assert any("definition owns" in note for note in notes)
        assert after["world"] == before["world"]
        assert after["plan"] == before["plan"]
        assert after["releases"]["specs"] == before["releases"]["specs"]
    else:
        published = json.loads((robot_tree / "verification.lock.json").read_text())
        for key in ("scenario", "world", "plan"):
            assert after[key] == published[key]
        assert after["releases"]["runner"] == published["releases"]["runner"]
        # A thin robot without explicit ownership must retain this baseline, and say so.
        _ref(definition, "v2.3.5")
        retained, retained_notes = _generate(definition, tmp_path / "retained")
        assert any(note.startswith("thin robot:") for note in retained_notes)
        for key in ("scenario", "world", "plan"):
            assert retained[key] == after[key]
        assert retained["releases"]["runner"] == after["releases"]["runner"]
        assert retained["releases"]["runner"] != lock_tool._runner_from_uv_lock(
            tmp_path / "retained/robot", "test"
        )
    assert all(p.read_bytes() == data for p, data in untouched.items())
    # The original evidence inputs are never written by this test.
    assert all(p.read_bytes() == data for p, data in original.items())
