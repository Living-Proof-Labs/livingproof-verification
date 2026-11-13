# SPDX-License-Identifier: Apache-2.0
"""Both Renovate entry points complete through the real lock generator."""

import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import update_verifier as updater  # noqa: E402


@pytest.fixture
def checkout(tmp_path):
    for name in (".github", ".devcontainer", "verifications"):
        shutil.copytree(ROOT / name, tmp_path / name)
    shutil.copyfile(ROOT / "README.md", tmp_path / "README.md")
    return tmp_path


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def resolve(monkeypatch):
    monkeypatch.setattr(updater.lock, "resolve_tag", lambda repo, tag: "a" * 40)
    monkeypatch.setattr(updater.lock, "tag_of_commit", lambda repo, sha: "v9.9.9")
    monkeypatch.setattr(updater.lock, "image_digest", lambda image, tag: "sha256:" + "b" * 64)
    monkeypatch.setattr(
        updater.lock,
        "_verifier_pyproject",
        lambda *args: '[project]\ndependencies = ["livingproof-evaluator-scenario @ '
        'git+https://github.com/Living-Proof-Labs/livingproof-evaluator-scenario.git@v1.1.2"]',
    )


@pytest.mark.parametrize("entry", ["image", "workflow", "digest", "grouped"])
def test_completion_coordinates_both_entry_points(checkout, monkeypatch, entry):
    resolve(monkeypatch)
    original = snapshot(checkout)
    dev = checkout / ".devcontainer/devcontainer.json"
    workflow = checkout / ".github/workflows/verify.yml"
    if entry in ("image", "grouped", "digest"):
        data = json.loads(dev.read_text())
        data["image"] = updater.lock.VERIFIER_IMAGE + ":9.9.9@sha256:" + "c" * 64
        if entry == "digest":
            data["image"] = data["image"].replace(":9.9.9", ":1.18.5")
        dev.write_text(json.dumps(data))
    if entry in ("workflow", "grouped"):
        workflow.write_text(
            updater.CALL.sub(lambda m: m["prefix"] + "a" * 40 + "  # v9.9.9", workflow.read_text())
        )
    updater.update(checkout, "v9.9.9")
    assert workflow.read_text().count("a" * 40 + "  # v9.9.9") == 4
    pin = updater.lock.VERIFIER_IMAGE + ":9.9.9@sha256:" + "b" * 64
    assert json.loads(dev.read_text())["image"] == pin
    assert pin in (checkout / "README.md").read_text()
    for path in (checkout / "verifications").glob("*.lock.json"):
        before = json.loads(original[str(path.relative_to(checkout))])
        after = json.loads(path.read_text())
        assert after["releases"]["verifier"]["commit"] == "a" * 40
        assert after["releases"]["verifier"]["digest"] == "sha256:" + "b" * 64
        before["releases"].pop("verifier")
        after["releases"].pop("verifier")
        before["evaluators"].pop(updater.lock.EVALUATOR)
        after["evaluators"].pop(updater.lock.EVALUATOR)
        assert after == before
    assert updater.update(checkout, "v9.9.9") == []


@pytest.mark.parametrize("failure", ["registry", "layout", "release"])
def test_failure_leaves_checkout_untouched(checkout, monkeypatch, failure):
    resolve(monkeypatch)
    if failure == "registry":

        def fail(*args):
            raise updater.lock.LockError("registry unavailable")

        monkeypatch.setattr(updater.lock, "image_digest", fail)
    elif failure == "release":
        monkeypatch.setattr(updater.lock, "tag_of_commit", lambda *args: "v1.0.0")
    else:
        (checkout / "README.md").write_text("no immutable image example\n")
    before = snapshot(checkout)
    with pytest.raises(updater.lock.LockError):
        updater.update(checkout, "v9.9.9")
    assert snapshot(checkout) == before


@pytest.mark.parametrize("mutation", ["tag", "digest"])
def test_existing_guard_rejects_image_only_drift(checkout, monkeypatch, mutation):
    spec = importlib.util.spec_from_file_location("guard", ROOT / "tests/test_locks.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    monkeypatch.setattr(guard, "ROOT", checkout)
    dev = checkout / ".devcontainer/devcontainer.json"
    data = json.loads(dev.read_text())
    tag, digest = data["image"].split("@")
    data["image"] = (
        tag + "@sha256:" + "f" * 64
        if mutation == "digest"
        else updater.lock.VERIFIER_IMAGE + ":99.0.0@" + digest
    )
    dev.write_text(json.dumps(data))
    with pytest.raises(AssertionError):
        guard.test_walk_hallway_pin_locks_and_hallway_image_agree()


def test_renovate_policy_covers_image_and_workflow_without_automerge():
    config = json.loads((ROOT / "renovate.json").read_text())
    (rule,) = config["packageRules"]
    assert set(rule["matchManagers"]) == {"github-actions", "devcontainer"}
    assert set(rule["matchPackageNames"]) == {updater.REPOSITORY, updater.lock.VERIFIER_IMAGE}
    assert rule["automerge"] is False
    assert rule["groupName"]
    assert "update_verifier.py --tag vX.Y.Z" in rule["prBodyNotes"][0]
    assert "postUpgradeTasks" not in config
