# SPDX-License-Identifier: Apache-2.0
"""Step 12 of robot-capability-specs#40: walk-corner definitions beside walk-hallway.

Three corner definitions, one per robot, pin released robot tags and run walk-corner 0.1.0
on hallway-corner 0.1.0 (livingproof-worlds v1.3.0) through capability-verifier's
``scenario.yml``. The robots are thin, so each definition owns its scenario, world, plan
and specs release, and ``tools/lock.py`` resolves them. The four walk-hallway definitions
keep every evidence input, so their runs and results stay the same.
"""

import hashlib
import importlib.util
import json
from pathlib import Path
import tomllib

import pytest
import yaml

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location("lock_tool", ROOT / "tools/lock.py")
lock_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lock_tool)
WORKFLOW = ROOT / ".github/workflows/verify.yml"
VERIFIER = "Living-Proof-Labs/capability-verifier"

# Released tags, resolved once by hand (git ls-remote) and recorded here.
VERIFIER_RELEASE = ("v1.24.0", "f1ca679eddfa55440f0988e1e4591c0ea85dabac")
EVALUATOR_RELEASE = ("v1.3.0", "965629ab1e9508cb3090931abbae6a957bf5d294")
# The first reporter release whose HTML step card writes out corner-turned's list of
# places (livingproof-capability-report#79, 3453a14). v3.10.1 fails with
# "float() argument must be a string or a real number, not 'list'".
REPORTER_RELEASE = ("v3.11.1", "3453a141746bca38adda2a7d66f517afb0c48bf4")
WORLD = {
    "id": "hallway-corner",
    "version": "0.1.0",
    "repository": "Living-Proof-Labs/livingproof-worlds",
    "tag": "v1.3.0",
    "commit": "12915e34e6ad1dfe376ec2fcb49d5afb957caff9",
    # worlds/hallway-corner/0.1.0/world.json, the file livingproof-sim-mujoco v3.2.x vendors.
    "sha256": "8393881e8b38a090f51c3a424ca444623a901f642f75186cb4d41ac7482898f5",
}
SPECS = {
    "repository": "Living-Proof-Labs/robot-capability-specs",
    "tag": "v2.3.0",
    "commit": "57317e617014a6df97bb7fba9d92a890af8d42e5",
}
SIM_3_2_0 = ("v3.2.0", "4ff176028a0f20ce30f022d45796d098bd8f1025")
SIM_3_2_1 = ("v3.2.1", "f9c326f370cc3e54bf38932bfb332455dc4b5ef1")
CORNER = {
    "unitree-g1-stock-mujoco-corner": {
        "repository": "Living-Proof-Labs/livingproof-unitree-g1",
        "ref": "v2.2.1",
        "commit": "68725986336c38d25a5ea421f895d9d796aed7f1",
        "configurations": ["stock"],
        "runner": SIM_3_2_1,
    },
    "booster-t1-stock-mujoco-corner": {
        "repository": "Living-Proof-Labs/livingproof-booster-t1",
        "ref": "v2.2.0",
        "commit": "8e09bb82234742c3ce88aa5fe2e7e02afaf3858a",
        "configurations": ["stock"],
        "runner": SIM_3_2_0,
    },
    "unitree-go1-playground-mujoco-corner": {
        "repository": "Living-Proof-Labs/livingproof-unitree-go1",
        "ref": "v2.2.0",
        "commit": "c2fc81f6d5c03206bde4ed858dee2074542bfe5d",
        "configurations": ["playground"],
        "runner": SIM_3_2_0,
    },
}
# The four walk-hallway definitions on main before step 12: sha256 of the definition file,
# and sha256 of everything the evidence run reads from the lock (procedure, scenario, robot,
# world, plan, configurations, and the specs and runner releases), as sorted-key JSON.
HALLWAY = {
    "booster-t1-stock-mujoco-hallway": (
        "6b60b94820ded893f120fb727e45d9d8a1f33653178921e8c405668dfe77a544",
        "ecea3c2cac9f90236cd9eddec95292e8f78811960c00b12df6b2b2e802351e37",
    ),
    "unitree-g1-playground-mujoco-hallway": (
        "9e22ced904062ac60cd3339b231497d2eb5ee8e6f4db56063c8f78e7d400fbdb",
        "0ef98f48c5ecfdbb70dc825815c3953827659c4170a39b1710b5235d5a88e7a8",
    ),
    "unitree-g1-stock-mujoco-hallway": (
        "3d842a80dce610e5af4b0f179b54ceb0d4b674a3080e7323aff7e72dd34ed43f",
        "1e7e37c2a9e32055ac52d3e11f856acc26666d9731f45fff1e3a4834ac736cb7",
    ),
    "unitree-go1-playground-mujoco-hallway": (
        "d0acee2b58cd728763ea9e3cd80ebcadc5c0ce5f2ae84f5f46746a6a0560753a",
        "821d6be337fc04526f7c377df4d3152e1d324af09082dec382cf884a79f9781d",
    ),
}
EVIDENCE_KEYS = ("procedure", "scenario", "robot", "world", "plan", "configurations")


def _lock(name: str) -> dict:
    return json.loads((ROOT / "verifications" / f"{name}.lock.json").read_text())


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def _evidence_digest(lock: dict) -> str:
    projection = {key: lock[key] for key in EVIDENCE_KEYS}
    projection["releases"] = {key: lock["releases"][key] for key in ("specs", "runner")}
    return hashlib.sha256(json.dumps(projection, sort_keys=True).encode()).hexdigest()


# ------------------------------------------------------------ the committed definitions


@pytest.mark.parametrize("name", sorted(CORNER))
def test_each_robot_has_a_corner_definition_on_its_released_tag(name):
    want = CORNER[name]
    definition = tomllib.loads((ROOT / "verifications" / f"{name}.toml").read_text())
    assert definition["id"] == name
    assert definition["procedure"] == "scenario"
    assert definition["scenario"] == "walk-corner@0.1.0"
    assert definition["robot"] == {
        "repository": want["repository"],
        "ref": want["ref"],
        "configurations": want["configurations"],
    }
    assert definition["world"] == {
        key: WORLD[key] for key in ("id", "version", "repository", "tag")
    }
    assert definition["plan"] == {"trials": {"count": 1}}
    assert definition["releases"] == {
        "specs": {"repository": SPECS["repository"], "tag": SPECS["tag"]}
    }


@pytest.mark.parametrize("name", sorted(CORNER))
def test_each_corner_lock_pins_the_released_robot_world_runner_and_verifier(name):
    want = CORNER[name]
    lock = _lock(name)
    assert lock_tool.v2_problems(lock) == []
    assert lock["procedure"] == "scenario"
    assert lock["scenario"] == "walk-corner@0.1.0"
    assert lock["robot"]["repository"] == want["repository"]
    assert lock["robot"]["ref"] == want["ref"]
    assert lock["robot"]["commit"] == want["commit"]
    assert lock["robot"]["configurations"] == want["configurations"]
    assert lock["world"] == WORLD
    assert lock["plan"] == {"trials": {"count": 1}}
    assert "command_speed" not in lock["plan"]
    assert lock["releases"]["specs"] == SPECS
    tag, commit = want["runner"]
    assert lock["releases"]["runner"] == {
        "repository": "Living-Proof-Labs/livingproof-sim-mujoco",
        "tag": tag,
        "commit": commit,
    }
    verifier = lock["releases"]["verifier"]
    assert (verifier["repository"], verifier["tag"], verifier["commit"]) == (
        VERIFIER,
        *VERIFIER_RELEASE,
    )
    evaluator = lock["evaluators"]["livingproof-evaluator-scenario"]
    assert (evaluator["tag"], evaluator["commit"]) == EVALUATOR_RELEASE


@pytest.mark.parametrize("name", sorted(CORNER))
def test_each_corner_job_calls_scenario_yml_at_the_locked_release(name):
    lock = _lock(name)
    job = _jobs()[name]
    assert job["uses"] == f"{VERIFIER}/.github/workflows/scenario.yml@{VERIFIER_RELEASE[1]}"
    assert job["needs"] == "locks"
    assert job["with"]["repository"] == lock["robot"]["repository"]
    assert job["with"]["ref"] == lock["robot"]["commit"]
    assert json.loads(job["with"]["configurations"]) == lock["robot"]["configurations"]
    assert job["with"]["verification-lock"] == f"verifications/{name}.lock.json"
    assert job["with"]["artifact-prefix"] == f"{name}-"
    assert job["with"]["check"] is False
    assert job["with"]["comment"] is False


def test_the_site_publishes_the_corner_reports_with_the_hallway_ones():
    jobs = _jobs()
    definitions = sorted(
        path.stem
        for path in (ROOT / "verifications").glob("*.toml")
        if path.name != "settings.toml"
    )
    assert set(CORNER) | set(HALLWAY) == set(definitions)
    assert sorted(jobs["site"]["needs"]) == definitions


def test_every_definition_pins_one_verifier_release():
    """lock-check-git.txt installs one verifier for the locks job, so every job shares it."""
    for name in [*CORNER, *HALLWAY]:
        verifier = _lock(name)["releases"]["verifier"]
        assert (verifier["tag"], verifier["commit"]) == VERIFIER_RELEASE
    tools = (ROOT / ".github/requirements/lock-check-git.txt").read_text()
    assert f"capability-verifier @ git+https://github.com/{VERIFIER}@{VERIFIER_RELEASE[1]}" in tools
    assert (
        "livingproof-evaluator-scenario @ git+https://github.com/Living-Proof-Labs/"
        f"livingproof-evaluator-scenario.git@{EVALUATOR_RELEASE[1]}"
    ) in tools


def test_every_lock_pins_a_reporter_that_renders_walk_corner_places():
    """Each report job renders HTML from its lock's reporter, so all seven need #79's fix."""
    settings = tomllib.loads((ROOT / "verifications/settings.toml").read_text())
    assert settings["reporter"]["tag"] == REPORTER_RELEASE[0]
    for name in [*CORNER, *HALLWAY]:
        reporter = _lock(name)["releases"]["reporter"]
        assert (reporter["tag"], reporter["commit"]) == REPORTER_RELEASE, name


# ------------------------------------------------------------ walk-hallway unchanged


@pytest.mark.parametrize("name", sorted(HALLWAY))
def test_walk_hallway_definitions_and_evidence_inputs_are_unchanged(name):
    definition_sha, evidence_sha = HALLWAY[name]
    definition = ROOT / "verifications" / f"{name}.toml"
    assert hashlib.sha256(definition.read_bytes()).hexdigest() == definition_sha
    lock = _lock(name)
    assert lock["definition"]["sha256"] == definition_sha
    assert lock["procedure"] == "walk-hallway"
    assert lock["scenario"] == "walk-hallway@0.1.0"
    assert lock["world"]["id"] == "hallway-open"
    assert _evidence_digest(lock) == evidence_sha


@pytest.mark.parametrize("name", sorted(HALLWAY))
def test_walk_hallway_jobs_still_call_walk_hallway_yml_with_the_same_robot(name):
    lock = _lock(name)
    job = _jobs()[name]
    assert job["uses"] == f"{VERIFIER}/.github/workflows/walk-hallway.yml@{VERIFIER_RELEASE[1]}"
    assert job["with"]["ref"] == lock["robot"]["commit"]
    assert json.loads(job["with"]["configurations"]) == lock["robot"]["configurations"]
    assert job["with"]["artifact-prefix"] == f"{name}-"


def test_walk_corner_needs_no_command_speed_and_walk_hallway_still_does():
    corner = _lock("unitree-g1-stock-mujoco-corner")
    assert lock_tool.v2_problems(corner) == []
    hallway = _lock("unitree-g1-stock-mujoco-hallway")
    hallway["plan"].pop("command_speed")
    assert "plan.command_speed must be a number" in lock_tool.v2_problems(hallway)
    corner["plan"]["command_speed"] = True
    assert "plan.command_speed must be a number" in lock_tool.v2_problems(corner)
