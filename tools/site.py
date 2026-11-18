# SPDX-License-Identifier: Apache-2.0
"""Assemble the GitHub Pages site from a run's capability-report artifacts.

The verifier's grade and trial counts are read from report.json, never recomputed.
A stored grade of ``UNSUPPORTED`` is labelled "not supported" on the history
pages. The report files stay the verifier's. A compared run keeps those counts
null and carries ``reproducibility.statement``
(or the stored finding, when no statement was recorded) onto the history record.
The public run page is the reporter's HTML: this module asks the pinned reporter to
render it with this site's evidence paths, retention and a link to the run history.
The incoming SHA256SUMS are checked, then Actions URLs are removed from the
published report and SHA256SUMS is rewritten over those published files. Per
verification and configuration:

    <robot>/<variant>/runs/<run-id>/   the styled report as index.html;
                                       report.{html,json,md,txt}, SHA256SUMS, run.json
                                       (no report.html.orig; artifact ids stay in run.json)
                                       and each thresholds.json the evaluator results
                                       cite, digest-checked. These grading inputs are kept
                                       with the report for as long as the run's history
                                       is, and are never pruned with the evidence copies.
    <robot>/<variant>/runs/<run-id>/evidence/<name>/
                                       site copies of the run's linked evidence (MCAP,
                                       videos), with sha256 in run.json. Copies older
                                       than the retention setting are removed on publish.
    <robot>/<variant>/latest/          a redirect to the newest run, and nothing else
    <robot>/<variant>/index.html       run history
    index.html                         every robot/variant with grade and counts,
                                       or the stored statement for compared repeats

A run's commit carries the bundle release tag (`commit.tag`) only when a vX.Y.Z tag resolves
to that same commit in the checkout (`bundle_tag`); otherwise the commit stands alone. Runs
already published are never retagged.

Earlier runs come from the previous Pages artifact (`restore`), so history survives
without committing anything to git. If that artifact is listed but the download
fails or the archive is corrupt, restore fails instead of publishing a site that
drops those runs.
"""

import argparse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import html
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PAGES_ARTIFACT = "github-pages"
SEGMENT = re.compile(r"[a-z0-9][a-z0-9.-]*")
RUN_ID = re.compile(r"[0-9]+(-[0-9]+)?")
REPORT_FILES = ("report.html", "report.json", "report.md", "report.txt")
# A run's linked evidence (MCAP and videos, about 5 MB for walk-hallway) is copied into
# the site so it outlives the Actions artifacts and needs no GitHub sign-in. Pages sites
# are limited to 1 GB; past this per-run budget an artifact is listed but not copied.
MAX_RUN_EVIDENCE_BYTES = 25_000_000
ORIGINAL_HTML = "report.html.orig"  # never published; deleted if a restore still has it
SETTINGS = ROOT / "verifications" / "settings.toml"
# Reporter anchors this site must not publish. The public page is re-rendered instead.
ARTIFACT_LINK = re.compile(
    r"<a href=(['\"])(https://github\.com/[^'\"<>]+/actions/runs/[0-9]+/artifacts/([0-9]+))\1>"
    r"(.*?)</a>",
    re.S,
)
ACTIONS_ANCHOR = re.compile(
    r"<a\s[^>]*?href=(['\"])([^'\"<>]*/actions/[^'\"<>]*)\1[^>]*>(.*?)</a>", re.S | re.I
)
ACTIONS_URL = re.compile(r"https://github\.com/\S*?/actions/\S*", re.I)
ARTIFACT_WORD = re.compile(r"artifacts?", re.I)
# Copied evidence. Report pages and run.json are not under this suffix set.
MEDIA_SUFFIXES = {
    ".mcap",
    ".mp4",
    ".webm",
    ".mov",
    ".mkv",
    ".avi",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
}
SITE_PATH = re.compile(r"[A-Za-z0-9._~!$&'()*+,;=@%/-]{1,240}")
HISTORY_LABEL = "Run history"
# Evidence 2.0.0 stores a trial index in the reporter's seed field.
# livingproof-capability-report v3.3.0 writes "trial N". Older reporters write "seed N".
# Rewrite only a leading "seed ", so a report that already says "trial N" is not labelled twice.
SEED_LABEL = re.compile(r"^seed (?=\d)")
# A bundle release is a plain vX.Y.Z tag of this repository (the shared semantic-release
# workflow's tagFormat). Anything else, a prerelease or a branch-like name, is never shown.
BUNDLE_TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
MARKER = "site.json"  # written by every build; its presence on the live site means history
LOCAL_TZ = ZoneInfo("Australia/Sydney")  # tooltip dates on the public site (#129)


class SiteError(Exception):
    pass


# ---------------------------------------------------------------- definitions


def targets(verifications: Path = ROOT / "verifications") -> list[dict]:
    """One site target per (definition, configuration), from the reviewed locks."""
    found = []
    for lock_path in sorted(verifications.glob("*.lock.json")):
        lock = json.loads(lock_path.read_text())
        identifier = lock_path.name.removesuffix(".lock.json")
        repository = lock["robot"]["repository"]
        robot = repository.rsplit("/", 1)[1].removeprefix("livingproof-")
        rest = identifier.removeprefix(robot + "-")
        if rest == identifier:
            raise SiteError(f"{identifier}: id must start with the robot name {robot!r}")
        configurations = lock["robot"]["configurations"]
        for configuration in configurations:
            variant = rest if len(configurations) == 1 else f"{rest}-{configuration}"
            for segment in (robot, variant):
                if not SEGMENT.fullmatch(segment):
                    raise SiteError(f"{identifier}: unsafe path segment {segment!r}")
            found.append(
                {
                    "id": identifier,
                    "configuration": configuration,
                    "robot": robot,
                    "variant": variant,
                    "artifact": f"{identifier}-capability-report-{configuration}",
                    "lock": lock,
                }
            )
    return found


# ---------------------------------------------------------------- restore

# Five downloads. The waits after a failure are 10, 20, 40 and 80 seconds, each
# multiplied by a jitter in [0.8, 1.2], so a download that keeps failing waits
# about two to three minutes and then the publish stops.
DOWNLOAD_ATTEMPTS = 5
DOWNLOAD_BACKOFF_BASE = 10
DOWNLOAD_JITTER = (0.8, 1.2)
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Raise on 3xx so the download loop follows the blob hop itself.

    The artifact URL returns 302 to blob storage. The 503 that failed publish
    ("Egress is over the account limit") was that second response. Each retry
    starts again at the artifact URL, follows the new Location, and does not
    send the GitHub token to the blob host.
    """

    def http_error_302(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _request(url: str, token: str | None):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token:
        # Not forwarded on the redirect to the artifact storage host.
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(request, timeout=120)


def _open_once(url: str, token: str | None):
    """One GET that does not follow redirects. The token is the caller's decision."""
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token:
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=120)


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc


def _retryable(error: BaseException) -> bool:
    """5xx, 429, and connection failures. A 404 is the server's answer, not a blip."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 429 or 500 <= error.code < 600
    # HTTPError is an OSError and was classified above. Everything else here is a
    # dropped connection, a timeout, or a body cut off mid-download.
    return isinstance(error, (OSError, http.client.HTTPException))


def _error_text(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        where = getattr(error, "url", "") or ""
        text = f"HTTP {error.code} {error.reason}".rstrip()
        return f"{text} from {where}" if where else text
    if isinstance(error, urllib.error.URLError):
        return f"connection error: {error.reason}"
    return f"{type(error).__name__}: {error}"


def _backoff_seconds(failed_attempt: int) -> float:
    """Wait after failed attempt 1, 2, 3, 4: about 10s, 20s, 40s, 80s, with jitter."""
    return DOWNLOAD_BACKOFF_BASE * (2 ** (failed_attempt - 1)) * random.uniform(*DOWNLOAD_JITTER)


def _redirect_target(current: str, error: urllib.error.HTTPError) -> str:
    headers = error.headers
    location = headers.get("Location") if headers is not None else None
    if not location:
        raise SiteError(
            f"Redirect ({error.code}) from {current} had no Location header. "
            "Refusing to publish a site without its earlier runs."
        ) from error
    nxt = urllib.parse.urljoin(current, location.strip())
    if urllib.parse.urlsplit(nxt).scheme not in ("http", "https"):
        raise SiteError(f"Refusing to follow a redirect from {current} to {nxt}.") from error
    return nxt


def _read_following_redirects(url: str, token: str | None) -> bytes:
    """GET `url` and every redirect. The token is sent only to `url`'s host."""
    current = url
    home = _host(url)
    for _hop in range(_MAX_REDIRECTS + 1):
        try:
            with _open_once(current, token if _host(current) == home else None) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            try:
                if error.code not in _REDIRECTS:
                    raise
                current = _redirect_target(current, error)
            finally:
                error.close()
            continue
    raise SiteError(
        f"Too many redirects downloading the Pages artifact (stopped at {current}). "
        "Refusing to publish a site without its earlier runs."
    )


def _download_pages_archive(url: str, token: str | None) -> bytes:
    """Download one Pages zip, retrying each hop of the redirect chain.

    Each attempt starts at the artifact URL again, so a fresh 302 is followed and
    the blob fetch is inside the retry. A signed blob URL from the previous try
    is not reused.
    """
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            body = _read_following_redirects(url, token)
        except SiteError:
            raise
        except Exception as error:
            detail = _error_text(error)
            if attempt < DOWNLOAD_ATTEMPTS and _retryable(error):
                delay = _backoff_seconds(attempt)
                print(
                    f"Pages artifact download attempt {attempt}/{DOWNLOAD_ATTEMPTS} "
                    f"failed: {detail}; retrying in {delay:.1f}s.",
                    flush=True,
                )
                time.sleep(delay)
                continue
            print(
                f"Pages artifact download attempt {attempt}/{DOWNLOAD_ATTEMPTS} failed: {detail}.",
                flush=True,
            )
            noun = "attempt" if attempt == 1 else "attempts"
            raise SiteError(
                f"Could not download the previous {PAGES_ARTIFACT} artifact from {url} "
                f"after {attempt} {noun} ({detail}). "
                "Refusing to publish a site without its earlier runs."
            ) from error
        print(
            f"Pages artifact download attempt {attempt}/{DOWNLOAD_ATTEMPTS} "
            f"succeeded ({len(body)} bytes).",
            flush=True,
        )
        return body
    raise SiteError(
        f"Could not download the previous {PAGES_ARTIFACT} artifact from {url}. "
        "Refusing to publish a site without its earlier runs."
    )


def _list_pages_artifacts(repository: str, token: str | None, api: str) -> tuple[list[dict], str]:
    """Pages artifacts, and why the list is empty when it is.

    A 404 on the first page is "no artifact", the same as an empty list. Any other
    listing failure refuses the publish: we cannot tell whether history exists.
    """
    artifacts: list[dict] = []
    page = 1
    while True:
        url = (
            f"{api}/repos/{repository}/actions/artifacts?name={PAGES_ARTIFACT}"
            f"&per_page=100&page={page}"
        )
        try:
            with _request(url, token) as response:
                batch = json.load(response)["artifacts"]
        except urllib.error.HTTPError as error:
            if error.code == 404 and page == 1:
                error.close()
                return [], "artifact listing returned HTTP 404"
            detail = _error_text(error)
            error.close()
            raise SiteError(
                f"Could not list {PAGES_ARTIFACT} artifacts: {detail}. "
                "Refusing to publish a site without its earlier runs."
            ) from error
        except OSError as error:
            raise SiteError(
                f"Could not list {PAGES_ARTIFACT} artifacts: {_error_text(error)}. "
                "Refusing to publish a site without its earlier runs."
            ) from error
        artifacts.extend(batch)
        if len(batch) < 100:
            return artifacts, "none listed"
        page += 1


def _unpack_pages_archive(archive: bytes, destination: Path) -> int:
    """Unpack a downloaded Pages zip. A partial or corrupt body fails the publish."""
    try:
        return extract_pages(archive, destination)
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, KeyError) as error:
        raise SiteError(
            f"The previous {PAGES_ARTIFACT} artifact downloaded but is not a valid "
            f"Pages archive ({type(error).__name__}: {error}). "
            "Refusing to publish a site without its earlier runs."
        ) from error


def latest_pages_artifact(artifacts: list[dict], branch: str, current_run: int | None):
    """Newest Pages artifact from an earlier run on `branch`; error if it expired."""
    candidates = [
        a
        for a in artifacts
        if a.get("name") == PAGES_ARTIFACT
        and (a.get("workflow_run") or {}).get("head_branch") == branch
        and (a.get("workflow_run") or {}).get("id") != current_run
    ]
    if not candidates:
        return None
    newest = max(candidates, key=lambda a: a["created_at"])
    if newest.get("expired"):
        raise SiteError(
            f"The last Pages artifact ({newest['id']}, {newest['created_at']}) "
            "has expired; restoring would drop the published run history."
        )
    return newest


def extract_pages(archive: bytes, destination: Path) -> int:
    """Unpack an upload-pages-artifact zip (one artifact.tar inside); return files."""
    with zipfile.ZipFile(io.BytesIO(archive)) as outer:
        tar_bytes = outer.read("artifact.tar")
    count = 0
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise SiteError(f"Unsafe path in the Pages artifact: {member.name}")
            if member.isfile():
                count += 1
            elif not member.isdir():
                raise SiteError(f"Unexpected entry in the Pages artifact: {member.name}")
        tar.extractall(destination, filter="data")
    return count


def _fetch_status(url: str) -> int:
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def live_history(site_url: str) -> str | None:
    """The live page that shows published history (site.json, else index.html), or None.

    Only a 404 for both counts as "nothing published"; any other answer is an error, so a
    flaky host never lets a run start a fresh site over existing history.
    """
    base = site_url.rstrip("/") + "/"
    for name in (MARKER, "index.html"):
        try:
            status = _fetch_status(base + name)
        except OSError as error:
            raise SiteError(f"Cannot check {base}{name} for published history: {error}")
        if status == 200:
            return base + name
        if status != 404:
            raise SiteError(f"Cannot check {base}{name} for published history: HTTP {status}")
    return None


def restore(
    destination: Path,
    repository: str,
    branch: str,
    current_run: int | None,
    token: str | None,
    api: str = "https://api.github.com",
    dry_run: bool = False,
    site_url: str | None = None,
    reset_history: bool = False,
):
    destination.mkdir(parents=True, exist_ok=True)
    if reset_history:
        # Read first: reset is the way out when the last artifact expired, so nothing below
        # (the listing, the expiry check, the live-site check) may refuse it.
        print(f"reset-history set: starting a new site; {PAGES_ARTIFACT} history is dropped.")
        return None
    artifacts, why_empty = _list_pages_artifacts(repository, token, api)
    artifact = latest_pages_artifact(artifacts, branch, current_run)
    if artifact is None:
        if not site_url:
            raise SiteError(
                f"No earlier {PAGES_ARTIFACT} artifact on {branch} ({why_empty}) and no "
                "--site-url to check for published history; pass --site-url, or "
                "--reset-history to start anew."
            )
        published = live_history(site_url)
        if published:
            raise SiteError(
                f"No earlier {PAGES_ARTIFACT} artifact on {branch} ({why_empty}), but "
                f"{published} is live: the artifact probably expired. Refusing to publish "
                "a site without its run history; run the workflow with reset-history to "
                "start anew."
            )
        print(
            f"WARNING: No earlier {PAGES_ARTIFACT} artifact on {branch} ({why_empty}) and "
            "nothing is published: starting a new site without run history.",
            flush=True,
        )
        return None
    run = artifact["workflow_run"]["id"]
    print(
        f"Restoring run history from artifact {artifact['id']} (run {run}, "
        f"{artifact['created_at']})."
    )
    if dry_run:
        return artifact
    count = _unpack_pages_archive(
        _download_pages_archive(artifact["archive_download_url"], token), destination
    )
    print(f"Restored {count} files.")
    return artifact


# ---------------------------------------------------------------- build


def check_reports(directory: Path):
    sums = directory / "SHA256SUMS"
    if not sums.is_file():
        raise SiteError(f"{directory}: missing SHA256SUMS")
    listed = {}
    for line in sums.read_text().splitlines():
        digest, _, name = line.partition("  ")
        listed[name.strip()] = digest
    for name in REPORT_FILES:
        path = directory / name
        if name not in listed:
            raise SiteError(f"{directory}: SHA256SUMS has no entry for {name}")
        if not path.is_file():
            raise SiteError(f"{directory}: missing {name}")
        if listed[name] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise SiteError(f"{path}: does not match SHA256SUMS")
    check_evaluators(read_report(directory / "report.json"), directory / "report.json")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def check_evaluators(report: object, where: Path) -> None:
    """Refuse a report whose assessed trials lack an evaluator result or its descriptor.

    Every trial in ``source_bundle.results`` needs at least one evaluator result, and
    each result's ``descriptor`` needs a ``title`` and a ``purpose``. Without them the
    page shows a placeholder evaluator ("No evaluator descriptor was stored"). A report
    whose every summary is ``UNSUPPORTED`` and that assessed no trial (scenario not
    declared) has nothing to describe and is published as "not supported".
    """
    if not isinstance(report, dict):
        return  # outcome() names a report.json that is not an object
    bundle = report.get("source_bundle")
    results = bundle.get("results") if isinstance(bundle, dict) else None
    summaries = report.get("summaries")
    unsupported = (
        isinstance(summaries, list)
        and bool(summaries)
        and all(isinstance(s, dict) and s.get("grade") == "UNSUPPORTED" for s in summaries)
    )
    if not results:
        if unsupported:
            return
        raise SiteError(
            f"{where}: no assessed trials in source_bundle.results, so no evaluator "
            "result to show. Only an UNSUPPORTED report with no trials run publishes "
            "without one."
        )
    for index, result in enumerate(results, start=1):
        trial = result.get("id") if isinstance(result, dict) else None
        trial = trial if _text(trial) else f"trial {index}"
        evaluated = result.get("evaluator_results") if isinstance(result, dict) else None
        if not isinstance(evaluated, list) or not evaluated:
            raise SiteError(
                f"{where}: {trial} has no evaluator result. The report would show a "
                "placeholder evaluator. Append the evaluator results to the bundle "
                "before rendering the report."
            )
        for item in evaluated:
            evaluator = item.get("evaluator") if isinstance(item, dict) else None
            name = evaluator.get("id") if isinstance(evaluator, dict) else None
            name = name if _text(name) else "(no id)"
            descriptor = item.get("descriptor") if isinstance(item, dict) else None
            if not isinstance(descriptor, dict):
                raise SiteError(f"{where}: {trial} evaluator {name} has no descriptor")
            for field in ("title", "purpose"):
                if not _text(descriptor.get(field)):
                    raise SiteError(f"{where}: {trial} evaluator {name} descriptor has no {field}")


REPORT_VERSIONS = {"1.0.0"}  # report.json shapes this site knows how to read
SUMMARY_FIELDS = ("capability", "system", "grade", "k", "n", "required_passes")


def read_report(path: Path) -> object:
    """report.json parsed, or a SiteError naming it when it is not valid JSON."""
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise SiteError(f"{path}: not valid JSON ({error.msg} at line {error.lineno})") from None


def _stored_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def reproducibility_statement(summary: dict) -> str | None:
    """``reproducibility.statement`` for a compared run. Nothing is filled in."""
    block = summary.get("reproducibility")
    if not isinstance(block, dict) or block.get("compared") is not True:
        return None
    return _stored_text(block.get("statement"))


def reproducibility_finding(summary: dict) -> str | None:
    """Finding detail stored on a compared run, or None."""
    block = summary.get("reproducibility")
    if not isinstance(block, dict) or block.get("compared") is not True:
        return None
    finding = block.get("finding")
    if isinstance(finding, dict):
        return _stored_text(finding.get("detail"))
    return _stored_text(finding)


def outcome(report: dict, where: str = "report.json") -> list[dict]:
    """The verifier's grade and counts as the reporter recorded them (no regrading).

    Compared repeats keep null ``k``, ``n`` and ``required_passes``. The stored
    statement is copied onto the history record; a finding is copied only when
    that statement is absent.
    """
    if not isinstance(report, dict):
        raise SiteError(f"{where}: expected a JSON object, found {type(report).__name__}")
    version = report.get("report_version")
    if not isinstance(version, str) or version not in REPORT_VERSIONS:
        raise SiteError(
            f"{where}: report_version {version!r} is not one this site reads "
            f"({', '.join(sorted(REPORT_VERSIONS))}); update tools/site.py for the new shape"
        )
    summaries = report.get("summaries")
    if not isinstance(summaries, list) or not summaries:
        raise SiteError(f"{where}: report_version {version} without a non-empty summaries list")
    for index, s in enumerate(summaries):
        if not isinstance(s, dict):
            raise SiteError(f"{where}: summaries[{index}] is {type(s).__name__}, not an object")
        missing = [f for f in SUMMARY_FIELDS if f not in s]
        if not isinstance(s.get("system"), dict) or "id" not in s["system"]:
            missing.append("system.id")
        if missing:
            raise SiteError(f"{where}: summaries[{index}] lacks {', '.join(missing)}")
    rows = []
    for s in summaries:
        row = {
            "capability": s["capability"],
            "system": s["system"]["id"],
            "grade": s["grade"],
            "passed": s["k"],
            "trials": s["n"],
            "required": s["required_passes"],
        }
        statement = reproducibility_statement(s)
        if statement is not None:
            row["statement"] = statement
        else:
            finding = reproducibility_finding(s)
            if finding is not None:
                row["finding"] = finding
        rows.append(row)
    return rows


def bundle_tag(commit: str, repository: Path = ROOT) -> str | None:
    """The bundle release tag that resolves to ``commit`` itself, or None.

    Ordering is explicit: a run shows a tag only when the tag names the run's own commit
    in this checkout, annotated or lightweight. A tag for another commit, no tag, a name that
    is not a plain vX.Y.Z or a commit missing from the checkout all give None, and the run
    keeps its commit alone. Nothing is guessed from a branch or from the newest tag.
    """
    if not COMMIT_SHA.fullmatch(commit):
        return None

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(repository), *args], capture_output=True, text=True, check=False
        )
        return done.stdout.strip() if done.returncode == 0 else ""

    found = []
    for name in git("tag", "--list", "--points-at", commit).splitlines():
        if (
            BUNDLE_TAG.fullmatch(name)
            and git("rev-parse", f"refs/tags/{name}^{{commit}}") == commit
        ):
            found.append(name)
    # Several tags on one commit are not expected; the highest version is the release.
    return max(found, key=lambda name: tuple(map(int, name[1:].split(".")))) if found else None


def _software_version(target: dict, report: object = None) -> str | None:
    if isinstance(report, dict):
        summaries = report.get("summaries")
        if isinstance(summaries, list):
            for summary in summaries:
                if not isinstance(summary, dict):
                    continue
                system = summary.get("system")
                if isinstance(system, dict):
                    version = _stored_text(system.get("version"))
                    if version is not None:
                        return version
    for entry in target.get("lock", {}).get("configurations") or []:
        if isinstance(entry, dict) and entry.get("name") == target.get("configuration"):
            version = _stored_text(entry.get("version"))
            if version is not None:
                return version
    return None


def _evaluator_documents(report: object) -> list[dict]:
    if not isinstance(report, dict):
        return []
    docs: list[dict] = []
    bundle = report.get("source_bundle")
    if isinstance(bundle, dict):
        results = bundle.get("results")
        if isinstance(results, list):
            for result in results:
                if not isinstance(result, dict):
                    continue
                for key in ("evaluator_results",):
                    items = result.get(key)
                    if isinstance(items, list):
                        docs.extend(d for d in items if isinstance(d, dict))
                prov = result.get("provenance")
                if isinstance(prov, dict):
                    items = prov.get("evaluator_results")
                    if isinstance(items, list):
                        docs.extend(d for d in items if isinstance(d, dict))
                    one = prov.get("evaluator_result")
                    if isinstance(one, dict):
                        docs.append(one)
                original = result.get("original_result")
                if isinstance(original, dict):
                    docs.append(original)
        assessment = bundle.get("assessment")
        integrity = assessment.get("integrity_results") if isinstance(assessment, dict) else None
        if isinstance(integrity, dict):
            for item in integrity.values():
                if isinstance(item, dict):
                    docs.append(item)
                elif isinstance(item, list):
                    docs.extend(d for d in item if isinstance(d, dict))
    summaries = report.get("summaries")
    if isinstance(summaries, list):
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            items = summary.get("evaluator_results")
            if isinstance(items, list):
                docs.extend(d for d in items if isinstance(d, dict))
    return docs


def _finding_summary(findings: list, descriptor: dict) -> str | None:
    for item in findings:
        if isinstance(item, dict):
            text = _stored_text(item.get("text")) or _stored_text(item.get("summary"))
            if text is not None:
                return text
        elif isinstance(item, str):
            text = _stored_text(item)
            if text is not None:
                return text
    return _stored_text(descriptor.get("details_copy"))


def failing_evaluators(report: object) -> list[dict]:
    """Evaluators that spoke on a non-passing run, deduplicated by evaluator id."""
    if not isinstance(report, dict):
        return []
    summaries = report.get("summaries")
    if isinstance(summaries, list) and summaries:
        grades = [s.get("grade") for s in summaries if isinstance(s, dict)]
        if grades and all(g in ("PASS", "UNSUPPORTED") for g in grades):
            return []
    seen: dict[str, dict] = {}
    for doc in _evaluator_documents(report):
        ev = doc.get("evaluator") if isinstance(doc.get("evaluator"), dict) else {}
        ev_id = _stored_text(ev.get("id"))
        if ev_id is None:
            continue
        desc = doc.get("descriptor") if isinstance(doc.get("descriptor"), dict) else {}
        raw_findings = (
            desc.get("findings") if isinstance(desc.get("findings"), list) else doc.get("findings")
        )
        findings = raw_findings if isinstance(raw_findings, list) else []
        grade = desc.get("grade") if "grade" in desc else doc.get("grade")
        integrity = doc.get("integrity") if isinstance(doc.get("integrity"), dict) else {}
        run_info = integrity.get("run") if isinstance(integrity.get("run"), dict) else {}
        status = run_info.get("test_system_status")
        spoke = (
            bool(findings)
            or grade == "FAIL"
            or (isinstance(grade, (int, float)) and grade < 100)
            or (isinstance(status, str) and status.upper() in {"ERROR", "WARN", "FAIL"})
        )
        if not spoke:
            continue
        title = _stored_text(ev.get("display_name")) or _stored_text(desc.get("title")) or ev_id
        summary = _finding_summary(findings, desc)
        if ev_id not in seen:
            entry = {"id": ev_id, "title": title, "href": f"#evaluator-{ev_id}"}
            if summary is not None:
                entry["summary"] = summary
            seen[ev_id] = entry
        elif "summary" not in seen[ev_id] and summary is not None:
            seen[ev_id]["summary"] = summary
    return list(seen.values())


def provenance(target: dict, run: dict, report: object = None) -> dict:
    lock, server = target["lock"], run["server"]
    releases = lock["releases"]
    robot = lock["robot"]
    commit = {
        "sha": run["commit"],
        "url": f"{server}/{run['repository']}/commit/{run['commit']}",
    }
    if run.get("tag") is not None:  # bundle_tag() resolved it to this commit
        if not BUNDLE_TAG.fullmatch(run["tag"]):
            raise SiteError(f"Unexpected bundle tag {run['tag']!r}")
        commit["tag"] = run["tag"]
    robot_ref = _stored_text(robot.get("tag") or robot.get("ref"))
    robot_tag = robot_ref if robot_ref and not re.fullmatch(r"[0-9a-f]{40}", robot_ref) else None
    robot_entry = {
        "repository": robot["repository"],
        "commit": robot["commit"],
        "configuration": target["configuration"],
        "url": (
            f"{server}/{robot['repository']}/releases/tag/{robot_tag}"
            if robot_tag
            else f"{server}/{robot['repository']}/tree/{robot['commit']}"
        ),
    }
    if robot_tag is not None:
        robot_entry["tag"] = robot_tag
    software_version = _software_version(target, report)
    if software_version is not None:
        robot_entry["software_version"] = software_version
    poster = run.get("poster")
    if not isinstance(poster, dict) or not _stored_text(poster.get("name")):
        poster = {"type": "ci", "name": "CI"}
    return {
        "run": {
            "id": run["id"],
            "url": f"{server}/{run['repository']}/actions/runs/{run['id'].split('-')[0]}",
            "created": run["created"],
        },
        "commit": commit,
        "robot": robot_entry,
        "poster": poster,
        "releases": {
            name: {**value, "url": f"{server}/{value['repository']}/releases/tag/{value['tag']}"}
            for name, value in releases.items()
        },
        "definition": f"verifications/{target['id']}.toml",
    }


THRESHOLDS_NAME = "thresholds.json"


def _cited_path(uri: object) -> str | None:
    """A cited thresholds file as a safe path relative to the report, or None."""
    if not isinstance(uri, str) or not uri or "\\" in uri or "://" in uri or uri.startswith("/"):
        return None
    parts = PurePosixPath(uri).parts
    if not parts or any(part in ("", ".", "..") for part in parts) or parts[-1] != THRESHOLDS_NAME:
        return None
    return "/".join(parts)


def grading_inputs(source: Path, report: object) -> list[dict]:
    """The thresholds files the report's evaluator results cite, checked against the artifact.

    Every ``inputs`` item with role ``thresholds`` (or a ``thresholds.json`` uri) must name
    a path inside the report artifact. That file must exist and its sha256 must be the
    cited one. One path cited with two digests is a substitution. An item the incoming
    SHA256SUMS lists must also match it. Returns ``[{role, path, sha256, bytes}]`` sorted
    by path; empty for a report that cites none (from before the thresholds export).
    """
    bundle = report.get("source_bundle") if isinstance(report, dict) else None
    results = bundle.get("results") if isinstance(bundle, dict) else None
    cited: dict[str, str] = {}
    for result in results if isinstance(results, list) else []:
        evaluated = result.get("evaluator_results") if isinstance(result, dict) else None
        for item in evaluated if isinstance(evaluated, list) else []:
            inputs = item.get("inputs") if isinstance(item, dict) else None
            for entry in inputs if isinstance(inputs, list) else []:
                if not isinstance(entry, dict):
                    continue
                uri = entry.get("uri")
                named = (
                    isinstance(uri, str) and uri.rstrip("/").rsplit("/", 1)[-1] == THRESHOLDS_NAME
                )
                if entry.get("role") != "thresholds" and not named:
                    continue
                relative = _cited_path(uri)
                if relative is None:
                    raise SiteError(
                        f"{source}: substituted: an evaluator result cites {uri!r}, not a "
                        f"{THRESHOLDS_NAME} inside the report"
                    )
                digest = entry.get("sha256")
                previous = cited.setdefault(relative, digest)
                if previous != digest:
                    raise SiteError(
                        f"{source}: substituted: {relative} is cited with two different "
                        f"sha256 values ({previous} and {digest})"
                    )
    listed = {}
    sums = source / "SHA256SUMS"
    if sums.is_file():
        for line in sums.read_text().splitlines():
            digest, _, name = line.partition("  ")
            listed[name.strip()] = digest
    found = []
    for relative, digest in sorted(cited.items()):
        path = source.joinpath(*PurePosixPath(relative).parts)
        current = path
        while current != source:
            if current.is_symlink():
                raise SiteError(f"{source}: substituted: {relative} is a symlink")
            current = current.parent
        if not path.is_file():
            raise SiteError(
                f"{source}: missing: {relative} is cited by the evaluator results but is not "
                "in the report artifact. capability-verifier v1.21.0 or later copies it there."
            )
        data = path.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if relative in listed and listed[relative] != actual:
            raise SiteError(f"{path}: does not match SHA256SUMS")
        if actual != digest:
            raise SiteError(
                f"{source}: altered: {relative} sha256 is {actual}, not the cited {digest!r}"
            )
        found.append({"role": "thresholds", "path": relative, "sha256": actual, "bytes": data})
    return found


def add_run(
    site: Path,
    reports: Path,
    target: dict,
    run: dict,
    evidence: Path | None = None,
    artifacts: list[dict] | None = None,
) -> Path:
    source = reports / target["artifact"]
    if not source.is_dir():
        raise SiteError(f"No report artifact {target['artifact']} in {reports}")
    check_reports(source)
    inputs = grading_inputs(source, read_report(source / "report.json"))
    if not RUN_ID.fullmatch(run["id"]):
        raise SiteError(f"Unexpected run id {run['id']!r}")
    folder = site / target["robot"] / target["variant"] / "runs" / run["id"]
    if folder.exists():
        shutil.rmtree(folder)  # a re-run attempt of the same run replaces itself
    folder.mkdir(parents=True)
    for name in (*REPORT_FILES, "SHA256SUMS"):
        shutil.copyfile(source / name, folder / name)
    for item in inputs:
        destination = folder.joinpath(*PurePosixPath(item["path"]).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(item["bytes"])
    report_data = read_report(folder / "report.json")
    record = {
        "robot": target["robot"],
        "variant": target["variant"],
        "verification": target["id"],
        "outcome": outcome(report_data, str(folder / "report.json")),
        **provenance(target, run, report_data),
    }
    failing = failing_evaluators(report_data)
    if failing:
        record["failing_evaluators"] = failing
    if inputs:
        record["grading_inputs"] = [
            {key: item[key] for key in ("role", "path", "sha256")} for item in inputs
        ]
    if evidence is not None:
        preserved = preserve_evidence(folder, target, evidence, artifacts or [])
        if preserved is not None:
            record["evidence"] = preserved
    (folder / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    return folder


def fetch_artifacts(
    repository: str, run_id: str, token: str | None, api: str = "https://api.github.com"
) -> list[dict]:
    """Metadata (id, name, expires_at, digest, size) of every artifact of a workflow run."""
    found, page = [], 1
    while True:
        with _request(
            f"{api}/repos/{repository}/actions/runs/{run_id.split('-')[0]}/artifacts"
            f"?per_page=100&page={page}",
            token,
        ) as response:
            batch = json.load(response)["artifacts"]
        found += batch
        if len(batch) < 100:
            return found
        page += 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def preserve_evidence(
    folder: Path, target: dict, evidence: Path, artifacts: list[dict]
) -> list[dict] | None:
    """Copy the evidence the report links to into ``folder/evidence``; describe every link.

    The links come from the run's ``<id>-links-<configuration>`` artifact (what the report
    rendered). Each record keeps the original Actions artifact (id, name, URL, expiry, its
    digest) next to the site copy's files and sha256, so identity stays tied to the
    assessment. An artifact not downloaded, or past the per-run budget, is listed with
    ``preserved: false`` and a reason. None when the run has no links artifact.
    """
    links_dir = evidence / f"{target['id']}-links-{target['configuration']}"
    sources = sorted(links_dir.glob("*.json")) if links_dir.is_dir() else []
    if not sources:
        return None
    links = json.loads(sources[0].read_text())["links"]
    by_id = {str(a["id"]): a for a in artifacts}
    budget = MAX_RUN_EVIDENCE_BYTES
    records = []
    for link in links:
        artifact_id = link["url"].rstrip("/").rsplit("/", 1)[1]
        meta = by_id.get(artifact_id, {})
        name = meta.get("name")
        record = {k: link[k] for k in ("label", "kind", "seed", "status") if k in link} | {
            "artifact": {
                "id": artifact_id,
                "name": name,
                "url": link["url"],
                "expires_at": meta.get("expires_at"),
                "digest": meta.get("digest"),
                "size": meta.get("size_in_bytes"),
            },
            "preserved": False,
            "files": [],
        }
        source = evidence / name if name and SEGMENT.fullmatch(name) else None
        files = (
            sorted(p for p in source.rglob("*") if p.is_file() and not p.is_symlink())
            if source and source.is_dir()
            else []
        )
        size = sum(p.stat().st_size for p in files)
        if not files:
            record["reason"] = "not downloaded when this run was published"
        elif size > budget:
            record["reason"] = f"over the site's per-run evidence budget ({size} bytes)"
        else:
            budget -= size
            for p in files:
                relative = PurePosixPath("evidence", name, *p.relative_to(source).parts)
                destination = folder.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, destination)
                record["files"].append(
                    {
                        "path": str(relative),
                        "sha256": _sha256(destination),
                        "bytes": p.stat().st_size,
                    }
                )
            record["preserved"] = True
        records.append(record)
    return records


def runs(site: Path) -> dict:
    """{(robot, variant): [run records, newest first]} from every runs/*/run.json."""
    found = {}
    for path in site.glob("*/*/runs/*/run.json"):
        record = json.loads(path.read_text())
        record["folder"] = path.parent.name
        found.setdefault((path.parts[-5], path.parts[-4]), []).append(record)
    for records in found.values():
        records.sort(key=lambda r: (r["run"]["created"], r["folder"]), reverse=True)
    return found


STYLE = """body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:72rem;padding:0 1rem;
color:#1b1f24}table{border-collapse:collapse}td,th{border:1px solid #d0d7de;padding:.35rem .6rem;
text-align:left}.PASS{color:#1a7f37;font-weight:600}.FAIL{color:#cf222e;font-weight:600}
.INSUFFICIENT_EVIDENCE{color:#9a6700;font-weight:600}
.UNSUPPORTED{color:#57606a;font-weight:600}
dl{display:grid;grid-template-columns:max-content 1fr;gap:.2rem 1rem}dt{font-weight:600}
code{font-size:.9em}"""


def page(title: str, body: str, up: str = "") -> str:
    nav = f'<p><a href="{up}">← All verification reports</a></p>' if up else ""
    return (
        f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
        f"<body>{nav}<h1>{html.escape(title)}</h1>\n{body}\n</body></html>\n"
    )


def a(url: str, text: str) -> str:
    return f'<a href="{html.escape(url)}">{html.escape(text)}</a>'


ACTIONS_HREF = re.compile(r"""(?:href|src)\s*=\s*["']?[^"'\s>]*/actions/""", re.I)


def actions_links(site: Path) -> list[str]:
    """Pages in SITE that still link to GitHub Actions (sign-in required, then expiry)."""
    return sorted(
        str(page.relative_to(site))
        for page in site.rglob("*.html")
        if ACTIONS_HREF.search(page.read_text(errors="replace"))
    )


# History written before statements existed stores null counts and no statement key.
# Show this instead of formatting those nulls as None/None.
COUNTS_NOT_STORED = "counts not stored"


def grade_label(grade: str) -> str:
    """The words a reader sees. ``UNSUPPORTED`` is not a failure."""
    if grade == "UNSUPPORTED":
        return "not supported"
    return grade


def outcome_detail(item: dict) -> str:
    """Text beside the grade. Real counts stay; null counts are not a fraction."""
    if item.get("grade") == "UNSUPPORTED":
        return "no trials run"
    statement = _stored_text(item.get("statement"))
    if statement is not None:
        return html.escape(statement)
    passed, trials = item.get("passed"), item.get("trials")
    if passed is not None and trials is not None:
        return f"{passed}/{trials} passed ({item['required']} needed)"
    finding = _stored_text(item.get("finding"))
    if finding is not None:
        return html.escape(finding)
    return COUNTS_NOT_STORED


def outcome_cells(record: dict) -> str:
    return "<br>".join(
        f'<span class="{html.escape(o["grade"])}">{html.escape(grade_label(o["grade"]))}</span> '
        f"{outcome_detail(o)} · {html.escape(o['capability'])}"
        for o in record["outcome"]
    )


ACTIONS_NOTE = (
    "The original GitHub Actions artifacts need a GitHub sign-in to download and are deleted "
    "when they expire, so the report on this site links its evidence to the copies here and "
    "names each Actions artifact only as a secondary reference with its expiry"
)


def trial_model(node: object) -> bool:
    """Evidence 2.0.0: ``original_result.index`` is present, or ``plan.trials`` is an object.

    Evidence 1.0.0 keeps an integer ``plan.trials`` and ``original_result.seed``. The stored
    ``seed`` key and result ids stay as the reporter wrote them; only the visible label changes.
    """
    if isinstance(node, dict):
        original = node.get("original_result")
        if isinstance(original, dict) and "index" in original:
            return True
        plan = node.get("plan")
        if isinstance(plan, dict) and isinstance(plan.get("trials"), dict):
            return True
        return any(trial_model(value) for value in node.values())
    if isinstance(node, list):
        return any(trial_model(value) for value in node)
    return False


def display_label(label: str, trial: bool) -> str:
    """User-visible per-trial label. 2.0.0 says trial; 1.0.0 stays seed.

    A leading ``seed `` from an older reporter becomes ``trial ``. A v3.3.0 report
    that already says ``trial N`` is left as written.
    """
    if trial:
        return SEED_LABEL.sub("trial ", label, count=1)
    return label


def retention_days(path: Path | None = None) -> int:
    """How long copied evidence stays. The number lives only in settings.toml."""
    path = path or SETTINGS
    try:
        document = tomllib.loads(Path(path).read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SiteError(f"{path}: cannot read the retention setting ({error})") from error
    site = document.get("site") if isinstance(document, dict) else None
    value = site.get("evidence_retention_days") if isinstance(site, dict) else None
    if type(value) is not int or value < 1:
        raise SiteError(f"{path}: site.evidence_retention_days must be a positive integer")
    return value


def _stamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise SiteError(f"Unexpected time {value!r}") from error


def evidence_expired(created: str, now: str, days: int) -> bool:
    """True once a run is at least `days` old. Report pages are not affected."""
    return _stamp(now) - _stamp(created) >= timedelta(days=days)


def prune_expired_evidence(site: Path, now: str, days: int) -> list[str]:
    """Remove copied MCAP, video and media under runs older than the setting.

    Report pages and run.json stay. Runs that never received copies are untouched.
    """
    removed = []
    if not now:
        return removed
    for record_path in sorted(site.glob("*/*/runs/*/run.json")):
        record = json.loads(record_path.read_text())
        created = (record.get("run") or {}).get("created")
        if not isinstance(created, str) or not evidence_expired(created, now, days):
            continue
        evidence = record_path.parent / "evidence"
        if not evidence.is_dir():
            continue
        for path in sorted(evidence.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix.lower() in MEDIA_SUFFIXES or path.name == "media.json":
                path.unlink()
                removed.append(str(path.relative_to(site)))
    return removed


def site_path(value: object) -> str | None:
    """A relative path of a file this site copied, or None when it must not be published."""
    if not isinstance(value, str) or not SITE_PATH.fullmatch(value):
        return None
    if (
        "://" in value
        or value.startswith(("/", "\\"))
        or ".." in value.split("/")
        or ":" in value
        or "/actions/" in value.lower()
        or ARTIFACT_WORD.search(value)
    ):
        return None
    return value


def _plain_label(value: object) -> str:
    text = value if isinstance(value, str) else ""
    cleaned = ARTIFACT_WORD.sub("", text).strip(" .:-")
    if not cleaned or re.search(r"[\[\]()|`<>\n]", cleaned) or len(cleaned) > 80:
        return "evidence"
    return cleaned


def listing_path(record: dict) -> str | None:
    """``evidence/<name>.html`` when one link covers several copied files."""
    name = (record.get("artifact") or {}).get("name")
    if not (record.get("preserved") and len(record.get("files") or []) > 1 and name):
        return None
    return f"evidence/{name}.html"


def link_path(record: dict, *, expired: bool) -> str | None:
    """Where a live copy is published, or None when the page must not point at it."""
    if expired or not record.get("preserved"):
        return None
    listing = listing_path(record)
    if listing and site_path(listing):
        return listing
    paths = [
        site_path(item.get("path")) for item in record.get("files") or [] if isinstance(item, dict)
    ]
    kept = [path for path in paths if path]
    if len(kept) == 1:
        return kept[0]
    return None


def evidence_links_document(records: list[dict] | None, *, expired: bool) -> dict:
    """Links for the reporter: relative site paths only.

    A GitHub Actions address, including a plain ``actions/runs/<id>`` URL, is never
    copied into the document. There is no ``run`` field. Pruned copies are
    ``expired: true`` and have no path, so the page cannot offer a broken link.
    """
    links = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        kind = record.get("kind")
        if kind not in {"video", "mcap", "log", "note"}:
            continue
        entry = {"label": _plain_label(record.get("label")), "kind": kind}
        if type(record.get("seed")) is int and record.get("status") in {"PASS", "FAIL"}:
            entry["seed"] = record["seed"]
            entry["status"] = record["status"]
        if expired or not record.get("preserved") or not record.get("files"):
            # Never copied: leave the run alone. Pruned: the reporter's kept-for state.
            if record.get("preserved"):
                entry["expired"] = True
                links.append(entry)
            continue
        target = link_path(record, expired=False)
        if target is None:
            for item in record.get("files") or []:
                path = site_path(item.get("path")) if isinstance(item, dict) else None
                if path:
                    links.append(entry | {"path": path, "expired": False})
            continue
        entry["path"] = target
        entry["expired"] = False
        links.append(entry)
    document = {"links": links}
    blob = json.dumps(document)
    if "run" in document or "://" in blob or "/actions/" in blob or ARTIFACT_WORD.search(blob):
        raise SiteError("refusing to pass a non-site evidence link to the reporter")
    return document


def write_listing(folder: Path, record: dict, up: str) -> None:
    """One page linking every copied file of a multi-file record (for example every trial)."""
    path = listing_path(record)
    if path is None or not site_path(path):
        return
    prefix = path.removesuffix(".html").removeprefix("evidence/") + "/"
    rows = "".join(
        "<tr><td>"
        + a(item["path"].removeprefix("evidence/"), item["path"].removeprefix(f"evidence/{prefix}"))
        + f"</td><td>{item['bytes']}</td><td><code>{html.escape(item['sha256'])}</code></td></tr>"
        for item in record["files"]
    )
    title = _plain_label(record.get("label"))
    body = (
        f"<p>All {len(record['files'])} files copied to this site. "
        f"{a('../index.html', 'Back to the report')}</p>"
        f"<table><tr><th>File</th><th>Bytes</th><th>sha256</th></tr>{rows}</table>"
    )
    (folder / path).write_text(page(title, body, up=f"../{up}index.html"))


def drop_listing_pages(folder: Path) -> None:
    evidence = folder / "evidence"
    if not evidence.is_dir():
        return
    for path in evidence.glob("*.html"):
        path.unlink()


def html_is_dirty(text: str) -> bool:
    return ARTIFACT_WORD.search(text) is not None or "/actions/" in text.lower()


def _is_actions_url(value: object) -> bool:
    return isinstance(value, str) and (
        "/actions/" in value.lower() or ARTIFACT_WORD.search(value) is not None
    )


def strip_actions_links(report: dict) -> bool:
    """Drop Actions run and artifact URLs from ``evidence_links``. Returns whether it changed."""
    links = report.get("evidence_links")
    if not isinstance(links, dict):
        return False
    changed = False
    if _is_actions_url(links.get("run")):
        links.pop("run", None)
        changed = True
    entries = links.get("links")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict) and _is_actions_url(entry.get("url")):
                entry.pop("url", None)
                changed = True
    return changed


def publish_evidence_links(report: dict, links: dict) -> bool:
    """Make ``report.json``'s ``evidence_links`` the site document the page uses.

    Each entry is then a copied site path, or ``expired: true`` once pruned, as on the
    page. A stored https link that is not an Actions URL stays. The Actions ``run``
    field, and an entry left with neither a url nor a path, are dropped, so the pinned
    reporter can render the published file on its own. Returns whether it changed.
    """
    stored = report.get("evidence_links")
    if not isinstance(stored, dict):
        return False
    kept = [
        entry
        for entry in stored.get("links") or []
        if isinstance(entry, dict)
        and isinstance(entry.get("url"), str)
        and entry["url"].startswith("https://")
        and not _is_actions_url(entry["url"])
    ]
    public = {"links": [dict(entry) for entry in links.get("links", [])] + kept}
    if public == stored:
        return False
    report["evidence_links"] = public
    return True


def strip_actions_text(text: str) -> str:
    """Remove Actions URLs and the word artifact from a published text file."""
    text = ACTIONS_ANCHOR.sub(lambda match: match.group(3), text)
    text = ACTIONS_URL.sub("", text)
    return ARTIFACT_WORD.sub("", text)


def reporter_html(folder: Path) -> str:
    """The reporter's HTML, read once. A restored ``report.html.orig`` is not left published."""
    parked = folder / ORIGINAL_HTML
    if parked.is_file():
        text = parked.read_text()
        parked.unlink()
        return text
    report = folder / "report.html"
    if report.is_file():
        return report.read_text()
    return ""


def write_published_checksums(folder: Path) -> None:
    """Checksum every file in the run folder except this list. ``report.html.orig`` is absent."""
    names = sorted(
        path.relative_to(folder).as_posix()
        for path in folder.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    if any(name == ORIGINAL_HTML or name.endswith("/" + ORIGINAL_HTML) for name in names):
        raise SiteError(f"{folder}: {ORIGINAL_HTML} is not a published file")
    lines = [
        f"{hashlib.sha256((folder / name).read_bytes()).hexdigest()}  {name}" for name in names
    ]
    (folder / "SHA256SUMS").write_text(("\n".join(lines) + "\n") if lines else "")


def publish_report_files(folder: Path, html_text: str) -> None:
    """Strip Actions URLs, then checksum the files that stay in the run folder."""
    parked = folder / ORIGINAL_HTML
    if parked.is_file():
        parked.unlink()
    report_path = folder / "report.json"
    if report_path.is_file():
        report = read_report(report_path)
        if isinstance(report, dict) and strip_actions_links(report):
            report_path.write_text(json.dumps(report, indent=2) + "\n")
    if html_text:
        (folder / "report.html").write_text(strip_actions_text(html_text))
    for name in ("report.md", "report.txt"):
        path = folder / name
        if not path.is_file():
            continue
        text = path.read_text(errors="replace")
        cleaned = strip_actions_text(text)
        if cleaned != text:
            path.write_text(cleaned)
    write_published_checksums(folder)


def scrub_reporter_html(text: str, record: dict, *, trial: bool, expired: bool) -> str:
    """Point a stored report at site copies. Used when the file is not a re-renderable report."""
    by_id = {
        entry["artifact"]["id"]: entry
        for entry in record.get("evidence") or []
        if isinstance(entry, dict) and isinstance(entry.get("artifact"), dict)
    }

    def shown(label: str) -> str:
        # Anchor text may contain parentheses ("trial 1 (PASS): video"). Only the
        # reporter's link labels are restricted; this text is the visible page.
        cleaned = ARTIFACT_WORD.sub("", label).strip()
        return cleaned or "evidence"

    def replace(match: re.Match) -> str:
        label = shown(display_label(match.group(4), trial))
        entry = by_id.get(match.group(3))
        target = None if entry is None else link_path(entry, expired=expired)
        if target:
            return f"<a href='{html.escape(target)}'>{html.escape(label)}</a>"
        return html.escape(label)

    def unwrap(match: re.Match) -> str:
        return html.escape(shown(match.group(3)))

    text = ARTIFACT_LINK.sub(replace, text)
    text = ACTIONS_ANCHOR.sub(unwrap, text)
    text = ACTIONS_URL.sub("", text)
    return ARTIFACT_WORD.sub("", text)


def report_is_renderable(report: object) -> bool:
    """A saved reporter document the pinned reporter can render again."""
    if not isinstance(report, dict):
        return False
    return (
        report.get("report_version") == "1.0.0"
        and isinstance(report.get("source_bundle"), dict)
        and isinstance(report.get("source_bundle_sha256"), str)
        and isinstance(report.get("decisions"), list)
        and isinstance(report.get("summaries"), list)
        and bool(report["summaries"])
    )


def reporter_bin() -> str:
    found = shutil.which("livingproof-capability-report")
    if not found:
        raise SiteError(
            "livingproof-capability-report is not on PATH; the public page is rendered "
            "by the pinned reporter"
        )
    return found


def render_with_reporter(
    report: dict,
    links: dict,
    *,
    history_href: str,
    history_label: str,
    retention: int | None,
) -> str:
    """Render the public page. Evidence links are the caller's relative paths only.

    v3.6.0 accepts ``--evidence-links`` on a bundle manifest, and refuses it on a saved
    report. Re-rendering the bundle also demands the observation files, which the site
    does not keep. The links document is the same object that flag merges; it is set on
    a copy of the saved report, and history plus retention are passed as flags.
    """
    if set(links) - {"links"} or any(
        set(entry) - {"label", "kind", "seed", "status", "path", "expired", "poster", "why"}
        for entry in links.get("links", [])
    ):
        raise SiteError("refusing to pass a non-site evidence link to the reporter")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        source = folder / "report.json"
        public = dict(report)
        public["evidence_links"] = links
        source.write_text(json.dumps(public))
        destination = folder / "index.html"
        command = [
            reporter_bin(),
            "render",
            str(source),
            "--format",
            "html",
            "-o",
            str(destination),
            "--history-href",
            history_href,
            "--history-label",
            history_label,
        ]
        if links.get("links"):
            if retention is None:
                raise SiteError("evidence retention is required to render evidence")
            command.extend(["--evidence-retention", str(retention)])
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or "").strip()
            raise SiteError(f"reporter render failed: {detail}") from error
        return destination.read_text()


def publish_run_page(folder: Path, record: dict, *, now: str, days: int, trial: bool) -> None:
    """The run directory's index is the styled report, not a frame around it."""
    html_text = reporter_html(folder)
    created = (record.get("run") or {}).get("created", "")
    expired = bool(now) and bool(created) and evidence_expired(created, now, days)
    drop_listing_pages(folder)
    if not expired:
        for entry in record.get("evidence") or []:
            write_listing(folder, entry, up="../../../../")
    links = evidence_links_document(record.get("evidence"), expired=expired)
    report_path = folder / "report.json"
    report = read_report(report_path)
    if isinstance(report, dict) and (
        strip_actions_links(report) | publish_evidence_links(report, links)
    ):
        # Before the published checksum, and before the page embeds this JSON.
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    if report_is_renderable(report):
        public = render_with_reporter(
            report,
            links,
            history_href="../../index.html",
            history_label=HISTORY_LABEL,
            retention=days if links["links"] else None,
        )
    else:
        public = scrub_reporter_html(html_text, record, trial=trial, expired=expired)
    if html_is_dirty(public):
        raise SiteError(
            f"{folder / 'index.html'}: public page still names an artifact or links to /actions/"
        )
    (folder / "index.html").write_text(public)
    publish_report_files(folder, html_text)


def redirect_page(target: str, title: str = "Latest report") -> str:
    """A static redirect. GitHub Pages has no server redirect for a directory."""
    safe = html.escape(target, quote=True)
    shown = html.escape(title)
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url={safe}">'
        f'<link rel="canonical" href="{safe}">'
        f"<title>{shown}</title></head><body>"
        f'<p><a href="{safe}">{shown}</a></p>'
        "</body></html>\n"
    )


_TEXT_SUFFIXES = {
    ".html",
    ".json",
    ".txt",
    ".md",
    ".svg",
    ".orig",
    ".xml",
    ".css",
    ".js",
    ".yml",
    ".yaml",
}


def published_text(path: Path) -> bool:
    """A published file the lint reads. ``run.json`` is the one place ids may remain."""
    if not path.is_file() or path.name == "run.json":
        return False
    if path.suffix.lower() in _TEXT_SUFFIXES or path.name.endswith(".orig"):
        return True
    return path.name in {"SHA256SUMS"}


def public_page_problems(site: Path) -> list[str]:
    """Published text, other than run.json, that names an artifact or an Actions URL."""
    problems = []
    for path in sorted(site.rglob("*")):
        if not published_text(path):
            continue
        text = path.read_text(errors="replace")
        relative = str(path.relative_to(site))
        if ARTIFACT_WORD.search(text):
            problems.append(f"{relative} mentions an artifact")
        if "/actions/" in text.lower():
            problems.append(f"{relative} links to /actions/")
    return problems


def remember_stored_wording(folder: Path, record: dict) -> None:
    """Copy a dropped statement or finding from report.json onto the history record.

    Publishes before this fix stored null counts and left the statement in the
    report file. Counts are not filled in. A record with neither is left alone.
    """
    outcomes = record.get("outcome")
    report_path = folder / "report.json"
    if not isinstance(outcomes, list) or not report_path.is_file():
        return
    report = read_report(report_path)
    summaries = report.get("summaries") if isinstance(report, dict) else None
    if not isinstance(summaries, list) or len(summaries) != len(outcomes):
        return
    changed = False
    for item, summary in zip(outcomes, summaries):
        if not isinstance(item, dict) or not isinstance(summary, dict):
            continue
        if _stored_text(item.get("statement")) or _stored_text(item.get("finding")):
            continue
        if item.get("passed") is not None and item.get("trials") is not None:
            continue
        statement = reproducibility_statement(summary)
        if statement is not None:
            item["statement"] = statement
            changed = True
            continue
        finding = reproducibility_finding(summary)
        if finding is not None:
            item["finding"] = finding
            changed = True
    if not changed:
        return
    stored = json.loads((folder / "run.json").read_text())
    stored["outcome"] = outcomes
    (folder / "run.json").write_text(json.dumps(stored, indent=2) + "\n")


SITE_STYLES = """:root {
  color-scheme: light;
  --font-sans: "DM Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --navy: #01152d;
  --teal: #0e8785;
  --teal-bright: #64dfc7;
  --text: #0f172a;
  --text-muted: #475569;
  --bg: #f4f7fb;
  --bg-muted: #e8eef5;
  --surface: #ffffff;
  --border: #d5dee9;
  --border-strong: #c3cedb;
  --shadow: 0 12px 32px rgba(1, 21, 45, 0.06);
  --chip-pass-fg: #0f5f5c;
  --chip-pass-bg: #d7f3ef;
  --chip-fail-fg: #9f1239;
  --chip-fail-bg: #ffe4e6;
  --chip-warn-fg: #92400e;
  --chip-warn-bg: #fef3c7;
  --chip-muted-fg: #334155;
  --chip-muted-bg: #e2e8f0;
  --header-text: #f8fafc;
  --header-muted: #b7c5d6;
  --link: #0b6e6c;
  --speak: #8a6a32;
  --speak-text: #6b5428;
  --focus: #0e8785;
}
*, *::before, *::after { box-sizing: border-box; }
html, body { margin: 0; }
body {
  font-family: var(--font-sans);
  font-size: 1.0625rem;
  line-height: 1.55;
  color: var(--text);
  background: var(--bg);
  -webkit-font-smoothing: antialiased;
  font-variant-numeric: tabular-nums;
}
a {
  color: var(--link);
  text-decoration-thickness: 1px;
  text-underline-offset: 0.15em;
}
a:hover { color: #08524f; }
a:focus-visible, button:focus-visible, .row:focus-visible {
  outline: 3px solid var(--focus);
  outline-offset: 3px;
}
.skip {
  position: absolute;
  left: -999px;
  top: 0;
}
.skip:focus {
  left: 1rem;
  top: 1rem;
  z-index: 10;
  background: var(--surface);
  padding: 0.4rem 0.7rem;
  border-radius: 0.4rem;
}
.masthead {
  background:
    radial-gradient(ellipse 70% 80% at 88% 0%, rgba(14, 135, 133, 0.28), transparent 58%),
    var(--navy);
  color: var(--header-text);
  border-bottom: 3px solid var(--teal);
}
.wrap {
  width: min(72rem, 100% - 2.25rem);
  margin-inline: auto;
}
.masthead-inner { padding: 1.2rem 0 1.45rem; }
.eyebrow {
  margin: 0 0 0.45rem;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.72rem;
  font-weight: 650;
  color: var(--teal-bright);
}
.masthead h1 {
  margin: 0 0 0.35rem;
  font-size: 1.7rem;
  letter-spacing: -0.03em;
  font-weight: 650;
  line-height: 1.2;
}
.lede {
  margin: 0;
  max-width: 44rem;
  color: var(--header-muted);
  font-size: 0.98rem;
}
.lede a { color: var(--teal-bright); }
.crumb {
  margin: 0 0 0.7rem;
  font-size: 0.9rem;
}
.crumb a { color: var(--teal-bright); }
.meta-line {
  margin: 0.55rem 0 0;
  color: var(--header-muted);
  font-size: 0.88rem;
}
.meta-line a { color: var(--teal-bright); }
.meta-line .quiet { color: var(--header-muted); }
.page { padding: 1.25rem 0 2.4rem; }
.status {
  margin: 0 0 1.15rem;
  color: var(--text-muted);
  font-size: 0.95rem;
}
.status strong { color: var(--text); font-weight: 650; }
.robot-group { margin: 1.35rem 0 0; }
.robot-group:first-child { margin-top: 0; }
.robot-group h2 {
  margin: 0 0 0.65rem;
  font-size: 0.88rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text);
}
.stack {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.85rem;
  box-shadow: var(--shadow);
  padding: 0.85rem 1rem 0.9rem;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.card:hover, .card:focus-within {
  background: color-mix(in srgb, var(--bg-muted) 65%, var(--surface));
  border-color: var(--border-strong);
}
.card.is-fail {
  border-left: 3px solid color-mix(in srgb, var(--chip-fail-fg) 70%, #fda4af);
}
.entry-main {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 0.65rem 0.85rem;
  align-items: start;
}
.entry-body { min-width: 0; }
.entry-side {
  text-align: right;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.35rem;
}
.title {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 650;
  letter-spacing: -0.02em;
  line-height: 1.3;
}
.title a { color: inherit; text-decoration: none; }
.title a:hover { color: var(--link); text-decoration: underline; }
.sub {
  margin: 0.15rem 0 0;
  color: var(--text-muted);
  font-size: 0.9rem;
}
.finding {
  margin: 0.5rem 0 0;
  padding: 0.35rem 0.55rem;
  border-radius: 0.45rem;
  border-left: 3px solid var(--speak);
  background: color-mix(in srgb, var(--speak) 8%, var(--surface));
  color: var(--text);
  font-size: 0.92rem;
  font-weight: 550;
}
.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 3.6rem;
  padding: 0.12rem 0.5rem;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  line-height: 1.4;
}
.badge-pass { color: var(--chip-pass-fg); background: var(--chip-pass-bg); }
.badge-fail { color: var(--chip-fail-fg); background: var(--chip-fail-bg); }
.badge-warn { color: var(--chip-warn-fg); background: var(--chip-warn-bg); }
.badge-muted { color: var(--chip-muted-fg); background: var(--chip-muted-bg); }
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
  margin-top: 0.4rem;
  justify-content: flex-start;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.12rem 0.55rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--speak) 40%, var(--border));
  background: color-mix(in srgb, var(--speak) 12%, var(--surface));
  color: var(--speak-text);
  font-size: 0.78rem;
  font-weight: 650;
  text-decoration: none;
  line-height: 1.4;
}
.chip:hover {
  background: color-mix(in srgb, var(--speak) 20%, var(--surface));
  color: var(--speak-text);
  border-color: color-mix(in srgb, var(--speak) 65%, var(--border));
}
.when, .release, .poster, .links {
  font-size: 0.88rem;
  color: var(--text-muted);
}
.release {
  font-weight: 650;
  color: var(--text);
  text-decoration: none;
}
.release.is-fallback {
  font-weight: 500;
  color: var(--text-muted);
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  font-size: 0.82rem;
  font-variant-numeric: normal;
  letter-spacing: 0;
}
a.release:hover { color: var(--link); text-decoration: underline; }
.dot { margin: 0 0.35rem; color: #94a3b8; }
.links a { margin-left: 0.7rem; }
.links a:first-child { margin-left: 0; }
.side-meta { color: var(--text-muted); font-size: 0.88rem; }
.side-meta .release { margin-right: 0; }
.history {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.row {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.85rem;
  padding: 1rem;
  text-decoration: none;
  color: inherit;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.row:hover, .row:focus-within {
  background: color-mix(in srgb, var(--bg-muted) 70%, var(--surface));
  border-color: var(--border-strong);
}
.row.is-fail {
  border-left: 3px solid color-mix(in srgb, var(--chip-fail-fg) 70%, #fda4af);
}
.row.is-pass {
  border-left: 3px solid color-mix(in srgb, var(--teal) 45%, var(--border));
}
.row-top {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.65rem 0.85rem;
  align-items: start;
}
.row-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.15rem 0;
  margin-top: 0.5rem;
  color: var(--text-muted);
  font-size: 0.88rem;
}
.row-meta a { font-weight: 550; }
@media (max-width: 720px) {
  .entry-main, .row-top {
    grid-template-columns: auto 1fr;
  }
  .entry-side {
    grid-column: 2;
    text-align: left;
    align-items: flex-start;
  }
  .chips, .entry-body .chips { justify-content: flex-start; }
  .links a { margin-left: 0; margin-right: 0.8rem; }
  .masthead h1 { font-size: 1.4rem; }
  .wrap { width: min(72rem, 100% - 1.5rem); }
}
@media (prefers-reduced-motion: reduce) {
  .card, .row { transition: none; }
}
"""

_ROBOT_TITLES = {
    "booster-t1": "Booster T1",
    "unitree-g1": "Unitree G1",
    "unitree-go1": "Unitree Go1",
}


def _robot_title(robot: str) -> str:
    if robot in _ROBOT_TITLES:
        return _ROBOT_TITLES[robot]
    parts = []
    for part in robot.split("-"):
        if re.fullmatch(r"[a-z]+\d+", part) and len(part) <= 3:
            parts.append(part.upper())
        else:
            parts.append(part.capitalize())
    return " ".join(parts)


def _scenario_title(record: dict, variant: str) -> str:
    outcomes = record.get("outcome") or []
    capability = outcomes[0].get("capability", "") if outcomes else ""
    if variant.endswith("-corner-left") or capability == "walk-corner-left":
        return "Walk around the corner (left)"
    if variant.endswith("-corner-right") or capability == "walk-corner-right":
        return "Walk around the corner (right)"
    if capability == "walk-corner" or variant.endswith("-corner"):
        return "Walk around the corner"
    if capability == "walk-hallway" or variant.endswith("-hallway"):
        return "Walk the hallway"
    return (capability or variant).replace("-", " ").capitalize()


def _variant_subtitle(record: dict, variant: str) -> str:
    robot = record.get("robot") or {}
    config = _stored_text(robot.get("configuration")) or variant.split("-", 1)[0]
    tokens = variant.split("-")
    sim = ""
    if "mujoco" in tokens:
        sim = "MuJoCo"
    elif "gazebo" in tokens:
        sim = "Gazebo"
    return f"{config} · {sim}" if sim else config


def _record_grade(record: dict) -> str:
    outcomes = record.get("outcome") or []
    grades = [o.get("grade", "") for o in outcomes if isinstance(o, dict)]
    if "FAIL" in grades:
        return "FAIL"
    if "INSUFFICIENT_EVIDENCE" in grades:
        return "INSUFFICIENT_EVIDENCE"
    if grades and all(g == "UNSUPPORTED" for g in grades):
        return "UNSUPPORTED"
    if "PASS" in grades:
        return "PASS"
    return grades[0] if grades else "UNSUPPORTED"


def _badge_html(grade: str) -> str:
    if grade == "PASS":
        return '<span class="badge badge-pass">PASS</span>'
    if grade == "FAIL":
        return '<span class="badge badge-fail">FAIL</span>'
    if grade == "UNSUPPORTED":
        return '<span class="badge badge-muted">Not supported</span>'
    if grade == "INSUFFICIENT_EVIDENCE":
        return '<span class="badge badge-warn">Insufficient evidence</span>'
    return f'<span class="badge badge-muted">{html.escape(grade)}</span>'


def _relative_time_html(created: str, now: str) -> str:
    created_dt = _stamp(created)
    now_dt = _stamp(now) if now else created_dt
    seconds = max(0, int((now_dt - created_dt).total_seconds()))
    if seconds < 60:
        relative = "just now"
    elif seconds < 3600:
        minutes = seconds // 60
        suffix = "s" if minutes != 1 else ""
        relative = f"{minutes} minute{suffix} ago"
    elif seconds < 86400:
        hours = seconds // 3600
        suffix = "s" if hours != 1 else ""
        relative = f"{hours} hour{suffix} ago"
    elif seconds < 172800:
        relative = "yesterday"
    elif seconds < 30 * 86400:
        days = seconds // 86400
        relative = f"{days} days ago"
    else:
        relative = f"{created_dt.day} {created_dt.strftime('%b %Y')}"
    local = created_dt.astimezone(LOCAL_TZ)
    tooltip = f"{local.day} {local.strftime('%b %Y, %H:%M %Z')}"
    return (
        f'<time datetime="{html.escape(created, quote=True)}" '
        f'title="{html.escape(tooltip, quote=True)}">{html.escape(relative)}</time>'
    )


def _release_html(record: dict) -> str:
    commit = record.get("commit") or {}
    tag = _stored_text(record.get("bundle_tag") or commit.get("tag"))
    sha = commit.get("sha") or ""
    commit_url = commit.get("url") or ""
    if tag is not None:
        if "/commit/" in commit_url:
            tag_url = commit_url.split("/commit/", 1)[0] + f"/releases/tag/{tag}"
        else:
            tag_url = commit_url
        title = f"Verification bundle release {tag}"
        if tag_url:
            return (
                f'<a class="release" href="{html.escape(tag_url, quote=True)}" '
                f'title="{html.escape(title, quote=True)}">{html.escape(tag)}</a>'
            )
        return (
            f'<span class="release" title="{html.escape(title, quote=True)}">'
            f"{html.escape(tag)}</span>"
        )
    short = sha[:8]
    title = "No release tag on this livingproof-verification commit; short SHA shown"
    if commit_url:
        return (
            f'<a class="release is-fallback" href="{html.escape(commit_url, quote=True)}" '
            f'title="{html.escape(title, quote=True)}">{html.escape(short)}</a>'
        )
    return (
        f'<span class="release is-fallback" title="{html.escape(title, quote=True)}">'
        f"{html.escape(short)}</span>"
    )


def _chips_html(record: dict, prefix: str) -> str:
    grade = _record_grade(record)
    if grade in ("PASS", "UNSUPPORTED"):
        return ""
    evaluators = record.get("failing_evaluators") or []
    if not isinstance(evaluators, list) or not evaluators:
        return ""
    links = []
    for ev in evaluators:
        if not isinstance(ev, dict):
            continue
        title = _stored_text(ev.get("title"))
        href = _stored_text(ev.get("href"))
        if title is None or href is None:
            continue
        tip = f"Open the {title} finding in the report"
        links.append(
            f'<a class="chip" href="{html.escape(prefix + href, quote=True)}" '
            f'title="{html.escape(tip, quote=True)}" '
            f'aria-label="{html.escape(tip, quote=True)}">{html.escape(title)}</a>'
        )
    if not links:
        return ""
    return f'<div class="chips">{"".join(links)}</div>'


def _finding_html(record: dict) -> str:
    grade = _record_grade(record)
    if grade in ("PASS", "UNSUPPORTED"):
        return ""
    for ev in record.get("failing_evaluators") or []:
        if isinstance(ev, dict):
            summary = _stored_text(ev.get("summary"))
            if summary is not None:
                return f'<p class="finding">{html.escape(summary)}</p>'
    for item in record.get("outcome") or []:
        if isinstance(item, dict):
            finding = _stored_text(item.get("finding"))
            if finding is not None:
                return f'<p class="finding">{html.escape(finding)}</p>'
    return ""


def _styled_document(
    title: str, css_href: str, skip_id: str, skip_label: str, masthead: str, main_body: str
) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,650;0,9..40,700;1,9..40,400&display=swap" rel="stylesheet">\n'
        f'<link rel="stylesheet" href="{html.escape(css_href, quote=True)}">\n'
        "<body>\n"
        f'<a class="skip" href="#{html.escape(skip_id, quote=True)}">{html.escape(skip_label)}</a>\n'
        f'<header class="masthead"><div class="wrap masthead-inner">{masthead}</div></header>\n'
        f'<main class="page wrap" id="{html.escape(skip_id, quote=True)}">{main_body}</main>\n'
        "</body>\n"
        "</html>\n"
    )


def _history_page_html(robot: str, variant: str, records: list[dict], now: str) -> str:
    newest = records[0]
    robot_title = _robot_title(robot)
    scenario_title = _scenario_title(newest, variant)
    subtitle = _variant_subtitle(newest, variant)
    count = len(records)
    plural = "s" if count != 1 else ""
    ref_now = now or newest["run"]["created"]
    releases = " · ".join(
        f"{html.escape(name)} {a(release['url'], release['tag'])}"
        for name, release in newest["releases"].items()
    )
    robot_info = newest["robot"]
    repo_short = robot_info["repository"].rsplit("/", 1)[-1]
    robot_tag = _stored_text(robot_info.get("tag"))
    robot_ref_label = (
        f"{repo_short} @ {robot_tag}" if robot_tag else f"{repo_short} @ {robot_info['commit'][:8]}"
    )
    robot_line = a(robot_info["url"], robot_ref_label)
    software_version = _stored_text(robot_info.get("software_version"))
    if software_version is not None:
        config = _stored_text(robot_info.get("configuration")) or ""
        sw_label = f"{config} {software_version}".strip()
        robot_line += f'<span class="quiet"> · software {html.escape(sw_label)}</span>'
    masthead = (
        '<p class="crumb"><a href="../../index.html">← All verification reports</a></p>'
        f'<p class="eyebrow">{html.escape(robot_title)} · {html.escape(subtitle)}</p>'
        f"<h1>{html.escape(scenario_title)}</h1>"
        f'<p class="lede">{count} run{plural}, newest first. A continuous list — no pagination. '
        "History is pruned holistically elsewhere.</p>"
        f'<p class="meta-line">{a("latest/", "Latest report")}'
        f'<span class="quiet"> · {releases}</span></p>'
        f'<p class="meta-line">{robot_line}</p>'
    )
    row_cards = []
    for record in records:
        grade = _record_grade(record)
        row_class = "row is-fail" if grade in ("FAIL", "INSUFFICIENT_EVIDENCE") else "row is-pass"
        run_prefix = f"runs/{record['folder']}/"
        poster = record.get("poster")
        poster_name = (
            _stored_text(poster.get("name")) if isinstance(poster, dict) else None
        ) or "CI"
        chips = _chips_html(record, run_prefix)
        finding = _finding_html(record)
        row_cards.append(
            f'<article class="{row_class}">'
            '<div class="row-top">'
            f"{_badge_html(grade)}"
            "<div>"
            f'<h2 class="title">{html.escape(scenario_title)}</h2>'
            f"{chips}{finding}"
            '<div class="row-meta">'
            f"{_release_html(record)}"
            '<span class="dot">·</span>'
            f"{_relative_time_html(record['run']['created'], ref_now)}"
            '<span class="dot">·</span>'
            f'<span class="poster">{html.escape(poster_name)}</span>'
            '<span class="dot">·</span>'
            f"{a(run_prefix, 'Report')}"
            "</div>"
            "</div>"
            "</div>"
            "</article>"
        )
    main_body = f'<div class="history">{"".join(row_cards)}</div>'
    return _styled_document(
        f"{robot_title} · {subtitle} · {scenario_title} · run history",
        "../../styles.css",
        "history",
        "Skip to history",
        masthead,
        main_body,
    )


def _index_page_html(history: dict, now: str) -> str:
    grouped: dict[str, list[tuple[str, list[dict]]]] = {}
    all_latest: list[dict] = []
    for (robot, variant), records in sorted(history.items()):
        grouped.setdefault(robot, []).append((variant, records))
        all_latest.append(records[0])
    ref_now = now or max((r["run"]["created"] for r in all_latest), default="")
    total_count = len(all_latest)
    failed_count = sum(1 for r in all_latest if _record_grade(r) == "FAIL")
    if failed_count:
        status_html = (
            f'<p class="status"><strong>{failed_count} of {total_count}</strong> '
            "latest results failed. Failures list the evaluators that spoke; passing rows stay quiet.</p>"
        )
    elif total_count:
        status_html = (
            f'<p class="status"><strong>All {total_count}</strong> '
            "latest results passed. Passing rows stay quiet.</p>"
        )
    else:
        status_html = '<p class="status">No verification reports published yet.</p>'
    sections = []
    for robot, variants in grouped.items():
        robot_title = _robot_title(robot)
        heading_id = f"r-{robot}"
        cards = []
        for variant, records in variants:
            newest = records[0]
            grade = _record_grade(newest)
            card_class = "card is-fail" if grade in ("FAIL", "INSUFFICIENT_EVIDENCE") else "card"
            scenario_title = _scenario_title(newest, variant)
            subtitle = _variant_subtitle(newest, variant)
            latest_href = f"{robot}/{variant}/latest/"
            history_href = f"{robot}/{variant}/"
            run_prefix = f"{robot}/{variant}/runs/{newest['folder']}/"
            chips = _chips_html(newest, run_prefix)
            cards.append(
                f'<article class="{card_class}">'
                '<div class="entry-main">'
                f"{_badge_html(grade)}"
                '<div class="entry-body">'
                f'<h3 class="title">{a(latest_href, scenario_title)}</h3>'
                f'<p class="sub">{html.escape(subtitle)}</p>'
                f"{chips}"
                "</div>"
                '<div class="entry-side">'
                f'<div class="side-meta">{_release_html(newest)}'
                f'<span class="dot">·</span>{_relative_time_html(newest["run"]["created"], ref_now)}</div>'
                f'<div class="links">{a(latest_href, "Latest report")}'
                f"{a(history_href, f'History ({len(records)})')}</div>"
                "</div>"
                "</div>"
                "</article>"
            )
        sections.append(
            f'<section class="robot-group" aria-labelledby="{html.escape(heading_id, quote=True)}">'
            f'<h2 id="{html.escape(heading_id, quote=True)}">{html.escape(robot_title)}</h2>'
            f'<div class="stack">{"".join(cards)}</div>'
            "</section>"
        )
    masthead = (
        '<p class="eyebrow">Living Proof Labs</p>'
        "<h1>Verification reports</h1>"
        '<p class="lede">The latest result for each robot and scenario. '
        "Open a report for the evidence, or the history to scan what failed.</p>"
    )
    return _styled_document(
        "Living Proof verification reports",
        "styles.css",
        "results",
        "Skip to results",
        masthead,
        status_html + "".join(sections),
    )


def render(site: Path, now: str = ""):
    days = retention_days()
    prune_expired_evidence(site, now, days)
    history = runs(site)
    (site / "styles.css").write_text(SITE_STYLES)
    for (robot, variant), records in sorted(history.items()):
        base = site / robot / variant
        latest = base / "latest"
        if latest.exists():
            shutil.rmtree(latest)
        trials = {
            record["folder"]: trial_model(
                read_report(base / "runs" / record["folder"] / "report.json")
            )
            for record in records
        }
        newest = records[0]
        latest.mkdir(parents=True)
        (latest / "index.html").write_text(redirect_page(f"../runs/{newest['folder']}/"))
        for record in records:
            remember_stored_wording(base / "runs" / record["folder"], record)
            publish_run_page(
                base / "runs" / record["folder"],
                record,
                now=now,
                days=days,
                trial=trials[record["folder"]],
            )
        (base / "index.html").write_text(_history_page_html(robot, variant, records, now))
    (site / "index.html").write_text(_index_page_html(history, now))


def build(
    site: Path,
    reports: Path,
    run: dict,
    verifications: Path = ROOT / "verifications",
    evidence: Path | None = None,
    artifacts: list[dict] | None = None,
):
    added = [add_run(site, reports, t, run, evidence, artifacts) for t in targets(verifications)]
    render(site, now=run["created"])
    if problems := public_page_problems(site):
        raise SiteError("pages still link to GitHub Actions (#23): " + ", ".join(problems))
    (site / MARKER).write_text(
        json.dumps(
            {"runs": sum(len(r) for r in runs(site).values()), "updated": run["created"]},
            indent=2,
        )
        + "\n"
    )
    return added


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("restore", help="unpack the previous Pages artifact into SITE")
    r.add_argument("site", type=Path)
    r.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    r.add_argument("--branch", default="main")
    r.add_argument("--current-run", type=int, default=os.environ.get("GITHUB_RUN_ID"))
    r.add_argument("--dry-run", action="store_true", help="find the artifact, download nothing")
    r.add_argument(
        "--site-url",
        default=os.environ.get("SITE_URL"),
        help="the published site; with no artifact, history found here stops the restore",
    )
    r.add_argument(
        "--reset-history", action="store_true", help="start a new site, dropping run history"
    )
    b = sub.add_parser("build", help="add this run's reports to SITE and write the pages")
    b.add_argument("site", type=Path)
    b.add_argument("--reports", type=Path, required=True)
    b.add_argument("--run-id", required=True)
    b.add_argument("--commit", required=True)
    b.add_argument("--created", required=True, help="UTC ISO 8601 time of the run")
    b.add_argument(
        "--evidence",
        type=Path,
        help="downloaded links, MCAP and video artifacts to copy into the site",
    )
    b.add_argument(
        "--git-dir",
        type=Path,
        default=ROOT,
        help="checkout whose tags name --commit; a bundle tag is recorded only if it resolves to it",
    )
    b.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    b.add_argument("--server", default=os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
    args = parser.parse_args(argv)
    if args.command == "restore":
        restore(
            args.site,
            args.repository,
            args.branch,
            args.current_run,
            os.environ.get("GITHUB_TOKEN"),
            dry_run=args.dry_run,
            site_url=args.site_url,
            reset_history=args.reset_history,
        )
    else:
        artifacts = (
            fetch_artifacts(args.repository, args.run_id, os.environ.get("GITHUB_TOKEN"))
            if args.evidence
            else None
        )
        tag = bundle_tag(args.commit, args.git_dir)
        print(f"Bundle tag for {args.commit}: {tag or 'none (the run keeps its commit alone)'}")
        added = build(
            args.site,
            args.reports,
            {
                "id": args.run_id,
                "commit": args.commit,
                "created": args.created,
                "repository": args.repository,
                "server": args.server,
                "tag": tag,
            },
            evidence=args.evidence,
            artifacts=artifacts,
        )
        for folder in added:
            print(f"Added {folder.relative_to(args.site)}")


if __name__ == "__main__":
    main()
