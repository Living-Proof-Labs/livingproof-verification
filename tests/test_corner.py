# SPDX-License-Identifier: Apache-2.0
"""Step 12 of robot-capability-specs#40: walk-corner definitions beside walk-hallway.

Three corner definitions, one per robot, pin released robot tags and run walk-corner 0.1.0
on hallway-corner 0.1.0 (livingproof-worlds v1.3.0) through capability-verifier's
``scenario.yml``. The four walk-hallway definitions run walk-hallway 0.1.0 on
hallway-open 0.1.0 (livingproof-worlds v1.1.0) through ``walk-hallway.yml``. The robots
are thin, so each definition owns its scenario, world, plan and specs release, and
``tools/lock.py`` resolves them. Issue 100 step 5 moves the hallway definitions to
specs v2.3.0 (v2.0.2 has no parameters.json; capability-verifier v2.0.0 refuses that pin).
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
VERIFIER_RELEASE = ("v2.3.0", "39fc7b53f7e4342abf5be221165ae6e1694f56c4")
EVALUATOR_RELEASE = ("v1.3.0", "965629ab1e9508cb3090931abbae6a957bf5d294")
# The first reporter release whose HTML step card writes out corner-turned's list of
# places (livingproof-capability-report#79, 3453a14). v3.10.1 fails with
# "float() argument must be a string or a real number, not 'list'".
# The constant is the settings pin; every lock must name that same release.
REPORTER_RELEASE = ("v3.11.6", "bc1a05eaeb5c27fd084cd29d2cd399bb485310e6")
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
    "files": {
        "specs/scenarios/walk-corner/description.json": (
            "be8de80e1a7b6f2273cee7abf7303d2e78a407ba0a5f11f9d06ed8cbed7c252c"
        ),
        "specs/scenarios/walk-corner/parameters.json": (
            "62f2912c33b557d58cd454daf7d1eea543e919dec0e9b53e613d4101a5a12977"
        ),
    },
}
SIM_3_4_1 = ("v3.4.1", "4e97b6c1ee72a9f6598a91d0a1476116dc17ae13")
CORNER = {
    "unitree-g1-stock-mujoco-corner": {
        "repository": "Living-Proof-Labs/livingproof-unitree-g1",
        "ref": "v2.2.11",
        "commit": "3c8a781dc42b56126cc599e00a7d7ed360ed6671",
        "configurations": ["stock"],
        "runner": SIM_3_4_1,
    },
    "booster-t1-stock-mujoco-corner": {
        "repository": "Living-Proof-Labs/livingproof-booster-t1",
        "ref": "v2.2.10",
        "commit": "a00c7d884b764c89404dc91be9232c0d55eb392c",
        "configurations": ["stock"],
        "runner": SIM_3_4_1,
    },
    "unitree-go1-playground-mujoco-corner": {
        "repository": "Living-Proof-Labs/livingproof-unitree-go1",
        "ref": "v2.2.11",
        "commit": "f2aa573ded0d845897f2c7b7f1c02b645ad0ad3b",
        "configurations": ["playground"],
        "runner": SIM_3_4_1,
    },
}
# hallway-open 0.1.0 at livingproof-worlds v1.1.0, the world livingproof-sim-mujoco
# v2.0.3 vendors. The lock records the world.json sha256 v2.0.0 assessment requires.
HALLWAY_WORLD = {
    "id": "hallway-open",
    "version": "0.1.0",
    "repository": "Living-Proof-Labs/livingproof-worlds",
    "tag": "v1.1.0",
    "commit": "9b3e7119aa93a3370383227fb30bd39a9dd72b4b",
    "sha256": "d60bb8aa6a72c8403fb6674cc9d803962c1484bc3e163527988e8c57ccec775c",
}
HALLWAY_SPECS = {
    "repository": "Living-Proof-Labs/robot-capability-specs",
    "tag": "v2.3.0",
    "commit": "57317e617014a6df97bb7fba9d92a890af8d42e5",
    "files": {
        "specs/scenarios/walk-hallway/description.json": (
            "788bcd6345dad4d262ea14fc346d8883933b790f4991302f01d6eee84f52ff21"
        ),
        "specs/scenarios/walk-hallway/parameters.json": (
            "9226aa6bc0e577863bdc210514e7e7c3a7edc32f49c4f7b46aa7249837f08b3d"
        ),
    },
}
HALLWAY_ROBOT = {
    "booster-t1-stock-mujoco-hallway": {
        "repository": "Living-Proof-Labs/livingproof-booster-t1",
        "ref": "v1.0.0",
        "commit": "61b2559ebb401973ec7733b4c886753e6d2b704c",
        "configurations": ["stock"],
    },
    "unitree-g1-playground-mujoco-hallway": {
        "repository": "Living-Proof-Labs/livingproof-unitree-g1",
        "ref": "v1.0.0",
        "commit": "da7d9504cb5d4deaa509b92a1973490e43888f90",
        "configurations": ["playground"],
    },
    "unitree-g1-stock-mujoco-hallway": {
        "repository": "Living-Proof-Labs/livingproof-unitree-g1",
        "ref": "v1.0.0",
        "commit": "da7d9504cb5d4deaa509b92a1973490e43888f90",
        "configurations": ["stock"],
    },
    "unitree-go1-playground-mujoco-hallway": {
        "repository": "Living-Proof-Labs/livingproof-unitree-go1",
        "ref": "v1.0.0",
        "commit": "fa5383a64e6d351a7b592e75796bf1f32c107197",
        "configurations": ["playground"],
    },
}
# sha256 of the definition file, and sha256 of everything the evidence run reads
# from the lock (procedure, scenario, robot, world, plan, configurations, and the
# specs and runner releases), as sorted-key JSON.
HALLWAY = {
    "booster-t1-stock-mujoco-hallway": (
        "2da51134f83543616e9973112e28fdae2e72d29159c6da04816969b265a3fce5",
        "fc3088da8163e46ea12aa533fcb4bea5af1818b0bfbc325ba135c94cce4bf97a",
    ),
    "unitree-g1-playground-mujoco-hallway": (
        "67db35a30d7c078c6fa908818076f99de1475733f3b9d61ad5e81a916926bd79",
        "fc7fda01d4f8d0878e31433e5ad987c5fa2a670d21f1e751dd6e59411add0a28",
    ),
    "unitree-g1-stock-mujoco-hallway": (
        "885d33d20a8cd82d7fd41cabd99656c9446896e8a331d45bc48aaa451ebd2bea",
        "2b1e80b5e1f4088d69b3fe1c3721b3b7da53796c46d71e4728ebdbe265094e74",
    ),
    "unitree-go1-playground-mujoco-hallway": (
        "ee01a8bf4849deb1297b9b0e83f603786ad6d591648277602f8bc1c415bb5495",
        "b7129ba2c55d1826e5ad8203a3668d889b670e097378cafcd81b812b1d0fc6f4",
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


# ------------------------------------------------------------ walk-hallway current spec


@pytest.mark.parametrize("name", sorted(HALLWAY))
def test_each_hallway_definition_owns_the_current_specs_release(name):
    want = HALLWAY_ROBOT[name]
    definition = tomllib.loads((ROOT / "verifications" / f"{name}.toml").read_text())
    assert definition["id"] == name
    assert definition["procedure"] == "walk-hallway"
    assert definition["scenario"] == "walk-hallway@0.1.0"
    assert definition["robot"] == {
        "repository": want["repository"],
        "ref": want["ref"],
        "configurations": want["configurations"],
    }
    assert definition["world"] == {
        key: HALLWAY_WORLD[key] for key in ("id", "version", "repository", "tag")
    }
    assert definition["plan"] == {"trials": {"count": 3}, "command_speed": 0.5}
    assert definition["releases"] == {
        "specs": {"repository": HALLWAY_SPECS["repository"], "tag": HALLWAY_SPECS["tag"]}
    }


@pytest.mark.parametrize("name", sorted(HALLWAY))
def test_each_hallway_lock_pins_the_released_robot_world_runner_and_verifier(name):
    want = HALLWAY_ROBOT[name]
    definition_sha, evidence_sha = HALLWAY[name]
    definition = ROOT / "verifications" / f"{name}.toml"
    assert hashlib.sha256(definition.read_bytes()).hexdigest() == definition_sha
    lock = _lock(name)
    assert lock_tool.v2_problems(lock) == []
    assert lock["definition"]["sha256"] == definition_sha
    assert lock["procedure"] == "walk-hallway"
    assert lock["scenario"] == "walk-hallway@0.1.0"
    assert lock["robot"]["repository"] == want["repository"]
    assert lock["robot"]["ref"] == want["ref"]
    assert lock["robot"]["commit"] == want["commit"]
    assert lock["robot"]["configurations"] == want["configurations"]
    assert lock["world"] == HALLWAY_WORLD
    assert lock["plan"] == {"trials": {"count": 3}, "command_speed": 0.5}
    assert lock["releases"]["specs"] == HALLWAY_SPECS
    assert lock["releases"]["runner"] == {
        "repository": "Living-Proof-Labs/livingproof-sim-mujoco",
        "tag": "v2.0.3",
        "commit": "292589ad15582c26cff2bffcc851e3abaf4ea708",
    }
    verifier = lock["releases"]["verifier"]
    assert (verifier["repository"], verifier["tag"], verifier["commit"]) == (
        VERIFIER,
        *VERIFIER_RELEASE,
    )
    evaluator = lock["evaluators"]["livingproof-evaluator-scenario"]
    assert (evaluator["tag"], evaluator["commit"]) == EVALUATOR_RELEASE
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
