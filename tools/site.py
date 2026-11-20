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


INCONCLUSIVE_COPY = "Not graded: no check applied to this run."


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
            or status == "INCONCLUSIVE"
        )
        if not spoke:
            continue
        title = _stored_text(ev.get("display_name")) or _stored_text(desc.get("title")) or ev_id
        summary = _finding_summary(findings, desc)
        if summary is None and status == "INCONCLUSIVE":
            # 1.2.0: no check applied. Not graded, not failed. Any other status this site
            # does not know is not graded and does not speak (a 1.x minor may add statuses).
            summary = INCONCLUSIVE_COPY
        if ev_id not in seen:
            entry = {"id": ev_id, "title": title, "href": f"#evaluator-{ev_id}"}
            if summary is not None:
                entry["summary"] = summary
            if status == "INCONCLUSIVE":
                entry["not_graded"] = True
            seen[ev_id] = entry
        elif "summary" not in seen[ev_id] and summary is not None:
            seen[ev_id]["summary"] = summary
    return list(seen.values())


MARKED_INTEGRITY_STATUSES = ("ERROR", "INCONCLUSIVE")


def integrity_markers(report: object) -> list[dict]:
    """Run Integrity results whose status is ERROR or INCONCLUSIVE, by evaluator id.

    Shown on a PASS or UNSUPPORTED run, which has no chips of its own (#168). Any other
    status, including one this site does not know, is not marked.
    """
    if not isinstance(report, dict):
        return []
    found: dict[str, dict] = {}
    for doc in _evaluator_documents(report):
        integrity = doc.get("integrity") if isinstance(doc.get("integrity"), dict) else {}
        run_info = integrity.get("run") if isinstance(integrity.get("run"), dict) else {}
        status = run_info.get("test_system_status")
        if status not in MARKED_INTEGRITY_STATUSES:
            continue
        ev = doc.get("evaluator") if isinstance(doc.get("evaluator"), dict) else {}
        ev_id = _stored_text(ev.get("id"))
        if ev_id is None or ev_id in found:
            continue
        desc = doc.get("descriptor") if isinstance(doc.get("descriptor"), dict) else {}
        title = _stored_text(ev.get("display_name")) or _stored_text(desc.get("title")) or ev_id
        found[ev_id] = {"id": ev_id, "title": title, "status": status}
    return list(found.values())


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
    markers = integrity_markers(report_data)
    if markers:
        record["integrity_status"] = markers
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


_REPO_PATH = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+")


def _spec_repository_url(spec: dict) -> str | None:
    """Normalize repository links without accepting credentials or ambiguous paths."""
    repository = _stored_text(spec.get("repository"))
    if repository is None:
        return None
    repository = repository.rstrip("/").removesuffix(".git")
    if repository.startswith("https://"):
        try:
            parsed = urllib.parse.urlsplit(repository)
        except ValueError:
            return None
        if not re.fullmatch(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*", parsed.netloc):
            return None
        if parsed.query or parsed.fragment:
            return None
        path = parsed.path.removeprefix("/")
        url = repository
    else:
        path = repository
        url = "https://github.com/" + repository
    if not _REPO_PATH.fullmatch(path) or path.rsplit("/", 1)[-1] in {".", ".."}:
        return None
    return url


def _spec_repository_label(spec: dict) -> str | None:
    """Use owner/name for either stored form; keep unrecognized values as plain text."""
    url = _spec_repository_url(spec)
    if url:
        return urllib.parse.urlsplit(url).path.removeprefix("/")
    return _stored_text(spec.get("repository"))


def _spec_repository_name(spec: dict) -> str | None:
    repository = _stored_text(spec.get("repository"))
    if repository is None:
        return None
    return repository.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1] or None


def _spec_assessments(report: object) -> list[dict]:
    """Every distinct assessment identity the saved summaries record, in order."""
    summaries = report.get("summaries", []) if isinstance(report, dict) else []
    assessed: list[dict] = []
    for summary in summaries if isinstance(summaries, list) else []:
        spec = summary.get("spec") if isinstance(summary, dict) else None
        if isinstance(spec, dict) and spec and spec not in assessed:
            assessed.append(spec)
    return assessed


def _spec_core(spec: object) -> tuple | None:
    """What makes two recorded specs the same release: repository name, tag and commit."""
    if not isinstance(spec, dict) or not spec:
        return None
    return (
        _spec_repository_name(spec),
        _stored_text(spec.get("tag")),
        _stored_text(spec.get("commit")),
    )


def _spec_line_html(spec: object, *, assessed: bool) -> str:
    """One recorded spec: linked version, repository name, scenario and revision.

    A commit id is never visible text (#146). A release tag is the link text; a spec with no
    tag is the link text "commit". The full commit is in the link and its title.
    """
    if _spec_core(spec) is None:
        return "Not recorded"
    _, tag, commit = _spec_core(spec)
    repository_url = _spec_repository_url(spec)
    pieces = []
    if tag is not None:
        label = html.escape(tag)
        if repository_url:
            href = f"{repository_url}/releases/tag/{urllib.parse.quote(tag, safe='')}"
            title = f"Release {tag}" + (f", commit {commit}" if commit is not None else "")
            pieces.append(
                f'<a href="{html.escape(href, quote=True)}" '
                f'title="{html.escape(title, quote=True)}">{label}</a>'
            )
        else:
            pieces.append(label)
    elif commit is not None:
        title = html.escape(f"Commit {commit}", quote=True)
        if repository_url and COMMIT_SHA.fullmatch(commit):
            pieces.append(
                f'<a href="{html.escape(repository_url, quote=True)}/commit/{commit}" '
                f'title="{title}" aria-label="{title}">commit</a>'
            )
        else:
            pieces.append(f'<span title="{title}">commit</span>')
    repository_label = _spec_repository_label(spec)
    if repository_label is not None:
        pieces.append(html.escape(repository_label))
    if assessed:
        if _stored_text(spec.get("capability")):
            pieces.append(html.escape(_stored_text(spec["capability"])))
        if _stored_text(spec.get("revision")):
            pieces.append(html.escape("revision " + _stored_text(spec["revision"])))
    return " · ".join(pieces)


def _spec_summary_html(generation: object, assessed: list[dict]) -> str:
    """The one line shown while the block is collapsed: the spec the report was assessed with."""
    spec = assessed[0] if assessed else generation
    if _spec_core(spec) is None:
        return "<strong>Spec</strong> not recorded"
    _, tag, _commit = _spec_core(spec)
    pieces = [html.escape(tag or "commit")]
    if _stored_text(spec.get("capability")):
        pieces.append(html.escape(_stored_text(spec["capability"])))
    if _stored_text(spec.get("revision")):
        pieces.append(html.escape("revision " + _stored_text(spec["revision"])))
    if len(assessed) > 1:
        pieces.append(f"+{len(assessed) - 1} more")
    return "<strong>Spec</strong> " + " · ".join(pieces)


# The reporter styles every <summary> as small, letter-spaced capitals, which turns a version
# and a scenario id into hard-to-read text. Inline, so it holds whatever the reporter's CSS
# order is: sentence case at body size, with Show / Hide after it as the reporter's own
# disclosures have.
_PROVENANCE_SUMMARY_STYLE = (
    "cursor:pointer;margin:0;font-size:inherit;font-weight:400;"
    "text-transform:none;letter-spacing:normal;color:inherit"
)
_PROVENANCE_SUMMARY_CSS = (
    "details.spec-provenance>summary::after{"
    'content:"Show";float:none;margin-left:.6rem;text-transform:none;'
    "letter-spacing:normal;font-weight:600;color:var(--link,#0b5cad)}"
    'details.spec-provenance[open]>summary::after{content:"Hide"}'
)


def spec_provenance_html(record: dict, report: object) -> str:
    """Recorded spec roles for the report page, collapsed below the result (#146).

    The caller pin is not proof that an old runner loaded a catalog. Assessment
    identities come from the saved summaries, never the current verification lock.
    Keep every distinct assessment identity (a report can contain several). When the
    one assessed identity is the generation pin, show one line instead of two.
    """
    generation = (record.get("releases") or {}).get("specs")
    assessed = _spec_assessments(report)
    if len(assessed) == 1 and _spec_core(generation) == _spec_core(assessed[0]) is not None:
        lines = [("Generation and assessment", _spec_line_html(assessed[0], assessed=True))]
    elif not assessed and _spec_core(generation) is None:
        lines = [("Generation and assessment", "Not recorded")]
    else:
        lines = [("Generation", _spec_line_html(generation, assessed=False))]
        lines += [("Assessment", _spec_line_html(spec, assessed=True)) for spec in assessed] or [
            ("Assessment", "Not recorded")
        ]
    rows = "".join(
        f'<dt style="font-weight:600">{label}</dt><dd style="margin:0 0 .5rem">{line}</dd>'
        for label, line in lines
    )
    return (
        '<details class="spec-provenance" id="spec-provenance" '
        'style="margin:1.5rem 0 0;font-size:.875rem;overflow-wrap:anywhere">'
        f'<summary style="{_PROVENANCE_SUMMARY_STYLE}">'
        f"{_spec_summary_html(generation, assessed)}</summary>"
        f"<style>{_PROVENANCE_SUMMARY_CSS}</style>"
        f'<dl style="margin:.5rem 0 0">{rows}</dl>'
        '<p style="margin:0;font-size:.8rem;opacity:.75">'
        "Recorded at run time; not reassessed.</p></details>"
    )


def with_spec_provenance(public: str, record: dict, report: object) -> str:
    """Add site provenance to the rendered view, leaving report downloads unchanged.

    The block goes last: before the closing </main> (or </body> for older HTML, or at the end
    of a text page), which is after the evaluators without reading the reporter's layout.
    """
    section = spec_provenance_html(record, report)
    for closing in (r"</main\s*>", r"</body\s*>"):
        found = list(re.finditer(closing, public, re.I))
        if found:
            return public[: found[-1].start()] + section + public[found[-1].start() :]
    return public + section


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
    public = with_spec_provenance(public, record, report)
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
  --pip-pass: #0e8785;
  --pip-fail: #e11d48;
  --pip-warn: #d97706;
  --pip-muted: #94a3b8;
  --pip-size: 0.7rem;
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
a:focus-visible, button:focus-visible, summary:focus-visible, .row:focus-visible {
  outline: 3px solid var(--focus);
  outline-offset: 3px;
}
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
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
  max-width: 46rem;
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
.status, .all-pass {
  margin: 0 0 1.35rem;
  color: var(--text-muted);
  font-size: 0.95rem;
}
.status strong, .all-pass strong { color: var(--text); font-weight: 650; }
.needs { margin: 0 0 1.6rem; }
.needs h2, .robots-label {
  margin: 0 0 0.25rem;
  font-size: 0.88rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text);
}
.robots-label { margin-bottom: 0.55rem; }
.needs .lede-section {
  margin: 0 0 0.65rem;
  color: var(--text-muted);
  font-size: 0.95rem;
}
.stack {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}
.card {
  position: relative;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.85rem;
  box-shadow: var(--shadow);
  padding: 0.85rem 1rem 0.9rem;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.card:hover, .card:focus-within {
  background: var(--bg-muted);
  border-color: var(--border-strong);
}
.card:has(.card-link:focus-visible) {
  outline: 3px solid var(--focus);
  outline-offset: 3px;
}
.card.is-fail {
  border-left: 3px solid color-mix(in srgb, var(--chip-fail-fg) 70%, #fda4af);
}
.card.is-fail:hover, .card.is-fail:focus-within {
  border-left-color: color-mix(in srgb, var(--chip-fail-fg) 70%, #fda4af);
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
.card-link:focus-visible { outline: none; }
.card-link::after {
  content: "";
  position: absolute;
  inset: 0;
  z-index: 1;
  border-radius: 0.85rem;
}
.history-link {
  position: relative;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  border-radius: 0.45rem;
}
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
.finding-neutral {
  border-left-color: var(--border-strong);
  background: var(--bg-muted);
  color: var(--text-muted);
  font-weight: 450;
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
.chip-spec {
  border-color: var(--border);
  background: var(--bg-muted);
  color: var(--text-muted);
  font-weight: 550;
}
a.chip:hover {
  background: color-mix(in srgb, var(--speak) 20%, var(--surface));
  color: var(--speak-text);
  border-color: color-mix(in srgb, var(--speak) 65%, var(--border));
}
.chip-integrity-error {
  border-color: color-mix(in srgb, var(--chip-fail-fg) 45%, var(--border));
  background: var(--chip-fail-bg);
  color: var(--chip-fail-fg);
}
.chip-integrity-inconclusive {
  border-style: dashed;
  border-color: var(--border-strong);
  background: var(--chip-muted-bg);
  color: var(--chip-muted-fg);
}
a.chip-spec:hover {
  background: var(--bg-muted);
  color: var(--text);
  border-color: var(--border-strong);
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
/* "commit" is a plain link like the Report link beside it: link colour, underline, weight. */
a.release.is-fallback {
  font-weight: 550;
  color: var(--link);
  text-decoration: underline;
}
a.release.is-fallback:hover { color: #08524f; }
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
.card:hover .card-link { color: var(--link); text-decoration: underline; }
.robots {
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
}
.robot {
  background: transparent;
  border-radius: 0.85rem;
}
.robot > summary {
  list-style: none;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.85rem;
  box-shadow: var(--shadow);
  padding: 0.7rem 0.9rem;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.robot > summary::-webkit-details-marker { display: none; }
.robot > summary::marker { content: ""; }
.robot > summary:hover {
  background: var(--bg-muted);
  border-color: var(--border-strong);
}
.robot[open] {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 0.85rem;
  box-shadow: var(--shadow);
  padding-bottom: 0.55rem;
}
.robot[open] > summary {
  background: transparent;
  border: 0;
  box-shadow: none;
  border-radius: 0.85rem 0.85rem 0 0;
  margin-bottom: 0.35rem;
}
.robot[open] > summary:hover { background: var(--bg-muted); }
.robot[open] .robot-body { padding: 0 0.7rem 0.15rem; }
.summary-row {
  display: grid;
  grid-template-columns: auto minmax(16rem, 1.15fr) minmax(8rem, 1fr) auto;
  align-items: center;
  column-gap: 0.85rem;
  row-gap: 0.3rem;
}
.chevron {
  flex: none;
  width: 1rem;
  height: 1rem;
  color: var(--teal);
  transition: transform 0.15s ease;
}
.robot[open] .chevron { transform: rotate(90deg); }
.summary-id {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.25rem 0.7rem;
  flex: 1 1 14rem;
  min-width: 0;
}
.robot-name {
  font-weight: 700;
  font-size: 1.02rem;
  letter-spacing: -0.01em;
}
.counts {
  color: var(--text-muted);
  font-size: 0.9rem;
}
.fail-count { color: var(--chip-fail-fg); font-weight: 700; }
.pass-count { color: var(--text-muted); }
.count-sep { margin: 0 0.3rem; color: #94a3b8; }
.dots {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 0.35rem;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  justify-self: start;
}
.pip {
  flex: none;
  width: var(--pip-size);
  height: var(--pip-size);
  border-radius: 50%;
  display: block;
}
.pip[hidden], .pip-more[hidden] { display: none; }
.pip-pass { background: var(--pip-pass); }
.pip-fail { background: var(--pip-fail); }
.pip-warn { background: var(--pip-warn); }
.pip-muted { background: var(--pip-muted); }
.pip-more {
  flex: none;
  color: var(--text-muted);
  font-size: 0.78rem;
  font-weight: 650;
  letter-spacing: 0.01em;
  white-space: nowrap;
}
.meta-latest {
  justify-self: end;
  color: var(--text-muted);
  font-size: 0.88rem;
  white-space: nowrap;
  flex: none;
}
.robot-body { padding: 0 0 0.2rem; }
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
  .links a { margin-left: 0; }
  .history-link {
    min-height: 44px;
    padding: 0 0.75rem;
    border: 1px solid var(--border);
    background: var(--surface);
    font-weight: 650;
    text-decoration: none;
  }
  .history-link:hover {
    border-color: var(--border-strong);
    color: var(--link);
  }
  .masthead h1 { font-size: 1.4rem; }
  .wrap { width: min(72rem, 100% - 1.5rem); }
  .summary-row {
    grid-template-columns: auto 1fr auto;
    grid-template-areas:
      "chevron ident ident"
      "dots    dots    meta";
    align-items: center;
    column-gap: 0.55rem;
    row-gap: 0.35rem;
  }
  .chevron { grid-area: chevron; align-self: start; margin-top: 0.28rem; }
  .summary-id { grid-area: ident; flex: none; min-width: 0; display: flex; }
  .dots { grid-area: dots; max-width: 100%; }
  .meta-latest { grid-area: meta; justify-self: end; }
}
@media (prefers-reduced-motion: reduce) {
  .card, .row, .robot > summary, .chevron { transition: none; }
}
"""

INDEX_SCRIPT = """// Open a robot section when the URL hash is its id (e.g. #unitree-g1).
// Truncate the scenario-dot strip to one line and show +n when dots don't fit.
(function () {
  function hashId() {
    try {
      return decodeURIComponent((location.hash || "").replace(/^#/, ""));
    } catch {
      return "";
    }
  }

  function openFromHash() {
    const id = hashId();
    if (!id) return;
    const el = document.getElementById(id);
    if (el && el.tagName === "DETAILS") {
      el.open = true;
      el.scrollIntoView({ block: "start" });
    }
  }

  function setHashFor(details) {
    if (!details.id) return;
    const next = details.open ? "#" + details.id : location.pathname + location.search;
    history.replaceState(null, "", next);
  }

  function fitDots(strip) {
    const more = strip.querySelector(".pip-more");
    const pips = [...strip.querySelectorAll(".pip")];
    pips.forEach((p) => { p.hidden = false; });
    if (more) {
      more.hidden = true;
      more.textContent = "";
    }
    if (!pips.length || strip.clientWidth === 0) return;
    const fits = () => strip.scrollWidth <= strip.clientWidth + 1;
    if (fits()) return;
    if (!more) return;
    more.hidden = false;
    let hidden = 0;
    for (let i = pips.length - 1; i >= 1; i--) {
      pips[i].hidden = true;
      hidden += 1;
      more.textContent = "+" + hidden;
      if (fits()) return;
    }
  }

  function fitAll() {
    document.querySelectorAll(".dots").forEach(fitDots);
  }

  document.querySelectorAll("details.robot").forEach((d) => {
    d.addEventListener("toggle", () => {
      if (d.open || hashId() === d.id) setHashFor(d);
    });
  });

  function start() {
    openFromHash();
    fitAll();
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(fitAll);
    }
  }
  window.addEventListener("hashchange", openFromHash);
  window.addEventListener("resize", fitAll);
  window.addEventListener("load", fitAll);
  document.addEventListener("DOMContentLoaded", start);
  if (document.readyState !== "loading") start();
})();
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
    return (grades[0] if grades else "") or "UNKNOWN"


def _badge_label(grade: str) -> str:
    if grade == "UNSUPPORTED":
        return "Not supported"
    if grade == "INSUFFICIENT_EVIDENCE":
        return "Insufficient evidence"
    return grade


def _badge_html(grade: str) -> str:
    cls = {
        "PASS": "badge-pass",
        "FAIL": "badge-fail",
        "UNSUPPORTED": "badge-muted",
        "INSUFFICIENT_EVIDENCE": "badge-warn",
    }.get(grade, "badge-muted")
    return f'<span class="badge {cls}">{html.escape(_badge_label(grade))}</span>'


def _relative_time_text(created: str, now: str) -> tuple[str, str]:
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
    return relative, tooltip


def _relative_time_html(created: str, now: str) -> str:
    relative, tooltip = _relative_time_text(created, now)
    return (
        f'<time datetime="{html.escape(created, quote=True)}" '
        f'title="{html.escape(tooltip, quote=True)}">{html.escape(relative)}</time>'
    )


def _release_label(record: dict) -> str:
    commit = record.get("commit") or {}
    tag = _stored_text(record.get("bundle_tag") or commit.get("tag"))
    return tag if tag is not None else "commit"


def _release_html(record: dict, *, linked: bool = True) -> str:
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
        if linked and tag_url:
            return (
                f'<a class="release" href="{html.escape(tag_url, quote=True)}" '
                f'title="{html.escape(title, quote=True)}">{html.escape(tag)}</a>'
            )
        return (
            f'<span class="release" title="{html.escape(title, quote=True)}">'
            f"{html.escape(tag)}</span>"
        )
    # No release tag: the link text is "commit". The id itself is never visible (#146).
    title = html.escape(
        "No release tag on this livingproof-verification commit" + (f": {sha}" if sha else ""),
        quote=True,
    )
    if linked and commit_url:
        return (
            f'<a class="release is-fallback" href="{html.escape(commit_url, quote=True)}" '
            f'title="{title}">commit</a>'
        )
    return f'<span class="release is-fallback" title="{title}">commit</span>'


def _chips_html(record: dict, prefix: str, *, linked: bool = True, extra: str = "") -> str:
    grade = _record_grade(record)
    evaluators = record.get("failing_evaluators") or []
    items = []
    if grade not in ("PASS", "UNSUPPORTED") and isinstance(evaluators, list):
        for ev in evaluators:
            if not isinstance(ev, dict):
                continue
            title = _stored_text(ev.get("title"))
            href = _stored_text(ev.get("href"))
            if title is None or href is None:
                continue
            # INCONCLUSIVE is not graded, not failing: the neutral chip-spec style.
            klass = "chip chip-spec" if ev.get("not_graded") is True else "chip"
            if linked:
                tip = f"Open the {title} finding in the report"
                items.append(
                    f'<a class="{klass}" href="{html.escape(prefix + href, quote=True)}" '
                    f'title="{html.escape(tip, quote=True)}" '
                    f'aria-label="{html.escape(tip, quote=True)}">{html.escape(title)}</a>'
                )
            else:
                items.append(f'<span class="{klass}">{html.escape(title)}</span>')
    markers = record.get("integrity_status") or []
    if grade in ("PASS", "UNSUPPORTED") and isinstance(markers, list):
        for marker in markers:
            if not isinstance(marker, dict):
                continue
            title = _stored_text(marker.get("title"))
            status = marker.get("status")
            if title is None or status not in MARKED_INTEGRITY_STATUSES:
                continue
            tip = f"{title}: integrity status {status}"
            if status == "INCONCLUSIVE":
                tip += " (not graded)"
            words = "error" if status == "ERROR" else "not graded"
            icon = "\u2715" if status == "ERROR" else "?"
            sentence = title[:1].upper() + title[1:].lower()
            klass = f"chip chip-integrity chip-integrity-{status.lower()}"
            body = f'<span aria-hidden="true">{icon}</span> {html.escape(sentence)} {words}'
            href = _stored_text(marker.get("id"))
            if linked and href is not None:
                target = html.escape(f"{prefix}#evaluator-{href}", quote=True)
                items.append(
                    f'<a class="{klass}" href="{target}" '
                    f'title="{html.escape(tip, quote=True)}">{body}</a>'
                )
            else:
                items.append(
                    f'<span class="{klass}" title="{html.escape(tip, quote=True)}">{body}</span>'
                )
    if extra:
        items.append(extra)
    if not items:
        return ""
    return f'<div class="chips">{"".join(items)}</div>'


def _spec_versions(record: dict, report: object) -> dict:
    """The recorded spec of one run, as far as it is known: generation tag, assessed tags and
    revisions. An unrecorded part is None, and an unknown is never a change."""
    generation = _stored_text(((record.get("releases") or {}).get("specs") or {}).get("tag"))
    assessed = _spec_assessments(report)

    def distinct(key: str) -> tuple | None:
        values = sorted({v for spec in assessed if (v := _stored_text(spec.get(key)))})
        return tuple(values) or None

    return {"generation": generation, "tags": distinct("tag"), "revisions": distinct("revision")}


def _spec_change(newer: dict, older: dict) -> str | None:
    """`v2.3.0 → v2.4.0` when the spec differs from the run below, else None (#146)."""

    def differs(key: str) -> bool:
        return newer[key] is not None and older[key] is not None and newer[key] != older[key]

    def label(value: object) -> str:
        return ", ".join(value) if isinstance(value, tuple) else str(value)

    pieces = []
    if differs("generation"):
        pieces.append(f"{older['generation']} → {newer['generation']}")
    elif differs("tags"):
        pieces.append(f"{label(older['tags'])} → {label(newer['tags'])}")
    if differs("revisions"):
        pieces.append(f"revision {label(older['revisions'])} → {label(newer['revisions'])}")
    return " · ".join(pieces) or None


def _spec_chip_html(change: str | None) -> str:
    if change is None:
        return ""
    return f'<span class="chip chip-spec">Spec changed: {html.escape(change)}</span>'


def _finding_html(record: dict) -> str:
    grade = _record_grade(record)
    if grade in ("PASS", "UNSUPPORTED"):
        return ""
    for ev in record.get("failing_evaluators") or []:
        if isinstance(ev, dict):
            summary = _stored_text(ev.get("summary"))
            if summary is not None:
                klass = "finding finding-neutral" if ev.get("not_graded") is True else "finding"
                return f'<p class="{klass}">{html.escape(summary)}</p>'
    for item in record.get("outcome") or []:
        if isinstance(item, dict):
            finding = _stored_text(item.get("finding"))
            if finding is not None:
                return f'<p class="finding">{html.escape(finding)}</p>'
    return ""


def _styled_document(
    title: str,
    css_href: str,
    skip_id: str,
    skip_label: str,
    masthead: str,
    main_body: str,
    *,
    script_href: str | None = None,
) -> str:
    script = (
        f'<script src="{html.escape(script_href, quote=True)}"></script>\n' if script_href else ""
    )
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
        f"{script}"
        "</body>\n"
        "</html>\n"
    )


def known_robot_tags(verifications: Path = ROOT / "verifications") -> dict[tuple[str, str], str]:
    """(robot repository, commit) to the tag the reviewed locks pin that commit by.

    A run stored before #129 records only the robot commit. The locks name the same commit by
    tag, so the history header can show the tag for it. Nothing is guessed: a commit no lock
    pins by a tag keeps its short SHA.
    """
    found: dict[tuple[str, str], str] = {}
    for lock_path in sorted(verifications.glob("*.lock.json")):
        robot = json.loads(lock_path.read_text()).get("robot") or {}
        ref = _stored_text(robot.get("tag") or robot.get("ref"))
        commit = _stored_text(robot.get("commit"))
        repository = _stored_text(robot.get("repository"))
        if ref and commit and repository and not COMMIT_SHA.fullmatch(ref):
            found.setdefault((repository, commit), ref)
    return found


def _history_page_html(
    robot: str,
    variant: str,
    records: list[dict],
    now: str,
    reports: dict | None = None,
    robot_tags: dict | None = None,
) -> str:
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
    robot_tag = _stored_text(robot_info.get("tag")) or (robot_tags or {}).get(
        (robot_info["repository"], robot_info["commit"])
    )
    robot_url = robot_info["url"]
    if robot_tag and not robot_info.get("tag"):
        # A record stored without its tag: link the release the locks name, not the bare commit.
        site_base = robot_url.split(f"/{robot_info['repository']}/", 1)[0]
        robot_url = f"{site_base}/{robot_info['repository']}/releases/tag/{robot_tag}"
    # The pin is its tag; with no tag it is "commit", linked to the commit. Never a hash (#146).
    robot_label = html.escape(f"{repo_short} {robot_tag or 'commit'}")
    robot_pin_title = (
        "" if robot_tag else f' title="{html.escape("Commit " + robot_info["commit"], quote=True)}"'
    )
    robot_line = f'<a href="{html.escape(robot_url)}"{robot_pin_title}>{robot_label}</a>'
    software_version = _stored_text(robot_info.get("software_version"))
    if software_version is not None:
        config = _stored_text(robot_info.get("configuration")) or ""
        sw_label = f"{config} {software_version}".strip()
        robot_line += f'<span class="quiet"> · software {html.escape(sw_label)}</span>'
    masthead = (
        '<p class="crumb"><a href="../../index.html">← All verification reports</a></p>'
        f'<p class="eyebrow">{html.escape(robot_title)} · {html.escape(subtitle)}</p>'
        f"<h1>{html.escape(scenario_title)}</h1>"
        f'<p class="lede">{count} run{plural}, newest first. '
        "Older runs are trimmed over time.</p>"
        f'<p class="meta-line">{a("latest/", "Latest report")}'
        f'<span class="quiet"> · {releases}</span></p>'
        f'<p class="meta-line">{robot_line}</p>'
    )
    row_cards = []
    for position, record in enumerate(records):
        grade = _record_grade(record)
        row_class = "row is-fail" if grade in ("FAIL", "INSUFFICIENT_EVIDENCE") else "row is-pass"
        run_prefix = f"runs/{record['folder']}/"
        poster = record.get("poster")
        poster_name = (
            _stored_text(poster.get("name")) if isinstance(poster, dict) else None
        ) or "CI"
        # A row speaks only when its spec differs from the run below it (the next, older record).
        below = records[position + 1] if position + 1 < len(records) else None
        change = None
        if below is not None:
            change = _spec_change(
                _spec_versions(record, (reports or {}).get(record["folder"])),
                _spec_versions(below, (reports or {}).get(below["folder"])),
            )
        chips = _chips_html(record, run_prefix, extra=_spec_chip_html(change))
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


def _index_card_html(
    robot: str,
    variant: str,
    records: list[dict],
    ref_now: str,
    *,
    include_robot: bool,
) -> str:
    newest = records[0]
    grade = _record_grade(newest)
    card_class = "card is-fail" if grade in ("FAIL", "INSUFFICIENT_EVIDENCE") else "card"
    scenario_title = _scenario_title(newest, variant)
    subtitle = _variant_subtitle(newest, variant)
    if include_robot:
        robot_title = _robot_title(robot)
        sub_text = f"{robot_title} · {subtitle}" if subtitle else robot_title
    else:
        sub_text = subtitle
    latest_href = f"{robot}/{variant}/latest/"
    history_href = f"{robot}/{variant}/"
    run_prefix = f"{robot}/{variant}/runs/{newest['folder']}/"
    chips = _chips_html(newest, run_prefix, linked=False)
    finding = _finding_html(newest)
    card_label = f"{scenario_title}, latest report ({_badge_label(grade)})"
    return (
        f'<article class="{card_class}">'
        '<div class="entry-main">'
        f"{_badge_html(grade)}"
        '<div class="entry-body">'
        f'<h3 class="title"><a class="card-link" href="{html.escape(latest_href, quote=True)}" '
        f'aria-label="{html.escape(card_label, quote=True)}">{html.escape(scenario_title)}</a></h3>'
        f'<p class="sub">{html.escape(sub_text)}</p>'
        f"{chips}{finding}"
        "</div>"
        '<div class="entry-side">'
        f'<div class="side-meta">{_release_html(newest, linked=False)}'
        f'<span class="dot">·</span>{_relative_time_html(newest["run"]["created"], ref_now)}</div>'
        f'<div class="links"><a class="history-link" href="{html.escape(history_href, quote=True)}">'
        f"History ({len(records)})</a></div>"
        "</div>"
        "</div>"
        "</article>"
    )


def _index_page_html(history: dict, now: str) -> str:
    grouped: dict[str, list[tuple[str, list[dict]]]] = {}
    all_latest: list[dict] = []
    for (robot, variant), records in sorted(history.items()):
        grouped.setdefault(robot, []).append((variant, records))
        all_latest.append(records[0])
    ref_now = now or max((r["run"]["created"] for r in all_latest), default="")
    total_count = len(all_latest)

    failed_entries = [
        (robot, variant, records)
        for (robot, variant), records in history.items()
        if _record_grade(records[0]) == "FAIL"
    ]
    failed_entries.sort(
        key=lambda item: (
            _robot_title(item[0]).lower(),
            _scenario_title(item[2][0], item[1]).lower(),
            _variant_subtitle(item[2][0], item[1]).lower(),
            item[1],
        )
    )
    failed_entries.sort(
        key=lambda item: (item[2][0]["run"]["created"], item[2][0].get("folder", "")),
        reverse=True,
    )
    if failed_entries:
        failed_count = len(failed_entries)
        noun = "result" if failed_count == 1 else "results"
        needs_cards = [
            _index_card_html(robot, variant, records, ref_now, include_robot=True)
            for robot, variant, records in failed_entries
        ]
        needs_html = (
            '<section class="needs" aria-labelledby="needs-attention">'
            '<h2 id="needs-attention">Needs attention</h2>'
            f'<p class="lede-section">{failed_count} latest {noun} failed, newest first. '
            "Each card opens the latest report.</p>"
            f'<div class="stack">{"".join(needs_cards)}</div>'
            "</section>"
        )
    elif total_count and all(_record_grade(r) == "PASS" for r in all_latest):
        needs_html = f'<p class="all-pass">All {total_count} latest results pass.</p>'
    elif total_count:
        labels = {
            "PASS": "passed",
            "INSUFFICIENT_EVIDENCE": "insufficient evidence",
            "UNSUPPORTED": "not supported",
        }
        counts = {label: 0 for label in (*labels.values(), "unknown")}
        for record in all_latest:
            counts[labels.get(_record_grade(record), "unknown")] += 1
        summary = ", ".join(f"{count} {label}" for label, count in counts.items() if count)
        noun = "result" if total_count == 1 else "results"
        needs_html = f'<p class="status">{total_count} latest {noun}: {summary}.</p>'
    else:
        needs_html = '<p class="status">No verification reports published yet.</p>'

    chevron_svg = (
        '<svg class="chevron" viewBox="0 0 16 16" aria-hidden="true" focusable="false">'
        '<path fill="currentColor" d="M6.2 3.2a.75.75 0 0 0 0 1.06L9.94 8 6.2 11.74a.75.75 0 1 0 '
        '1.06 1.06l4.27-4.27a.75.75 0 0 0 0-1.06L7.26 3.2a.75.75 0 0 0-1.06 0Z"/></svg>'
    )
    robot_sections = []
    for robot, variants in sorted(
        grouped.items(), key=lambda kv: (_robot_title(kv[0]).lower(), kv[0])
    ):
        robot_title = _robot_title(robot)
        ordered = sorted(
            variants,
            key=lambda item: (
                0 if _record_grade(item[1][0]) == "FAIL" else 1,
                _scenario_title(item[1][0], item[0]).lower(),
                _variant_subtitle(item[1][0], item[0]).lower(),
                item[0],
            ),
        )
        scenario_count = len(ordered)
        fail_count = sum(1 for _, recs in ordered if _record_grade(recs[0]) == "FAIL")
        insuff_count = sum(
            1 for _, recs in ordered if _record_grade(recs[0]) == "INSUFFICIENT_EVIDENCE"
        )
        pass_count = sum(1 for _, recs in ordered if _record_grade(recs[0]) == "PASS")
        unsup_count = sum(1 for _, recs in ordered if _record_grade(recs[0]) == "UNSUPPORTED")
        unknown_count = scenario_count - fail_count - insuff_count - pass_count - unsup_count
        scenario_noun = "scenario" if scenario_count == 1 else "scenarios"
        count_parts = [f"{scenario_count} {scenario_noun}"]
        sr_parts = [f"{scenario_count} {scenario_noun}"]
        if fail_count:
            count_parts.append(f'<span class="fail-count">{fail_count} failed</span>')
            sr_parts.append(f"{fail_count} failed")
        if insuff_count:
            count_parts.append(f'<span class="fail-count">{insuff_count} insufficient</span>')
            sr_parts.append(f"{insuff_count} insufficient")
        if pass_count:
            count_parts.append(f'<span class="pass-count">{pass_count} passed</span>')
            sr_parts.append(f"{pass_count} passed")
        if unsup_count:
            count_parts.append(f'<span class="pass-count">{unsup_count} not supported</span>')
            sr_parts.append(f"{unsup_count} not supported")
        if unknown_count:
            count_parts.append(f'<span class="counts">{unknown_count} unknown</span>')
            sr_parts.append(f"{unknown_count} unknown")
        counts_html = '<span class="count-sep">·</span>'.join(count_parts)

        dots = []
        cards = []
        for variant, records in ordered:
            newest = records[0]
            grade = _record_grade(newest)
            scenario_title = _scenario_title(newest, variant)
            pip_cls, pip_status = {
                "FAIL": ("pip-fail", "failed"),
                "PASS": ("pip-pass", "passed"),
                "INSUFFICIENT_EVIDENCE": ("pip-warn", "insufficient evidence"),
                "UNSUPPORTED": ("pip-muted", "not supported"),
            }.get(grade, ("pip-muted", grade.lower()))
            dots.append(
                f'<span class="pip {pip_cls}" '
                f'title="{html.escape(f"{scenario_title}: {pip_status}", quote=True)}"></span>'
            )
            cards.append(_index_card_html(robot, variant, records, ref_now, include_robot=False))

        latest_record = max(
            (recs[0] for _, recs in ordered),
            key=lambda r: (r["run"]["created"], r.get("folder", "")),
        )
        latest_rel = _release_label(latest_record)
        latest_time_text, _ = _relative_time_text(latest_record["run"]["created"], ref_now)
        sr_label = f"{robot_title}, {', '.join(sr_parts)}, latest {latest_rel}, {latest_time_text}"
        meta_latest = (
            f"{_release_html(latest_record, linked=False)}"
            '<span class="dot">·</span>'
            f"{_relative_time_html(latest_record['run']['created'], ref_now)}"
        )
        robot_sections.append(
            f'<details class="robot" id="{html.escape(robot, quote=True)}">'
            "<summary>"
            f'<span class="visually-hidden">{html.escape(sr_label)}</span>'
            '<div class="summary-row" aria-hidden="true">'
            f"{chevron_svg}"
            f'<div class="summary-id"><span class="robot-name">{html.escape(robot_title)}</span>'
            f'<span class="counts">{counts_html}</span></div>'
            f'<span class="dots">{"".join(dots)}<span class="pip-more" hidden></span></span>'
            f'<span class="meta-latest">{meta_latest}</span>'
            "</div>"
            "</summary>"
            f'<div class="robot-body"><div class="stack">{"".join(cards)}</div></div>'
            "</details>"
        )

    robots_html = (
        '<h2 class="robots-label" id="robots">Robots</h2>'
        f'<div class="robots">{"".join(robot_sections)}</div>'
        if robot_sections
        else ""
    )
    masthead = (
        '<p class="eyebrow">Living Proof Labs</p>'
        "<h1>Verification reports</h1>"
        '<p class="lede">The latest result for each robot and scenario. '
        "Failures come first, so they get fixed. Open a robot to see everything it runs.</p>"
    )
    return _styled_document(
        "Living Proof verification reports",
        "styles.css",
        "results",
        "Skip to results",
        masthead,
        needs_html + robots_html,
        script_href="open.js",
    )


def render(site: Path, now: str = "", verifications: Path = ROOT / "verifications"):
    days = retention_days()
    robot_tags = known_robot_tags(verifications)
    prune_expired_evidence(site, now, days)
    history = runs(site)
    (site / "styles.css").write_text(SITE_STYLES)
    (site / "open.js").write_text(INDEX_SCRIPT)
    for (robot, variant), records in sorted(history.items()):
        base = site / robot / variant
        latest = base / "latest"
        if latest.exists():
            shutil.rmtree(latest)
        reports = {
            record["folder"]: read_report(base / "runs" / record["folder"] / "report.json")
            for record in records
        }
        trials = {folder: trial_model(report) for folder, report in reports.items()}
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
        (base / "index.html").write_text(
            _history_page_html(robot, variant, records, now, reports, robot_tags)
        )
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
    render(site, now=run["created"], verifications=verifications)
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
