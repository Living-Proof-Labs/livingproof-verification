# SPDX-License-Identifier: Apache-2.0
"""tools/lock.py records per-file sha256 digests for already-committed specs pins (issue 100).

A matching digest is recorded. A tampered digest is refused. specs v2.0.2 has no
walk-hallway parameters.json, and the lock records that absence. Tags are not
changed and no release is republished.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location("lock_tool", ROOT / "tools/lock.py")
lock_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lock_tool)

HALLWAY_PARAMETERS = "specs/scenarios/walk-hallway/parameters.json"
HALLWAY_DESCRIPTION = "specs/scenarios/walk-hallway/description.json"
# Bytes of these two files are identical at specs v2.0.2 and v2.3.0.
V2_0_2_DESCRIPTION_SHA256 = "788bcd6345dad4d262ea14fc346d8883933b790f4991302f01d6eee84f52ff21"
V2_3_0_HALLWAY_PARAMETERS_SHA256 = (
    "9226aa6bc0e577863bdc210514e7e7c3a7edc32f49c4f7b46aa7249837f08b3d"
)
V2_3_0_CORNER_DESCRIPTION_SHA256 = (
    "be8de80e1a7b6f2273cee7abf7303d2e78a407ba0a5f11f9d06ed8cbed7c252c"
)
V2_3_0_CORNER_PARAMETERS_SHA256 = "62f2912c33b557d58cd454daf7d1eea543e919dec0e9b53e613d4101a5a12977"
HALLWAY_LOCKS = (
    "booster-t1-stock-mujoco-hallway",
    "unitree-g1-playground-mujoco-hallway",
    "unitree-g1-stock-mujoco-hallway",
    "unitree-go1-playground-mujoco-hallway",
)
CORNER_LOCKS = (
    "booster-t1-stock-mujoco-corner",
    "unitree-g1-stock-mujoco-corner",
    "unitree-go1-playground-mujoco-corner",
)
SPECS = "Living-Proof-Labs/robot-capability-specs"
DESCRIPTION = '{"format": "livingproof-scenario-description/0.1"}\n'
PARAMETERS = '{"format": "livingproof-scenario-parameters/0.1", "command_speed": 0.5}\n'


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(base: Path, name: str, files: dict[str, str], tag: str | None = None) -> str:
    path = base / name
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    _git(path, "config", "uploadpack.allowReachableSHA1InWant", "true")
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    if tag:
        _git(path, "tag", "-a", tag, "-m", tag)
    return _git(path, "rev-parse", "HEAD")


def _digest(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _specs_files(*, parameters: str | None, name: str = "walk-hallway") -> dict[str, str]:
    files = {f"specs/scenarios/{name}/description.json": DESCRIPTION}
    if parameters is not None:
        files[f"specs/scenarios/{name}/parameters.json"] = parameters
    return files


def _project(tmp_path: Path, monkeypatch, *, specs_files: dict[str, str], specs_tag: str):
    """A thin robot, an existing 0.2.0 lock, and a local specs pin at ``specs_tag``."""
    base = tmp_path / "git"
    specs = _repo(base, f"{SPECS}.git", specs_files, specs_tag)
    robot_files = {
        "pyproject.toml": (
            '[project]\nname = "fixture-robot"\nversion = "0.1.0"\n\n'
            '[tool.livingproof]\nadapter = "lpl_g1.adapter:ROBOT"\n'
        ),
        "robot.json": json.dumps(
            {
                "format": "livingproof-robot-profile/0.1",
                "configurations": [{"name": "stock", "path": "configurations/stock.json"}],
            }
        ),
        "configurations/stock.json": json.dumps(
            {"format": "livingproof-configuration-profile/0.1", "name": "stock", "version": "1.0.0"}
        ),
    }
    _repo(base, "Org/robot.git", robot_files)
    monkeypatch.setenv("LIVINGPROOF_GIT_BASE", f"file://{base}")
    definition = tmp_path / "proj" / "g1.toml"
    definition.parent.mkdir(parents=True)
    definition.write_text(
        f'format = "{lock_tool.DEFINITION_FORMAT}"\n'
        'id = "g1-stock"\n'
        'procedure = "walk-hallway"\n'
        "[robot]\n"
        'repository = "Org/robot"\n'
        'ref = "main"\n'
        'configurations = ["stock"]\n'
    )
    (definition.parent / "settings.toml").write_text(
        "[reporter]\n"
        'repository = "Living-Proof-Labs/livingproof-capability-report"\n'
        'tag = "v3.11.1"\n'
        'image = "ghcr.io/living-proof-labs/livingproof-capability-report"\n'
    )
    existing = {
        "format": lock_tool.FORMAT_V2,
        "definition": {"path": definition.name, "sha256": "0" * 64},
        "procedure": "walk-hallway",
        "scenario": "walk-hallway@0.1.0",
        "robot": {
            "repository": "Org/robot",
            "ref": "main",
            "commit": "1" * 40,
            "configurations": ["stock"],
            "adapter": "lpl_g1.adapter:ROBOT",
        },
        "releases": {
            "specs": {"repository": SPECS, "tag": specs_tag, "commit": specs},
            "runner": {
                "repository": "Living-Proof-Labs/livingproof-sim-mujoco",
                "tag": "v2.0.3",
                "commit": "b" * 40,
            },
            "verifier": {
                "repository": "Living-Proof-Labs/capability-verifier",
                "tag": "v1.25.0",
                "commit": "c" * 40,
                "image": "ghcr.io/living-proof-labs/capability-verifier-hallway",
                "digest": "sha256:" + "ab" * 32,
            },
            "reporter": {
                "repository": "Living-Proof-Labs/livingproof-capability-report",
                "tag": "v3.11.1",
                "commit": "d" * 40,
                "image": "ghcr.io/living-proof-labs/livingproof-capability-report",
                "digest": "sha256:" + "cd" * 32,
            },
        },
        "world": {"id": "hallway-open", "version": "0.1.0"},
        "configurations": [
            {
                "name": "stock",
                "version": "1.0.0",
                "path": "configurations/stock.json",
                "sha256": "e" * 64,
            }
        ],
        "plan": {"trials": {"count": 3}, "command_speed": 0.5},
        "evaluators": {
            "livingproof-evaluator-scenario": {
                "repository": "Living-Proof-Labs/livingproof-evaluator-scenario",
                "tag": "v1.3.0",
                "commit": "f" * 40,
            }
        },
    }
    return definition, existing, specs


def _generate(tmp_path: Path, definition: Path, existing: dict):
    lock_tool.lock_path(definition).write_text(json.dumps(existing, indent=2) + "\n")
    return lock_tool.generate(
        definition,
        tmp_path / "work",
        resolve=lambda repository, tag: "d" * 40,
        verifier_pyproject=(
            'dependencies = ["livingproof-evaluator-scenario @ git+https://github.com/'
            'Living-Proof-Labs/livingproof-evaluator-scenario.git@v1.3.0"]\n'
        ),
        digester=lambda image, tag: "sha256:" + "ab" * 32,
    )


def test_a_matching_parameters_digest_is_recorded(tmp_path, monkeypatch):
    definition, existing, specs_commit = _project(
        tmp_path, monkeypatch, specs_files=_specs_files(parameters=PARAMETERS), specs_tag="v2.3.0"
    )
    lock, _notes = _generate(tmp_path, definition, existing)
    files = lock["releases"]["specs"]["files"]
    assert lock["releases"]["specs"]["tag"] == "v2.3.0"
    assert lock["releases"]["specs"]["commit"] == specs_commit
    assert files[HALLWAY_PARAMETERS] == _digest(PARAMETERS)
    assert files[HALLWAY_DESCRIPTION] == _digest(DESCRIPTION)


def test_a_tampered_parameters_digest_is_refused(tmp_path, monkeypatch):
    definition, existing, _commit = _project(
        tmp_path, monkeypatch, specs_files=_specs_files(parameters=PARAMETERS), specs_tag="v2.3.0"
    )
    existing["releases"]["specs"]["files"] = {
        HALLWAY_PARAMETERS: "0" * 64,
        HALLWAY_DESCRIPTION: _digest(DESCRIPTION),
    }
    with pytest.raises(lock_tool.LockError, match="sha256 is") as caught:
        _generate(tmp_path, definition, existing)
    assert "0" * 64 in str(caught.value)
    assert HALLWAY_PARAMETERS in str(caught.value)


def test_specs_v2_0_2_records_that_walk_hallway_parameters_are_missing(tmp_path, monkeypatch):
    definition, existing, specs_commit = _project(
        tmp_path, monkeypatch, specs_files=_specs_files(parameters=None), specs_tag="v2.0.2"
    )
    lock, _notes = _generate(tmp_path, definition, existing)
    files = lock["releases"]["specs"]["files"]
    assert lock["releases"]["specs"]["tag"] == "v2.0.2"
    assert lock["releases"]["specs"]["commit"] == specs_commit
    assert files[HALLWAY_PARAMETERS] is None
    assert files[HALLWAY_DESCRIPTION] == _digest(DESCRIPTION)


@pytest.mark.parametrize("name", HALLWAY_LOCKS)
def test_committed_hallway_locks_record_the_missing_v2_0_2_parameters_path(name):
    lock = json.loads((ROOT / "verifications" / f"{name}.lock.json").read_text())
    specs = lock["releases"]["specs"]
    assert specs["tag"] == "v2.0.2"
    assert specs["commit"] == "16524cbfeef18ff0454ebf125668b010d38a3993"
    assert specs["files"][HALLWAY_PARAMETERS] is None
    assert specs["files"][HALLWAY_DESCRIPTION] == V2_0_2_DESCRIPTION_SHA256


@pytest.mark.parametrize("name", CORNER_LOCKS)
def test_committed_corner_locks_record_matching_v2_3_0_digests(name):
    lock = json.loads((ROOT / "verifications" / f"{name}.lock.json").read_text())
    specs = lock["releases"]["specs"]
    assert specs["tag"] == "v2.3.0"
    assert specs["commit"] == "57317e617014a6df97bb7fba9d92a890af8d42e5"
    assert specs["files"]["specs/scenarios/walk-corner/parameters.json"] == (
        V2_3_0_CORNER_PARAMETERS_SHA256
    )
    assert specs["files"]["specs/scenarios/walk-corner/description.json"] == (
        V2_3_0_CORNER_DESCRIPTION_SHA256
    )
    assert V2_3_0_HALLWAY_PARAMETERS_SHA256 not in specs["files"].values()
