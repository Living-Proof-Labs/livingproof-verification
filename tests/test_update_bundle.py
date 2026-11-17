# SPDX-License-Identifier: Apache-2.0
"""Bundle transaction boundaries; existing generator tests cover actual pin resolution."""

import hashlib
import io
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import update_bundle as bundle  # noqa: E402


@pytest.fixture
def checkout(tmp_path):
    for p in bundle.paths(ROOT):
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / p, tmp_path / p)
    return tmp_path


def snapshot(root):
    return {p: (root / p).read_bytes() for p in bundle.paths(root)}


@pytest.fixture
def resolver(monkeypatch):
    def generate(definition, work):
        result = bundle.lock.read_lock(bundle.lock.lock_path(definition))
        result["releases"]["reporter"]["tag"] = bundle.lock.load_reporter_setting(
            definition.parent / "settings.toml"
        )["tag"]
        result["releases"]["reporter"]["commit"] = "a" * 40
        result["releases"]["reporter"]["digest"] = "sha256:" + "b" * 64
        return result, []

    monkeypatch.setattr(bundle.lock, "generate", generate)
    monkeypatch.setattr(bundle, "release_wheel", lambda tag: "c" * 64)
    return generate


def test_reporter_bundle_is_stable_and_preserves_unrelated_inputs(checkout, resolver):
    before = snapshot(checkout)
    changed = bundle.update(checkout, reporter="v3.99.0")
    assert bundle.SETTINGS in changed and bundle.WHEELS in changed
    for p in bundle.paths(checkout):
        if p.name.endswith(".lock.json"):
            old, new = json.loads(before[p]), json.loads((checkout / p).read_bytes())
            assert bundle.unrelated(old, True, False) == bundle.unrelated(new, True, False)
            assert new["releases"]["reporter"]["tag"] == "v3.99.0"
    assert bundle.update(checkout, reporter="v3.99.0") == []
    # No assertions, frozen hashes or evidence constants are removed/rewritten.
    for p in bundle.CURRENT_TESTS:
        old = before[p].decode().splitlines()
        new = (checkout / p).read_text().splitlines()
        assert len(old) == len(new)
        changes = [(a, b) for a, b in zip(old, new) if a != b]
        assert len(changes) == 1
        assert all("REPORTER_RELEASE =" in a or "assert reporter_tag ==" in a for a, _ in changes)


@pytest.mark.parametrize("failure", ["wheel", "last-lock", "drift", "layout", "race"])
def test_failure_is_transactional(checkout, resolver, monkeypatch, failure):
    if failure == "layout":
        (checkout / bundle.CURRENT_TESTS[0]).write_text("unexpected layout\n")
    before = snapshot(checkout)
    count = 0

    def generate(definition, work):
        nonlocal count
        count += 1
        result, notes = resolver(definition, work)
        if failure == "last-lock" and count == 7:
            raise bundle.lock.LockError("resolution failed")
        if failure == "drift":
            result["releases"]["runner"]["commit"] = "d" * 40
        if failure == "race" and count == 7:
            (checkout / "README.md").write_text("concurrent custom change\n")
        return result, notes

    monkeypatch.setattr(bundle.lock, "generate", generate)
    if failure == "wheel":

        def fail(tag):
            raise bundle.lock.LockError("bad checksum")

        monkeypatch.setattr(bundle, "release_wheel", fail)
    with pytest.raises(bundle.lock.LockError):
        bundle.update(checkout, reporter="v3.99.0")
    if failure == "race":
        before[Path("README.md")] = b"concurrent custom change\n"
    assert snapshot(checkout) == before


@pytest.mark.parametrize("bad", [False, True])
def test_wheel_bytes_must_match_published_digest(monkeypatch, bad):
    wheel = b"published wheel"
    digest = hashlib.sha256(wheel).hexdigest()
    release = {
        "tag_name": "v3.99.0",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "livingproof_capability_report-3.99.0-py3-none-any.whl",
                "digest": "sha256:" + digest,
            }
        ],
    }

    def download(url, timeout):
        return io.BytesIO(
            json.dumps(release).encode()
            if "api.github.com" in url
            else b"tampered"
            if bad
            else wheel
        )

    monkeypatch.setattr(bundle.urllib.request, "urlopen", download)
    if bad:
        with pytest.raises(bundle.lock.LockError, match="differs"):
            bundle.release_wheel("v3.99.0")
    else:
        assert bundle.release_wheel("v3.99.0") == digest


def test_existing_wheel_checksum_is_immutable(checkout):
    with pytest.raises(bundle.lock.LockError, match="changed"):
        bundle.wheel_pin(checkout / bundle.WHEELS, "v3.11.2", "f" * 64)


@pytest.mark.parametrize("tag", ["main", "v3.2", "v3.2.1;echo x", "v3.2.1-rc.1"])
def test_invalid_release_rejected_before_changes(checkout, tag):
    before = snapshot(checkout)
    with pytest.raises(bundle.lock.LockError):
        bundle.update(checkout, reporter=tag)
    assert snapshot(checkout) == before


@pytest.mark.parametrize("include_reporter", [False, True])
def test_verifier_and_combined_bundle_use_existing_updater(checkout, monkeypatch, include_reporter):
    monkeypatch.setattr(bundle.lock, "resolve_tag", lambda *args: "a" * 40)
    monkeypatch.setattr(bundle.lock, "tag_of_commit", lambda *args: "v9.9.9")
    monkeypatch.setattr(bundle.lock, "image_digest", lambda *args: "sha256:" + "b" * 64)
    monkeypatch.setattr(
        bundle.lock,
        "_verifier_pyproject",
        lambda *args: '[project]\ndependencies = ["livingproof-evaluator-scenario @ '
        'git+https://github.com/Living-Proof-Labs/livingproof-evaluator-scenario.git@v1.1.2"]',
    )

    def generate(definition, work):
        result = bundle.lock.read_lock(bundle.lock.lock_path(definition))
        if include_reporter:
            result["releases"]["reporter"].update(tag="v3.99.0", commit="c" * 40)
        return result, []

    monkeypatch.setattr(bundle.lock, "generate", generate)
    monkeypatch.setattr(bundle, "release_wheel", lambda *args: "d" * 64)
    before = snapshot(checkout)
    kwargs = {"verifier": "v9.9.9", "reporter": "v3.99.0" if include_reporter else None}
    changed = bundle.update(checkout, **kwargs)
    assert Path(".github/workflows/verify.yml") in changed
    assert Path(".devcontainer/devcontainer.json") in changed
    for p in before:
        if p.name.endswith(".lock.json"):
            old, new = json.loads(before[p]), json.loads((checkout / p).read_bytes())
            assert new["releases"]["verifier"]["tag"] == "v9.9.9"
            assert bundle.unrelated(old, include_reporter, True) == bundle.unrelated(
                new, include_reporter, True
            )
    assert bundle.update(checkout, **kwargs) == []
