#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write a ``livingproof-verification-lock/0.2.0`` for one verification definition.

    python3 tools/lock.py verifications/<definition>.toml

The lock owns releases (specs, runner, verifier, reporter digest), the plan, the world,
each configuration's sha256 and the ``livingproof-evaluator-scenario`` pin. ``0.1.0``
locks stay readable: this command loads one when it is still beside the definition, and
the capability-verifier checker accepts both formats during the migration.

A robot that still publishes ``verification.lock.json`` supplies scenario, plan, world
and the specs and runner releases. The reporter tag, repository and image come from
``verifications/settings.toml`` in this repository; the commit and digest are resolved
from that tag. The verifier pin is the walk-hallway commit that definition's job calls
in ``.github/workflows/verify.yml``, with that tag's hallway image digest. The
scenario-evaluator pin is read from that verifier release. A thin robot publishes the
adapter and configurations only. ``lock`` then keeps those owned fields from the
existing ``0.2.0`` and refreshes the commit, adapter and configuration sha256s. With
neither source it exits with that explanation. It does not raise ``FileNotFoundError``
for a missing robot lock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib
import urllib.error
import urllib.request

FORMAT_V1 = "livingproof-verification-lock/0.1.0"
FORMAT_V2 = "livingproof-verification-lock/0.2.0"
DEFINITION_FORMAT = "livingproof-verification-definition/0.1.0"
ROBOT_LOCK = "verification.lock.json"
EVALUATOR = "livingproof-evaluator-scenario"
# Decision 7 of livingproof-verification#40. Used only when the pinned verifier
# release does not declare the package (v1.16.3 and earlier). The commit is resolved
# from the tag; it is never invented.
DEFAULT_EVALUATOR = ("Living-Proof-Labs/livingproof-evaluator-scenario", "v1.0.0")
OWNED = ("releases", "scenario", "plan", "world", "evaluators")

FILE_SHA = re.compile(r"[0-9a-f]{64}")
SEMVER = re.compile(r"\d+\.\d+\.\d+")
SCENARIO_ID = re.compile(r"[a-z0-9-]+@\d+\.\d+\.\d+")
TAG = re.compile(r"v\d+\.\d+\.\d+")
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
EVALUATOR_DEP = re.compile(
    r"livingproof-evaluator-scenario\s*@\s*git\+https://github\.com/"
    r"(?P<repository>[A-Za-z0-9-]+/[A-Za-z0-9._-]+)\.git@(?P<tag>v\d+\.\d+\.\d+)\b"
)
PROCEDURE = ".github/workflows/walk-hallway.yml"
VERIFIER_IMAGE = "ghcr.io/living-proof-labs/capability-verifier-hallway"
MANIFESTS = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


class LockError(SystemExit):
    """A lock problem the caller can fix. A string, so the process exits 1 with the text."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_base() -> str:
    return os.environ.get("LIVINGPROOF_GIT_BASE", "https://github.com")


def git(*args: str, cwd: Path | None = None) -> str:
    try:
        done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise LockError("git is required to lock a definition") from None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        tail = f": {detail[-1]}" if detail else ""
        shown = " ".join(args[:4])
        raise LockError(f"git {shown} failed (exit {exc.returncode}){tail}") from None
    return done.stdout.strip()


def checkout(repository: str, ref: str, dest: Path) -> str:
    """Fetch only ``ref`` of ``repository`` into ``dest`` and return the commit."""
    if not (dest / ".git").exists():
        dest.mkdir(parents=True, exist_ok=True)
        git("init", "-q", cwd=dest)
    git("fetch", "-q", "--depth", "1", f"{git_base()}/{repository}.git", ref, cwd=dest)
    git("checkout", "-q", "--detach", "FETCH_HEAD", cwd=dest)
    return git("rev-parse", "HEAD", cwd=dest)


def resolve_tag(repository: str, tag: str) -> str:
    """The commit ``tag`` points at. A missing tag is an error, never a guessed commit."""
    if not TAG.fullmatch(tag):
        raise LockError(f"{repository} tag {tag!r} is not a vX.Y.Z release")
    url = f"{git_base()}/{repository}.git"
    try:
        done = subprocess.run(
            ["git", "ls-remote", "--tags", url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise LockError("git is required to lock a definition") from None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        tail = f": {detail[-1]}" if detail else ""
        raise LockError(
            f"cannot resolve {repository} {tag} (exit {exc.returncode}){tail}"
        ) from None
    refs = {}
    for line in done.stdout.splitlines():
        commit, _, name = line.partition("\t")
        if name:
            refs[name] = commit
    commit = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")
    if not commit or not SHA.fullmatch(commit):
        raise LockError(
            f"{repository} has no tag {tag}; release it first (tags are never invented)"
        )
    return commit


def image_digest(image: str, tag: str) -> str:
    """Manifest digest of a public ghcr.io image. ``tag`` has no leading v."""
    host, _, name = image.partition("/")
    if host != "ghcr.io":
        raise LockError(f"{image}: only ghcr.io images are supported")
    try:
        with urllib.request.urlopen(
            f"https://ghcr.io/token?scope=repository:{name}:pull", timeout=60
        ) as response:
            token = json.load(response)["token"]
        request = urllib.request.Request(
            f"https://ghcr.io/v2/{name}/manifests/{tag}",
            method="HEAD",
            headers={"Authorization": f"Bearer {token}", "Accept": MANIFESTS},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            digest = response.headers.get("Docker-Content-Digest", "")
    except urllib.error.HTTPError as exc:
        raise LockError(f"{image}:{tag} is not published ({exc.code}); release it first") from None
    except OSError as exc:
        raise LockError(f"{image}:{tag}: cannot read the registry digest ({exc})") from None
    if not DIGEST.fullmatch(digest):
        raise LockError(f"{image}:{tag}: registry returned no sha256 digest")
    return digest


def tag_of_commit(repository: str, commit: str) -> str:
    """The one ``vX.Y.Z`` tag that peels to ``commit``. Several tags, or none, is an error."""
    if not SHA.fullmatch(commit):
        raise LockError(f"{repository} commit {commit!r} is not a full sha")
    url = f"{git_base()}/{repository}.git"
    try:
        done = subprocess.run(
            ["git", "ls-remote", "--tags", url],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise LockError("git is required to lock a definition") from None
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        tail = f": {detail[-1]}" if detail else ""
        raise LockError(f"cannot list tags of {repository} (exit {exc.returncode}){tail}") from None
    lightweight: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for line in done.stdout.splitlines():
        sha, _, name = line.partition("\t")
        if name.endswith("^{}"):
            peeled[name.removeprefix("refs/tags/")[:-3]] = sha
        elif name.startswith("refs/tags/"):
            lightweight[name.removeprefix("refs/tags/")] = sha
    matches = [
        tag
        for tag in set(lightweight) | set(peeled)
        if TAG.fullmatch(tag) and (peeled.get(tag) or lightweight.get(tag)) == commit
    ]
    if len(matches) != 1:
        found = ", ".join(sorted(matches)) or "none"
        raise LockError(
            f"{repository} commit {commit[:12]} has {found} vX.Y.Z tags; the lock needs exactly one"
        )
    return matches[0]


def _workflow_path(definition: Path) -> Path | None:
    parents = definition.resolve().parents
    if len(parents) < 2:
        return None
    path = parents[1] / ".github" / "workflows" / "verify.yml"
    return path if path.is_file() else None


def _job_uses(text: str, job: str) -> str | None:
    """The ``uses`` value of ``job``. A commented-out line does not count."""
    prefix = f"  {job}:"
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.split("#", 1)[0].rstrip() == prefix:
            start = index + 1
            break
    if start is None:
        return None
    for line in lines[start:]:
        code = line.split("#", 1)[0].rstrip()
        if not code.strip():
            continue
        if code.startswith("  ") and not code.startswith("   ") and code.endswith(":"):
            return None
        stripped = code.strip()
        if stripped.startswith("uses:"):
            return stripped.split(":", 1)[1].strip()
    return None


def called_verifier(
    workflow: Path,
    definition_id: str,
    *,
    tag_of=None,
    digester=None,
) -> dict | None:
    """The walk-hallway release ``definition_id``'s job calls, or None when it does not."""
    uses = _job_uses(workflow.read_text(), definition_id)
    if not isinstance(uses, str) or "@" not in uses:
        return None
    target, _, commit = uses.partition("@")
    if not target.lower().endswith("/" + PROCEDURE.lower()) or not SHA.fullmatch(commit):
        return None
    repository = "/".join(target.split("/")[:2])
    if not REPO.fullmatch(repository):
        raise LockError(f"{workflow}: job {definition_id} uses {uses!r}")
    tag = (tag_of or tag_of_commit)(repository, commit)
    digest = (digester or image_digest)(VERIFIER_IMAGE, tag.removeprefix("v"))
    return {
        "repository": repository,
        "tag": tag,
        "commit": commit,
        "image": VERIFIER_IMAGE,
        "digest": digest,
    }


def settings_path(definition: Path) -> Path | None:
    """``settings.toml`` beside the definition, when this repository names the reporter."""
    path = definition.parent / "settings.toml"
    return path if path.is_file() else None


def load_reporter_setting(path: Path) -> dict:
    """Repository, tag and image for the reporter. The commit and digest are resolved later."""
    try:
        document = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise LockError(f"{path}: not valid TOML ({exc})") from None
    reporter = document.get("reporter") if isinstance(document, dict) else None
    if not isinstance(reporter, dict):
        raise LockError(f"{path}: reporter must be a table with repository, tag and image")
    repository, tag, image = reporter.get("repository"), reporter.get("tag"), reporter.get("image")
    problems = []
    if not isinstance(repository, str) or not REPO.fullmatch(repository):
        problems.append("reporter.repository must be OWNER/NAME")
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        problems.append(f"reporter.tag must be a vX.Y.Z release (got {tag!r})")
    if not isinstance(image, str) or not image.startswith("ghcr.io/"):
        problems.append("reporter.image must be a ghcr.io image")
    if problems:
        raise LockError(f"{path}: " + "; ".join(problems))
    return {"repository": repository, "tag": tag, "image": image}


def reporter_pin(setting: dict, resolve, digester) -> dict:
    """The reporter release: the setting's tag, the commit it resolves to, and its digest."""
    tag = setting["tag"]
    return {
        "repository": setting["repository"],
        "tag": tag,
        "commit": resolve(setting["repository"], tag),
        "image": setting["image"],
        "digest": digester(setting["image"], tag.removeprefix("v")),
    }


def lock_path(definition: Path) -> Path:
    return definition.with_name(definition.stem + ".lock.json")


def load_definition(path: Path) -> dict:
    if not path.is_file():
        raise LockError(f"{path}: no such definition")
    try:
        document = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise LockError(f"{path}: not valid TOML ({exc})") from None
    problems = []
    if document.get("format") != DEFINITION_FORMAT:
        problems.append(f"format must be {DEFINITION_FORMAT!r}")
    if document.get("procedure") != "walk-hallway":
        problems.append("procedure must be 'walk-hallway'")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(document.get("id", ""))):
        problems.append("id must be lower-case letters, digits and dashes")
    robot = document.get("robot", {})
    if not isinstance(robot, dict) or not REPO.fullmatch(str(robot.get("repository", ""))):
        problems.append("robot.repository must be OWNER/NAME")
    if not isinstance(robot, dict) or not robot.get("ref"):
        problems.append("robot.ref is required (a tag or a full commit)")
    configurations = robot.get("configurations") if isinstance(robot, dict) else None
    if not (
        isinstance(configurations, list)
        and configurations
        and all(isinstance(name, str) and name for name in configurations)
    ):
        problems.append("robot.configurations must be a non-empty list of names")
    if problems:
        raise LockError(f"{path}: " + "; ".join(problems))
    return document


def read_lock(path: Path) -> dict:
    """Load a ``0.1.0`` or ``0.2.0`` lock. Other formats are an error, not a traceback."""
    try:
        lock = json.loads(path.read_text())
    except OSError as exc:
        raise LockError(f"{path}: {exc.strerror}") from None
    except json.JSONDecodeError as exc:
        raise LockError(f"{path}: not valid JSON ({exc})") from None
    if not isinstance(lock, dict):
        raise LockError(f"{path}: must be a JSON object")
    if lock.get("format") not in (FORMAT_V1, FORMAT_V2):
        raise LockError(f"{path}: format is not {FORMAT_V1} or {FORMAT_V2}")
    return lock


def _relative(path: object) -> bool:
    if not isinstance(path, str) or not path:
        return False
    return not Path(path).is_absolute() and ".." not in Path(path).parts


def _pin_problems(label: str, ref: object, *, require_digest: bool = False) -> list[str]:
    """Tag, commit and digest problems. The same rules as capability-verifier ``repin``."""
    if not isinstance(ref, dict):
        return [f"{label} must be an object"]
    problems = []
    repository = ref.get("repository")
    if not isinstance(repository, str) or not REPO.fullmatch(repository):
        problems.append(f"{label}.repository must be OWNER/NAME")
    tag = ref.get("tag")
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        problems.append(f"{label}.tag must be a vX.Y.Z release tag (got {tag!r})")
    commit = ref.get("commit")
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        problems.append(f"{label}.commit must be a full commit sha")
    if require_digest or "image" in ref or "digest" in ref:
        digest = ref.get("digest")
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            problems.append(f"{label}.digest must be a sha256 digest")
    if require_digest and not isinstance(ref.get("image"), str):
        problems.append(f"{label}.image is required")
    return problems


def v2_problems(lock: dict) -> list[str]:
    """Why ``lock`` is not a complete ``0.2.0`` document. Empty when it is."""
    problems = []
    if lock.get("format") != FORMAT_V2:
        problems.append(f"format must be {FORMAT_V2}")
    definition = lock.get("definition")
    digest = definition.get("sha256") if isinstance(definition, dict) else None
    if not isinstance(digest, str) or not FILE_SHA.fullmatch(digest):
        problems.append("definition.sha256 must be 64 hex characters")
    if lock.get("procedure") != "walk-hallway":
        problems.append("procedure must be 'walk-hallway'")
    scenario = lock.get("scenario")
    if not isinstance(scenario, str) or not SCENARIO_ID.fullmatch(scenario):
        problems.append(f"scenario must be name@version (got {scenario!r})")
    robot = lock.get("robot") if isinstance(lock.get("robot"), dict) else {}
    if not isinstance(lock.get("robot"), dict):
        problems.append("robot must be an object")
    adapter = robot.get("adapter")
    if not isinstance(adapter, str) or not adapter:
        problems.append("robot.adapter must be the [tool.livingproof] adapter string")
    selected = robot.get("configurations")
    if (
        not isinstance(selected, list)
        or not selected
        or not all(isinstance(name, str) for name in selected)
    ):
        problems.append("robot.configurations must be a non-empty list of names")
        selected = []
    if not isinstance(robot.get("commit"), str) or not SHA.fullmatch(robot.get("commit", "")):
        problems.append("robot.commit must be a full commit sha")
    if not isinstance(robot.get("repository"), str) or not REPO.fullmatch(
        str(robot.get("repository", ""))
    ):
        problems.append("robot.repository must be OWNER/NAME")
    entries = lock.get("configurations")
    names: list[str] = []
    if not isinstance(entries, list) or not entries:
        problems.append("configurations must be a non-empty list")
        entries = []
    for index, entry in enumerate(entries):
        where = f"configurations[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{where} must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            problems.append(f"{where}.name must be a string")
        else:
            names.append(name)
        version = entry.get("version")
        if not isinstance(version, str) or not SEMVER.fullmatch(version):
            problems.append(f"{where}.version must be X.Y.Z")
        if not _relative(entry.get("path")):
            problems.append(f"{where}.path must stay inside the robot repository")
        file_sha = entry.get("sha256")
        if not isinstance(file_sha, str) or not FILE_SHA.fullmatch(file_sha):
            problems.append(f"{where}.sha256 must be 64 hex characters")
    if len(names) != len(set(names)):
        problems.append("configuration names must be unique")
    missing = [name for name in selected if name not in names]
    if missing:
        problems.append(f"robot.configurations {missing} are not in configurations")
    plan = lock.get("plan")
    speed = plan.get("command_speed") if isinstance(plan, dict) else None
    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        problems.append("plan.command_speed must be a number")
    world = lock.get("world")
    if not isinstance(world, dict):
        problems.append("world must be an object")
    else:
        for key in ("id", "version"):
            if not isinstance(world.get(key), str) or not world[key]:
                problems.append(f"world.{key} must be a string")
        declared = world.get("sha256")
        if declared is not None and (
            not isinstance(declared, str) or not FILE_SHA.fullmatch(declared)
        ):
            problems.append("world.sha256 must be 64 hex characters")
    releases = lock.get("releases") if isinstance(lock.get("releases"), dict) else {}
    if not isinstance(lock.get("releases"), dict):
        problems.append("releases must be an object")
    for name in ("specs", "runner", "verifier"):
        if name not in releases:
            problems.append(f"missing releases.{name}")
        else:
            problems.extend(_pin_problems(f"releases.{name}", releases[name]))
    if "reporter" not in releases:
        problems.append("missing releases.reporter")
    else:
        problems.extend(
            _pin_problems("releases.reporter", releases["reporter"], require_digest=True)
        )
    evaluators = lock.get("evaluators") if isinstance(lock.get("evaluators"), dict) else {}
    if not isinstance(lock.get("evaluators"), dict) or EVALUATOR not in evaluators:
        problems.append(f"missing evaluators.{EVALUATOR}")
    else:
        problems.extend(_pin_problems(f"evaluators.{EVALUATOR}", evaluators[EVALUATOR]))
    return problems


def _adapter(root: Path, where: str) -> str:
    path = root / "pyproject.toml"
    if not path.is_file():
        raise LockError(f"{where}: pyproject.toml is missing")
    try:
        adapter = tomllib.loads(path.read_text())["tool"]["livingproof"]["adapter"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError):
        raise LockError(
            f"{where}: pyproject.toml has no [tool.livingproof] adapter string"
        ) from None
    if not isinstance(adapter, str) or not adapter:
        raise LockError(f"{where}: [tool.livingproof] adapter must be a string")
    return adapter


def _profile(root: Path, where: str) -> list[dict]:
    path = root / "robot.json"
    if not path.is_file():
        raise LockError(f"{where}: robot.json is missing")
    try:
        listed = json.loads(path.read_text())["configurations"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise LockError(
            f"{where}: robot.json configurations must be a list of objects with name and path"
        ) from None
    if not isinstance(listed, list) or not listed:
        raise LockError(f"{where}: robot.json configurations must be a non-empty list")
    entries = []
    for index, item in enumerate(listed):
        if not isinstance(item, dict):
            raise LockError(f"{where}: robot.json configurations[{index}] must be an object")
        name, rel = item.get("name"), item.get("path")
        if not isinstance(name, str) or not name:
            raise LockError(f"{where}: robot.json configurations[{index}].name must be a string")
        if not _relative(rel):
            raise LockError(
                f"{where}: robot.json configurations[{index}].path must stay inside the robot"
            )
        entries.append({"name": name, "path": rel})
    names = [entry["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise LockError(f"{where}: robot.json configuration names must be unique")
    return entries


def _file_version(path: Path) -> str:
    if not path.is_file():
        raise LockError(f"{path}: configuration file is missing")
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise LockError(f"{path}: not valid JSON ({exc})") from None
    version = document.get("version") if isinstance(document, dict) else None
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise LockError(f"{path}: version must be X.Y.Z so the lock can record it")
    return version


def _configurations(root: Path, where: str, prior: list | None, *, published: bool) -> list[dict]:
    """Configuration records in robot.json order. ``published`` requires the prior sha and version."""
    by_name = {}
    if isinstance(prior, list):
        for entry in prior:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                by_name[entry["name"]] = entry
    built = []
    for item in _profile(root, where):
        rel = item["path"]
        file = root / rel
        source = by_name.get(item["name"])
        if source is not None and source.get("path") not in (None, rel):
            raise LockError(
                f"{item['name']}: robot.json path {rel} differs from the lock path {source.get('path')}"
            )
        if not file.is_file():
            raise LockError(f"{where}: {rel} is missing")
        digest = sha256(file.read_bytes())
        version = _file_version(file)
        if published and source is not None:
            if "sha256" in source and source["sha256"] != digest:
                raise LockError(
                    f"{where}: {rel} sha256 is {digest}, not the published {source['sha256']}"
                )
            if source.get("version") not in (None, version):
                raise LockError(
                    f"{where}: {rel} version is {version}, not the published {source.get('version')}"
                )
        entry = dict(source) if source is not None else {}
        entry.update(name=item["name"], version=version, path=rel, sha256=digest)
        built.append(entry)
    return built


def _declared_evaluator(pyproject: str) -> tuple[str, str] | None:
    match = EVALUATOR_DEP.search(pyproject)
    if match is None:
        return None
    return match.group("repository"), match.group("tag")


def _verifier_pyproject(repository: str, commit: str, work: Path) -> str:
    checkout(repository, commit, work)
    path = work / "pyproject.toml"
    if not path.is_file():
        raise LockError(f"{repository}@{commit[:12]} has no pyproject.toml")
    return path.read_text()


def _evaluator_pin(
    releases: dict,
    work: Path,
    *,
    resolve,
    verifier_pyproject: str | None,
) -> tuple[dict, str | None]:
    verifier = releases.get("verifier") if isinstance(releases, dict) else None
    if not isinstance(verifier, dict):
        raise LockError("releases.verifier must be an object")
    repository = verifier.get("repository")
    commit = verifier.get("commit")
    if not isinstance(repository, str) or not REPO.fullmatch(repository):
        raise LockError("releases.verifier.repository must be OWNER/NAME")
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise LockError("releases.verifier.commit must be a full commit sha")
    text = (
        verifier_pyproject
        if verifier_pyproject is not None
        else _verifier_pyproject(repository, commit, work / "verifier")
    )
    declared = _declared_evaluator(text)
    if declared is not None:
        dep_repository, tag = declared
        note = None
    else:
        dep_repository, tag = DEFAULT_EVALUATOR
        label = verifier.get("tag") if isinstance(verifier.get("tag"), str) else commit[:12]
        note = (
            f"evaluators.{EVALUATOR} recorded as {dep_repository} {tag}; "
            f"{repository} {label} does not declare it"
        )
    return (
        {"repository": dep_repository, "tag": tag, "commit": resolve(dep_repository, tag)},
        note,
    )


def _owned_from_robot(robot_lock: dict, where: str) -> dict:
    if not isinstance(robot_lock, dict):
        raise LockError(f"{where}: {ROBOT_LOCK} must be a JSON object")
    missing = [key for key in ("scenario", "releases", "world", "plan") if key not in robot_lock]
    if missing:
        raise LockError(f"{where}: {ROBOT_LOCK} is missing {', '.join(missing)}")
    return {
        "scenario": robot_lock["scenario"],
        "releases": robot_lock["releases"],
        "world": robot_lock["world"],
        "plan": robot_lock["plan"],
        "configurations": robot_lock.get("configurations"),
    }


def _owned_from_existing(existing: dict | None, definition: Path, where: str) -> dict:
    if isinstance(existing, dict):
        beside = f"the lock beside {definition.name} is {existing.get('format')}"
    else:
        beside = f"no lock beside {definition.name}"
    if not isinstance(existing, dict) or existing.get("format") != FORMAT_V2:
        raise LockError(
            f"{where} publishes no {ROBOT_LOCK}. A thin robot publishes its adapter and "
            "configurations only. releases, scenario, plan, world and evaluators have to "
            f"come from that file or from an existing {FORMAT_V2} ({beside})."
        )
    missing = [key for key in OWNED if key not in existing]
    if missing:
        raise LockError(
            f"{lock_path(definition).name} is missing {', '.join(missing)}. "
            "A thin robot does not publish them."
        )
    return {
        "scenario": existing["scenario"],
        "releases": existing["releases"],
        "world": existing["world"],
        "plan": existing["plan"],
        "evaluators": existing["evaluators"],
        "configurations": existing.get("configurations"),
    }


def generate(
    definition: Path,
    work: Path,
    *,
    resolve=None,
    verifier_pyproject: str | None = None,
    tag_of=None,
    digester=None,
) -> tuple[dict, list[str]]:
    """Resolve ``definition`` into a ``0.2.0`` lock and notes. Writes nothing."""
    if resolve is None:
        resolve = resolve_tag
    document = load_definition(definition)
    robot = document["robot"]
    root = work / "robot"
    commit = checkout(robot["repository"], str(robot["ref"]), root)
    where = f"{robot['repository']}@{commit[:12]}"
    robot_lock_path = root / ROBOT_LOCK
    existing_path = lock_path(definition)
    # Read a 0.1.0 or 0.2.0 beside the definition. A missing robot lock is the thin case.
    existing = read_lock(existing_path) if existing_path.is_file() else None
    notes: list[str] = []
    if robot_lock_path.is_file():
        try:
            robot_lock = json.loads(robot_lock_path.read_text())
        except json.JSONDecodeError as exc:
            raise LockError(f"{where}: {ROBOT_LOCK} is not valid JSON ({exc})") from None
        owned = _owned_from_robot(robot_lock, where)
        workflow = _workflow_path(definition)
        if workflow is not None:
            called = called_verifier(workflow, document["id"], tag_of=tag_of, digester=digester)
            if called is not None:
                releases = dict(owned["releases"])
                releases["verifier"] = called
                owned["releases"] = releases
        settings = settings_path(definition)
        if settings is not None:
            releases = dict(owned["releases"])
            releases["reporter"] = reporter_pin(
                load_reporter_setting(settings), resolve, digester or image_digest
            )
            owned["releases"] = releases
        evaluator, note = _evaluator_pin(
            owned["releases"],
            work,
            resolve=resolve,
            verifier_pyproject=verifier_pyproject,
        )
        if note:
            notes.append(note)
        evaluators = {EVALUATOR: evaluator}
        thin = False
    else:
        owned = _owned_from_existing(existing, definition, where)
        evaluators = owned["evaluators"]
        thin = True
    lock = {
        "format": FORMAT_V2,
        "definition": {"path": definition.name, "sha256": sha256(definition.read_bytes())},
        "procedure": document["procedure"],
        "scenario": owned["scenario"],
        "robot": {
            "repository": robot["repository"],
            "ref": robot["ref"],
            "commit": commit,
            "configurations": robot["configurations"],
            "adapter": _adapter(root, where),
        },
        "releases": owned["releases"],
        "world": owned["world"],
        "configurations": _configurations(
            root, where, owned.get("configurations"), published=not thin
        ),
        "plan": owned["plan"],
        "evaluators": evaluators,
    }
    problems = v2_problems(lock)
    if problems:
        raise LockError(f"{definition.name}: " + "; ".join(problems))
    if thin:
        notes.insert(
            0,
            "thin robot: kept releases, scenario, plan, world and evaluators from the existing lock",
        )
    return lock, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("definition", type=Path)
    parser.add_argument("--work", type=Path, default=Path("artifacts/.lock-work"))
    args = parser.parse_args(argv)
    lock, notes = generate(args.definition, args.work)
    path = lock_path(args.definition)
    path.write_text(json.dumps(lock, indent=2) + "\n")
    robot = lock["robot"]
    thin = any(note.startswith("thin robot:") for note in notes)
    if thin:
        print(
            f"wrote {path} for thin robot {robot['repository']}@{robot['commit'][:12]} "
            f"(no {ROBOT_LOCK}); kept releases, scenario, plan, world and evaluators "
            "from the existing lock. Review it before committing."
        )
    else:
        print(f"wrote {path}; Review it before committing.")
    for note in notes:
        if not note.startswith("thin robot:"):
            print(note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
