# SPDX-License-Identifier: Apache-2.0
"""Fail-closed public evidence reads and bounded same-run publication receipts."""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import bundle_proposal as writer

ACTIONS_APP = 15368
WORKFLOWS = {
    ".github/workflows/checks.yml": ("checks", "bundle-release-engine", "renovate"),
    ".github/workflows/verify.yml": ("verification",),
}
# Independent job contexts required by server enforcement. The first three
# belong to checks.yml. Validate each individual check as well as its workflow run.
REQUIRED_CONTEXTS = frozenset({"checks", "bundle-release-engine", "renovate", "verification"})
WORKFLOW_REF = "Living-Proof-Labs/.github/.github/workflows/bundle-completion.yml@refs/heads/main"
# One page is 100. Twenty pages is a hard stop so a repeated Link cannot loop.
_PAGE_SIZE = 100
_MAX_EVIDENCE_PAGES = 20
_NEXT_LINK = re.compile(r"""<([^>]+)>\s*;\s*rel="?next"?(?=;|,|\s|$)""")
# Cached result of the unauthenticated repository read. Link checks compare
# against this id; it is not a second copy of a literal.
_repository_id_cache = None
# In-job evidence budget. GitHub kills a job at 6h, so the wait refuses at
# 5.5h and the workflow job timeout is 360 minutes.
EVIDENCE_DEADLINE = 5 * 60 * 60 + 30 * 60
DEADLINE_MESSAGE = "evidence wait exceeded 5.5h and the next scheduled or triggered run will retry"
# Set by the queue wait. A rate-limit sleep that would pass it refuses.
_wait_deadline = None
# Unix time of X-RateLimit-Reset after a response reports no remaining calls.
_rate_reset_at = None


class PendingEvidence(ValueError):
    """A scheduled exact-head check has not produced complete evidence yet."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("evidence redirect refused")


class _Page(dict):
    """One evidence document plus the next relative path from its Link header."""

    def __init__(self, payload, next_path):
        super().__init__(payload)
        self.next_path = next_path


def _evidence_path(path):
    if not re.fullmatch(r"[A-Za-z0-9_./?=&-]+", path) or ".." in path:
        raise ValueError("invalid evidence path")
    return path


def arm_deadline(deadline):
    """Refuse a rate-limit or retry wait that would finish after `deadline`."""
    global _wait_deadline
    _wait_deadline = deadline


def arm_job_deadline():
    """Bound a direct enqueue. A wait already in progress keeps its deadline."""
    if _wait_deadline is None:
        arm_deadline(time.time() + EVIDENCE_DEADLINE)


def require_created(record):
    """A receipt with no integer `created` time cannot choose a deadline."""
    created = record.get("created") if isinstance(record, dict) else None
    if type(created) is not int or created < 0:
        raise ValueError("receipt created time is missing or unparseable")
    return created


def _repository_id():
    """Numeric id of this repository, read once, unauthenticated, and then reused.

    Link headers name the repository as `/repositories/<id>/`. The id comes from
    this read, not from a literal in the comparison.
    """
    global _repository_id_cache
    if type(_repository_id_cache) is int:
        return _repository_id_cache
    payload, _next = _fetch(f"https://api.github.com/repos/{writer.REPO}", parse_link=False)
    repo_id = payload.get("id") if isinstance(payload, dict) else None
    if type(repo_id) is not int:
        raise ValueError("incomplete evidence page")
    _repository_id_cache = repo_id
    return repo_id


def _relative_next(url):
    """Path under this repository for one Link URL, or a refusal.

    GitHub sends either `/repos/<owner>/<repo>/` or `/repositories/<id>/` on
    https://api.github.com. Any other scheme, host, owner, repository, or id
    refuses, as do userinfo, a port, a fragment, percent-encoding, and `..`.
    """
    if any(mark in url for mark in "%\\@ \t\r\n"):
        raise ValueError("incomplete evidence page")
    parts = urllib.parse.urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc != "api.github.com"
        or parts.fragment
        or parts.username
        or parts.password
    ):
        raise ValueError("incomplete evidence page")
    name_prefix = f"/repos/{writer.REPO}/"
    if parts.path.startswith(name_prefix):
        relative = parts.path[len(name_prefix) :]
    else:
        id_prefix = f"/repositories/{_repository_id()}/"
        if not parts.path.startswith(id_prefix):
            raise ValueError("incomplete evidence page")
        relative = parts.path[len(id_prefix) :]
    if parts.query:
        relative = f"{relative}?{parts.query}"
    if "//" in relative:
        raise ValueError("incomplete evidence page")
    try:
        return _evidence_path(relative)
    except ValueError as error:
        raise ValueError("incomplete evidence page") from error


def _next_path(link):
    """Relative path of rel=next, or None when this page is the last.

    Two rel=next targets refuse. A next URL this repository does not own refuses.
    """
    if not link:
        return None
    found = None
    for match in _NEXT_LINK.finditer(link):
        path = _relative_next(match.group(1))
        if found is not None:
            raise ValueError("incomplete evidence page")
        found = path
    return found


def _header(headers, name):
    if headers is None:
        return None
    return headers.get(name)


def _wait(seconds):
    """Sleep `seconds` on the fakeable clock, or refuse if that passes the deadline."""
    if type(seconds) is not int or seconds < 0:
        raise ValueError("incomplete evidence page")
    now = time.time()
    if _wait_deadline is not None and now + seconds > _wait_deadline:
        raise ValueError(DEADLINE_MESSAGE)
    if seconds:
        time.sleep(seconds)


def _retry_after_seconds(headers):
    raw = _header(headers, "Retry-After")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text.isdigit():
        raise ValueError("incomplete evidence page")
    return int(text)


def _note_rate_limit(headers):
    """Remember a reset time when this response used the last call in the window."""
    global _rate_reset_at
    remaining = _header(headers, "X-RateLimit-Remaining")
    reset = _header(headers, "X-RateLimit-Reset")
    if remaining is None or str(remaining).strip() != "0":
        _rate_reset_at = None
        return
    if reset is None or not str(reset).strip().isdigit():
        raise ValueError("incomplete evidence page")
    _rate_reset_at = int(str(reset).strip())


def _honor_saved_limit():
    """Wait out a window that the previous response said was exhausted."""
    global _rate_reset_at
    if _rate_reset_at is None:
        return
    delay = int(_rate_reset_at - time.time())
    _rate_reset_at = None
    if delay > 0:
        _wait(delay)


def _fetch(url, parse_link):
    """GET `url` with no credential. 403 and 429 honour Retry-After once.

    The body of a rate-limited response is never treated as evidence. A wait
    that would pass the armed deadline raises instead.
    """
    _honor_saved_limit()
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "bundle-admission"},
    )
    opener = urllib.request.build_opener(NoRedirect)
    for attempt in (1, 2):
        try:
            with opener.open(request, timeout=30) as response:
                payload = json.load(response)
                headers = getattr(response, "headers", None)
                _note_rate_limit(headers)
                link = _header(headers, "Link")
        except urllib.error.HTTPError as error:
            try:
                if error.code in (403, 429) and attempt == 1:
                    delay = _retry_after_seconds(error.headers)
                    if delay is not None:
                        _wait(delay)
                        continue
                raise
            finally:
                error.close()
        if not parse_link:
            return payload, None
        return payload, _next_path(link)
    raise ValueError("incomplete evidence page")


def public_read(path):
    # The App intentionally has no Checks/Actions read grants. Public evidence uses
    # no credential; rate limits, denied reads, malformed or truncated data refuse.
    url = f"https://api.github.com/repos/{writer.REPO}/{_evidence_path(path)}"
    payload, next_path = _fetch(url, parse_link=True)
    if isinstance(payload, dict):
        return _Page(payload, next_path)
    return payload


def _collect_pages(read, path, collection, evidence):
    """Every item of `collection`, following Link rel=next until it is exhausted.

    A page error propagates. A short page, a count mismatch, a repeated next
    path, or a page past the hard cap refuses instead of using a partial list.
    """
    collected = []
    seen = set()
    total = None
    current = path
    while current is not None:
        if current in seen or len(seen) >= _MAX_EVIDENCE_PAGES:
            raise ValueError(f"incomplete {evidence} evidence")
        seen.add(current)
        page = read(current)
        if not isinstance(page, dict):
            raise ValueError(f"incomplete {evidence} evidence")
        batch = page.get(collection)
        page_total = page.get("total_count")
        if (
            not isinstance(batch, list)
            or type(page_total) is not int
            or page_total < 0
            or len(batch) > _PAGE_SIZE
        ):
            raise ValueError(f"incomplete {evidence} evidence")
        if total is None:
            total = page_total
        elif page_total != total:
            raise ValueError(f"incomplete {evidence} evidence")
        collected.extend(batch)
        if len(collected) > total:
            raise ValueError(f"incomplete {evidence} evidence")
        next_path = getattr(page, "next_path", None)
        if next_path is not None and (type(next_path) is not str or not next_path):
            raise ValueError(f"incomplete {evidence} evidence")
        if len(collected) == total:
            if next_path:
                raise ValueError(f"incomplete {evidence} evidence")
            return collected
        if not next_path or not batch:
            raise ValueError(f"incomplete {evidence} evidence")
        current = next_path


def context():
    env = os.environ
    if (
        env.get("GITHUB_REPOSITORY") != "Living-Proof-Labs/.github"
        or env.get("GITHUB_REF") != "refs/heads/main"
        or env.get("GITHUB_WORKFLOW_REF") != WORKFLOW_REF
        or env.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}
        or not writer.SHA.fullmatch(env.get("GITHUB_SHA", ""))
    ):
        raise ValueError("receipt requires trusted central workflow")
    return {
        "run_id": int(env["GITHUB_RUN_ID"]),
        "attempt": int(env["GITHUB_RUN_ATTEMPT"]),
        "workflow_sha": env["GITHUB_SHA"],
        "workflow_ref": WORKFLOW_REF,
    }


def save_receipt(plan, head, digest, path):
    record = {
        **context(),
        "repository": writer.REPO,
        "pr": plan["pr"],
        "branch": plan["branch"],
        "main": plan["main"],
        "head": head,
        "plan_digest": digest,
        "created": int(time.time()),
    }
    raw = (json.dumps(record, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def load_receipt(path, digest):
    raw = path.read_bytes()
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("publication receipt digest mismatch")
    record = json.loads(raw)
    current = context()
    if (
        record["repository"] != writer.REPO
        or type(record["pr"]) is not int
        or record["pr"] < 1
        or not writer.BRANCH.fullmatch(record["branch"])
        or any(not writer.SHA.fullmatch(record[key]) for key in ["head", "main"])
        or not re.fullmatch(r"[0-9a-f]{64}", record["plan_digest"])
        or any(record[key] != current[key] for key in ["run_id", "workflow_sha", "workflow_ref"])
        or not 1 <= record["attempt"] <= current["attempt"] <= record["attempt"] + 2
        or not 0 <= time.time() - record["created"] <= 86400
    ):
        raise ValueError("publication receipt is stale or belongs to another run")
    return record


def protections(read):
    rules = read("rules/branches/main")
    queues = [r for r in rules if r["type"] == "merge_queue"]
    checks = [r for r in rules if r["type"] == "required_status_checks"]
    if not queues or not checks:
        raise ValueError("required checks and merge queue must be server-enforced")
    if any(r["parameters"].get("grouping_strategy") != "ALLGREEN" for r in queues):
        raise ValueError("merge queue must require fresh successful merge-group checks")
    required = set()
    for rule in checks:
        if rule["parameters"].get("strict_required_status_checks_policy") is not True:
            raise ValueError("strict required checks missing")
        if rule["parameters"].get("do_not_enforce_on_create") is not False:
            raise ValueError("required checks must explicitly enforce on creation")
        required.update(
            (c["context"], c.get("integration_id"))
            for c in rule["parameters"]["required_status_checks"]
            if type(c.get("integration_id")) is int
        )
    if not {(name, ACTIONS_APP) for name in REQUIRED_CONTEXTS} <= required:
        raise ValueError("trusted required check contexts missing")
    for rule in queues + checks:
        # Missing bypass evidence is not an empty bypass list.
        detail = read(f"rulesets/{int(rule['ruleset_id'])}")
        if detail.get("enforcement") != "active" or detail.get("bypass_actors") != []:
            raise ValueError("active no-bypass ruleset evidence missing")


def successful_checks(record, read):
    head = record["head"]
    runs = _collect_pages(
        read,
        f"actions/runs?head_sha={head}&event=pull_request&per_page={_PAGE_SIZE}",
        "workflow_runs",
        "workflow",
    )
    checks = _collect_pages(
        read,
        f"commits/{head}/check-runs?filter=latest&per_page={_PAGE_SIZE}",
        "check_runs",
        "check",
    )
    snapshot = {}
    seen_check_ids = set()
    for path, names in WORKFLOWS.items():
        matching = [r for r in runs if r.get("path") == path]
        if not matching:
            raise PendingEvidence("trusted workflow has not reported")
        if len(matching) != 1:
            raise ValueError("ambiguous trusted workflow run")
        run = matching[0]
        if run.get("status") in {"queued", "in_progress"} and run.get("conclusion") is None:
            raise PendingEvidence("trusted workflow still pending")
        if (
            run.get("head_sha") != head
            or run.get("event") != "pull_request"
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or run.get("repository", {}).get("full_name") != writer.REPO
            or run.get("head_repository", {}).get("full_name") != writer.REPO
            or not any(
                p.get("number") == record["pr"]
                and p.get("head", {}).get("sha") == head
                and p.get("base", {}).get("sha") == record["main"]
                for p in run.get("pull_requests", [])
            )
        ):
            raise ValueError("workflow did not successfully verify this head and base")
        run_identity = (run.get("id"), run.get("run_attempt"), run.get("check_suite_id"))
        if any(type(value) is not int or value < 1 for value in run_identity):
            raise ValueError("trusted run/attempt/suite identity missing")
        check_identities = []
        for name in names:
            matching = [c for c in checks if c.get("name") == name]
            if not matching:
                raise PendingEvidence("required check has not reported")
            if len(matching) != 1:
                raise ValueError("ambiguous required check")
            check = matching[0]
            if check.get("status") in {"queued", "in_progress"} and check.get("conclusion") is None:
                raise PendingEvidence("required check still pending")
            if (
                check.get("head_sha") != head
                or check.get("app", {}).get("id") != ACTIONS_APP
                or check.get("check_suite", {}).get("id") != run["check_suite_id"]
                or check.get("status") != "completed"
                or check.get("conclusion") != "success"
                or type(check.get("id")) is not int
                or check["id"] < 1
                or check["id"] in seen_check_ids
            ):
                raise ValueError("required check is not trusted successful exact-head evidence")
            seen_check_ids.add(check["id"])
            check_identities.append((name, check["id"]))
        # Freeze every context identity; a successful workflow summary cannot
        # substitute for an omitted or skipped independent check.
        snapshot[path] = (*run_identity, tuple(check_identities))
    return snapshot


def revalidate_checks(record, snapshot, read):
    if successful_checks(record, read) != snapshot:
        raise ValueError("trusted workflow run/attempt/check changed before enqueue")
    # The list and check reads above are not atomic. Read the current run endpoint
    # too, catching a rerun begun during that collection. A race after the last
    # read still requires server-enforced fresh merge-group checks.
    for path, (run_id, attempt, suite, _check_identities) in snapshot.items():
        run = read(f"actions/runs/{run_id}")
        if (
            run.get("id") != run_id
            or run.get("run_attempt") != attempt
            or run.get("check_suite_id") != suite
            or run.get("path") != path
            or run.get("head_sha") != record["head"]
            or run.get("event") != "pull_request"
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
        ):
            raise ValueError("trusted workflow attempt superseded or no longer successful")
