# SPDX-License-Identifier: Apache-2.0
"""Review guards, run in CI by the `locks` job before any verification: every definition
has a current lock, and the thin workflow call passes exactly the locked values. The
workflow is parsed as YAML, so a commented-out or misplaced pin never counts. `uses` must
name the locked verifier commit and pass that definition's lock as `verification-lock`.

Release pins are checked by resolving them again. `tools/lock.py` takes the reporter tag
from `verifications/settings.toml` and the verifier commit from the workflow call, then
resolves commits with git and image digests with ghcr. The committed lock must match that
resolution. Editing a tag, commit or digest in the file fails the diff. A workflow-only
verifier bump names `python3 tools/lock.py --update`. The full check is
`livingproof capability verify lock --check --workflow .github/workflows/verify.yml`."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tomllib

import pytest
import yaml

ROOT = Path(__file__).parents[1]
DEFINITIONS = sorted(
    path for path in (ROOT / "verifications").glob("*.toml") if path.name != "settings.toml"
)
WORKFLOW = ROOT / ".github/workflows/verify.yml"
_spec = importlib.util.spec_from_file_location("lock_tool", ROOT / "tools/lock.py")
lock_tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lock_tool)
PROCEDURE = ".github/workflows/walk-hallway.yml"
VERIFIER_COMMIT = "c27c6376b4adc1e5b1d9567e4d5b210ab1570a4e"


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


def workflow_problems(text: str, definitions=None) -> list[str]:
    """Every job calling walk-hallway.yml must be a definition's job with its exact locked call."""
    jobs = yaml.safe_load(text)["jobs"]
    locks = {p.stem: lock_of(p) for p in (DEFINITIONS if definitions is None else definitions)}
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
    if not same_call(uses, pinned):
        problems.append(lock_tool.UPDATE_HINT)
    return problems


def _pin_deltas(fresh: dict, committed: dict) -> list[str]:
    """Fields a fresh resolution disagrees with. Empty when the committed lock matches."""
    problems = []
    for side in ("verifier", "reporter"):
        got = (committed.get("releases") or {}).get(side) or {}
        resolved = (fresh.get("releases") or {}).get(side) or {}
        if not isinstance(got, dict):
            got = {}
        if not isinstance(resolved, dict):
            resolved = {}
        for key in sorted(set(got) | set(resolved)):
            if got.get(key) != resolved.get(key):
                problems.append(
                    f"releases.{side}.{key} is {got.get(key)!r}, resolved {resolved.get(key)!r}"
                )
    name = "livingproof-evaluator-scenario"
    got = (committed.get("evaluators") or {}).get(name) or {}
    resolved = (fresh.get("evaluators") or {}).get(name) or {}
    if not isinstance(got, dict):
        got = {}
    if not isinstance(resolved, dict):
        resolved = {}
    for key in ("repository", "tag", "commit"):
        if got.get(key) != resolved.get(key):
            problems.append(
                f"evaluators.{name}.{key} is {got.get(key)!r}, resolved {resolved.get(key)!r}"
            )
    got_conf = (committed.get("robot") or {}).get("configurations")
    resolved_conf = (fresh.get("robot") or {}).get("configurations")
    if got_conf != resolved_conf:
        problems.append(f"robot.configurations is {got_conf!r}, resolved {resolved_conf!r}")
    if not problems and fresh != committed:
        problems.append("resolved lock differs from the committed file")
    return problems


def release_drift(root: Path, work: Path) -> list[str]:
    """Why each committed lock is not what ``tools/lock.py`` would write now."""
    problems = []
    for definition in sorted(
        path for path in (root / "verifications").glob("*.toml") if path.name != "settings.toml"
    ):
        try:
            fresh, _notes = lock_tool.generate(definition, work / definition.stem)
        except lock_tool.LockError as exc:
            problems.append(f"{definition.stem}: {exc}")
            continue
        committed_path = definition.with_name(definition.stem + ".lock.json")
        try:
            committed = json.loads(committed_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{definition.stem}: {exc}")
            continue
        deltas = _pin_deltas(fresh, committed)
        for delta in deltas:
            problems.append(f"{definition.stem}: {delta}")
        if any(delta.startswith("releases.verifier.") for delta in deltas):
            problems.append(f"{definition.stem}: {lock_tool.UPDATE_HINT}")
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
    verifier = lock_of(DEFINITIONS[0])["releases"]["verifier"]
    for definition in DEFINITIONS:
        assert lock_of(definition)["releases"]["verifier"]["commit"] == verifier["commit"]
    tools = (ROOT / ".github/requirements/lock-check-git.txt").read_text()
    assert (
        f"capability-verifier @ git+https://github.com/{verifier['repository']}@{verifier['commit']}"
        in tools
    )
    assert verifier["tag"] in tools
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
    assert verifier["tag"] == "v1.18.1"
    assert verifier["commit"] == VERIFIER_COMMIT
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    job = jobs[definition.stem]
    assert job["uses"].endswith("@" + VERIFIER_COMMIT)
    assert job["with"]["verification-lock"] == lock_input(lock)
    assert call_problems(lock, job) == []
    drifted = yaml.safe_load(WORKFLOW.read_text())
    drifted["jobs"][definition.stem]["uses"] = f"{verifier['repository']}/{PROCEDURE}@{'0' * 40}"
    problems = call_problems(lock, drifted["jobs"][definition.stem])
    assert [p.split(" ")[0] for p in problems] == ["uses", "run"]
    assert lock_tool.UPDATE_HINT in problems


def test_committed_locks_match_a_fresh_resolution(tmp_path):
    # Tags come from this repo (settings.toml and the workflow call). Commits and
    # digests are resolved from git and ghcr, then compared to the committed files.
    assert release_drift(ROOT, tmp_path) == []


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
    assert [p.split(" ")[0] for p in problems] == ["uses", "with.ref", "run"]
    assert lock_tool.UPDATE_HINT in problems


# Released capability-verifier tags. A regression moves the workflow to the one that is
# not the locked commit. Neither value is a branch, a pull request, or an unreleased sha.
RELEASED_VERIFIER_COMMITS = {
    "v1.18.1": "c27c6376b4adc1e5b1d9567e4d5b210ab1570a4e",
    "v1.18.2": "1489cd20d29302800171d934734d6165e7c5604d",
}


def _other_released_verifier(current: str) -> tuple[str, str]:
    matches = [
        (tag, commit) for tag, commit in RELEASED_VERIFIER_COMMITS.items() if commit != current
    ]
    assert matches, "need a released verifier commit that is not the live pin"
    return matches[0]


def _copy_verification_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for source in (ROOT / "verifications").iterdir():
        if not source.is_file():
            continue
        dest = root / "verifications" / source.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())
    workflow = root / ".github" / "workflows" / "verify.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_bytes(WORKFLOW.read_bytes())
    requirements = root / ".github" / "requirements"
    requirements.mkdir(parents=True)
    (requirements / "lock-check-git.txt").write_bytes(
        (ROOT / ".github" / "requirements" / "lock-check-git.txt").read_bytes()
    )
    return root


def _original_locks(root: Path) -> dict[str, dict]:
    originals = {}
    for path in (root / "verifications").glob("*.toml"):
        if path.name == "settings.toml":
            continue
        originals[path.name] = json.loads(lock_tool.lock_path(path).read_text())
    return originals


def test_a_workflow_only_verifier_bump_with_stale_locks_is_rejected(tmp_path, monkeypatch):
    locked = lock_of(DEFINITIONS[0])["releases"]["verifier"]["commit"]
    other_tag, other_commit = _other_released_verifier(locked)
    text = WORKFLOW.read_text().replace(locked, other_commit)
    jobs = yaml.safe_load(text)["jobs"]
    for definition in DEFINITIONS:
        problems = call_problems(lock_of(definition), jobs[definition.stem])
        assert any(item.startswith("uses ") for item in problems), problems
        assert lock_tool.UPDATE_COMMAND in "\n".join(problems)
        assert jobs[definition.stem]["uses"].endswith("@" + other_commit)
    root = _copy_verification_repo(tmp_path)
    originals = _original_locks(root)
    digest = "sha256:" + "ab" * 32
    (root / ".github" / "workflows" / "verify.yml").write_text(text)

    def generate(definition, work, **kwargs):
        fresh = json.loads(json.dumps(originals[definition.name]))
        fresh["releases"]["verifier"]["tag"] = other_tag
        fresh["releases"]["verifier"]["commit"] = other_commit
        fresh["releases"]["verifier"]["digest"] = digest
        return fresh, []

    monkeypatch.setattr(lock_tool, "generate", generate)
    problems = release_drift(root, tmp_path / "work")
    for field in ("tag", "commit", "digest"):
        assert any(f"releases.verifier.{field}" in item for item in problems), problems
    assert any(lock_tool.UPDATE_COMMAND in item for item in problems)


def test_a_stale_verifier_digest_is_rejected(tmp_path, monkeypatch):
    root = _copy_verification_repo(tmp_path)
    originals = _original_locks(root)
    path = lock_tool.lock_path(root / "verifications" / DEFINITIONS[0].name)
    lock = json.loads(path.read_text())
    digest = lock["releases"]["verifier"]["digest"]
    flipped = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    lock["releases"]["verifier"]["digest"] = flipped
    path.write_text(json.dumps(lock, indent=2) + "\n")

    def generate(definition, work, **kwargs):
        return json.loads(json.dumps(originals[definition.name])), []

    monkeypatch.setattr(lock_tool, "generate", generate)
    problems = release_drift(root, tmp_path / "work")
    assert any("releases.verifier.digest" in item and flipped in item for item in problems), (
        problems
    )
    assert any(lock_tool.UPDATE_COMMAND in item for item in problems)
    assert not any("evaluators." in item for item in problems)


def test_a_stale_verifier_commit_is_rejected(tmp_path, monkeypatch):
    root = _copy_verification_repo(tmp_path)
    originals = _original_locks(root)
    path = lock_tool.lock_path(root / "verifications" / DEFINITIONS[0].name)
    lock = json.loads(path.read_text())
    current = lock["releases"]["verifier"]["commit"]
    _other_tag, other_commit = _other_released_verifier(current)
    lock["releases"]["verifier"]["commit"] = other_commit
    path.write_text(json.dumps(lock, indent=2) + "\n")

    def generate(definition, work, **kwargs):
        return json.loads(json.dumps(originals[definition.name])), []

    monkeypatch.setattr(lock_tool, "generate", generate)
    problems = release_drift(root, tmp_path / "work")
    assert any("releases.verifier.commit" in item and other_commit in item for item in problems), (
        problems
    )
    assert any(lock_tool.UPDATE_COMMAND in item for item in problems)
    assert not any(item.startswith(DEFINITIONS[0].stem + ": robot.") for item in problems)


def test_update_does_not_change_robot_or_evaluator_pins(tmp_path, monkeypatch, capsys):
    root = _copy_verification_repo(tmp_path)
    before = _original_locks(root)
    current = next(iter(before.values()))["releases"]["verifier"]["commit"]
    other_tag, other_commit = _other_released_verifier(current)
    workflow = root / ".github" / "workflows" / "verify.yml"
    workflow.write_text(workflow.read_text().replace(current, other_commit))
    digest = "sha256:" + "cd" * 32
    git_before = (root / ".github" / "requirements" / "lock-check-git.txt").read_text()
    livingproof = next(
        line for line in git_before.splitlines() if line.startswith("livingproof @ ")
    )

    def tag_of(repository, commit):
        assert repository == "Living-Proof-Labs/capability-verifier"
        assert commit == other_commit
        return other_tag

    def digester(image, tag):
        assert image == lock_tool.VERIFIER_IMAGE
        assert tag == other_tag.removeprefix("v")
        return digest

    monkeypatch.setattr(lock_tool, "repo_root", lambda: root)
    monkeypatch.setattr(lock_tool, "tag_of_commit", tag_of)
    monkeypatch.setattr(lock_tool, "image_digest", digester)
    assert lock_tool.main(["--update"]) == 0
    text = capsys.readouterr().out
    assert "fix:" in text and "chore:" in text
    for name, prior in before.items():
        lock_path = (root / "verifications" / name).with_suffix(".lock.json")
        written = json.loads(lock_path.read_text())
        assert written["robot"] == prior["robot"]
        assert written["evaluators"] == prior["evaluators"]
        assert written["releases"]["specs"] == prior["releases"]["specs"]
        assert written["releases"]["runner"] == prior["releases"]["runner"]
        assert written["releases"]["reporter"] == prior["releases"]["reporter"]
        assert written["plan"] == prior["plan"]
        assert written["world"] == prior["world"]
        assert written["configurations"] == prior["configurations"]
        verifier = written["releases"]["verifier"]
        assert verifier["tag"] == other_tag
        assert verifier["commit"] == other_commit
        assert verifier["digest"] == digest
        assert verifier["repository"] == prior["releases"]["verifier"]["repository"]
        assert verifier["image"] == prior["releases"]["verifier"]["image"]
    git_after = (root / ".github" / "requirements" / "lock-check-git.txt").read_text()
    assert livingproof in git_after
    assert other_commit in git_after
    assert current not in git_after
    assert other_tag in git_after


def test_the_maintainer_command_is_documented():
    readme = (ROOT / "README.md").read_text()
    renovate = json.loads((ROOT / "renovate.json").read_text())
    notes = [
        note for rule in renovate.get("packageRules", []) for note in rule.get("prBodyNotes", [])
    ]
    assert lock_tool.UPDATE_COMMAND in readme
    assert any(lock_tool.UPDATE_COMMAND in note for note in notes)
    assert "fix:" in readme and "chore:" in readme
