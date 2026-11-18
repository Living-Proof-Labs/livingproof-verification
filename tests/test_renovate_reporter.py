# SPDX-License-Identifier: Apache-2.0
"""The reporter Renovate pin stays reviewed, and every lock stays generator output."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
REPORTER = "Living-Proof-Labs/livingproof-capability-report"
DEFINITIONS = (
    "verifications/booster-t1-stock-mujoco-corner-left.toml",
    "verifications/booster-t1-stock-mujoco-corner-right.toml",
    "verifications/booster-t1-stock-mujoco-hallway.toml",
    "verifications/unitree-g1-stock-mujoco-corner-left.toml",
    "verifications/unitree-g1-stock-mujoco-corner-right.toml",
    "verifications/unitree-g1-playground-mujoco-hallway.toml",
    "verifications/unitree-g1-stock-mujoco-hallway.toml",
    "verifications/unitree-go1-playground-mujoco-corner-left.toml",
    "verifications/unitree-go1-playground-mujoco-corner-right.toml",
    "verifications/unitree-go1-playground-mujoco-hallway.toml",
)


def _config():
    return json.loads((ROOT / "renovate.json").read_text())


def test_reporter_manager_is_targeted_and_automerge_stays_off():
    renovate = _config()
    # One organization preset, pinned to a full release tag. Renovate moves the
    # tag, so the test checks the shape rather than a version.
    assert len(renovate["extends"]) == 1
    assert re.fullmatch(
        r"github>Living-Proof-Labs/collaboration-guidelines:renovate-preset#v\d+\.\d+\.\d+",
        renovate["extends"][0],
    )
    assert renovate["automerge"] is False
    assert renovate["platformAutomerge"] is False
    assert "ignoreUnstable" not in renovate
    assert "rangeStrategy" not in renovate
    assert "schedule" not in renovate
    assert "postUpgradeTasks" not in renovate
    assert "ignorePaths" not in renovate
    assert "custom.regex" in renovate["enabledManagers"]
    (manager,) = [
        item
        for item in renovate["customManagers"]
        if any(
            "settings" in pattern and "toml" in pattern for pattern in item["managerFilePatterns"]
        )
    ]
    assert manager["customType"] == "regex"
    assert manager["datasourceTemplate"] == "github-releases"
    assert manager["versioningTemplate"] == "semver"
    assert REPORTER in manager["matchStrings"][0]
    assert "v[0-9]+" in manager["matchStrings"][0]
    assert all("lock" not in pattern for pattern in manager["managerFilePatterns"])
    (rule,) = [
        item for item in renovate["packageRules"] if item.get("matchPackageNames") == [REPORTER]
    ]
    assert rule["automerge"] is False
    assert rule["matchManagers"] == ["custom.regex"]
    assert rule["matchFileNames"] == ["verifications/settings.toml"]
    assert "schedule" not in rule
    assert "prHourlyLimit" not in rule
    assert "prConcurrentLimit" not in rule
    assert rule["allowedVersions"] == r"/^v[0-9]+\.[0-9]+\.[0-9]+$/"
    note = rule["prBodyNotes"][0]
    assert "python3 tools/lock.py verifications/<definition>.toml" in note
    assert "Never hand-edit" in note
    assert "python3 tools/lock.py --update" in note
    (blocked,) = [item for item in renovate["packageRules"] if item.get("enabled") is False]
    assert blocked["matchFileNames"] == ["verifications/*.lock.json"]
    assert renovate["excludeCommitPaths"] == ["verifications/*.lock.json"]


def test_reporter_completion_is_documented_for_every_lock():
    readme = (ROOT / "README.md").read_text()
    assert "Never hand-edit" in readme
    assert "python3 tools/lock.py --update" in readme
    section = readme.split("## Updating the reporter", 1)[1].split("\n## ", 1)[0]
    for definition in DEFINITIONS:
        assert f"python3 tools/lock.py {definition}" in section
        lock = ROOT / definition.replace(".toml", ".lock.json")
        assert lock.is_file()


def _manager():
    config = _config()
    (manager,) = [
        item
        for item in config["customManagers"]
        if any("settings" in pattern for pattern in item["managerFilePatterns"])
    ]
    return config, manager


def _pattern(manager):
    """Renovate's ``(?<name>`` groups are ``(?P<name>`` in Python."""
    (source,) = manager["matchStrings"]
    return re.compile(source.replace("(?<", "(?P<"))


def _current():
    return tomllib.loads((ROOT / "verifications" / "settings.toml").read_text())["reporter"]["tag"]


def _next_minor(tag):
    major, minor, _patch = (int(part) for part in tag.removeprefix("v").split("."))
    return f"v{major}.{minor + 1}.0"


def test_the_match_string_finds_exactly_the_settings_reporter_tag():
    _config, manager = _manager()
    settings = ROOT / "verifications" / "settings.toml"
    found = list(_pattern(manager).finditer(settings.read_text()))
    assert len(found) == 1
    assert found[0]["depName"] == REPORTER
    assert found[0]["currentValue"] == _current()


def test_every_lock_names_the_settings_reporter_tag():
    locks = sorted((ROOT / "verifications").glob("*.lock.json"))
    definitions = [
        path for path in (ROOT / "verifications").glob("*.toml") if path.name != "settings.toml"
    ]
    assert len(locks) == len(definitions) == 10
    for path in locks:
        reporter = json.loads(path.read_text())["releases"]["reporter"]
        assert (reporter["repository"], reporter["tag"]) == (REPORTER, _current()), path.name


def test_the_match_string_sees_no_pin_in_any_other_tracked_text_file():
    _config, manager = _manager()
    pattern = _pattern(manager)
    settings = ROOT / "verifications" / "settings.toml"
    skip = {".git", "node_modules", ".pytest_cache", ".ruff_cache", "__pycache__", "artifacts"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or skip & set(path.parts) or path == settings:
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        assert not pattern.search(text), path.relative_to(ROOT)


def test_the_file_pattern_matches_only_settings_toml():
    _config, manager = _manager()
    (pattern,) = manager["managerFilePatterns"]
    regex = re.compile(pattern.strip("/"))
    files = [
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.parts and "node_modules" not in path.parts
    ]
    assert [name for name in files if regex.search(name)] == ["verifications/settings.toml"]


def test_a_simulated_bump_changes_only_the_tag_line():
    _config, manager = _manager()
    pattern = _pattern(manager)
    before = (ROOT / "verifications" / "settings.toml").read_text()
    current, proposed = _current(), _next_minor(_current())

    def replace(match):
        return match[0].replace(f'"{current}"', f'"{proposed}"')

    after = pattern.sub(replace, before)
    changed = [
        (left, right)
        for left, right in zip(before.splitlines(), after.splitlines(), strict=True)
        if left != right
    ]
    assert changed == [(f'tag = "{current}"', f'tag = "{proposed}"')]
    assert tomllib.loads(after)["reporter"]["tag"] == proposed
    assert [match["currentValue"] for match in pattern.finditer(after)] == [proposed]


def test_allowed_versions_take_only_release_tags():
    config, _found = _manager()
    (rule,) = [
        item for item in config["packageRules"] if item.get("matchPackageNames") == [REPORTER]
    ]
    allowed = re.compile(rule["allowedVersions"].strip("/"))
    assert allowed.fullmatch(_current())
    assert allowed.fullmatch(_next_minor(_current()))
    for tag in ("v3.9.0-rc.1", "3.9.0", "v3.9", "v3.9.0+build", "latest"):
        assert not allowed.fullmatch(tag), tag


def test_the_reporter_pin_has_no_digest_for_renovate_to_edit():
    _config, manager = _manager()
    assert "currentDigest" not in manager["matchStrings"][0]
    assert (
        "digest"
        not in tomllib.loads((ROOT / "verifications" / "settings.toml").read_text())["reporter"]
    )
    assert "autoReplaceStringTemplate" not in manager


def test_the_ci_node_satisfies_run_sh():
    workflow = (ROOT / ".github" / "workflows" / "checks.yml").read_text()
    script = (ROOT / "tools" / "renovate" / "run.sh").read_text()
    node = re.search(r"NODE_VERSION: (\d+)\.(\d+)\.(\d+)", workflow)
    floor = re.search(
        r"node_major < (\d+) \|\| \(node_major == \d+ && node_minor < (\d+)\)", script
    )
    assert node and floor
    assert (int(node[1]), int(node[2])) >= (int(floor[1]), int(floor[2]))
    assert re.search(r"NODE_SHA256: [0-9a-f]{64}$", workflow, re.M)
