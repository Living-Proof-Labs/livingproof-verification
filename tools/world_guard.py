#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail a run whose world declares walls but whose evidence never measured them,
or whose base starts at the origin when that world's named start place does not.

The capability verifier grades whatever the simulator recorded. A scene that
silently omitted the world still produces trials, and an empty ``wall_contacts``
list is also what a real run records when the robot never touches a wall
(G1 playground and Go1 playground on the current pins). Those two cases are
different fields:

- ``wall_intrusion.walls`` is the wall geoms whose faces were measured
  (``side``, ``inner``, ``outer``, and ``axis`` on a corner). The simulator
  writes one entry per geom it was given. An empty object means it was given
  none: no wall geoms, not "never touched."
- ``wall_contacts`` is only the contact intervals. It is empty in both cases,
  so it cannot make this decision.

The world the evidence lock names (``lock.content.world``, checked by sha256)
declares the geoms (``geometry`` role ``wall``) and the start place
(``places`` type ``pose``, ``position``). ``manifest.start.spawn`` is that
place's name. The start position is the first trace sample's ``x`` and ``y``.
``environment.walls`` is the geom list the run itself recorded. A world with
no wall role is not this check. Exit status is 1 on a violation or on evidence
that does not carry these fields. Nothing here warns and continues.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

# Same threshold livingproof-sim-mujoco compose.require_composed uses for a base
# or a place at the world origin. Recorded samples are already rounded to 4 decimals.
AT_ORIGIN = 1e-6
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class WorldGuardError(Exception):
    """The evidence or the world it names cannot be checked. This fails the run."""


def world_url(pin: dict) -> str:
    """Raw world.json for the lock pin. Rejects anything that is not a path segment."""
    repository = pin.get("repository")
    commit = pin.get("commit")
    world_id = pin.get("id")
    version = pin.get("version")
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise WorldGuardError(f"world repository {repository!r} is not owner/name")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise WorldGuardError(f"world commit {commit!r} is not a 40-hex sha")
    if not isinstance(world_id, str) or not _SEGMENT.fullmatch(world_id):
        raise WorldGuardError(f"world id {world_id!r} is not a path segment")
    if not isinstance(version, str) or not _SEGMENT.fullmatch(version):
        raise WorldGuardError(f"world version {version!r} is not a path segment")
    return (
        f"https://raw.githubusercontent.com/{repository}/{commit}/"
        f"worlds/{world_id}/{version}/world.json"
    )


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "livingproof-verification-world-guard"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read()
    if not isinstance(body, bytes) or not body:
        raise WorldGuardError(f"empty response from {url}")
    return body


def load_world(pin: dict, fetch: Callable[[str], bytes]) -> dict:
    """The world document, or an error when its sha256 is not the lock's."""
    expected = pin.get("sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise WorldGuardError("evidence lock world.sha256 is missing")
    url = world_url(pin)
    try:
        body = fetch(url)
    except WorldGuardError:
        raise
    except Exception as exc:
        raise WorldGuardError(f"could not read {url}: {exc}") from exc
    digest = hashlib.sha256(body).hexdigest()
    if digest != expected:
        raise WorldGuardError(
            f"world.json sha256 {digest} does not match the evidence lock {expected}"
        )
    try:
        world = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WorldGuardError(f"world.json is not JSON: {exc}") from exc
    if not isinstance(world, dict):
        raise WorldGuardError("world.json is not an object")
    return world


def _pin(manifest: dict) -> dict:
    lock = manifest.get("lock")
    content = lock.get("content") if isinstance(lock, dict) else None
    world = content.get("world") if isinstance(content, dict) else None
    if not isinstance(world, dict):
        raise WorldGuardError("evidence manifest has no lock.content.world")
    return world


def _declared_walls(world: dict) -> list[str]:
    geometry = world.get("geometry")
    if not isinstance(geometry, list):
        raise WorldGuardError("world.json has no geometry list")
    names = []
    for geom in geometry:
        if not isinstance(geom, dict) or geom.get("role") != "wall":
            continue
        name = geom.get("name")
        if not isinstance(name, str) or not name:
            raise WorldGuardError("a world wall geom has no name")
        names.append(name)
    return names


def _number(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorldGuardError(f"{where} is not a number")
    return float(value)


def _at_origin(x: float, y: float) -> bool:
    return abs(x) <= AT_ORIGIN and abs(y) <= AT_ORIGIN


def _place(world: dict, spawn: str) -> tuple[float, float]:
    places = world.get("places")
    if not isinstance(places, list):
        raise WorldGuardError("world.json has no places list")
    for place in places:
        if not isinstance(place, dict) or place.get("name") != spawn:
            continue
        if place.get("type") != "pose" or "position" not in place:
            raise WorldGuardError(f"start place {spawn!r} is not a pose with a position")
        position = place["position"]
        if not isinstance(position, list) or len(position) < 2:
            raise WorldGuardError(f"start place {spawn!r} position is not x, y")
        return _number(position[0], f"{spawn} x"), _number(position[1], f"{spawn} y")
    raise WorldGuardError(f"start place {spawn!r} is not in the world")


def _start_xy(trial: dict, trial_name: str) -> tuple[float, float]:
    trace = trial.get("trace")
    if not isinstance(trace, dict):
        raise WorldGuardError(f"{trial_name} has no trace")
    columns = trace.get("columns")
    samples = trace.get("samples")
    if not isinstance(columns, list) or not isinstance(samples, list) or not samples:
        raise WorldGuardError(f"{trial_name} trace has no samples")
    try:
        xi, yi = columns.index("x"), columns.index("y")
    except ValueError as exc:
        raise WorldGuardError(f"{trial_name} trace columns have no x, y") from exc
    sample = samples[0]
    if not isinstance(sample, list) or max(xi, yi) >= len(sample):
        raise WorldGuardError(f"{trial_name} first trace sample has no x, y")
    return (
        _number(sample[xi], f"{trial_name} start x"),
        _number(sample[yi], f"{trial_name} start y"),
    )


def _trials(evidence: Path, manifest: dict) -> list[tuple[str, dict]]:
    block = manifest.get("trials")
    results = block.get("results") if isinstance(block, dict) else None
    if not isinstance(results, list):
        raise WorldGuardError("evidence manifest has no trials.results")
    found = []
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("path"), str):
            raise WorldGuardError("a trial result has no path")
        rel = result["path"]
        path = evidence / rel
        if not path.is_file():
            raise WorldGuardError(f"missing {rel}")
        try:
            trial = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise WorldGuardError(f"{rel} is not JSON: {exc}") from exc
        if not isinstance(trial, dict):
            raise WorldGuardError(f"{rel} is not an object")
        found.append((rel, trial))
    return found


def _unmet_without_trials(manifest: dict, trials: list) -> bool:
    preconditions = manifest.get("preconditions")
    if not isinstance(preconditions, list):
        raise WorldGuardError("evidence manifest has no preconditions list")
    unmet = False
    for item in preconditions:
        if not isinstance(item, dict) or not isinstance(item.get("met"), bool):
            raise WorldGuardError("a precondition has no boolean met")
        unmet = unmet or item["met"] is False
    return unmet and not trials


def _measured_walls(trial: dict, trial_name: str) -> dict:
    intrusion = trial.get("wall_intrusion")
    if not isinstance(intrusion, dict) or not isinstance(intrusion.get("walls"), dict):
        raise WorldGuardError(f"{trial_name} has no wall_intrusion.walls object")
    walls = intrusion["walls"]
    for name, face in walls.items():
        if not isinstance(name, str) or not isinstance(face, dict) or not face:
            raise WorldGuardError(f"{trial_name} wall_intrusion.walls[{name!r}] was not measured")
    return walls


def violations(evidence: Path, world: dict) -> list[str]:
    """Violations for one evidence directory. Empty when the run may stand."""
    manifest_path = evidence / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise WorldGuardError(f"{manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise WorldGuardError(f"{manifest_path} is not an object")
    label = evidence.name
    pin = _pin(manifest)
    trials = _trials(evidence, manifest)
    if _unmet_without_trials(manifest, trials):
        return []
    problems = []
    declared = _declared_walls(world)
    if declared:
        environment = manifest.get("environment")
        recorded = environment.get("walls") if isinstance(environment, dict) else None
        names_ok = (
            isinstance(recorded, list)
            and all(isinstance(name, str) for name in recorded)
            and set(recorded) == set(declared)
        )
        if not names_ok:
            problems.append(
                f"{label}: world {pin.get('id')} {pin.get('version')} declares walls "
                f"{', '.join(declared)} but environment.walls is {recorded!r}"
            )
        if not trials:
            problems.append(
                f"{label}: world {pin.get('id')} {pin.get('version')} declares walls "
                f"{', '.join(declared)} but no trials were recorded"
            )
        for rel, trial in trials:
            measured = _measured_walls(trial, rel)
            if set(measured) != set(declared):
                problems.append(
                    f"{label}: world {pin.get('id')} {pin.get('version')} declares walls "
                    f"{', '.join(declared)} but {rel} wall_intrusion.walls is "
                    f"{', '.join(measured) or 'empty'}"
                )
    start = manifest.get("start")
    spawn = start.get("spawn") if isinstance(start, dict) else None
    if not isinstance(spawn, str) or not spawn:
        raise WorldGuardError(f"{label}: manifest start.spawn is missing")
    place_x, place_y = _place(world, spawn)
    if _at_origin(place_x, place_y):
        return problems
    for rel, trial in trials:
        x, y = _start_xy(trial, rel)
        if _at_origin(x, y):
            problems.append(
                f"{label}: start place {spawn} is at ({place_x}, {place_y}), not the origin, "
                f"but {rel} starts at ({x}, {y})"
            )
    return problems


def _evidence_directories(roots: list[Path]) -> list[Path]:
    found = []
    for root in roots:
        if (root / "manifest.json").is_file():
            found.append(root)
            continue
        found.extend(sorted(path.parent for path in root.rglob("manifest.json") if path.is_file()))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths", nargs="+", type=Path, help="evidence directories, or a parent of them"
    )
    parser.add_argument(
        "--world",
        type=Path,
        help="use this world.json instead of downloading it. Its sha256 must match the lock",
    )
    args = parser.parse_args(argv)
    directories = _evidence_directories(args.paths)
    if not directories:
        print("no evidence manifest.json found", file=sys.stderr)
        return 1
    override = args.world.read_bytes() if args.world else None
    problems = []
    cache: dict[str, dict] = {}
    for evidence in directories:
        try:
            manifest = json.loads((evidence / "manifest.json").read_text())
            pin = _pin(manifest)
            digest = pin.get("sha256")
            if override is not None:
                world = load_world(pin, lambda _url, body=override: body)
            elif isinstance(digest, str) and digest in cache:
                world = cache[digest]
            else:
                world = load_world(pin, fetch_bytes)
                if isinstance(digest, str):
                    cache[digest] = world
            problems.extend(violations(evidence, world))
        except WorldGuardError as exc:
            problems.append(f"{evidence.name}: {exc}")
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print(f"checked {len(directories)} evidence run(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
