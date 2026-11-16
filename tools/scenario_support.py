#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read ``scenarios`` from a robot manifest and say when a run is not supported.

robot-capability-specs v2.3.0 (commit 57317e6) is the rule. A scenario that lists
the precondition ``scenario_supported`` is checked against the manifest before any
trial. The list not naming it, or a manifest with no list, grades ``UNSUPPORTED``
naming ``scenario_supported``. No trial runs, and the result is never a ``FAIL``.
``walk-corner`` lists that precondition. ``walk-hallway`` does not, so a manifest
with no list keeps the trial grade.

The spec does not name the path of this file in a robot repository. The pinned
commit is read at ``robot-manifest.json``. A missing file is a manifest with no
``scenarios`` list. Fixture manifests cover robots that have not released one.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

FORMAT = "livingproof-robot-manifest/0.1"
OUTPUT_FORMAT = "livingproof-verification-scenario-support/0.1"
ROBOT_MANIFEST = "robot-manifest.json"
# Scenarios whose README lists scenario_supported. v2.3.0: walk-corner does, walk-hallway does not.
REQUIRES_SCENARIO_SUPPORTED = frozenset({"walk-corner"})
PRECONDITION = "scenario_supported"
NOT_SUPPORTED = "not supported"
UNSUPPORTED = "UNSUPPORTED"
ERROR = "ERROR"
DETAIL = "UNSUPPORTED: precondition scenario_supported unmet; no trials run."
INVALID = "The robot manifest scenarios list is invalid. The run is not graded."
SCENARIO_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION = re.compile(r"\d+\.\d+\.\d+")
SHA = re.compile(r"[0-9a-f]{40}")
RAW = "https://raw.githubusercontent.com"


class ScenarioSupportError(Exception):
    """A pinned manifest could not be read. Absence is not this error."""


def scenario_name(scenario: str) -> str | None:
    """``walk-corner`` or ``walk-corner@0.1.0``. None when it is not a scenario id."""
    if not isinstance(scenario, str):
        return None
    name, separator, version = scenario.partition("@")
    if not SCENARIO_ID.fullmatch(name):
        return None
    if separator and not VERSION.fullmatch(version):
        return None
    return name


def _problem(manifest: dict | None) -> str | None:
    """Why ``manifest`` cannot be read. None when it is usable, including a missing file."""
    if manifest is None:
        return None
    if not isinstance(manifest, dict):
        return "manifest must be an object"
    if manifest.get("format") != FORMAT:
        return f"format must be {FORMAT}"
    if "scenarios" not in manifest:
        return None
    scenarios = manifest["scenarios"]
    if not isinstance(scenarios, list):
        return "scenarios must be a list of scenario ids"
    if any(not isinstance(item, str) or not SCENARIO_ID.fullmatch(item) for item in scenarios):
        return "scenarios must be a list of scenario ids"
    if len(scenarios) != len(set(scenarios)):
        return "scenarios ids must be unique"
    return None


def declared_scenarios(manifest: dict | None) -> list[str] | None:
    """The list the manifest names, or None when the list is absent.

    None means the manifest names nothing. An empty list also names nothing and is
    returned as ``[]``.
    """
    if _problem(manifest) is not None or manifest is None or "scenarios" not in manifest:
        return None
    return list(manifest["scenarios"])


def grade(manifest: dict | None, scenario: str, outcome: str) -> str:
    """The catalog grade: invalid is ``ERROR``, undeclared is ``UNSUPPORTED``, else ``outcome``.

    ``outcome`` is the trial grade. It is returned only when the scenario does not
    require ``scenario_supported``, or the manifest names it. The declaration is not
    evidence.
    """
    if _problem(manifest) is not None:
        return ERROR
    name = scenario_name(scenario)
    if name is None or not isinstance(outcome, str):
        return ERROR
    listed = [] if manifest is None or "scenarios" not in manifest else manifest["scenarios"]
    if name in REQUIRES_SCENARIO_SUPPORTED and name not in listed:
        return UNSUPPORTED
    return outcome


def verification(manifest: dict | None, scenario: str, outcome: str) -> dict:
    """Generated verification output for one scenario on one manifest."""
    result = grade(manifest, scenario, outcome)
    name = scenario_name(scenario) or (scenario if isinstance(scenario, str) else "")
    document = {
        "format": OUTPUT_FORMAT,
        "scenario": name,
        "grade": result,
        "precondition": PRECONDITION if result == UNSUPPORTED else None,
        "trials": [] if result == UNSUPPORTED else None,
        "statement": NOT_SUPPORTED if result == UNSUPPORTED else None,
        "declared_scenarios": declared_scenarios(manifest),
        "detail": DETAIL if result == UNSUPPORTED else (INVALID if result == ERROR else None),
    }
    return document


def render(document: dict, system: dict | None = None) -> dict[str, str]:
    """Report text for ``document``. Every format states the same grade."""
    scenario = str(document.get("scenario") or "")
    result = str(document.get("grade") or "")
    if result == UNSUPPORTED:
        body = (
            f"{scenario} is not supported.\n\n"
            f"{document.get('detail')}\n\n"
            "No trial ran. This result is not a failure.\n"
        )
    elif result == ERROR:
        body = f"{document.get('detail')}\n"
    else:
        body = f"Grade: {result}\n"
    escaped = html.escape(body)
    page = (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>{html.escape(scenario)}</title></head><body>"
        f"<h1>{html.escape(scenario)}</h1><p>{escaped}</p></body></html>\n"
    )
    summary = {
        "capability": scenario,
        "system": system or {"id": scenario},
        "grade": result,
        "k": None,
        "n": None,
        "required_passes": None,
    }
    if document.get("statement"):
        summary["statement"] = document["statement"]
    report = {"report_version": "1.0.0", "summaries": [summary]}
    return {
        "md": f"# {scenario}\n\n{body}",
        "txt": body,
        "html": page,
        "json": json.dumps(report, indent=2) + "\n",
    }


def _summary(report: object) -> dict | None:
    if not isinstance(report, dict):
        return None
    summaries = report.get("summaries")
    if not isinstance(summaries, list) or not summaries or not isinstance(summaries[0], dict):
        return None
    return summaries[0]


def apply_published(folder: Path, lock: dict, manifest: dict | None) -> None:
    """Write verification output. Replace the report only when the trial grade must not stand.

    The copied report has already passed its checksum. A declared scenario, and a
    scenario that does not require ``scenario_supported``, keep that report.
    """
    path = folder / "report.json"
    report = json.loads(path.read_text())
    summary = _summary(report)
    outcome = summary.get("grade") if summary is not None else None
    scenario = lock.get("scenario") if isinstance(lock, dict) else None
    if not isinstance(outcome, str) or not isinstance(scenario, str):
        return
    document = verification(manifest, scenario, outcome)
    (folder / "verification.json").write_text(json.dumps(document, indent=2) + "\n")
    if document["grade"] not in {UNSUPPORTED, ERROR}:
        return
    system = summary.get("system") if isinstance(summary.get("system"), dict) else None
    rendered = render(document, system=system)
    (folder / "report.md").write_text(rendered["md"])
    (folder / "report.txt").write_text(rendered["txt"])
    (folder / "report.html").write_text(rendered["html"])
    path.write_text(rendered["json"])


def _open(url: str, timeout: int):
    return urllib.request.urlopen(url, timeout=timeout)


def pinned_manifest(repository: str, commit: str, *, opener=_open) -> dict | None:
    """The manifest at ``commit``, or None when the robot has not published one.

    Anything other than a missing file is ``ScenarioSupportError``. An outage is not
    read as a manifest that names nothing.
    """
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise ScenarioSupportError(f"{repository}: commit {commit!r} is not a full sha")
    if not isinstance(repository, str) or "/" not in repository:
        raise ScenarioSupportError(f"repository {repository!r} is not OWNER/NAME")
    url = f"{RAW}/{repository}/{commit}/{ROBOT_MANIFEST}"
    try:
        with opener(url, 60) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise ScenarioSupportError(
            f"{repository}@{commit[:12]}: {ROBOT_MANIFEST} could not be read ({exc.code})"
        ) from exc
    except OSError as exc:
        raise ScenarioSupportError(
            f"{repository}@{commit[:12]}: {ROBOT_MANIFEST} could not be read ({exc})"
        ) from exc
    try:
        document = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ScenarioSupportError(
            f"{repository}@{commit[:12]}: {ROBOT_MANIFEST} is not valid JSON ({exc.msg})"
        ) from exc
    if not isinstance(document, dict):
        raise ScenarioSupportError(
            f"{repository}@{commit[:12]}: {ROBOT_MANIFEST} must be a JSON object"
        )
    return document


def pinned_manifests(locks: list[dict], *, opener=_open) -> dict[str, dict | None]:
    """One manifest per robot repository, read at the commit the lock already pins."""
    found: dict[str, dict | None] = {}
    commits: dict[str, str] = {}
    for lock in locks:
        robot = lock.get("robot") if isinstance(lock, dict) else None
        if not isinstance(robot, dict):
            raise ScenarioSupportError("a lock is missing robot")
        repository = robot.get("repository")
        commit = robot.get("commit")
        if not isinstance(repository, str) or not isinstance(commit, str):
            raise ScenarioSupportError("a lock is missing robot.repository or robot.commit")
        previous = commits.get(repository)
        if previous is not None and previous != commit:
            raise ScenarioSupportError(
                f"{repository} is pinned at two commits ({previous[:12]} and {commit[:12]})"
            )
        if repository in found:
            continue
        commits[repository] = commit
        found[repository] = pinned_manifest(repository, commit, opener=opener)
    return found
