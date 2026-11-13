# SPDX-License-Identifier: Apache-2.0
"""Offline review guards, run in CI by the `locks` job before any verification: every
definition has a current lock, and the thin workflow call passes exactly the locked values.
The workflow is parsed as YAML, so a commented-out or misplaced pin never counts. `uses`
and `verification-lock` are derived from the lock's releases: the call must name the locked
verifier commit and pass the lock file whose verifier and reporter the run reads. Nothing
in this check reads a robot repository's own lock. The full check is
`livingproof capability verify lock --check --workflow .github/workflows/verify.yml`."""

import hashlib
import json
from pathlib import Path
import re
import tomllib

import pytest
import yaml

ROOT = Path(__file__).parents[1]
DEFINITIONS = sorted((ROOT / "verifications").glob("*.toml"))
WORKFLOW = ROOT / ".github/workflows/verify.yml"
PROCEDURE = ".github/workflows/walk-hallway.yml"
VERIFIER_COMMIT = "9a8069ade06012995b40bff1234e90ddce77476b"


def lock_of(definition: Path) -> dict:
    return json.loads(definition.with_name(definition.stem + ".lock.json").read_text())


def calls_verifier(uses) -> bool:
    """Any call of walk-hallway.yml: any owner (case-insensitive), a fork or a local copy."""
    return isinstance(uses, str) and uses.split("@", 1)[0].strip().lower().endswith(
        "/" + PROCEDURE.lower()
    )


def same_call(uses, pinned: str) -> bool:
    """Owner and repo compare case-insensitively, the commit exactly."""
    if not isinstance(uses, str):
        return False
    target, _, ref = uses.partition("@")
    want, _, commit = pinned.partition("@")
    return target.lower() == want.lower() and ref == commit


def workflow_problems(text: str) -> list[str]:
    """Every job calling walk-hallway.yml must be a definition's job with its exact locked call."""
    jobs = yaml.safe_load(text)["jobs"]
    locks = {p.stem: lock_of(p) for p in DEFINITIONS}
    problems = [
        f"{n}: no definition"
        for n, j in jobs.items()
        if n not in locks and calls_verifier(j.get("uses"))
    ]
    for name, lock in locks.items():
        problems += [f"{name}: {p}" for p in call_problems(lock, jobs.get(name))]
    return problems


def lock_input(lock: dict) -> str:
    """Path of the 0.2.0 lock the job passes as verification-lock."""
    name = Path(lock["definition"]["path"]).with_suffix(".lock.json")
    return f"verifications/{name}"


def passed_releases(job) -> dict | None:
    """Releases in the lock file the job passes. That file is what the run reads."""
    w = job.get("with") if isinstance(job.get("with"), dict) else {}
    rel = w.get("verification-lock")
    if not isinstance(rel, str) or Path(rel).is_absolute() or ".." in Path(rel).parts:
        return None
    path = ROOT / rel
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    releases = document.get("releases") if isinstance(document, dict) else None
    return releases if isinstance(releases, dict) else None


def call_problems(lock: dict, job) -> list[str]:
    """Why the parsed workflow ``job`` is not exactly the locked call (empty when it is)."""
    if not isinstance(job, dict):
        return ["no such job"]
    v = lock["releases"]["verifier"]
    w = job.get("with") if isinstance(job.get("with"), dict) else {}
    confs = w.get("configurations")
    pinned = f"{v['repository']}/{PROCEDURE}@{v['commit']}"
    uses = job.get("uses")
    want = {
        "uses": (pinned if same_call(uses, pinned) else uses, pinned),
        "with.repository": (w.get("repository"), lock["robot"]["repository"]),
        "with.ref": (w.get("ref"), lock["robot"]["commit"]),
        "with.configurations": (
            json.loads(confs) if isinstance(confs, str) else confs,
            lock["robot"]["configurations"],
        ),
        "with.verification-lock": (w.get("verification-lock"), lock_input(lock)),
    }
    problems = [f"{k} is {got!r}, locked {exp!r}" for k, (got, exp) in want.items() if got != exp]
    # The run reads verifier and reporter from the passed lock file, not from a robot lock.
    # An edited release the call does not pass is a failure.
    releases = passed_releases(job)
    for name in ("verifier", "reporter"):
        got = None if releases is None else releases.get(name)
        exp = lock["releases"][name]
        if got != exp:
            problems.append(f"releases.{name} is {got!r}, locked {exp!r}")
    return problems


def test_there_is_at_least_one_definition():
    assert DEFINITIONS


@pytest.mark.parametrize("definition", DEFINITIONS, ids=lambda p: p.stem)
def test_lock_is_current_for_its_definition(definition):
    lock = lock_of(definition)
    d = tomllib.loads(definition.read_text())
    assert lock["definition"]["sha256"] == hashlib.sha256(definition.read_bytes()).hexdigest()
    assert lock["format"] in {
        "livingproof-verification-lock/0.1.0",
        "livingproof-verification-lock/0.2.0",
    }
    if lock["format"] == "livingproof-verification-lock/0.2.0":
        for entry in lock["configurations"]:
            assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
    assert lock["robot"]["repository"] == d["robot"]["repository"]
    assert lock["robot"]["configurations"] == d["robot"]["configurations"]
    assert len(lock["robot"]["commit"]) == 40


@pytest.mark.parametrize("definition", DEFINITIONS, ids=lambda p: p.stem)
def test_workflow_calls_the_locked_verifier_robot_and_configurations(definition):
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    assert call_problems(lock_of(definition), jobs.get(definition.stem)) == []


def test_every_verifier_call_is_a_locked_definition_job():
    assert workflow_problems(WORKFLOW.read_text()) == []


@pytest.mark.parametrize(
    "uses",
    [
        "living-proof-labs/capability-verifier/.github/workflows/walk-hallway.yml@main",
        "someone-else/capability-verifier/.github/workflows/walk-hallway.yml@main",
        "./.github/workflows/walk-hallway.yml",
    ],
    ids=["lowercase-owner", "fork", "local"],
)
def test_a_second_call_to_any_walk_hallway_is_refused(uses):
    extra = (
        f"  extra:\n    uses: {uses}\n    with: {{repository: x/y, ref: main, "
        "configurations: '[\"stock\"]'}\n"
    )
    assert workflow_problems(WORKFLOW.read_text() + extra) == ["extra: no definition"]


def test_the_pin_matches_the_owner_case_insensitively():
    lock = lock_of(DEFINITIONS[0])
    v = lock["releases"]["verifier"]
    pinned = f"{v['repository']}/{PROCEDURE}@{v['commit']}"
    assert same_call(pinned.lower(), pinned)
    assert not same_call(pinned.split("@")[0] + "@main", pinned)


def test_verification_waits_for_the_lock_guard():
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    assert '/tests/bin/pytest" tests/test_locks.py' in jobs["locks"]["steps"][-1]["run"]
    check = [st.get("run", "") for st in jobs["locks"]["steps"]]
    assert any(
        "verify lock --check" in r and "--workflow .github/workflows/verify.yml" in r for r in check
    )
    installs = [line.strip() for r in check for line in r.splitlines() if "uv pip install" in line]
    git = "uv pip install --no-deps --no-build-isolation \\"
    assert installs and all(
        "uv pip install --require-hashes --no-deps" in line or line.endswith(git)
        for line in installs
    )
    assert sum(line.endswith(git) for line in installs) == 1
    assert any("-r .github/requirements/build.txt" in r for r in check)
    assert any("--require-hashes --no-deps -r .github/requirements/uv.txt" in r for r in check)
    assert any("a wrong livingproof-common hash installed" in r for r in check)
    tools = (ROOT / ".github/requirements/lock-check-git.txt").read_text()
    assert (
        "capability-verifier @ git+https://github.com/Living-Proof-Labs/capability-verifier@9a8069ade06012995b40bff1234e90ddce77476b"
        in tools
    )
    assert "v1.18.0" in tools
    for definition in DEFINITIONS:
        assert jobs[definition.stem]["needs"] == "locks"


def _entries(name):
    text = (ROOT / ".github/requirements" / name).read_text()
    return re.split(
        r"\n(?=\S)",
        "\n".join(
            line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
        ),
    )


@pytest.mark.parametrize("name", ["lock-check.txt", "tests.txt", "uv.txt", "build.txt"])
def test_hashed_ci_requirements_are_all_hashed(name):
    # A git requirement in the same install made uv skip a URL wheel's hash, so the hashed
    # files hold no git requirement and are installed with --require-hashes.
    entries = _entries(name)
    assert entries
    for entry in entries:
        assert "git+" not in entry and "--hash=sha256:" in entry, entry.split("\\")[0]


def test_git_ci_requirements_are_pinned_by_commit():
    entries = _entries("lock-check-git.txt")
    assert entries
    for entry in entries:
        assert re.fullmatch(r"\S+ @ git\+https://\S+@[0-9a-f]{40}", entry), entry


@pytest.mark.parametrize("definition", DEFINITIONS, ids=lambda p: p.stem)
def test_uses_commit_matches_the_locked_verifier(definition):
    lock = lock_of(definition)
    verifier = lock["releases"]["verifier"]
    assert verifier["tag"] == "v1.18.0"
    assert verifier["commit"] == VERIFIER_COMMIT
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    job = jobs[definition.stem]
    assert job["uses"].endswith("@" + VERIFIER_COMMIT)
    assert job["with"]["verification-lock"] == lock_input(lock)
    assert call_problems(lock, job) == []
    drifted = yaml.safe_load(WORKFLOW.read_text())
    drifted["jobs"][definition.stem]["uses"] = f"{verifier['repository']}/{PROCEDURE}@{'0' * 40}"
    problems = call_problems(lock, drifted["jobs"][definition.stem])
    assert [p.split(" ")[0] for p in problems] == ["uses"]


@pytest.mark.parametrize(
    "stem",
    [
        "unitree-g1-playground-mujoco-hallway",
        "unitree-go1-playground-mujoco-hallway",
    ],
)
def test_a_reporter_edit_fails_when_the_call_does_not_follow(stem):
    # Vera P8/P9: editing the reporter to v3.4.0 must fail unless the workflow passes it.
    lock = lock_of(ROOT / f"verifications/{stem}.toml")
    lock["releases"]["reporter"] = {
        **lock["releases"]["reporter"],
        "tag": "v3.4.0",
        "commit": "0" * 40,
    }
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"][stem]
    problems = call_problems(lock, job)
    assert any(p.startswith("releases.reporter") for p in problems)


def test_a_verifier_edit_fails_when_the_call_does_not_follow():
    definition = ROOT / "verifications/unitree-go1-playground-mujoco-hallway.toml"
    lock = lock_of(definition)
    lock["releases"]["verifier"] = {
        **lock["releases"]["verifier"],
        "tag": "v3.4.0",
        "commit": "0" * 40,
    }
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"][definition.stem]
    problems = call_problems(lock, job)
    assert any(p.startswith("uses") for p in problems)
    assert any(p.startswith("releases.verifier") for p in problems)


def test_a_commented_out_pin_does_not_count():
    # Regression: the pinned lines as comments next to drifted values used to pass.
    definition = DEFINITIONS[0]
    lock = lock_of(definition)
    v = lock["releases"]["verifier"]
    text = (
        "jobs:\n"
        f"  {definition.stem}:\n"
        f"    uses: {v['repository']}/{PROCEDURE}@main\n"
        f"    # uses: {v['repository']}/{PROCEDURE}@{v['commit']}\n"
        "    with:\n"
        f"      repository: {lock['robot']['repository']}\n"
        "      ref: main\n"
        f"      # ref: {lock['robot']['commit']}\n"
        f"      configurations: '{json.dumps(lock['robot']['configurations'])}'\n"
        f"      verification-lock: {lock_input(lock)}\n"
    )
    problems = call_problems(lock, yaml.safe_load(text)["jobs"][definition.stem])
    assert [p.split(" ")[0] for p in problems] == ["uses", "with.ref"]
