# SPDX-License-Identifier: Apache-2.0
import email.message
import hashlib
import json
from pathlib import Path
import sys
import urllib.error

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


def test_wrong_digest_on_valid_receipt_refuses(receipt):
    """A well-formed receipt with a different sha256 is not that publication."""
    path, digest = receipt
    wrong = ("0" if digest[0] != "0" else "1") * 64
    with pytest.raises(ValueError, match="digest"):
        admission.load_receipt(path, wrong)
    assert admission.load_receipt(path, digest)["head"] == "a" * 40


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


def test_link_next_is_the_relative_path_on_this_repository(monkeypatch):
    prefix = "https://api.github.com/repos/" + admission.writer.REPO + "/"
    nxt = "commits/abc/check-runs?filter=latest&per_page=100&page=2"
    link = f'<{prefix}{nxt}>; rel="next", <{prefix}{nxt}>; rel="last"'

    class Response:
        headers = {"Link": link}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"total_count": 1, "check_runs": []}'

    class Opener:
        def open(self, request, timeout):
            assert not request.has_header("Authorization")
            return Response()

    monkeypatch.setattr(admission.urllib.request, "build_opener", lambda *args: Opener())
    page = admission.public_read("commits/abc/check-runs?filter=latest&per_page=100")
    assert page.next_path == nxt
    assert page["total_count"] == 1

    class Foreign:
        headers = {"Link": '<https://example.invalid/repos/other>; rel="next"'}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b"{}"

    monkeypatch.setattr(
        admission.urllib.request,
        "build_opener",
        lambda *args: type("Opener", (), {"open": lambda self, request, timeout: Foreign()})(),
    )
    with pytest.raises(ValueError, match="incomplete evidence page"):
        admission.public_read("commits/abc/check-runs?per_page=100")


# Recorded 2026-11-23 from unauthenticated GETs, per_page=1.
# Date: Mon, 23 Nov 2026 01:12:49 GMT.
# Check runs:
# https://api.github.com/repos/Living-Proof-Labs/livingproof-verification/commits/314ffcbd630812d69a3390baabcca11b3549e17e/check-runs?filter=latest&per_page=1
# total_count 108. The same minute, GET /repos/Living-Proof-Labs/livingproof-verification
# returned id 1442523227.
RECORDED_CHECK_RUNS_LINK = (
    "<https://api.github.com/repositories/1442523227/commits/"
    "314ffcbd630812d69a3390baabcca11b3549e17e/check-runs?filter=latest&per_page=1&page=2>"
    '; rel="next", '
    "<https://api.github.com/repositories/1442523227/commits/"
    "314ffcbd630812d69a3390baabcca11b3549e17e/check-runs?filter=latest&per_page=1&page=108>"
    '; rel="last"'
)
# Actions runs:
# https://api.github.com/repos/Living-Proof-Labs/livingproof-verification/actions/runs?per_page=1
# total_count 1049. Date: Mon, 23 Nov 2026 01:12:49 GMT.
RECORDED_ACTIONS_RUNS_LINK = (
    "<https://api.github.com/repositories/1442523227/actions/runs?per_page=1&page=2>"
    '; rel="next", '
    "<https://api.github.com/repositories/1442523227/actions/runs?per_page=1&page=1049>"
    '; rel="last"'
)


def test_recorded_repository_id_links_parse(monkeypatch):
    monkeypatch.setattr(admission, "_repository_id", lambda: 1442523227)
    assert admission._next_path(RECORDED_CHECK_RUNS_LINK) == (
        "commits/314ffcbd630812d69a3390baabcca11b3549e17e/check-runs"
        "?filter=latest&per_page=1&page=2"
    )
    assert admission._next_path(RECORDED_ACTIONS_RUNS_LINK) == "actions/runs?per_page=1&page=2"


def test_repository_id_is_the_one_the_repo_read_returns(monkeypatch):
    admission._repository_id_cache = None
    admission._rate_reset_at = None

    class Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"id": 55}'

    seen = []

    class Opener:
        def open(self, request, timeout):
            seen.append(request.full_url)
            assert not request.has_header("Authorization")
            return Response()

    monkeypatch.setattr(admission.urllib.request, "build_opener", lambda *args: Opener())
    try:
        assert admission._repository_id() == 55
        assert seen == ["https://api.github.com/repos/" + admission.writer.REPO]
        assert admission._repository_id() == 55 and len(seen) == 1
        link = '<https://api.github.com/repositories/55/commits/abc/check-runs?page=2>; rel="next"'
        assert admission._next_path(link) == "commits/abc/check-runs?page=2"
        wrong = link.replace("/repositories/55/", "/repositories/1442523227/")
        with pytest.raises(ValueError, match="incomplete evidence page"):
            admission._next_path(wrong)
    finally:
        admission._repository_id_cache = None


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com/repositories/999/commits/abc/check-runs?page=2",
        "https://api.github.com/repos/Other-Org/livingproof-verification/commits/abc?page=2",
        "https://api.github.com/repos/Living-Proof-Labs/other/commits/abc?page=2",
        "https://example.invalid/repositories/1442523227/commits/abc?page=2",
        "http://api.github.com/repos/Living-Proof-Labs/livingproof-verification/commits/abc?page=2",
        "https://api.github.com/repos/Living-Proof-Labs/livingproof-verification/../secrets",
        "https://api.github.com/repositories/1442523227/%2e%2e/secrets",
        "https://user@api.github.com/repositories/1442523227/commits/abc?page=2",
    ],
)
def test_link_refuses_wrong_id_owner_repo_host_and_encoding(monkeypatch, url):
    monkeypatch.setattr(admission, "_repository_id", lambda: 1442523227)
    with pytest.raises(ValueError, match="incomplete evidence page"):
        admission._next_path(f'<{url}>; rel="next"')


def test_second_next_link_refuses(monkeypatch):
    monkeypatch.setattr(admission, "_repository_id", lambda: 1442523227)
    link = (
        '<https://api.github.com/repositories/1442523227/commits/abc/check-runs?page=2>; rel="next", '
        '<https://api.github.com/repositories/1442523227/commits/abc/check-runs?page=3>; rel="next"'
    )
    with pytest.raises(ValueError, match="incomplete evidence page"):
        admission._next_path(link)


def _http_error(code, headers):
    header = email.message.Message()
    for key, value in headers.items():
        header[key] = value
    return urllib.error.HTTPError("https://api.github.com/item", code, "limited", header, None)


def _clock(monkeypatch, now):
    clock = {"now": now}
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(admission.time, "time", lambda: clock["now"])
    monkeypatch.setattr(admission.time, "sleep", sleep)
    admission._rate_reset_at = None
    admission.arm_deadline(None)
    return clock, sleeps


def _scripted_opener(monkeypatch, outcomes):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            assert not request.has_header("Authorization")
            outcome = outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    monkeypatch.setattr(admission.urllib.request, "build_opener", lambda *args: Opener())
    return calls


class _Body:
    def __init__(self, payload, headers):
        self.headers = headers
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self._payload


def test_exhausted_rate_limit_waits_until_reset(monkeypatch):
    clock, sleeps = _clock(monkeypatch, 1_000)
    admission.arm_deadline(clock["now"] + 1_000)
    outcomes = [
        _Body(b'{"ok": 1}', {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1040"}),
        _Body(b'{"ok": 2}', {"X-RateLimit-Remaining": "59", "X-RateLimit-Reset": "5000"}),
    ]
    _scripted_opener(monkeypatch, outcomes)
    assert admission.public_read("rules/branches/main") == {"ok": 1}
    assert sleeps == []
    assert admission.public_read("rules/branches/main") == {"ok": 2}
    assert sleeps == [40]
    assert outcomes == []


@pytest.mark.parametrize("code", [403, 429])
def test_retry_after_on_limited_responses_waits_then_reads(monkeypatch, code):
    clock, sleeps = _clock(monkeypatch, 2_000)
    admission.arm_deadline(clock["now"] + 1_000)
    outcomes = [
        _http_error(code, {"Retry-After": "15"}),
        _Body(b'{"ok": 1}', {"X-RateLimit-Remaining": "9"}),
    ]
    calls = _scripted_opener(monkeypatch, outcomes)
    assert admission.public_read("rules/branches/main") == {"ok": 1}
    assert sleeps == [15]
    assert len(calls) == 2
    assert outcomes == []


def test_rate_limit_wait_past_the_deadline_refuses(monkeypatch):
    clock, sleeps = _clock(monkeypatch, 3_000)
    admission.arm_deadline(clock["now"] + 1_000)
    first = [
        _Body(b'{"ok": 1}', {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "3100"}),
    ]
    _scripted_opener(monkeypatch, first)
    assert admission.public_read("rules/branches/main") == {"ok": 1}
    admission.arm_deadline(clock["now"] + 5)
    later = [_Body(b'{"ok": 2}', {})]
    calls = _scripted_opener(monkeypatch, later)
    with pytest.raises(ValueError, match="5.5h"):
        admission.public_read("rules/branches/main")
    assert sleeps == []
    assert calls == []
    assert len(later) == 1


def test_retry_after_past_the_deadline_does_not_accept(monkeypatch):
    clock, sleeps = _clock(monkeypatch, 4_000)
    admission.arm_deadline(clock["now"] + 5)
    outcomes = [
        _http_error(429, {"Retry-After": "30"}),
        _Body(b'{"ok": 1}', {}),
    ]
    calls = _scripted_opener(monkeypatch, outcomes)
    with pytest.raises(ValueError, match="5.5h"):
        admission.public_read("rules/branches/main")
    assert sleeps == []
    assert len(calls) == 1
    assert len(outcomes) == 1
