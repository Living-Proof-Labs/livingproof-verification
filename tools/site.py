# SPDX-License-Identifier: Apache-2.0
"""Assemble the GitHub Pages site from a run's capability-report artifacts.

Layout only: the reports are copied exactly as the reporter rendered them (their
SHA256SUMS are checked), and the verifier's grade and trial counts are read from
report.json, never recomputed. Per verification and configuration:

    <robot>/<variant>/runs/<run-id>/   report.{html,json,md,txt}, SHA256SUMS, run.json,
                                       index.html (provenance, evidence + the report);
                                       report.html.orig, the reporter's report.html, when
                                       report.html's evidence links were pointed at the
                                       site copies (SHA256SUMS lists the reporter's bytes)
    <robot>/<variant>/runs/<run-id>/evidence/<artifact>/
                                       site copies of the run's linked evidence (MCAP,
                                       videos), with sha256 in run.json
    <robot>/<variant>/latest/          a copy of the newest run folder
    <robot>/<variant>/index.html       run history
    index.html                         every robot/variant with grade and counts

Earlier runs come from the previous Pages artifact (`restore`), so history survives
without committing anything to git.
"""

import argparse
import hashlib
import html
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import urllib.error
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
ORIGINAL_HTML = "report.html.orig"  # served as a download, not as a page
# The reporter's evidence links: Actions artifact pages (GitHub sign-in, then expiry).
ARTIFACT_LINK = re.compile(
    r"<a href=(['\"])(https://github\.com/[^'\"<>]+/actions/runs/[0-9]+/artifacts/([0-9]+))\1>"
    r"(.*?)</a>",
    re.S,
)
# Any other reporter anchor to Actions (v3.0.3's Evidence "CI run" link), shown as text (#23).
ACTIONS_ANCHOR = re.compile(
    r"<a\s[^>]*?href=(['\"])([^'\"<>]*/actions/[^'\"<>]*)\1[^>]*>(.*?)</a>", re.S | re.I
)
ACTIONS_RUN = re.compile(r"/actions/runs/([0-9]+)(?:/attempts/[0-9]+)?/?$")
MARKER = "site.json"  # written by every build; its presence on the live site means history


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


def _request(url: str, token: str | None):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token:
        # Not forwarded on the redirect to the artifact storage host.
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(request, timeout=120)


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
    artifacts, page = [], 1
    while True:
        with _request(
            f"{api}/repos/{repository}/actions/artifacts?name={PAGES_ARTIFACT}"
            f"&per_page=100&page={page}",
            token,
        ) as response:
            batch = json.load(response)["artifacts"]
        artifacts += batch
        if len(batch) < 100:
            break
        page += 1
    artifact = latest_pages_artifact(artifacts, branch, current_run)
    if artifact is None:
        if not site_url:
            raise SiteError(
                f"No earlier {PAGES_ARTIFACT} artifact on {branch} and no --site-url to check "
                "for published history; pass --site-url, or --reset-history to start anew."
            )
        published = live_history(site_url)
        if published:
            raise SiteError(
                f"No earlier {PAGES_ARTIFACT} artifact on {branch}, but {published} is live: "
                "the artifact probably expired. Refusing to publish a site without its run "
                "history; run the workflow with reset-history to start anew."
            )
        print(
            f"No earlier {PAGES_ARTIFACT} artifact on {branch} and nothing published: "
            "starting a new site."
        )
        return None
    run = artifact["workflow_run"]["id"]
    print(
        f"Restoring run history from artifact {artifact['id']} (run {run}, "
        f"{artifact['created_at']})."
    )
    if dry_run:
        return artifact
    with _request(artifact["archive_download_url"], token) as response:
        count = extract_pages(response.read(), destination)
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


REPORT_VERSIONS = {"1.0.0"}  # report.json shapes this site knows how to read
SUMMARY_FIELDS = ("capability", "system", "grade", "k", "n", "required_passes")


def read_report(path: Path) -> object:
    """report.json parsed, or a SiteError naming it when it is not valid JSON."""
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise SiteError(f"{path}: not valid JSON ({error.msg} at line {error.lineno})") from None


def outcome(report: dict, where: str = "report.json") -> list[dict]:
    """The verifier's grade and counts as the reporter recorded them (no regrading)."""
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
    return [
        {
            "capability": s["capability"],
            "system": s["system"]["id"],
            "grade": s["grade"],
            "passed": s["k"],
            "trials": s["n"],
            "required": s["required_passes"],
        }
        for s in summaries
    ]


def provenance(target: dict, run: dict) -> dict:
    lock, server = target["lock"], run["server"]
    releases = lock["releases"]
    robot = lock["robot"]
    return {
        "run": {
            "id": run["id"],
            "url": f"{server}/{run['repository']}/actions/runs/{run['id'].split('-')[0]}",
            "created": run["created"],
        },
        "commit": {
            "sha": run["commit"],
            "url": f"{server}/{run['repository']}/commit/{run['commit']}",
        },
        "robot": {
            "repository": robot["repository"],
            "commit": robot["commit"],
            "configuration": target["configuration"],
            "url": f"{server}/{robot['repository']}/tree/{robot['commit']}",
        },
        "releases": {
            name: {**value, "url": f"{server}/{value['repository']}/releases/tag/{value['tag']}"}
            for name, value in releases.items()
        },
        "definition": f"verifications/{target['id']}.toml",
    }


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
    if not RUN_ID.fullmatch(run["id"]):
        raise SiteError(f"Unexpected run id {run['id']!r}")
    folder = site / target["robot"] / target["variant"] / "runs" / run["id"]
    if folder.exists():
        shutil.rmtree(folder)  # a re-run attempt of the same run replaces itself
    folder.mkdir(parents=True)
    for name in (*REPORT_FILES, "SHA256SUMS"):
        shutil.copyfile(source / name, folder / name)
    record = {
        "robot": target["robot"],
        "variant": target["variant"],
        "verification": target["id"],
        "outcome": outcome(read_report(source / "report.json"), str(source / "report.json")),
        **provenance(target, run),
    }
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
dl{display:grid;grid-template-columns:max-content 1fr;gap:.2rem 1rem}dt{font-weight:600}
iframe{width:100%;height:80vh;border:1px solid #d0d7de}code{font-size:.9em}
.expired{color:#6e7781}.note{background:#fff8c5;padding:.5rem .8rem;border:1px solid #d4a72c}"""


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


def ci_run(run: dict) -> str:
    """The CI run as provenance text; Actions pages need a GitHub sign-in (#23)."""
    return f"CI run {html.escape(run['id'])}"


ACTIONS_HREF = re.compile(r"""(?:href|src)\s*=\s*["']?[^"'\s>]*/actions/""", re.I)


def actions_links(site: Path) -> list[str]:
    """Pages in SITE that still link to GitHub Actions (sign-in required, then expiry)."""
    return sorted(
        str(page.relative_to(site))
        for page in site.rglob("*.html")
        if ACTIONS_HREF.search(page.read_text(errors="replace"))
    )


def outcome_cells(record: dict) -> str:
    return "<br>".join(
        f'<span class="{html.escape(o["grade"])}">{html.escape(o["grade"])}</span> '
        f"{o['passed']}/{o['trials']} passed ({o['required']} needed) · "
        f"{html.escape(o['capability'])}"
        for o in record["outcome"]
    )


ACTIONS_NOTE = (
    "The original GitHub Actions artifacts need a GitHub sign-in to download and are deleted "
    "when they expire, so the report on this site links its evidence to the copies here and "
    "names each Actions artifact only as a secondary reference with its expiry"
)


def evidence_section(record: dict, now: str) -> str:
    """Site copies first; the original Actions artifact is a link only before it expires."""
    items = record.get("evidence")
    if items is None:
        return (
            "<h2>Evidence</h2>"
            '<p class="note">Evidence for this run was not copied to this site. The report '
            "names its GitHub Actions artifacts only as secondary references: they need a "
            "GitHub sign-in and, once expired, are unavailable here; the report's "
            "assessments and SHA256SUMS above are unchanged.</p>"
        )
    rows = []
    for e in items:
        what = html.escape(e["label"])
        if "seed" in e:
            what = f"seed {e['seed']} ({html.escape(str(e.get('status', '')))}): {what}"
        art = e["artifact"]
        name = f"Actions artifact {html.escape(art['id'])}"
        if art.get("name"):
            name += f" <code>{html.escape(art['name'])}</code>"
        if art.get("digest"):
            name += f" · <code>{html.escape(art['digest'])}</code>"
        expires = art.get("expires_at")
        if expires and expires <= now:
            original = f'<span class="expired">{name}: expired {html.escape(expires)}, no longer available</span>'
        elif expires:
            original = (
                f"{name} (maintainers only, GitHub sign-in required; "
                f"available until {html.escape(expires)})"
            )
        else:
            original = f'<span class="expired">{name}: expiry unknown</span>'
        if e["preserved"]:
            listing = listing_path(e)
            copies = (
                a(listing, f"all {len(e['files'])} files") + "<br>" if listing else ""
            ) + "<br>".join(
                a(f["path"], f["path"].rsplit("/", 1)[1])
                + f" · {f['bytes']} bytes · sha256 <code>{html.escape(f['sha256'])}</code>"
                for f in e["files"]
            )
        else:
            copies = (
                f'<span class="expired">not preserved: {html.escape(e.get("reason", ""))}</span>'
            )
        rows.append(f"<tr><td>{what}</td><td>{copies}</td><td>{original}</td></tr>")
    return (
        "<h2>Evidence</h2><table><tr><th>Evidence</th><th>Copy on this site</th>"
        f"<th>Original</th></tr>{''.join(rows)}</table>"
        f'<p class="note">{ACTIONS_NOTE}; use the copies on this site.</p>'
    )


def listing_path(e: dict) -> str | None:
    """Where a preserved artifact with several files is listed: ``evidence/<name>.html``."""
    name = e["artifact"].get("name")
    if not (e.get("preserved") and len(e.get("files") or []) > 1 and name):
        return None
    return f"evidence/{name}.html"


def write_listing(folder: Path, e: dict, up: str) -> None:
    """A page linking every file of one preserved artifact (e.g. all 20 trial MCAPs).
    ``up`` leads from ``folder`` to the site root, so the page links back to the reports index."""
    path = listing_path(e)
    if path is None:
        return
    prefix = path.removesuffix(".html").removeprefix("evidence/") + "/"
    rows = "".join(
        "<tr><td>"
        + a(f["path"].removeprefix("evidence/"), f["path"].removeprefix(f"evidence/{prefix}"))
        + f"</td><td>{f['bytes']}</td><td><code>{html.escape(f['sha256'])}</code></td></tr>"
        for f in e["files"]
    )
    body = (
        f"<p>All {len(e['files'])} files of Actions artifact "
        f"<code>{html.escape(e['artifact']['name'])}</code>, copied to this site. "
        f"{a('../index.html', 'Back to the run')} · {a('../report.html', 'report')}</p>"
        f"<table><tr><th>File</th><th>Bytes</th><th>sha256</th></tr>{rows}</table>"
    )
    title = e.get("label") or e["artifact"]["name"]
    (folder / path).write_text(page(title, body, up=f"../{up}index.html"))


def original_reference(artifact: dict, now: str) -> str:
    """The Actions artifact as a secondary reference, as text: never a link (#23).

    Logged-out evaluators get 404 from Actions pages, so the id and expiry are kept for
    maintainers but nothing on the site points there.
    """
    name = f"Actions artifact {html.escape(artifact['id'])}"
    expires = artifact.get("expires_at")
    if expires and expires <= now:
        return f"original {name} expired {html.escape(expires)}"
    if expires:
        return f"original {name}, maintainers only (GitHub sign-in), expires {html.escape(expires)}"
    return f"original {name}: expiry unknown"


def publish_report_html(folder: Path, record: dict, now: str) -> None:
    """Point report.html's evidence links at this site's copies.

    The reporter links evidence to Actions artifacts, which need a GitHub sign-in and
    expire. The published report.html links the site copy first and keeps the Actions
    artifact as a secondary reference labelled with its expiry. The reporter's bytes stay
    as report.html.orig (what SHA256SUMS lists); report.json, .md, .txt and SHA256SUMS are
    never changed. Any other reporter anchor to Actions (the Evidence "CI run" link) becomes
    text. Rebuilt from the original on every render, so expiry labels stay current.
    """
    published, original = folder / "report.html", folder / ORIGINAL_HTML
    source = original if original.is_file() else published
    text = source.read_text()
    if not (ARTIFACT_LINK.search(text) or ACTIONS_ANCHOR.search(text)):
        return
    if not original.is_file():
        shutil.copyfile(published, original)
    by_id = {e["artifact"]["id"]: e for e in record.get("evidence") or []}

    def replace(match: re.Match) -> str:
        url, artifact_id, label = match.group(2), match.group(3), match.group(4)
        e = by_id.get(artifact_id)
        artifact = e["artifact"] if e else {"id": artifact_id, "url": url}
        if e and listing_path(e):
            # One link that opens every file: the label promises all of them.
            primary = (
                f"<a href='{html.escape(listing_path(e))}'>{label}</a> ({len(e['files'])} files)"
            )
        elif e and e["preserved"] and e["files"]:
            first, *rest = e["files"]
            primary = f"<a href='{html.escape(first['path'])}'>{label}</a>" + "".join(
                f" · <a href='{html.escape(f['path'])}'>{html.escape(f['path'].rsplit('/', 1)[1])}</a>"
                for f in rest
            )
        else:
            reason = e.get("reason", "") if e else "no copy recorded for this run"
            primary = f"{label} (not on this site: {html.escape(reason)})"
        return f"{primary} <small>({original_reference(artifact, now)})</small>"

    def as_text(match: re.Match) -> str:
        run = ACTIONS_RUN.search(match.group(2))
        what = f" {run.group(1)}" if run else ""
        return f"{match.group(3)}{what} <small>(maintainers only, GitHub sign-in required)</small>"

    published.write_text(ACTIONS_ANCHOR.sub(as_text, ARTIFACT_LINK.sub(replace, text)))


def run_page(record: dict, label: str, up: str, now: str = "") -> str:
    """`up` is the relative path from the page's folder to <robot>/<variant>/."""
    p = record
    rows = [
        ("Outcome", outcome_cells(p)),
        (
            "CI run",
            ci_run(p["run"]) + f" · {html.escape(p['run']['created'])} · maintainers only",
        ),
        (
            "livingproof-verification",
            a(p["commit"]["url"], p["commit"]["sha"][:12])
            + " · "
            + a(
                p["commit"]["url"].replace("/commit/", "/blob/") + "/" + p["definition"],
                p["definition"],
            ),
        ),
        (
            "Robot",
            a(p["robot"]["url"], f"{p['robot']['repository']} @ {p['robot']['commit'][:12]}")
            + f" · configuration "
            f"<code>{html.escape(p['robot']['configuration'])}</code>",
        ),
    ]
    for name, release in p["releases"].items():
        text = f"{release['repository'].rsplit('/', 1)[1]} {release['tag']}"
        extra = f" · <code>{html.escape(release['digest'])}</code>" if "digest" in release else ""
        rows.append(
            (
                name.capitalize(),
                a(release["url"], text) + f" · <code>{release['commit'][:12]}</code>{extra}",
            )
        )
    details = "".join(f"<dt>{html.escape(k)}</dt><dd>{v}</dd>" for k, v in rows)
    files = " · ".join(a(n, n) for n in (*REPORT_FILES, "SHA256SUMS", "run.json"))
    if p.get("original_html"):
        files += (
            ". report.html links evidence to the copies on this site; the reporter's "
            f"report.html, as SHA256SUMS lists it, is {a(ORIGINAL_HTML, ORIGINAL_HTML)}"
        )
    body = (
        f"<dl>{details}</dl><p>Rendered by the reporter: {files}. "
        f'<a href="{up}index.html">Run history</a>.</p>'
        + evidence_section(p, now)
        + '<iframe src="report.html" title="Capability report"></iframe>'
    )
    title = f"{p['robot']['repository'].rsplit('/', 1)[1]} · {p['variant']} · {label}"
    return page(title, body, up=f"{up}../../index.html")


def render(site: Path, now: str = ""):
    history = runs(site)
    rows = []
    for (robot, variant), records in sorted(history.items()):
        base = site / robot / variant
        latest = base / "latest"
        if latest.exists():
            shutil.rmtree(latest)
        for record in records:
            folder = base / "runs" / record["folder"]
            for e in record.get("evidence") or []:
                write_listing(folder, e, up="../../../../")
            publish_report_html(folder, record, now)
            record["original_html"] = (folder / ORIGINAL_HTML).is_file()
        newest = records[0]
        shutil.copytree(base / "runs" / newest["folder"], latest)
        for e in newest.get("evidence") or []:
            write_listing(latest, e, up="../../../")
        for record in records:
            (base / "runs" / record["folder"] / "index.html").write_text(
                run_page(record, f"run {record['run']['id']}", up="../../", now=now)
            )
        (latest / "index.html").write_text(
            run_page(newest, f"latest (run {newest['run']['id']})", up="../", now=now)
        )
        items = "".join(
            f"<tr><td>{a('runs/' + r['folder'] + '/', r['run']['id'])}</td>"
            f"<td>{ci_run(r['run'])}</td>"
            f"<td>{html.escape(r['run']['created'])}</td><td>{outcome_cells(r)}</td>"
            f"<td>{a(r['commit']['url'], r['commit']['sha'][:12])}</td></tr>"
            for r in records
        )
        (base / "index.html").write_text(
            page(
                f"{robot} · {variant} · run history",
                f"<p>{a('latest/', 'Latest report')}</p><table><tr><th>Run</th><th>CI</th><th>Created</th>"
                f"<th>Outcome</th><th>Commit</th></tr>{items}</table>",
                up="../../index.html",
            )
        )
        rows.append(
            f"<tr><td>{html.escape(robot)}</td><td>{html.escape(variant)}</td>"
            f"<td>{outcome_cells(newest)}<br>"
            f"{ci_run(newest['run'])}</td>"
            f"<td>{a(f'{robot}/{variant}/latest/', 'latest')} · "
            f"{a(f'{robot}/{variant}/', f'history ({len(records)} run{"s" * (len(records) != 1)})')}</td></tr>"
        )
    body = (
        "<p>The latest capability report for each robot and variant, generated only by CI "
        "on <code>main</code> of "
        '<a href="https://github.com/Living-Proof-Labs/livingproof-verification">'
        "livingproof-verification</a>. The capability verifier grades; the reporter renders; "
        "this site only lays the reports out.</p>"
        "<table><tr><th>Robot</th><th>Variant</th><th>Latest outcome</th><th>Reports</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    (site / "index.html").write_text(page("Living Proof verification reports", body))


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
    if linked := actions_links(site):
        raise SiteError(f"pages still link to GitHub Actions (#23): {', '.join(linked)}")
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
        added = build(
            args.site,
            args.reports,
            {
                "id": args.run_id,
                "commit": args.commit,
                "created": args.created,
                "repository": args.repository,
                "server": args.server,
            },
            evidence=args.evidence,
            artifacts=artifacts,
        )
        for folder in added:
            print(f"Added {folder.relative_to(args.site)}")


if __name__ == "__main__":
    main()
