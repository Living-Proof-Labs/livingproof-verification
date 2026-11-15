# SPDX-License-Identifier: Apache-2.0
"""The reporter Renovate pin stays reviewed, and the four locks stay generator output."""

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
REPORTER = "Living-Proof-Labs/livingproof-capability-report"
DEFINITIONS = (
    "verifications/booster-t1-stock-mujoco-hallway.toml",
    "verifications/unitree-g1-playground-mujoco-hallway.toml",
    "verifications/unitree-g1-stock-mujoco-hallway.toml",
    "verifications/unitree-go1-playground-mujoco-hallway.toml",
)


def _config():
    return json.loads((ROOT / "renovate.json").read_text())


def test_reporter_manager_is_targeted_and_automerge_stays_off():
    renovate = _config()
    assert renovate["extends"] == [
        "github>Living-Proof-Labs/collaboration-guidelines:renovate-preset#v7.10.8"
    ]
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


def test_reporter_completion_is_documented_for_all_four_locks():
    readme = (ROOT / "README.md").read_text()
    assert "Never hand-edit" in readme
    assert "python3 tools/lock.py --update" in readme
    for definition in DEFINITIONS:
        assert f"python3 tools/lock.py {definition}" in readme
        lock = ROOT / definition.replace(".toml", ".lock.json")
        assert lock.is_file()
