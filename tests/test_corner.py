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
import os
from pathlib import Path
import tomllib
import urllib.request

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
# v3.4.1 validates against its schema. The lock records the world.json sha256 v2.0.0 assessment requires.
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
        "ref": "v2.2.10",
        "commit": "a00c7d884b764c89404dc91be9232c0d55eb392c",
        "configurations": ["stock"],
    },
    "unitree-g1-playground-mujoco-hallway": {
        "repository": "Living-Proof-Labs/livingproof-unitree-g1",
        "ref": "v2.2.11",
        "commit": "3c8a781dc42b56126cc599e00a7d7ed360ed6671",
        "configurations": ["playground"],
    },
    "unitree-g1-stock-mujoco-hallway": {
        "repository": "Living-Proof-Labs/livingproof-unitree-g1",
        "ref": "v2.2.11",
        "commit": "3c8a781dc42b56126cc599e00a7d7ed360ed6671",
        "configurations": ["stock"],
    },
    "unitree-go1-playground-mujoco-hallway": {
        "repository": "Living-Proof-Labs/livingproof-unitree-go1",
        "ref": "v2.2.11",
        "commit": "f2aa573ded0d845897f2c7b7f1c02b645ad0ad3b",
        "configurations": ["playground"],
    },
}
# sha256 of the definition file, and sha256 of everything the evidence run reads
# from the lock (procedure, scenario, robot, world, plan, configurations, and the
# specs and runner releases), as sorted-key JSON.
HALLWAY = {
    "booster-t1-stock-mujoco-hallway": (
        "bc6e864ca49d42fc14930fcfe302308c0a68be9dc350b0975feceee4a6a07ab7",
        "5ed6fc97066dbff74b539866ec7fb53748ee76330006617881e812054db673f9",
    ),
    "unitree-g1-playground-mujoco-hallway": (
        "d8edb6b4d5b371f4672a2ae8b1595cb660718359cb9b3817eef6cdcef306e675",
        "e5ef65f6fa0aa3e1b1487c171bf7bd47a38015d7b4285782e1deb31c1ba4186b",
    ),
    "unitree-g1-stock-mujoco-hallway": (
        "2fbb53fca6beeaa1f6662d4937a7fbeebccd81ed54bd2cae61ecd1dc3df6e829",
        "716f327edb5c33a8f0a48c64ac0daa054b0016560515bc9d0f0968b93f7a38d9",
    ),
    "unitree-go1-playground-mujoco-hallway": (
        "8b92ebc98abae63574cd66685bdc2bc159aa213c67f4ef24f16d9eaa018a11a8",
        "e3f7d3987434296fc12cdbde4b2afcdb0a0ae864514ca9d6af9b88d15957eaeb",
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


def test_every_definition_pins_the_same_simulator_release():
    """Hallways and corners share one runner: the release each robot's uv.lock installs."""
    runners = [_lock(name)["releases"]["runner"] for name in [*CORNER, *HALLWAY]]
    assert runners
    assert all(runner == runners[0] for runner in runners)
    assert runners[0] == {
        "repository": "Living-Proof-Labs/livingproof-sim-mujoco",
        "tag": "v3.4.1",
        "commit": "4e97b6c1ee72a9f6598a91d0a1476116dc17ae13",
    }


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
def test_pinned_robot_tags_uv_lock_installs_that_simulator(tmp_path):
    """The published uv.lock at each pinned robot tag names the same sim tag as the locks."""
    seen = set()
    for name in [*CORNER, *HALLWAY]:
        lock = _lock(name)
        robot = lock["robot"]
        key = (robot["repository"], robot["ref"])
        if key in seen:
            continue
        seen.add(key)
        url = f"https://raw.githubusercontent.com/{robot['repository']}/{robot['ref']}/uv.lock"
        root = tmp_path / robot["repository"].replace("/", "_")
        root.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=30) as response:
            (root / "uv.lock").write_bytes(response.read())
        runner = lock_tool._runner_from_uv_lock(root, f"{robot['repository']} {robot['ref']}")
        assert runner == lock["releases"]["runner"], key


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
        "tag": "v3.4.1",
        "commit": "4e97b6c1ee72a9f6598a91d0a1476116dc17ae13",
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
