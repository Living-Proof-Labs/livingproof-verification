# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import bundle_admission as admission


@pytest.fixture
def receipt(monkeypatch, tmp_path):
    for key, value in {
        "GITHUB_REPOSITORY": "Living-Proof-Labs/.github",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": admission.WORKFLOW_REF,
        "GITHUB_EVENT_NAME": "schedule",
        "GITHUB_SHA": "c" * 40,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(admission.time, "time", lambda: 100000)
    plan = {"pr": 123, "branch": "renovate/capability-verifier-evidence", "main": "b" * 40}
    path = tmp_path / "receipt.json"
    digest = admission.save_receipt(plan, "a" * 40, "d" * 64, path)
    return path, digest


def test_receipt_binds_publication_and_workflow(receipt):
    record = admission.load_receipt(*receipt)
    assert record["repository"] == admission.writer.REPO
    assert record["pr"] == 123 and record["head"] == "a" * 40
    assert record["plan_digest"] == "d" * 64 and record["run_id"] == 123


@pytest.mark.parametrize("attempt", ["1", "2", "3"])
def test_bounded_failed_job_recovery(receipt, monkeypatch, attempt):
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", attempt)
    admission.load_receipt(*receipt)


@pytest.mark.parametrize(
    "key,value",
    [
        ("GITHUB_RUN_ATTEMPT", "4"),
        ("GITHUB_RUN_ID", "456"),
        ("GITHUB_SHA", "d" * 40),
        ("GITHUB_REF", "refs/heads/other"),
        ("GITHUB_EVENT_NAME", "pull_request"),
        ("GITHUB_REPOSITORY", admission.writer.REPO),
    ],
)
def test_other_context_cannot_recover(receipt, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        admission.load_receipt(*receipt)


@pytest.mark.parametrize("now", [99999, 186401])
def test_expired_or_future_receipt_refuses(receipt, monkeypatch, now):
    monkeypatch.setattr(admission.time, "time", lambda: now)
    with pytest.raises(ValueError):
        admission.load_receipt(*receipt)


def test_tampered_and_interrupted_receipt_refuses(receipt):
    path, digest = receipt
    path.write_text('{"pr":')
    with pytest.raises(ValueError):
        admission.load_receipt(path, digest)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        admission.load_receipt(path, digest)


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository", "other/repo"),
        ("pr", True),
        ("head", "main"),
        ("branch", "main"),
        ("plan_digest", "invalid"),
    ],
)
def test_malformed_sealed_receipt_refuses(receipt, field, value):
    path, _ = receipt
    record = json.loads(path.read_text())
    record[field] = value
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        admission.load_receipt(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_public_evidence_never_attaches_credentials(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b"{}"

    class Opener:
        def open(self, request, timeout):
            assert request.full_url.startswith(
                "https://api.github.com/repos/" + admission.writer.REPO + "/"
            )
            assert not request.has_header("Authorization")
            assert timeout == 30
            return Response()

    monkeypatch.setenv("GH_TOKEN", "must-not-be-used")
    monkeypatch.setattr(admission.urllib.request, "build_opener", lambda *args: Opener())
    assert admission.public_read("rules/branches/main") == {}
    with pytest.raises(ValueError):
        admission.public_read("//elsewhere/../secrets")
    with pytest.raises(ValueError):
        admission.NoRedirect().redirect_request()
