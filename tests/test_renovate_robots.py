# SPDX-License-Identifier: Apache-2.0
"""Robot proposals select eligible corners, preserving frozen hallways and locks."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
ROBOTS = (
    "Living-Proof-Labs/livingproof-booster-t1",
    "Living-Proof-Labs/livingproof-unitree-g1",
    "Living-Proof-Labs/livingproof-unitree-go1",
)


def _config():
    return json.loads((ROOT / "renovate.json").read_text())


def _definitions():
    return sorted(
        path for path in (ROOT / "verifications").glob("*.toml") if path.name != "settings.toml"
    )


def _manager():
    config = _config()
    (manager,) = [
        item
        for item in config["customManagers"]
        if any(
            "verifications" in pattern and "settings" not in pattern
            for pattern in item["managerFilePatterns"]
        )
    ]
    return config, manager


def _pattern(manager):
    """Renovate's ``(?<name>`` groups are ``(?P<name>`` in Python."""
    (source,) = manager["matchStrings"]
    return re.compile(source.replace("(?<", "(?P<"))


def _next_minor(tag):
    major, minor, _patch = (int(part) for part in tag.removeprefix("v").split("."))
    return f"v{major}.{minor + 1}.0"


def test_robot_manager_is_targeted_and_automerge_stays_off():
    renovate, manager = _manager()
    assert renovate["automerge"] is False
    assert renovate["platformAutomerge"] is False
    assert "postUpgradeTasks" not in renovate
    assert "custom.regex" in renovate["enabledManagers"]
    assert manager["customType"] == "regex"
    assert manager["datasourceTemplate"] == "github-releases"
    assert manager["versioningTemplate"] == "semver"
    assert all("lock" not in pattern for pattern in manager["managerFilePatterns"])
    (rule,) = [
        item
        for item in renovate["packageRules"]
        if set(item.get("matchPackageNames", [])) == set(ROBOTS)
    ]
    assert rule["automerge"] is False
    assert rule["matchManagers"] == ["custom.regex"]
    assert rule["matchFileNames"] == ["verifications/*.toml"]
    assert rule["allowedVersions"] == r"/^v[0-9]+\.[0-9]+\.[0-9]+$/"
    assert "python3 tools/lock.py" in rule["prBodyNotes"][0]
    assert ".github/workflows/verify.yml" in rule["prBodyNotes"][0]
    assert "Never hand-edit" in rule["prBodyNotes"][0]
    assert "Completion owner: Keel" in rule["prBodyNotes"][0]
    assert "docs/robot-updates.md" in rule["prBodyNotes"][0]
    (blocked,) = [item for item in renovate["packageRules"] if item.get("enabled") is False]
    assert blocked["matchFileNames"] == ["verifications/*.lock.json"]
    assert renovate["excludeCommitPaths"] == ["verifications/*.lock.json"]


def test_the_raw_pattern_reads_robot_blocks_independently_of_file_eligibility():
    _config, manager = _manager()
    pattern = _pattern(manager)
    definitions = _definitions()
    assert len(definitions) == 10
    seen_repos = set()
    for path in definitions:
        text = path.read_text()
        robot = tomllib.loads(text)["robot"]
        found = list(pattern.finditer(text))
        assert len(found) == 1, path.name
        assert found[0]["depName"] == robot["repository"], path.name
        assert found[0]["currentValue"] == robot["ref"], path.name
        seen_repos.add(robot["repository"])
    assert seen_repos == set(ROBOTS)


def test_the_file_patterns_select_only_eligible_corner_definitions():
    _config, manager = _manager()
    regexes = [re.compile(pattern.strip("/")) for pattern in manager["managerFilePatterns"]]
    files = [
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.parts and "node_modules" not in path.parts
    ]
    matched = sorted(name for name in files if any(regex.search(name) for regex in regexes))
    expected = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "verifications").glob("*-corner-*.toml")
    )
    assert len(expected) == 6
    assert matched == expected
    frozen = list((ROOT / "verifications").glob("*-hallway.toml"))
    assert len(frozen) == 4
    for path in frozen:
        assert not any(regex.search(path.relative_to(ROOT).as_posix()) for regex in regexes)
    assert not any(regex.search("verifications/new-robot-corner-left.toml") for regex in regexes)


def test_the_match_string_sees_no_pin_in_settings_locks_or_other_files():
    _config, manager = _manager()
    pattern = _pattern(manager)
    definitions = set(_definitions())
    skip = {".git", "node_modules", ".pytest_cache", ".ruff_cache", "__pycache__", "artifacts"}
    fixtures_dir = ROOT / "tests" / "fixtures"
    for path in ROOT.rglob("*"):
        if (
            not path.is_file()
            or skip & set(path.parts)
            or path in definitions
            or fixtures_dir in path.parents
        ):
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        assert not pattern.search(text), path.relative_to(ROOT)


def test_a_simulated_bump_changes_only_the_robot_ref_line():
    _config, manager = _manager()
    pattern = _pattern(manager)
    for path in _definitions():
        if path.stem.endswith("-hallway"):
            continue
        before = path.read_text()
        robot = tomllib.loads(before)["robot"]
        current = robot["ref"]
        proposed = _next_minor(current)

        def replace(match, cur=current, prop=proposed):
            return match[0].replace(f'"{cur}"', f'"{prop}"')

        after = pattern.sub(replace, before)
        changed = [
            (left, right)
            for left, right in zip(before.splitlines(), after.splitlines(), strict=True)
            if left != right
        ]
        assert changed == [(f'ref = "{current}"', f'ref = "{proposed}"')], path.name
        assert tomllib.loads(after)["robot"]["ref"] == proposed
        assert [match["currentValue"] for match in pattern.finditer(after)] == [proposed]


def test_every_definition_has_an_explicit_documented_policy():
    inventory = (ROOT / "docs/robot-updates.md").read_text()
    for path in _definitions():
        row = next(line for line in inventory.splitlines() if f"`{path.name}`" in line)
        policy = "Frozen" if path.stem.endswith("-hallway") else "Eligible proposal"
        assert row.endswith(f"| {policy} |")
