# SPDX-License-Identifier: Apache-2.0
"""Review guards, run in CI by the `locks` job before any verification: every definition
has a current lock, and the thin workflow call passes exactly the locked values. The
workflow is parsed as YAML, so a commented-out or misplaced pin never counts. `uses` must
name the locked verifier commit, its `# vX.Y.Z` comment must be the locked tag, and the job
must pass that definition's lock as `verification-lock`.

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
    got_robot = committed.get("robot") if isinstance(committed.get("robot"), dict) else {}
    resolved_robot = fresh.get("robot") if isinstance(fresh.get("robot"), dict) else {}
    for key in ("repository", "ref", "commit", "adapter"):
        if got_robot.get(key) != resolved_robot.get(key):
            problems.append(
                f"robot.{key} is {got_robot.get(key)!r}, resolved {resolved_robot.get(key)!r}"
            )
    got_conf = got_robot.get("configurations")
    resolved_conf = resolved_robot.get("configurations")
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


def uses_tag_comment(text: str, job: str) -> str | None:
    """The ``# vX.Y.Z`` comment on ``job``'s ``uses`` line. A later comment does not count."""
    prefix = f"  {job}:"
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.split("#", 1)[0].rstrip() == prefix:
            start = index + 1
            break
    if start is None:
        return None
    for line in lines[start:]:
        code, separator, comment = line.partition("#")
        if not code.strip():
            continue
        if code.startswith("  ") and not code.startswith("   ") and code.rstrip().endswith(":"):
            return None
        if code.strip().startswith("uses:") and separator:
            tag = comment.strip()
            return tag if lock_tool.TAG.fullmatch(tag) else None
    return None


def assert_uses_matches_locked_verifier(text: str, lock: dict, job_name: str) -> None:
    """``uses`` commit equals the locked commit, and its ``# vX.Y.Z`` comment is the locked tag."""
    job = yaml.safe_load(text)["jobs"][job_name]
    verifier = lock["releases"]["verifier"]
    assert job["uses"].endswith("@" + verifier["commit"])
    assert uses_tag_comment(text, job_name) == verifier["tag"]
    assert job["with"]["verification-lock"] == lock_input(lock)
    assert call_problems(lock, job) == []


@pytest.mark.parametrize("definition", DEFINITIONS, ids=lambda p: p.stem)
def test_uses_commit_matches_the_locked_verifier(definition):
    lock = lock_of(definition)
    assert_uses_matches_locked_verifier(WORKFLOW.read_text(), lock, definition.stem)
    verifier = lock["releases"]["verifier"]
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


# Released capability-verifier tags. A regression moves the workflow to one that is
# not the locked commit. None of these is a branch, a pull request, or an unreleased sha.
RELEASED_VERIFIER_COMMITS = {
    "v1.18.1": "c27c6376b4adc1e5b1d9567e4d5b210ab1570a4e",
    "v1.18.2": "1489cd20d29302800171d934734d6165e7c5604d",
    "v1.18.3": "8b303a672c003b82a90178364e7f107a58eaedac",
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
    (requirements / "lock-check.in").write_bytes(
        (ROOT / ".github" / "requirements" / "lock-check.in").read_bytes()
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


_FAKE_EVALUATOR_REPOSITORY = "Example-Org/livingproof-evaluator-scenario"
_FAKE_EVALUATOR_TAG = "v9.9.9"
_FAKE_EVALUATOR_COMMIT = "ab" * 20
_FAKE_EVALUATOR_PYPROJECT = (
    "dependencies = [\n"
    '  "livingproof-evaluator-scenario @ '
    f"git+https://github.com/{_FAKE_EVALUATOR_REPOSITORY}.git@{_FAKE_EVALUATOR_TAG}"
    '",\n'
    "]\n"
)


def _declared_evaluator_pyproject(repository: str, tag: str) -> str:
    return (
        "dependencies = [\n"
        '  "livingproof-evaluator-scenario @ '
        f'git+https://github.com/{repository}.git@{tag}",\n'
        "]\n"
    )


def test_update_moves_the_evaluator_only_to_what_the_verifier_release_declares(
    tmp_path, monkeypatch, capsys
):
    """The evaluator moves only to what the new verifier release declares."""
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
    old_evaluator_commit = next(iter(before.values()))["evaluators"][
        "livingproof-evaluator-scenario"
    ]["commit"]
    extra_name = next(iter(before))
    extra_path = (root / "verifications" / extra_name).with_suffix(".lock.json")
    extra_lock = json.loads(extra_path.read_text())
    kept = {"repository": "Org/kept", "tag": "v1.2.3", "commit": "c" * 40}
    extra_lock["evaluators"]["kept-evaluator"] = kept
    extra_path.write_text(json.dumps(extra_lock, indent=2) + "\n")
    requirements_in = (root / ".github" / "requirements" / "lock-check.in").read_text()
    livingproof_in = next(
        line for line in requirements_in.splitlines() if line.startswith("livingproof @ ")
    )

    def tag_of(repository, commit):
        assert repository == "Living-Proof-Labs/capability-verifier"
        assert commit == other_commit
        return other_tag

    def digester(image, tag):
        assert image == lock_tool.VERIFIER_IMAGE
        assert tag == other_tag.removeprefix("v")
        return digest

    def pyproject(repository, commit, work):
        assert repository == "Living-Proof-Labs/capability-verifier"
        assert commit == other_commit
        return _FAKE_EVALUATOR_PYPROJECT

    def resolve(repository, tag):
        assert repository == _FAKE_EVALUATOR_REPOSITORY
        assert tag == _FAKE_EVALUATOR_TAG
        return _FAKE_EVALUATOR_COMMIT

    monkeypatch.setattr(lock_tool, "repo_root", lambda: root)
    monkeypatch.setattr(lock_tool, "tag_of_commit", tag_of)
    monkeypatch.setattr(lock_tool, "image_digest", digester)
    monkeypatch.setattr(lock_tool, "_verifier_pyproject", pyproject)
    monkeypatch.setattr(lock_tool, "resolve_tag", resolve)
    assert lock_tool.main(["--update"]) == 0
    text = capsys.readouterr().out
    assert "fix:" in text and "chore:" in text
    assert _FAKE_EVALUATOR_TAG in text
    for name, prior in before.items():
        lock_path = (root / "verifications" / name).with_suffix(".lock.json")
        written = json.loads(lock_path.read_text())
        assert written["robot"] == prior["robot"]
        evaluator = written["evaluators"]["livingproof-evaluator-scenario"]
        assert evaluator == {
            "repository": _FAKE_EVALUATOR_REPOSITORY,
            "tag": _FAKE_EVALUATOR_TAG,
            "commit": _FAKE_EVALUATOR_COMMIT,
        }
        assert lock_tool.TAG.fullmatch(evaluator["tag"])
        assert (
            written["evaluators"]["livingproof-evaluator-scenario"]
            != prior["evaluators"]["livingproof-evaluator-scenario"]
        )
        if name == extra_name:
            assert written["evaluators"]["kept-evaluator"] == kept
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
    assert _FAKE_EVALUATOR_COMMIT in git_after
    assert old_evaluator_commit not in git_after
    assert f"{_FAKE_EVALUATOR_REPOSITORY}.git@{_FAKE_EVALUATOR_COMMIT}" in git_after
    requirements_after = (root / ".github" / "requirements" / "lock-check.in").read_text()
    assert livingproof_in in requirements_after
    assert other_commit in requirements_after
    assert current not in requirements_after
    assert f"capability-verifier {other_tag}" in requirements_after
    assert "livingproof v1.4.9" in requirements_after


def test_update_records_a_full_evaluator_tag_when_the_declaration_is_not_one(tmp_path, monkeypatch):
    """A branch or partial version in the release is not recorded; the pin stays vX.Y.Z."""
    root = _copy_verification_repo(tmp_path)
    before = _original_locks(root)
    current = next(iter(before.values()))["releases"]["verifier"]["commit"]
    other_tag, other_commit = _other_released_verifier(current)
    workflow = root / ".github" / "workflows" / "verify.yml"
    workflow.write_text(workflow.read_text().replace(current, other_commit))
    declared_commit = "d" * 40

    monkeypatch.setattr(lock_tool, "tag_of_commit", lambda repository, commit: other_tag)
    monkeypatch.setattr(lock_tool, "image_digest", lambda image, tag: "sha256:" + "ef" * 32)
    monkeypatch.setattr(
        lock_tool,
        "_verifier_pyproject",
        lambda repository, commit, work: _declared_evaluator_pyproject(
            "Living-Proof-Labs/livingproof-evaluator-scenario", "main"
        ),
    )

    def resolve(repository, tag):
        assert tag == "v1.0.0"
        assert lock_tool.TAG.fullmatch(tag)
        return declared_commit

    monkeypatch.setattr(lock_tool, "resolve_tag", resolve)
    assert lock_tool.update_locks(root)
    for name in before:
        written = json.loads((root / "verifications" / name).with_suffix(".lock.json").read_text())
        evaluator = written["evaluators"]["livingproof-evaluator-scenario"]
        assert evaluator["tag"] == "v1.0.0"
        assert evaluator["commit"] == declared_commit
        assert evaluator["tag"] != "main"
        assert lock_tool.TAG.fullmatch(evaluator["tag"])


def _retarget_uses(text: str, old_commit: str, new_commit: str, old_tag: str, new_tag: str) -> str:
    """Move each walk-hallway ``uses`` commit and its ``# vX.Y.Z`` comment. Other lines stay."""
    rewritten = []
    for line in text.splitlines(keepends=True):
        code = line.split("#", 1)[0]
        if "uses:" in code and old_commit in code:
            line = line.replace(old_commit, new_commit, 1).replace(
                f"# {old_tag}", f"# {new_tag}", 1
            )
        rewritten.append(line)
    return "".join(rewritten)


def test_a_released_tag_bump_then_update_passes_the_lock_checks(tmp_path):
    """Moving the workflow pin to another released tag, then ``--update``, satisfies the checks.

    Resolution is the real git tag lookup and the ghcr digest. The lock module's uses,
    comment, and fresh-resolution checks then pass on that tree with no further edits.
    """
    root = _copy_verification_repo(tmp_path)
    before = _original_locks(root)
    current = next(iter(before.values()))["releases"]["verifier"]
    other_tag, other_commit = _other_released_verifier(current["commit"])
    workflow = root / ".github" / "workflows" / "verify.yml"
    workflow.write_text(
        _retarget_uses(
            workflow.read_text(), current["commit"], other_commit, current["tag"], other_tag
        )
    )
    assert lock_tool.update_locks(root)
    text = workflow.read_text()
    for name, prior in before.items():
        definition = root / "verifications" / name
        lock = json.loads(lock_tool.lock_path(definition).read_text())
        assert_uses_matches_locked_verifier(text, lock, definition.stem)
        assert lock["robot"] == prior["robot"]
        evaluator = lock["evaluators"]["livingproof-evaluator-scenario"]
        assert lock_tool.TAG.fullmatch(evaluator["tag"])
        assert lock_tool.SHA.fullmatch(evaluator["commit"])
        assert lock_tool.REPO.fullmatch(evaluator["repository"])
        assert lock["releases"]["specs"] == prior["releases"]["specs"]
        assert lock["releases"]["runner"] == prior["releases"]["runner"]
        assert lock["releases"]["reporter"] == prior["releases"]["reporter"]
        verifier = lock["releases"]["verifier"]
        assert verifier["tag"] == other_tag
        assert verifier["commit"] == other_commit
        assert lock_tool.DIGEST.fullmatch(verifier["digest"])
    tools = (root / ".github" / "requirements" / "lock-check-git.txt").read_text()
    assert other_commit in tools
    assert current["commit"] not in tools
    assert other_tag in tools
    assert release_drift(root, tmp_path / "work") == []


def _json_member(text: str, key: str) -> str:
    """The raw ``"key": { ... }`` text, including nested objects."""
    marker = f'"{key}": '
    start = text.index(marker)
    opener = start + len(marker)
    if text[opener] != "{":
        raise AssertionError(key)
    depth = 0
    for index in range(opener, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(key)


def _flip_hex(value: str) -> str:
    return value[:-1] + ("0" if value[-1] != "0" else "1")


def test_update_leaves_robot_pin_bytes_unchanged(tmp_path, monkeypatch):
    """``--update`` rewrites the verifier and leaves the robot pin bytes untouched."""
    root = _copy_verification_repo(tmp_path)
    before_text = {
        path.name: path.read_text() for path in (root / "verifications").glob("*.lock.json")
    }
    current = json.loads(next(iter(before_text.values())))["releases"]["verifier"]
    other_tag, other_commit = _other_released_verifier(current["commit"])
    workflow = root / ".github" / "workflows" / "verify.yml"
    workflow.write_text(workflow.read_text().replace(current["commit"], other_commit))
    digest = "sha256:" + "ab" * 32
    robot_before = {name: _json_member(text, "robot") for name, text in before_text.items()}

    monkeypatch.setattr(lock_tool, "tag_of_commit", lambda repository, commit: other_tag)
    monkeypatch.setattr(lock_tool, "image_digest", lambda image, tag: digest)
    monkeypatch.setattr(
        lock_tool,
        "_verifier_pyproject",
        lambda repository, commit, work: _FAKE_EVALUATOR_PYPROJECT,
    )
    monkeypatch.setattr(
        lock_tool,
        "resolve_tag",
        lambda repository, tag: _FAKE_EVALUATOR_COMMIT,
    )
    assert lock_tool.update_locks(root)
    evaluator = "livingproof-evaluator-scenario"
    for name, prior in before_text.items():
        after = (root / "verifications" / name).read_text()
        assert _json_member(after, "robot") == robot_before[name]
        assert _json_member(after, "robot") == _json_member(prior, "robot")
        assert _json_member(after, "evaluators") != _json_member(prior, "evaluators")
        written = json.loads(after)
        source = json.loads(prior)
        for field in ("repository", "ref", "commit", "adapter"):
            assert written["robot"][field] == source["robot"][field]
        assert written["evaluators"][evaluator] == {
            "repository": _FAKE_EVALUATOR_REPOSITORY,
            "tag": _FAKE_EVALUATOR_TAG,
            "commit": _FAKE_EVALUATOR_COMMIT,
        }
        assert lock_tool.TAG.fullmatch(written["evaluators"][evaluator]["tag"])
        assert written["releases"]["verifier"]["commit"] == other_commit
        assert written["releases"]["verifier"]["digest"] == digest


def test_the_committed_evaluator_pin_is_a_full_release_tag():
    """Every lock's scenario evaluator pin is a full vX.Y.Z tag, not a branch or a prefix."""
    for definition in DEFINITIONS:
        pin = lock_of(definition)["evaluators"]["livingproof-evaluator-scenario"]
        assert lock_tool.TAG.fullmatch(pin["tag"])
        assert lock_tool.SHA.fullmatch(pin["commit"])
        assert lock_tool.REPO.fullmatch(pin["repository"])


_HALLWAY_REF = re.compile(
    r"ghcr\.io/living-proof-labs/capability-verifier-hallway"
    r"(?::(?P<tag>\d+\.\d+\.\d+)(?:@(?P<digest>sha256:[0-9a-f]{64}))?)?"
)


def test_walk_hallway_pin_locks_and_hallway_image_agree():
    """The uses pin, the four locks' verifier tag and commit, and the hallway image agree."""
    text = WORKFLOW.read_text()
    verifiers = []
    for definition in DEFINITIONS:
        lock = lock_of(definition)
        assert_uses_matches_locked_verifier(text, lock, definition.stem)
        verifiers.append(lock["releases"]["verifier"])
    assert (
        len({(item["tag"], item["commit"], item["digest"], item["image"]) for item in verifiers})
        == 1
    )
    verifier = verifiers[0]
    assert lock_tool.TAG.fullmatch(verifier["tag"])
    assert lock_tool.SHA.fullmatch(verifier["commit"])
    image_tag = verifier["tag"].removeprefix("v")
    pinned = f"{verifier['image']}:{image_tag}@{verifier['digest']}"
    devcontainer = json.loads((ROOT / ".devcontainer" / "devcontainer.json").read_text())
    assert devcontainer["image"] == pinned
    blobs = [
        (ROOT / "README.md").read_text(),
        (ROOT / ".devcontainer" / "devcontainer.json").read_text(),
    ]
    refs = [match.groupdict() for blob in blobs for match in _HALLWAY_REF.finditer(blob)]
    assert refs
    assert {ref["tag"] for ref in refs if ref["tag"]} == {image_tag}
    assert {ref["digest"] for ref in refs if ref["digest"]} == {verifier["digest"]}


def test_an_edited_robot_commit_is_rejected(tmp_path, monkeypatch):
    root = _copy_verification_repo(tmp_path)
    originals = _original_locks(root)
    path = lock_tool.lock_path(root / "verifications" / DEFINITIONS[0].name)
    lock = json.loads(path.read_text())
    flipped = _flip_hex(lock["robot"]["commit"])
    lock["robot"]["commit"] = flipped
    path.write_text(json.dumps(lock, indent=2) + "\n")

    def generate(definition, work, **kwargs):
        return json.loads(json.dumps(originals[definition.name])), []

    monkeypatch.setattr(lock_tool, "generate", generate)
    problems = release_drift(root, tmp_path / "work")
    assert any("robot.commit" in item and flipped in item for item in problems), problems


def test_an_edited_evaluator_commit_is_rejected(tmp_path, monkeypatch):
    root = _copy_verification_repo(tmp_path)
    originals = _original_locks(root)
    path = lock_tool.lock_path(root / "verifications" / DEFINITIONS[0].name)
    lock = json.loads(path.read_text())
    name = "livingproof-evaluator-scenario"
    flipped = _flip_hex(lock["evaluators"][name]["commit"])
    lock["evaluators"][name]["commit"] = flipped
    path.write_text(json.dumps(lock, indent=2) + "\n")

    def generate(definition, work, **kwargs):
        return json.loads(json.dumps(originals[definition.name])), []

    monkeypatch.setattr(lock_tool, "generate", generate)
    problems = release_drift(root, tmp_path / "work")
    assert any(f"evaluators.{name}.commit" in item and flipped in item for item in problems), (
        problems
    )


def test_the_lock_check_step_names_the_update_command_when_it_fails():
    """The locks job's livingproof check fails before pytest, so that step names the command."""
    text = WORKFLOW.read_text()
    step = next(
        item
        for item in yaml.safe_load(text)["jobs"]["locks"]["steps"]
        if item.get("name") == "livingproof capability verify lock --check --workflow"
    )
    script = step["run"]
    _kept, separator, failure = script.partition("||")
    assert separator == "||"
    assert lock_tool.UPDATE_COMMAND in failure
    assert "::error::" in failure
    assert "exit 1" in failure
    assert "capability verify lock --check" in script


def test_the_maintainer_command_is_documented():
    readme = (ROOT / "README.md").read_text()
    renovate = json.loads((ROOT / "renovate.json").read_text())
    notes = [
        note for rule in renovate.get("packageRules", []) for note in rule.get("prBodyNotes", [])
    ]
    assert lock_tool.UPDATE_COMMAND in readme
    assert any(lock_tool.UPDATE_COMMAND in note for note in notes)
    assert "fix:" in readme and "chore:" in readme
