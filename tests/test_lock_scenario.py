# SPDX-License-Identifier: Apache-2.0
"""tools/lock.py for procedure ``scenario`` (robot-capability-specs#40 step 12).

A thin robot publishes its adapter and configurations only, so a new definition has no
robot lock and no earlier lock to take scenario, world, plan and releases from. A
definition may name scenario, world, plan and the specs release itself; the lock records
the commits they resolve to and the world file's sha256, and takes the runner from the
robot's ``uv.lock``. Procedure ``scenario`` is locked from a ``scenario.yml`` call.
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
VERIFIER = "Living-Proof-Labs/capability-verifier"


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
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    if tag:
        _git(path, "tag", "-a", tag, "-m", tag)
    return _git(path, "rev-parse", "HEAD")


WORLD_JSON = '{"id": "hallway-corner", "content_version": "0.1.0"}\n'


def _uv_lock(sim_commit: str, tag: str = "v3.2.1") -> str:
    return (
        "version = 1\n\n[[package]]\n"
        'name = "livingproof-sim-mujoco"\nversion = "3.2.1"\n'
        'source = { git = "https://github.com/Living-Proof-Labs/livingproof-sim-mujoco.git'
        f'?tag={tag}#{sim_commit}" }}\n\n'
        '[[package]]\nname = "fixture-robot"\nsource = { editable = "." }\n'
    )


def _corner_project(tmp_path: Path, monkeypatch, *, definition_extra: str = "", uv_tag="v3.2.1"):
    """Local git repositories for the robot, worlds, specs and sim, and a corner definition."""
    base = tmp_path / "git"
    sim = _repo(base, "Living-Proof-Labs/livingproof-sim-mujoco.git", {"README": "sim\n"}, "v3.2.1")
    worlds = _repo(
        base,
        "Living-Proof-Labs/livingproof-worlds.git",
        {"worlds/hallway-corner/0.1.0/world.json": WORLD_JSON},
        "v1.3.0",
    )
    corner_description = '{"id": "walk-corner"}\n'
    corner_parameters = '{"command_speed": 0.5}\n'
    specs = _repo(
        base,
        "Living-Proof-Labs/robot-capability-specs.git",
        {
            "specs/scenarios/walk-corner/description.json": corner_description,
            "specs/scenarios/walk-corner/parameters.json": corner_parameters,
        },
        "v2.3.0",
    )
    configuration = json.dumps({"name": "stock", "version": "1.1.1"})
    robot = _repo(
        base,
        "Org/robot.git",
        {
            "pyproject.toml": '[project]\nname = "fixture-robot"\n\n'
            '[tool.livingproof]\nadapter = "lpl_g1.adapter:ROBOT"\n',
            "robot.json": json.dumps(
                {"configurations": [{"name": "stock", "path": "configurations/stock.json"}]}
            ),
            "configurations/stock.json": configuration,
            "uv.lock": _uv_lock(sim, uv_tag),
        },
    )
    monkeypatch.setenv("LIVINGPROOF_GIT_BASE", f"file://{base}")
    project = tmp_path / "proj"
    (project / "verifications").mkdir(parents=True)
    (project / "verifications" / "settings.toml").write_text(
        "[reporter]\n"
        'repository = "Living-Proof-Labs/livingproof-capability-report"\n'
        'tag = "v3.10.1"\n'
        'image = "ghcr.io/living-proof-labs/livingproof-capability-report"\n'
    )
    definition = project / "verifications" / "robot-stock-mujoco-corner.toml"
    definition.write_text(
        f'format = "{lock_tool.DEFINITION_FORMAT}"\n'
        'id = "robot-stock-mujoco-corner"\n'
        'procedure = "scenario"\n'
        'scenario = "walk-corner@0.1.0"\n'
        "[robot]\n"
        'repository = "Org/robot"\nref = "main"\nconfigurations = ["stock"]\n'
        "[world]\n"
        'id = "hallway-corner"\nversion = "0.1.0"\n'
        'repository = "Living-Proof-Labs/livingproof-worlds"\ntag = "v1.3.0"\n'
        "[plan]\ntrials = { count = 1 }\n"
        "[releases.specs]\n"
        'repository = "Living-Proof-Labs/robot-capability-specs"\ntag = "v2.3.0"\n'
        + definition_extra
    )
    verifier_commit = "c" * 40
    workflow = project / ".github" / "workflows" / "verify.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n"
        "  robot-stock-mujoco-corner:\n"
        f"    uses: {VERIFIER}/.github/workflows/scenario.yml@{verifier_commit}  # v1.24.0\n"
    )
    local = {"livingproof-sim-mujoco", "livingproof-worlds", "robot-capability-specs"}

    def resolve(repository, tag):
        if repository.split("/")[1] in local:
            return lock_tool.resolve_tag(repository, tag)
        return "e" * 40

    commits = {"sim": sim, "worlds": worlds, "specs": specs, "robot": robot}
    return definition, workflow, resolve, commits, verifier_commit


def _corner_generate(tmp_path, definition, resolve):
    return lock_tool.generate(
        definition,
        tmp_path / "work",
        resolve=resolve,
        verifier_pyproject=(
            'dependencies = ["livingproof-evaluator-scenario @ git+https://github.com/'
            'Living-Proof-Labs/livingproof-evaluator-scenario.git@v1.3.0"]\n'
        ),
        tag_of=lambda repository, commit: "v1.24.0",
        digester=lambda image, tag: "sha256:" + "ab" * 32,
    )


def test_a_definition_that_owns_scenario_world_plan_and_specs_locks_a_thin_robot(
    tmp_path, monkeypatch
):
    definition, _workflow, resolve, commits, verifier_commit = _corner_project(
        tmp_path, monkeypatch
    )
    lock, notes = _corner_generate(tmp_path, definition, resolve)
    assert lock_tool.v2_problems(lock) == []
    assert lock["procedure"] == "scenario"
    assert lock["scenario"] == "walk-corner@0.1.0"
    assert lock["robot"]["commit"] == commits["robot"]
    assert lock["world"] == {
        "id": "hallway-corner",
        "version": "0.1.0",
        "repository": "Living-Proof-Labs/livingproof-worlds",
        "tag": "v1.3.0",
        "commit": commits["worlds"],
        "sha256": hashlib.sha256(WORLD_JSON.encode()).hexdigest(),
    }
    assert lock["plan"] == {"trials": {"count": 1}}
    assert lock["releases"]["specs"] == {
        "repository": "Living-Proof-Labs/robot-capability-specs",
        "tag": "v2.3.0",
        "commit": commits["specs"],
        "files": {
            "specs/scenarios/walk-corner/description.json": hashlib.sha256(
                b'{"id": "walk-corner"}\n'
            ).hexdigest(),
            "specs/scenarios/walk-corner/parameters.json": hashlib.sha256(
                b'{"command_speed": 0.5}\n'
            ).hexdigest(),
        },
    }
    assert lock["releases"]["runner"] == {
        "repository": "Living-Proof-Labs/livingproof-sim-mujoco",
        "tag": "v3.2.1",
        "commit": commits["sim"],
    }
    assert lock["releases"]["verifier"]["commit"] == verifier_commit
    assert lock["releases"]["verifier"]["tag"] == "v1.24.0"
    assert lock["evaluators"]["livingproof-evaluator-scenario"]["tag"] == "v1.3.0"
    assert [entry["name"] for entry in lock["configurations"]] == ["stock"]
    assert any(note.startswith("definition owns scenario") for note in notes)


def test_generate_records_world_and_parameter_source_bindings(tmp_path, monkeypatch):
    """Fail-first for livingproof-verification#119: generated locks bind the declared files."""
    definition, _workflow, resolve, _commits, _verifier = _corner_project(tmp_path, monkeypatch)
    lock, _notes = _corner_generate(tmp_path, definition, resolve)
    world_path = f"worlds/{lock['world']['id']}/{lock['world']['version']}/world.json"
    param_path = "specs/scenarios/walk-corner/parameters.json"
    assert lock["sources"]["world"] == {
        "path": world_path,
        "sha256": lock["world"]["sha256"],
    }
    assert lock["sources"]["parameters"] == {
        "path": param_path,
        "sha256": lock["releases"]["specs"]["files"][param_path],
    }
    assert lock_tool.v2_problems(lock) == []


def test_the_lock_is_the_same_when_written_twice(tmp_path, monkeypatch):
    definition, _workflow, resolve, _commits, _verifier = _corner_project(tmp_path, monkeypatch)
    first, _ = _corner_generate(tmp_path, definition, resolve)
    lock_tool.lock_path(definition).write_text(json.dumps(first, indent=2) + "\n")
    second, _ = _corner_generate(tmp_path, definition, resolve)
    assert second == first


def test_a_definition_that_names_only_some_owned_fields_is_refused(tmp_path):
    path = tmp_path / "partial.toml"
    path.write_text(
        f'format = "{lock_tool.DEFINITION_FORMAT}"\nid = "partial"\nprocedure = "scenario"\n'
        'scenario = "walk-corner@0.1.0"\n'
        '[robot]\nrepository = "Org/robot"\nref = "main"\nconfigurations = ["stock"]\n'
    )
    with pytest.raises(lock_tool.LockError, match="must name all of them"):
        lock_tool.load_definition(path)


def test_a_definition_may_not_name_the_world_commit_or_another_release(tmp_path, monkeypatch):
    definition, *_ = _corner_project(tmp_path, monkeypatch)
    text = definition.read_text().replace(
        'tag = "v1.3.0"\n', 'tag = "v1.3.0"\ncommit = "' + "1" * 40 + '"\n', 1
    )
    definition.write_text(text + '[releases.runner]\nrepository = "X/y"\ntag = "v1.0.0"\n')
    with pytest.raises(lock_tool.LockError) as caught:
        lock_tool.load_definition(definition)
    assert "the lock resolves commit and sha256" in str(caught.value)
    assert "releases must name specs only" in str(caught.value)


def test_a_uv_lock_runner_commit_that_is_not_its_tag_is_refused(tmp_path, monkeypatch):
    definition, _workflow, resolve, _commits, _verifier = _corner_project(tmp_path, monkeypatch)

    def moved(repository, tag):
        if repository.endswith("livingproof-sim-mujoco"):
            return "9" * 40
        return resolve(repository, tag)

    with pytest.raises(lock_tool.LockError, match="but that tag is 999999999999"):
        _corner_generate(tmp_path, definition, moved)


def test_a_world_the_tag_does_not_carry_is_refused(tmp_path, monkeypatch):
    definition, *_rest = _corner_project(tmp_path, monkeypatch)
    resolve = _rest[1]
    definition.write_text(definition.read_text().replace('version = "0.1.0"', 'version = "0.2.0"'))
    with pytest.raises(lock_tool.LockError, match="has no worlds/hallway-corner/0.2.0/world.json"):
        _corner_generate(tmp_path, definition, resolve)


def test_procedure_scenario_takes_its_verifier_only_from_a_scenario_yml_call(tmp_path):
    workflow = tmp_path / "verify.yml"
    commit = "c" * 40
    workflow.write_text(
        "jobs:\n"
        "  corner:\n"
        f"    uses: {VERIFIER}/.github/workflows/walk-hallway.yml@{commit}  # v1.24.0\n"
        "  hallway:\n"
        f"    uses: {VERIFIER}/.github/workflows/scenario.yml@{commit}  # v1.24.0\n"
    )
    kwargs = {"tag_of": lambda r, c: "v1.24.0", "digester": lambda i, t: "sha256:" + "ab" * 32}
    assert lock_tool.called_verifier(workflow, "corner", procedure="scenario", **kwargs) is None
    assert (
        lock_tool.called_verifier(workflow, "hallway", procedure="walk-hallway", **kwargs) is None
    )
    called = lock_tool.called_verifier(workflow, "hallway", procedure="scenario", **kwargs)
    assert called["commit"] == commit and called["tag"] == "v1.24.0"
