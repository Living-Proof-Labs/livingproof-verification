# SPDX-License-Identifier: Apache-2.0
"""Step 12 of robot-capability-specs#40: walk-corner definitions beside walk-hallway.

Six corner definitions select left/right traversals of one world revision
(hallway-corner 0.2.0, livingproof-worlds v1.4.0) through capability-verifier's
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
import re
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
VERIFIER_RELEASE = ("v2.9.1", "ca6d009a2d7ac65dd4c42ef4342df9e005bb056b")
EVALUATOR_RELEASE = ("v1.3.0", "965629ab1e9508cb3090931abbae6a957bf5d294")
# The first reporter release whose HTML step card writes out corner-turned's list of
# places (livingproof-capability-report#79, 3453a14). v3.10.1 fails with
# "float() argument must be a string or a real number, not 'list'".
# The constant is the settings pin; every lock must name that same release.
REPORTER_RELEASE = ("v3.14.0", "69e2150c4d3103244d06e40a85d77701a81a0a0a")
WORLD = {
    "id": "hallway-corner",
    "version": "0.2.0",
    "repository": "Living-Proof-Labs/livingproof-worlds",
    "tag": "v1.4.0",
    "commit": "971b39ee845a18c50da627a7410d22e3b98171f9",
    # worlds/hallway-corner/0.2.0/world.json, identical for both directions.
    "sha256": "f398f2705e630750f7b7ef3035ba4b5aed076de1f417165e58540e973150678e",
}
# Exact files from specs v3.1.0, independently read at the released commit.
SPECS_FILES = {
    "left": (
        "a0e7f73350bd573ca342e9a3d19f48c6aab07ada207e99b5861a6d975a163be3",
        "c9550c4e9462eb9b92e8efddd8b36a9d93d9977e111e9f3e8446927433dd2d4b",
    ),
    "right": (
        "67fb212f837427583c6db5cefaf29d58186ed6e4cd3e97b1520d34191034c143",
        "01e7666a3310f8d30c77ce9f5589ccb960fc495099df57e02be7724bbac880aa",
    ),
}
SPECS = {
    direction: {
        "repository": "Living-Proof-Labs/robot-capability-specs",
        "tag": "v3.1.0",
        "commit": "9ee830201f05943feb2b9787eea2ea747431d935",
        "files": {
            f"specs/scenarios/walk-corner-{direction}/{file}.json": digest
            for file, digest in zip(("description", "parameters"), digests, strict=True)
        },
    }
    for direction, digests in SPECS_FILES.items()
}
# The simulators the selected robot releases install (#145, option 2). v3.5.0 is the
# first release that records the scenario parameters digest (livingproof-sim-mujoco#104).
SIM_3_6_2 = ("v3.6.2", "988a57bdb54654aac57aa4de0574d62914074fd0")
SIM_3_6_4 = ("v3.6.4", "36e93e30f74315a753658e6d0a55af128301338b")
FIRST_SIM_WITH_PARAMETERS_DIGEST = (3, 5, 0)
# Frozen hallway robot refs; eligible corners have their own release expectations below.
# Commits independently read from the released tags.
HALLWAY_ROBOT_REFS = {
    "Living-Proof-Labs/livingproof-booster-t1": (
        "v2.3.5",
        "56f6051fd9ee9fdb17f609bf559b7efc8220088a",
    ),
    "Living-Proof-Labs/livingproof-unitree-g1": (
        "v2.3.12",
        "cdab1cfe50351bc8611cab5dbb6d1a1b23cc179f",
    ),
    "Living-Proof-Labs/livingproof-unitree-go1": (
        "v2.3.5",
        "fe48f8bda30aa0a65bd01b06107fc76874195f35",
    ),
}
CORNER = {
    "unitree-g1-stock-mujoco-corner": {
        "repository": "Living-Proof-Labs/livingproof-unitree-g1",
        "ref": "v2.3.13",
        "commit": "aa10b46f7e0480065d86edc909828d93da4b4a3a",
        "configurations": ["stock"],
        "runner": SIM_3_6_4,
    },
    "booster-t1-stock-mujoco-corner": {
        "repository": "Living-Proof-Labs/livingproof-booster-t1",
        "ref": "v2.3.5",
        "commit": "56f6051fd9ee9fdb17f609bf559b7efc8220088a",
        "configurations": ["stock"],
        "runner": SIM_3_6_2,
    },
    "unitree-go1-playground-mujoco-corner": {
        "repository": "Living-Proof-Labs/livingproof-unitree-go1",
        "ref": "v2.3.5",
        "commit": "fe48f8bda30aa0a65bd01b06107fc76874195f35",
        "configurations": ["playground"],
        "runner": SIM_3_6_2,
    },
}
CORNER = {
    f"{name}-{direction}": {
        **robot,
        "direction": direction,
        "scenario": f"walk-corner-{direction}@{revision}",
    }
    for name, robot in CORNER.items()
    for direction, revision in (("left", "0.2.0"), ("right", "0.1.0"))
}

# hallway-open 0.1.0 at livingproof-worlds v1.1.0, the world livingproof-sim-mujoco
# v3.6.2 validates against its schema. The lock records the world.json sha256 v2.0.0 assessment requires.
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
        "ref": "v2.3.5",
        "commit": "56f6051fd9ee9fdb17f609bf559b7efc8220088a",
        "configurations": ["stock"],
    },
    "unitree-g1-playground-mujoco-hallway": {
        "repository": "Living-Proof-Labs/livingproof-unitree-g1",
        "ref": "v2.3.12",
        "commit": "cdab1cfe50351bc8611cab5dbb6d1a1b23cc179f",
        "configurations": ["playground"],
    },
    "unitree-g1-stock-mujoco-hallway": {
        "repository": "Living-Proof-Labs/livingproof-unitree-g1",
        "ref": "v2.3.12",
        "commit": "cdab1cfe50351bc8611cab5dbb6d1a1b23cc179f",
        "configurations": ["stock"],
    },
    "unitree-go1-playground-mujoco-hallway": {
        "repository": "Living-Proof-Labs/livingproof-unitree-go1",
        "ref": "v2.3.5",
        "commit": "fe48f8bda30aa0a65bd01b06107fc76874195f35",
        "configurations": ["playground"],
    },
}
# sha256 of the definition file, and sha256 of everything the evidence run reads
# from the lock (procedure, scenario, robot, world, plan, configurations, and the
# specs and runner releases), as sorted-key JSON.
HALLWAY = {
    "booster-t1-stock-mujoco-hallway": (
        "aa55d99edb573d9e7117da52fe3eb33fe4695cdcf9eb137b195621b3ab1ba517",
        "56d5a2455b61898c6ea130492741a068e1919fefc2d1d060d8fef8b1bf91ebfd",
    ),
    "unitree-g1-playground-mujoco-hallway": (
        "084a60ac2b376e707eb6feaf3195f314e9af855623d059a745261acca9335c24",
        "08ab8ec17d6c6a8bca75da64cf2e4b68dccb795ec8510dd4c63ab1e07c9b9f0c",
    ),
    "unitree-g1-stock-mujoco-hallway": (
        "3b5ddd17d7d70937d7c15504108d25d4f16cc32f478c6e19ef4bea76a52ebb67",
        "42d4d685d3a8e3254b7e24df9971e4b727bb60bcbb2ff763d20eb5bedd5926a3",
    ),
    "unitree-go1-playground-mujoco-hallway": (
        "3b9570c7f86281631e45e175d5727b5bb1011203f3c8cd4d1e03a26c64b4d374",
        "42b49577f9ece03c6724017d361b74055c029b35aadde885c44b657ca85e13cf",
    ),
}
EVIDENCE_KEYS = ("procedure", "scenario", "robot", "world", "plan", "configurations")
GAP = {"max_gap_seconds": 0.05}


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
    assert definition["scenario"] == want["scenario"]
    assert definition["robot"] == {
        "repository": want["repository"],
        "ref": want["ref"],
        "configurations": want["configurations"],
    }
    assert definition["world"] == {
        key: WORLD[key] for key in ("id", "version", "repository", "tag")
    }
    assert definition["plan"] == {"trials": {"count": 1}, "recording": {"max_gap_seconds": 0.05}}
    assert definition["releases"] == {
        "specs": {"repository": SPECS[want["direction"]]["repository"], "tag": "v3.1.0"}
    }


@pytest.mark.parametrize("name", sorted(CORNER))
def test_each_corner_lock_pins_the_released_robot_world_runner_and_verifier(name):
    want = CORNER[name]
    lock = _lock(name)
    assert lock_tool.v2_problems(lock) == []
    assert lock["procedure"] == "scenario"
    assert lock["scenario"] == want["scenario"]
    assert lock["robot"]["repository"] == want["repository"]
    assert lock["robot"]["ref"] == want["ref"]
    assert lock["robot"]["commit"] == want["commit"]
    assert lock["robot"]["configurations"] == want["configurations"]
    assert lock["world"] == WORLD
    assert lock["plan"] == {"trials": {"count": 1}, "recording": {"max_gap_seconds": 0.05}}
    assert "command_speed" not in lock["plan"]
    assert lock["releases"]["specs"] == SPECS[want["direction"]]
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
    # `release` (#131) is an ordering dependency, not a verification.
    assert sorted(set(jobs["site"]["needs"]) - {"release"}) == definitions


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
    """Each report job renders HTML from its lock's reporter, so all ten need #79's fix."""
    settings = tomllib.loads((ROOT / "verifications/settings.toml").read_text())
    assert settings["reporter"]["tag"] == REPORTER_RELEASE[0]
    for name in [*CORNER, *HALLWAY]:
        reporter = _lock(name)["releases"]["reporter"]
        assert (reporter["tag"], reporter["commit"]) == REPORTER_RELEASE, name


def _semver(tag: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag)
    assert match, f"{tag!r} is not a released vX.Y.Z tag"
    return tuple(int(part) for part in match.groups())


@pytest.mark.parametrize("name", sorted([*CORNER, *HALLWAY]))
def test_every_runner_is_v3_5_0_or_later_from_a_released_robot_ref(name):
    """#145: the parameters digest is recorded from livingproof-sim-mujoco v3.5.0, so every lock
    must resolve the runner at or after it, through a released robot tag (not a branch or SHA)."""
    lock = _lock(name)
    robot = lock["robot"]
    assert lock_tool.v2_problems(lock) == []
    assert _semver(robot["ref"])  # a released robot tag
    expected = (
        (CORNER[name]["ref"], CORNER[name]["commit"])
        if name in CORNER
        else HALLWAY_ROBOT_REFS[robot["repository"]]
    )
    assert (robot["ref"], robot["commit"]) == expected, name
    runner = lock["releases"]["runner"]
    assert runner["repository"] == "Living-Proof-Labs/livingproof-sim-mujoco"
    assert _semver(runner["tag"]) >= FIRST_SIM_WITH_PARAMETERS_DIGEST, name
    assert re.fullmatch(r"[0-9a-f]{40}", runner["commit"])


def test_every_lock_pins_livingproof_evaluator_run_v1_7_0():
    """Every lock pins livingproof-evaluator-run v1.7.0, so CI runs all five Run Integrity
    checks: pins, completeness, plan-conformance, termination and recording (#136)."""
    expected = {
        "repository": "Living-Proof-Labs/livingproof-evaluator-run",
        "tag": "v1.7.0",
    }
    for name in [*CORNER, *HALLWAY]:
        assert _lock(name)["evaluators"]["livingproof-evaluator-run"] == expected, name


@pytest.mark.parametrize("name", sorted([*CORNER, *HALLWAY]))
def test_each_definition_pins_its_robot_simulator_release(name):
    """Eligible corners can advance while frozen hallway runners retain their baseline."""
    tag, commit = CORNER[name]["runner"] if name in CORNER else SIM_3_6_2
    assert _lock(name)["releases"]["runner"] == {
        "repository": "Living-Proof-Labs/livingproof-sim-mujoco",
        "tag": tag,
        "commit": commit,
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
    assert definition["plan"] == {"trials": {"count": 3}, "command_speed": 0.5, "recording": GAP}
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
    assert lock["plan"] == {"trials": {"count": 3}, "command_speed": 0.5, "recording": GAP}
    assert lock["releases"]["specs"] == HALLWAY_SPECS
    assert lock["releases"]["runner"] == {
        "repository": "Living-Proof-Labs/livingproof-sim-mujoco",
        "tag": "v3.6.2",
        "commit": "988a57bdb54654aac57aa4de0574d62914074fd0",
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
    corner = _lock("unitree-g1-stock-mujoco-corner-left")
    assert lock_tool.v2_problems(corner) == []
    hallway = _lock("unitree-g1-stock-mujoco-hallway")
    hallway["plan"].pop("command_speed")
    assert "plan.command_speed must be a number" in lock_tool.v2_problems(hallway)
    corner["plan"]["command_speed"] = True
    assert "plan.command_speed must be a number" in lock_tool.v2_problems(corner)


def test_left_and_right_reuse_world_but_have_distinct_parameters_and_artifacts():
    """World reuse does not alias the two executions or their published artifacts."""
    workflow = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    artifacts = set()
    for stem in (
        "unitree-g1-stock-mujoco-corner",
        "booster-t1-stock-mujoco-corner",
        "unitree-go1-playground-mujoco-corner",
    ):
        left, right = (_lock(f"{stem}-{direction}") for direction in ("left", "right"))
        assert left["world"] == right["world"] == WORLD
        assert left["sources"]["world"] == right["sources"]["world"]
        assert left["sources"]["parameters"] != right["sources"]["parameters"]
        assert left["scenario"] != right["scenario"]
        for direction in ("left", "right"):
            name = f"{stem}-{direction}"
            prefix = workflow[name]["with"]["artifact-prefix"]
            assert prefix not in artifacts
            artifacts.add(prefix)
            assert workflow[name]["with"]["verification-lock"] == f"verifications/{name}.lock.json"
    assert len(artifacts) == 6
