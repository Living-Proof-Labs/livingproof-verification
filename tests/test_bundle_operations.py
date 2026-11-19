# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bundle_operations as adapter


@pytest.fixture
def sealed(tmp_path, monkeypatch):
    plan = {"pr": 133, "branch": "renovate/living-proof-labs-livingproof-capability-report-3.x"}
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(adapter.writer, "validate_plan", lambda root, value: None)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_outputs_only_after_success(sealed, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(adapter.writer, "publish", lambda *args: print("a" * 40))
    adapter.publish(tmp_path, *sealed)
    assert capsys.readouterr().out == (
        f"head={'a' * 40}\npr=133\n"
        "branch=renovate/living-proof-labs-livingproof-capability-report-3.x\n"
    )


@pytest.mark.parametrize("result", ["", "bad", "a" * 40 + "\npr=999"])
def test_uncertain_output_never_reaches_queue(sealed, tmp_path, monkeypatch, capsys, result):
    monkeypatch.setattr(adapter.writer, "publish", lambda *args: print(result))
    with pytest.raises(ValueError, match="uncertain"):
        adapter.publish(tmp_path, *sealed)
    assert not capsys.readouterr().out


def test_bad_digest_never_publishes(sealed, tmp_path, monkeypatch):
    monkeypatch.setattr(adapter.writer, "publish", lambda *args: pytest.fail("published"))
    with pytest.raises(ValueError, match="digest"):
        adapter.publish(tmp_path, sealed[0], "0" * 64)


def test_stale_refusal_emits_no_outputs(sealed, tmp_path, monkeypatch, capsys):
    def refuse(*args):
        raise ValueError("stale head")

    monkeypatch.setattr(adapter.writer, "publish", refuse)
    with pytest.raises(ValueError, match="stale"):
        adapter.publish(tmp_path, *sealed)
    assert not capsys.readouterr().out


@pytest.mark.parametrize("number", [True, 0, -1, "133\nhead=bad"])
def test_invalid_number_cannot_inject_output(sealed, tmp_path, monkeypatch, number):
    path, _ = sealed
    plan = json.loads(path.read_text())
    plan["pr"] = number
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(adapter.writer, "publish", lambda *args: pytest.fail("published"))
    with pytest.raises(ValueError, match="PR number"):
        adapter.publish(tmp_path, path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_receipt_saved_only_after_publication(sealed, tmp_path, monkeypatch, capsys):
    events = []
    monkeypatch.setattr(adapter.admission, "context", lambda: events.append("context"))

    def publish(*args):
        events.append("publish")
        print("a" * 40)

    def save(plan, head, digest, path):
        events.append("receipt")
        assert head == "a" * 40 and digest == sealed[1]
        assert path == tmp_path / "publication.json"
        return "e" * 64

    monkeypatch.setattr(adapter.writer, "publish", publish)
    monkeypatch.setattr(adapter.admission, "save_receipt", save)
    adapter.publish(tmp_path, *sealed, tmp_path / "publication.json")
    assert events == ["context", "publish", "receipt"]
    assert f"receipt_digest={'e' * 64}" in capsys.readouterr().out


def test_receipt_failure_after_publish_emits_no_recovery_outputs(
    sealed, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(adapter.admission, "context", lambda: None)
    monkeypatch.setattr(adapter.writer, "publish", lambda *args: print("a" * 40))

    def interrupted(*args):
        raise OSError("interrupted persistence")

    monkeypatch.setattr(adapter.admission, "save_receipt", interrupted)
    with pytest.raises(OSError):
        adapter.publish(tmp_path, *sealed, tmp_path / "publication.json")
    assert not capsys.readouterr().out
