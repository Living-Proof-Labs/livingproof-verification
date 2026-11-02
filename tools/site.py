# SPDX-License-Identifier: Apache-2.0
"""Assemble the GitHub Pages site from a run's capability-report artifacts.

Layout only: the reports are copied exactly as the reporter rendered them (their
SHA256SUMS are checked), and the verifier's grade and trial counts are read from
report.json, never recomputed. Per verification and configuration:

    <robot>/<variant>/runs/<run-id>/   report.{html,json,md,txt}, SHA256SUMS, run.json,
                                       index.html (provenance + the report)
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
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PAGES_ARTIFACT = "github-pages"
SEGMENT = re.compile(r"[a-z0-9][a-z0-9.-]*")
RUN_ID = re.compile(r"[0-9]+(-[0-9]+)?")
REPORT_FILES = ("report.html", "report.json", "report.md", "report.txt")


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


def restore(
    destination: Path,
    repository: str,
    branch: str,
    current_run: int | None,
    token: str | None,
    api: str = "https://api.github.com",
    dry_run: bool = False,
):
    destination.mkdir(parents=True, exist_ok=True)
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
        print(f"No earlier {PAGES_ARTIFACT} artifact on {branch}: starting a new site.")
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


def outcome(report: dict) -> list[dict]:
    """The verifier's grade and counts as the reporter recorded them (no regrading)."""
    return [
        {
            "capability": s["capability"],
            "system": s["system"]["id"],
            "grade": s["grade"],
            "passed": s["k"],
            "trials": s["n"],
            "required": s["required_passes"],
        }
        for s in report["summaries"]
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


def add_run(site: Path, reports: Path, target: dict, run: dict) -> Path:
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
        "outcome": outcome(json.loads((source / "report.json").read_text())),
        **provenance(target, run),
    }
    (folder / "run.json").write_text(json.dumps(record, indent=2) + "\n")
    return folder


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
iframe{width:100%;height:80vh;border:1px solid #d0d7de}code{font-size:.9em}"""


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


def outcome_cells(record: dict) -> str:
    return "<br>".join(
        f'<span class="{html.escape(o["grade"])}">{html.escape(o["grade"])}</span> '
        f"{o['passed']}/{o['trials']} passed ({o['required']} needed) · "
        f"{html.escape(o['capability'])}"
        for o in record["outcome"]
    )


def run_page(record: dict, label: str, up: str) -> str:
    """`up` is the relative path from the page's folder to <robot>/<variant>/."""
    p = record
    rows = [
        ("Outcome", outcome_cells(p)),
        ("CI run", a(p["run"]["url"], f"run {p['run']['id']}") + f" · {p['run']['created']}"),
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
    body = (
        f"<dl>{details}</dl><p>Rendered by the reporter: {files}. "
        f'<a href="{up}index.html">Run history</a>.</p>'
        f'<iframe src="report.html" title="Capability report"></iframe>'
    )
    title = f"{p['robot']['repository'].rsplit('/', 1)[1]} · {p['variant']} · {label}"
    return page(title, body, up=f"{up}../../index.html")


def render(site: Path):
    history = runs(site)
    rows = []
    for (robot, variant), records in sorted(history.items()):
        base = site / robot / variant
        latest = base / "latest"
        if latest.exists():
            shutil.rmtree(latest)
        newest = records[0]
        shutil.copytree(base / "runs" / newest["folder"], latest)
        for record in records:
            (base / "runs" / record["folder"] / "index.html").write_text(
                run_page(record, f"run {record['run']['id']}", up="../../")
            )
        (latest / "index.html").write_text(
            run_page(newest, f"latest (run {newest['run']['id']})", up="../")
        )
        items = "".join(
            f"<tr><td>{a('runs/' + r['folder'] + '/', r['run']['id'])}</td>"
            f"<td>{a(r['run']['url'], 'CI run ' + r['run']['id'])}</td>"
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
            f"{a(newest['run']['url'], 'CI run ' + newest['run']['id'])}</td>"
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


def build(site: Path, reports: Path, run: dict, verifications: Path = ROOT / "verifications"):
    added = [add_run(site, reports, t, run) for t in targets(verifications)]
    render(site)
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
    b = sub.add_parser("build", help="add this run's reports to SITE and write the pages")
    b.add_argument("site", type=Path)
    b.add_argument("--reports", type=Path, required=True)
    b.add_argument("--run-id", required=True)
    b.add_argument("--commit", required=True)
    b.add_argument("--created", required=True, help="UTC ISO 8601 time of the run")
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
        )
    else:
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
        )
        for folder in added:
            print(f"Added {folder.relative_to(args.site)}")


if __name__ == "__main__":
    main()
