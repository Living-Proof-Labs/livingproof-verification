# SPDX-License-Identifier: Apache-2.0
"""0.2.0 lock generator: owned fields, a readable 0.1.0 lock, and a thin robot."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import urllib.request

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("lock_tool", ROOT / "tools/lock.py")
lock_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock_tool)

EVALUATOR_COMMIT = "e" * 40
VERIFIER_WITH_EVALUATOR = (
    "dependencies = [\n"
    '  "livingproof-evaluator-scenario @ git+https://github.com/Living-Proof-Labs/'
    'livingproof-evaluator-scenario.git@v1.2.3",\n'
    "]\n"
)
VERIFIER_WITHOUT_EVALUATOR = 'dependencies = ["pyyaml>=6.0"]\n'


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init(path: Path) -> None:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    # The generator fetches a verifier by commit sha. Local git refuses that unless asked.
    _git(path, "config", "uploadpack.allowReachableSHA1InWant", "true")


def _commit(path: Path) -> str:
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    return _git_out(path, "rev-parse", "HEAD")


def _git_out(path: Path, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def _specs(base: Path, *, tag: str = "v2.0.2", parameters: str | None = "{}\n") -> str:
    """A local Living-Proof-Labs/example specs pin generate() can hash."""
    repo = base / "Living-Proof-Labs" / "example.git"
    if not (repo / ".git").exists() and not (repo / "HEAD").exists():
        _init(repo)
        files = {"specs/scenarios/walk-hallway/description.json": "{}\n"}
        if parameters is not None:
            files["specs/scenarios/walk-hallway/parameters.json"] = parameters
        for rel, body in files.items():
            dest = repo / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body)
        return _commit(repo)
    return _git_out(repo, "rev-parse", "HEAD")


def _pin(commit: str, *, image: str | None = None) -> dict:
    ref = {
        "repository": "Living-Proof-Labs/example",
        "tag": "v1.2.3",
        "commit": commit,
    }
    if image is not None:
        ref["image"] = image
        ref["digest"] = "sha256:" + "ab" * 32
    return ref


def _releases() -> dict:
    return {
        "specs": _pin("a" * 40),
        "runner": _pin("b" * 40),
        "verifier": _pin("c" * 40, image="ghcr.io/living-proof-labs/capability-verifier-hallway"),
        "reporter": _pin("d" * 40, image="ghcr.io/living-proof-labs/livingproof-capability-report"),
    }


def _configuration(name: str, version: str) -> str:
    return json.dumps(
        {"format": "livingproof-configuration-profile/0.1", "name": name, "version": version}
    )


def _robot(base: Path, *, publish_lock: bool = True, sha: str | None = None) -> Path:
    repo = base / "Org" / "robot.git"
    specs_commit = _specs(base)
    _init(repo)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "fixture-robot"\nversion = "0.1.0"\n\n'
        '[tool.livingproof]\nadapter = "lpl_g1.adapter:ROBOT"\n'
    )
    (repo / "robot.json").write_text(
        json.dumps(
            {
                "format": "livingproof-robot-profile/0.1",
                "configurations": [
                    {"name": "stock", "path": "configurations/stock.json"},
                    {"name": "playground", "path": "configurations/playground.json"},
                ],
            }
        )
    )
    configurations = repo / "configurations"
    configurations.mkdir()
    stock = configurations / "stock.json"
    playground = configurations / "playground.json"
    stock.write_text(_configuration("stock", "1.1.0"))
    playground.write_text(_configuration("playground", "1.0.0"))
    if publish_lock:
        robot_lock = {
            "format": "livingproof-verification-lock/1.0.0",
            "scenario": "walk-hallway@0.1.0",
            "releases": {**_releases(), "specs": _pin(specs_commit)},
            "world": {"id": "hallway-open", "version": "0.1.0"},
            "configurations": [
                {
                    "name": "stock",
                    "version": "1.1.0",
                    "path": "configurations/stock.json",
                    "sha256": sha
                    if sha is not None
                    else hashlib.sha256(stock.read_bytes()).hexdigest(),
                },
                {
                    "name": "playground",
                    "version": "1.0.0",
                    "path": "configurations/playground.json",
                    "sha256": hashlib.sha256(playground.read_bytes()).hexdigest(),
                },
            ],
            "plan": {"trials": {"count": 3}, "command_speed": 0.5},
        }
        (repo / "verification.lock.json").write_text(json.dumps(robot_lock))
    _commit(repo)
    return repo


def _definition(tmp_path: Path, **robot) -> Path:
    values = {"repository": "Org/robot", "ref": "main", "configurations": ["stock"], **robot}
    path = tmp_path / "proj" / "g1.toml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        f'format = "{lock_tool.DEFINITION_FORMAT}"\n'
        'id = "g1-stock"\n'
        'procedure = "walk-hallway"\n'
        "[robot]\n"
        f'repository = "{values["repository"]}"\n'
        f'ref = "{values["ref"]}"\n'
        f"configurations = {json.dumps(values['configurations'])}\n"
    )
    return path


def _use(monkeypatch, base: Path) -> None:
    monkeypatch.setenv("LIVINGPROOF_GIT_BASE", f"file://{base}")


def _resolve(repository: str, tag: str) -> str:
    assert tag.startswith("v")
    return EVALUATOR_COMMIT


def _digest(image: str, tag: str) -> str:
    return "sha256:" + "ab" * 32


def _generate(tmp_path, definition, **kwargs):
    if not (definition.parent / "settings.toml").exists():
        _write_settings(definition, tag="v1.2.3")
    kwargs.setdefault("resolve", _resolve)
    kwargs.setdefault("verifier_pyproject", VERIFIER_WITH_EVALUATOR)
    kwargs.setdefault("digester", _digest)
    return lock_tool.generate(definition, tmp_path / "work", **kwargs)


def test_lock_writes_every_0_2_0_field(tmp_path, monkeypatch):
    base = tmp_path / "git"
    repo = _robot(base)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    lock, notes = _generate(tmp_path, definition)
    head = _git_out(repo, "rev-parse", "HEAD")
    stock = (repo / "configurations" / "stock.json").read_bytes()
    assert lock["format"] == lock_tool.FORMAT_V2
    assert lock["definition"]["sha256"] == hashlib.sha256(definition.read_bytes()).hexdigest()
    assert lock["procedure"] == "walk-hallway"
    assert lock["scenario"] == "walk-hallway@0.1.0"
    assert lock["robot"]["repository"] == "Org/robot"
    assert lock["robot"]["commit"] == head
    assert lock["robot"]["configurations"] == ["stock"]
    assert lock["robot"]["adapter"] == "lpl_g1.adapter:ROBOT"
    assert set(lock["releases"]) >= {"specs", "runner", "verifier", "reporter"}
    assert lock["releases"]["reporter"]["digest"].startswith("sha256:")
    assert lock["plan"]["command_speed"] == 0.5
    assert lock["world"]["id"] == "hallway-open"
    assert lock["world"]["version"] == "0.1.0"
    by_name = {entry["name"]: entry for entry in lock["configurations"]}
    assert by_name["stock"]["sha256"] == hashlib.sha256(stock).hexdigest()
    assert by_name["stock"]["version"] == "1.1.0"
    assert by_name["playground"]["path"] == "configurations/playground.json"
    evaluator = lock["evaluators"]["livingproof-evaluator-scenario"]
    assert evaluator == {
        "repository": "Living-Proof-Labs/livingproof-evaluator-scenario",
        "tag": "v1.2.3",
        "commit": EVALUATOR_COMMIT,
    }
    assert notes == []
    assert lock_tool.v2_problems(lock) == []


def test_an_existing_0_1_0_lock_is_readable(tmp_path, monkeypatch):
    base = tmp_path / "git"
    _robot(base)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    existing = lock_tool.lock_path(definition)
    existing.write_text(
        json.dumps(
            {
                "format": lock_tool.FORMAT_V1,
                "definition": {"sha256": "ab"},
                "robot": {"lock": {"path": "verification.lock.json"}},
            }
        )
    )
    loaded = lock_tool.read_lock(existing)
    assert loaded["format"] == lock_tool.FORMAT_V1
    lock, _notes = _generate(tmp_path, definition)
    assert lock["format"] == lock_tool.FORMAT_V2
    assert lock["scenario"] == "walk-hallway@0.1.0"


def test_read_lock_accepts_0_2_0_and_rejects_anything_else(tmp_path):
    path = tmp_path / "demo.lock.json"
    path.write_text(json.dumps({"format": lock_tool.FORMAT_V2, "scenario": "walk-hallway@0.1.0"}))
    assert lock_tool.read_lock(path)["format"] == lock_tool.FORMAT_V2
    path.write_text("{not json")
    with pytest.raises(lock_tool.LockError, match="not valid JSON"):
        lock_tool.read_lock(path)
    path.write_text(json.dumps({"format": "livingproof-verification-lock/1.0.0"}))
    with pytest.raises(lock_tool.LockError, match="format is not"):
        lock_tool.read_lock(path)


def test_a_published_configuration_sha_that_does_not_match_is_an_error(tmp_path, monkeypatch):
    base = tmp_path / "git"
    _robot(base, sha="0" * 64)
    _use(monkeypatch, base)
    with pytest.raises(lock_tool.LockError, match="sha256 is"):
        _generate(tmp_path, _definition(tmp_path))


def test_a_configuration_path_that_leaves_the_robot_is_an_error(tmp_path, monkeypatch):
    base = tmp_path / "git"
    repo = _robot(base)
    robot_json = repo / "robot.json"
    profile = json.loads(robot_json.read_text())
    profile["configurations"][0]["path"] = "../secret"
    robot_json.write_text(json.dumps(profile))
    _commit(repo)
    _use(monkeypatch, base)
    with pytest.raises(lock_tool.LockError, match="must stay inside the robot"):
        _generate(tmp_path, _definition(tmp_path))


def test_an_unknown_configuration_is_refused(tmp_path, monkeypatch):
    base = tmp_path / "git"
    _robot(base)
    _use(monkeypatch, base)
    with pytest.raises(lock_tool.LockError, match="are not in configurations"):
        _generate(tmp_path, _definition(tmp_path, configurations=["rough"]))


def test_a_verifier_that_does_not_declare_the_evaluator_records_v1_0_0(tmp_path, monkeypatch):
    base = tmp_path / "git"
    _robot(base)
    _use(monkeypatch, base)
    seen = {}

    def resolve(repository, tag):
        seen["repository"] = repository
        seen["tag"] = tag
        return "f" * 40

    lock, notes = _generate(
        tmp_path,
        _definition(tmp_path),
        resolve=resolve,
        verifier_pyproject=VERIFIER_WITHOUT_EVALUATOR,
    )
    assert seen == {
        "repository": "Living-Proof-Labs/livingproof-evaluator-scenario",
        "tag": "v1.0.0",
    }
    assert lock["evaluators"]["livingproof-evaluator-scenario"]["tag"] == "v1.0.0"
    assert any("does not declare it" in note for note in notes)


def test_the_evaluator_commit_is_resolved_from_the_verifier_checkout(tmp_path, monkeypatch):
    base = tmp_path / "git"
    _robot(base)
    verifier = base / "Living-Proof-Labs" / "capability-verifier.git"
    _init(verifier)
    (verifier / "pyproject.toml").write_text(VERIFIER_WITH_EVALUATOR)
    commit = _commit(verifier)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    # Point the robot lock's verifier pin at this local commit, then regenerate.
    repo = base / "Org" / "robot.git"
    robot_lock = json.loads((repo / "verification.lock.json").read_text())
    robot_lock["releases"]["verifier"]["repository"] = "Living-Proof-Labs/capability-verifier"
    robot_lock["releases"]["verifier"]["commit"] = commit
    (repo / "verification.lock.json").write_text(json.dumps(robot_lock))
    _commit(repo)

    def resolve(repository, tag):
        if repository.endswith("capability-report"):
            return "d" * 40
        assert repository == "Living-Proof-Labs/livingproof-evaluator-scenario"
        assert tag == "v1.2.3"
        return "a" * 40

    _write_settings(definition)
    lock, notes = lock_tool.generate(
        definition,
        tmp_path / "work",
        resolve=resolve,
        digester=_digest,
    )
    assert notes == []
    assert lock["evaluators"]["livingproof-evaluator-scenario"]["commit"] == "a" * 40


def _v2(definition: Path, repo: Path) -> dict:
    stock = (repo / "configurations" / "stock.json").read_bytes()
    playground = (repo / "configurations" / "playground.json").read_bytes()
    specs_commit = _specs(repo.parent.parent)
    return {
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
        "releases": {**_releases(), "specs": _pin(specs_commit)},
        "world": {"id": "hallway-open", "version": "0.1.0"},
        "configurations": [
            {
                "name": "stock",
                "version": "1.1.0",
                "path": "configurations/stock.json",
                "sha256": hashlib.sha256(stock).hexdigest(),
            },
            {
                "name": "playground",
                "version": "1.0.0",
                "path": "configurations/playground.json",
                "sha256": hashlib.sha256(playground).hexdigest(),
            },
        ],
        "plan": {"trials": {"count": 3}, "command_speed": 0.5},
        "evaluators": {
            "livingproof-evaluator-scenario": {
                "repository": "Living-Proof-Labs/livingproof-evaluator-scenario",
                "tag": "v1.0.0",
                "commit": "f" * 40,
            }
        },
    }


def _write_definition(tmp_path: Path, **robot) -> Path:
    """A definition under ``verifications/``, so a workflow beside the project is found."""
    values = {"repository": "Org/robot", "ref": "main", "configurations": ["stock"], **robot}
    path = tmp_path / "proj" / "verifications" / "g1-stock.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'format = "{lock_tool.DEFINITION_FORMAT}"\n'
        'id = "g1-stock"\n'
        'procedure = "walk-hallway"\n'
        "[robot]\n"
        f'repository = "{values["repository"]}"\n'
        f'ref = "{values["ref"]}"\n'
        f"configurations = {json.dumps(values['configurations'])}\n"
    )
    return path


def _write_settings(definition: Path, *, tag: str = "v3.5.0") -> Path:
    """``settings.toml`` beside the definition, naming the reporter release."""
    path = definition.parent / "settings.toml"
    path.write_text(
        "[reporter]\n"
        'repository = "Living-Proof-Labs/livingproof-capability-report"\n'
        f'tag = "{tag}"\n'
        'image = "ghcr.io/living-proof-labs/livingproof-capability-report"\n'
    )
    return path


def _write_workflow(tmp_path: Path, commit: str, *, tag: str = "v9.9.9") -> Path:
    """The walk-hallway call for the ``g1-stock`` job, at the project root."""
    workflow = tmp_path / "proj" / ".github" / "workflows" / "verify.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "jobs:\n"
        "  g1-stock:\n"
        "    uses: Living-Proof-Labs/capability-verifier/.github/workflows/"
        f"walk-hallway.yml@{commit}  # {tag}\n"
    )
    return workflow


def test_a_thin_robot_keeps_owned_fields_and_refreshes_the_tree(tmp_path, monkeypatch, capsys):
    """Scenario, plan, world, specs and runner stay. Reporter and evaluator do not."""
    base = tmp_path / "git"
    repo = _robot(base, publish_lock=False)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    existing = _v2(definition, repo)
    kept = json.loads(json.dumps(existing["releases"]))
    lock_tool.lock_path(definition).write_text(json.dumps(existing))
    _write_settings(definition, tag="v3.5.0")
    stock = repo / "configurations" / "stock.json"
    stock.write_text(_configuration("stock", "1.1.0") + "\n")
    head = _commit(repo)
    digest = "sha256:" + "ef" * 32
    monkeypatch.setattr(
        lock_tool,
        "resolve_tag",
        lambda repository, tag: "7" * 40
        if repository.endswith("capability-report")
        else EVALUATOR_COMMIT,
    )
    monkeypatch.setattr(lock_tool, "image_digest", lambda image, tag: digest)
    monkeypatch.setattr(
        lock_tool,
        "_verifier_pyproject",
        lambda repository, commit, work: VERIFIER_WITH_EVALUATOR,
    )
    code = lock_tool.main([str(definition), "--work", str(tmp_path / "work")])
    assert code == 0
    text = capsys.readouterr().out
    assert "thin robot Org/robot@" in text
    assert "no verification.lock.json" in text
    assert "kept scenario, plan, world and the specs and runner" in text
    assert "Review it before committing." in text
    written = json.loads(lock_tool.lock_path(definition).read_text())
    assert written["format"] == lock_tool.FORMAT_V2
    # Tags and commits stay. Issue 100 records per-file sha256 digests on the same pin.
    specs = written["releases"]["specs"]
    assert {key: specs[key] for key in ("repository", "tag", "commit")} == {
        key: kept["specs"][key] for key in ("repository", "tag", "commit")
    }
    assert specs["files"]["specs/scenarios/walk-hallway/parameters.json"] is not None
    assert written["releases"]["runner"] == kept["runner"]
    assert written["plan"] == existing["plan"]
    assert written["world"] == existing["world"]
    assert written["scenario"] == existing["scenario"]
    assert written["releases"]["reporter"]["tag"] == "v3.5.0"
    assert written["releases"]["reporter"]["commit"] == "7" * 40
    assert written["releases"]["reporter"]["digest"] == digest
    assert written["releases"]["reporter"]["tag"] != kept["reporter"]["tag"]
    assert written["evaluators"]["livingproof-evaluator-scenario"]["tag"] == "v1.2.3"
    assert written["evaluators"] != existing["evaluators"]
    assert written["robot"]["commit"] == head
    assert written["robot"]["adapter"] == "lpl_g1.adapter:ROBOT"
    stock_entry = next(entry for entry in written["configurations"] if entry["name"] == "stock")
    assert stock_entry["sha256"] == hashlib.sha256(stock.read_bytes()).hexdigest()
    assert stock_entry["sha256"] != existing["configurations"][0]["sha256"]


def test_a_thin_robot_without_owned_fields_is_an_error_not_file_not_found(tmp_path, monkeypatch):
    base = tmp_path / "git"
    _robot(base, publish_lock=False)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    with pytest.raises(lock_tool.LockError, match="publishes no verification.lock.json") as caught:
        lock_tool.main([str(definition), "--work", str(tmp_path / "work")])
    message = str(caught.value)
    assert type(caught.value) is lock_tool.LockError
    assert not isinstance(caught.value, FileNotFoundError)
    assert "FileNotFoundError" not in message
    assert "adapter and configurations only" in message
    assert f"no lock beside {definition.name}" in message
    assert not lock_tool.lock_path(definition).exists()


def test_a_thin_robot_with_only_a_0_1_0_lock_names_what_is_missing(tmp_path, monkeypatch):
    base = tmp_path / "git"
    _robot(base, publish_lock=False)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    lock_tool.lock_path(definition).write_text(
        json.dumps({"format": lock_tool.FORMAT_V1, "releases": _releases()})
    )
    with pytest.raises(lock_tool.LockError, match="livingproof-verification-lock/0.1.0") as caught:
        lock_tool.main([str(definition), "--work", str(tmp_path / "work")])
    message = str(caught.value)
    assert "FileNotFoundError" not in message
    assert not isinstance(caught.value, FileNotFoundError)
    assert f"the lock beside {definition.name} is {lock_tool.FORMAT_V1}" in message


def test_main_writes_the_lock_and_asks_for_review(tmp_path, monkeypatch, capsys):
    base = tmp_path / "git"
    _robot(base)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    _write_settings(definition)
    # The CLI resolves the evaluator tag and the reporter digest. Stub both.
    monkeypatch.setattr(lock_tool, "resolve_tag", _resolve)
    monkeypatch.setattr(lock_tool, "image_digest", _digest)
    monkeypatch.setattr(
        lock_tool,
        "_verifier_pyproject",
        lambda repository, commit, work: VERIFIER_WITH_EVALUATOR,
    )
    assert lock_tool.main([str(definition), "--work", str(tmp_path / "work")]) == 0
    text = capsys.readouterr().out
    assert text.startswith("wrote ")
    assert "Review it before committing." in text
    written = json.loads(lock_tool.lock_path(definition).read_text())
    assert written["format"] == lock_tool.FORMAT_V2
    assert lock_tool.v2_problems(written) == []


def test_the_workflow_call_replaces_the_robot_verifier(tmp_path, monkeypatch):
    base = tmp_path / "git"
    _robot(base)
    _use(monkeypatch, base)
    definition = tmp_path / "proj" / "verifications" / "g1-stock.toml"
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
    _write_settings(definition, tag="v1.2.3")
    called = "9" * 40
    workflow = tmp_path / "proj" / ".github" / "workflows" / "verify.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n"
        "  g1-stock:\n"
        "    uses: Living-Proof-Labs/capability-verifier/.github/workflows/"
        f"walk-hallway.yml@{called}  # v9.9.9\n"
        "    # uses: Living-Proof-Labs/capability-verifier/.github/workflows/"
        f"walk-hallway.yml@{'0' * 40}\n"
    )
    digest = "sha256:" + "cd" * 32
    seen = {}

    def tag_of(repository, commit):
        seen["repository"] = repository
        seen["commit"] = commit
        return "v9.9.9"

    def digester(image, tag):
        seen.setdefault("digests", []).append((image, tag))
        return digest

    lock, notes = lock_tool.generate(
        definition,
        tmp_path / "work",
        resolve=_resolve,
        verifier_pyproject=VERIFIER_WITH_EVALUATOR,
        tag_of=tag_of,
        digester=digester,
    )
    assert seen["repository"] == "Living-Proof-Labs/capability-verifier"
    assert seen["commit"] == called
    assert (lock_tool.VERIFIER_IMAGE, "9.9.9") in seen["digests"]
    assert lock["releases"]["verifier"] == {
        "repository": "Living-Proof-Labs/capability-verifier",
        "tag": "v9.9.9",
        "commit": called,
        "image": lock_tool.VERIFIER_IMAGE,
        "digest": digest,
    }
    assert lock["releases"]["reporter"]["tag"] == "v1.2.3"
    assert notes == []
    assert not any("releases.verifier recorded as" in note for note in notes)


def test_settings_replace_the_robot_reporter(tmp_path, monkeypatch):
    base = tmp_path / "git"
    _robot(base)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    settings = definition.parent / "settings.toml"
    settings.write_text(
        "[reporter]\n"
        'repository = "Living-Proof-Labs/livingproof-capability-report"\n'
        'tag = "v3.5.0"\n'
        'image = "ghcr.io/living-proof-labs/livingproof-capability-report"\n'
    )
    digest = "sha256:" + "ef" * 32
    seen = {}

    def resolve(repository, tag):
        seen.setdefault("resolved", []).append((repository, tag))
        if repository.endswith("capability-report"):
            return "7" * 40
        return EVALUATOR_COMMIT

    def digester(image, tag):
        seen["image"] = image
        seen["tag"] = tag
        return digest

    lock, notes = lock_tool.generate(
        definition,
        tmp_path / "work",
        resolve=resolve,
        verifier_pyproject=VERIFIER_WITH_EVALUATOR,
        digester=digester,
    )
    assert ("Living-Proof-Labs/livingproof-capability-report", "v3.5.0") in seen["resolved"]
    assert seen["image"] == "ghcr.io/living-proof-labs/livingproof-capability-report"
    assert seen["tag"] == "3.5.0"
    assert lock["releases"]["reporter"] == {
        "repository": "Living-Proof-Labs/livingproof-capability-report",
        "tag": "v3.5.0",
        "commit": "7" * 40,
        "image": "ghcr.io/living-proof-labs/livingproof-capability-report",
        "digest": digest,
    }
    assert lock["releases"]["reporter"]["tag"] != "v1.2.3"
    assert notes == []


def test_resolve_tag_uses_the_peeled_commit(tmp_path, monkeypatch):
    repo = tmp_path / "Living-Proof-Labs" / "livingproof-evaluator-scenario.git"
    _init(repo)
    (repo / "README").write_text("evaluator\n")
    commit = _commit(repo)
    _git(repo, "tag", "-a", "v1.0.0", "-m", "release")
    monkeypatch.setenv("LIVINGPROOF_GIT_BASE", f"file://{tmp_path}")
    assert (
        lock_tool.resolve_tag("Living-Proof-Labs/livingproof-evaluator-scenario", "v1.0.0")
        == commit
    )
    with pytest.raises(lock_tool.LockError, match="has no tag v9.9.9"):
        lock_tool.resolve_tag("Living-Proof-Labs/livingproof-evaluator-scenario", "v9.9.9")


def test_settings_replace_the_reporter_on_a_thin_robot(tmp_path, monkeypatch):
    """(a) A thin robot still takes the reporter from settings.toml."""
    base = tmp_path / "git"
    repo = _robot(base, publish_lock=False)
    _use(monkeypatch, base)
    definition = _write_definition(tmp_path)
    existing = _v2(definition, repo)
    assert existing["releases"]["reporter"]["tag"] == "v1.2.3"
    lock_tool.lock_path(definition).write_text(json.dumps(existing))
    _write_settings(definition, tag="v3.5.0")
    digest = "sha256:" + "ef" * 32
    seen = {}

    def resolve(repository, tag):
        seen.setdefault("resolved", []).append((repository, tag))
        if repository.endswith("capability-report"):
            return "7" * 40
        return EVALUATOR_COMMIT

    def digester(image, tag):
        seen["image"] = image
        seen["tag"] = tag
        return digest

    lock, notes = lock_tool.generate(
        definition,
        tmp_path / "work",
        resolve=resolve,
        verifier_pyproject=VERIFIER_WITH_EVALUATOR,
        digester=digester,
    )
    assert ("Living-Proof-Labs/livingproof-capability-report", "v3.5.0") in seen["resolved"]
    assert lock["releases"]["reporter"] == {
        "repository": "Living-Proof-Labs/livingproof-capability-report",
        "tag": "v3.5.0",
        "commit": "7" * 40,
        "image": "ghcr.io/living-proof-labs/livingproof-capability-report",
        "digest": digest,
    }
    assert lock["releases"]["reporter"]["tag"] != "v1.2.3"
    assert lock["plan"] == existing["plan"]
    assert lock["world"] == existing["world"]
    assert lock["scenario"] == existing["scenario"]
    assert any("thin robot" in note for note in notes)


def test_workflow_and_evaluator_are_applied_on_a_thin_robot(tmp_path, monkeypatch):
    """(a) A thin robot still takes the verifier from the workflow call
    and the evaluator from that verifier release."""
    base = tmp_path / "git"
    repo = _robot(base, publish_lock=False)
    _use(monkeypatch, base)
    definition = _write_definition(tmp_path)
    existing = _v2(definition, repo)
    lock_tool.lock_path(definition).write_text(json.dumps(existing))
    _write_settings(definition)
    called = "9" * 40
    _write_workflow(tmp_path, called, tag="v9.9.9")
    digest = "sha256:" + "cd" * 32

    def tag_of(repository, commit):
        assert repository == "Living-Proof-Labs/capability-verifier"
        assert commit == called
        return "v9.9.9"

    def resolve(repository, tag):
        if repository.endswith("capability-report"):
            return "7" * 40
        assert repository == "Living-Proof-Labs/livingproof-evaluator-scenario"
        assert tag == "v1.2.3"
        return "a" * 40

    def digester(image, tag):
        return digest

    lock, notes = lock_tool.generate(
        definition,
        tmp_path / "work",
        resolve=resolve,
        verifier_pyproject=VERIFIER_WITH_EVALUATOR,
        tag_of=tag_of,
        digester=digester,
    )
    assert lock["releases"]["verifier"] == {
        "repository": "Living-Proof-Labs/capability-verifier",
        "tag": "v9.9.9",
        "commit": called,
        "image": lock_tool.VERIFIER_IMAGE,
        "digest": digest,
    }
    assert lock["evaluators"]["livingproof-evaluator-scenario"]["commit"] == "a" * 40
    assert lock["evaluators"]["livingproof-evaluator-scenario"]["tag"] == "v1.2.3"
    assert lock["evaluators"] != existing["evaluators"]


def test_missing_settings_toml_is_an_error(tmp_path, monkeypatch):
    """(b) A missing settings.toml fails with a clear error, it does not
    copy the reporter from the robot lock or the existing 0.2.0."""
    base = tmp_path / "git"
    _robot(base, publish_lock=True)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    assert not (definition.parent / "settings.toml").exists()
    with pytest.raises(lock_tool.LockError, match="settings.toml") as caught:
        lock_tool.generate(
            definition,
            tmp_path / "work",
            resolve=lambda repository, tag: EVALUATOR_COMMIT,
            verifier_pyproject=VERIFIER_WITH_EVALUATOR,
        )
    message = str(caught.value)
    assert "settings.toml" in message
    assert "FileNotFoundError" not in message


def test_resolve_tag_timeout_is_a_lock_error(monkeypatch):
    """(c') git TimeoutExpired ends in LockError, not a traceback."""

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=60)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(lock_tool.LockError, match="timed out|timeout") as caught:
        lock_tool.resolve_tag("Living-Proof-Labs/livingproof-capability-report", "v3.7.0")
    assert type(caught.value) is lock_tool.LockError
    assert not isinstance(caught.value, subprocess.TimeoutExpired)
    with pytest.raises(lock_tool.LockError, match="timed out|timeout") as listed:
        lock_tool.tag_of_commit("Living-Proof-Labs/capability-verifier", "a" * 40)
    assert type(listed.value) is lock_tool.LockError
    assert not isinstance(listed.value, subprocess.TimeoutExpired)


# Review checks for #69: thin-robot caller pins, a missing settings.toml and git or
# registry timeouts (bounded retry, then LockError).

LS_REMOTE = "7" * 40 + "\trefs/tags/v3.7.0\n"


def _thin(tmp_path, monkeypatch):
    base = tmp_path / "git"
    repo = _robot(base, publish_lock=False)
    _use(monkeypatch, base)
    definition = _write_definition(tmp_path)
    existing = _v2(definition, repo)
    lock_tool.lock_path(definition).write_text(json.dumps(existing))
    return definition, existing


def _thin_resolve(repository, tag):
    return "7" * 40 if repository.endswith("capability-report") else EVALUATOR_COMMIT


def _thin_digest(image, tag):
    return "sha256:" + "ef" * 32


def _thin_generate(definition, tmp_path, **kwargs):
    kwargs.setdefault("resolve", _thin_resolve)
    kwargs.setdefault("verifier_pyproject", VERIFIER_WITH_EVALUATOR)
    kwargs.setdefault("digester", _thin_digest)
    return lock_tool.generate(definition, tmp_path / "work", **kwargs)


def _git_calls(monkeypatch, outcomes):
    """Patch subprocess.run with a queue of outcomes; return the list of calls."""
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return subprocess.CompletedProcess(args, 0, stdout=outcome, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def _no_sleep(monkeypatch):
    pauses = []
    monkeypatch.setattr(lock_tool.time, "sleep", lambda seconds: pauses.append(seconds))
    return pauses


def _timeout():
    return subprocess.TimeoutExpired(cmd="git", timeout=lock_tool.GIT_REMOTE_TIMEOUT)


def test_a_thin_robot_without_settings_toml_is_an_error(tmp_path, monkeypatch):
    """(b) on the thin path: no fallback to the existing 0.2.0 reporter either."""
    definition, existing = _thin(tmp_path, monkeypatch)
    assert existing["releases"]["reporter"]["tag"] == "v1.2.3"
    assert not (definition.parent / "settings.toml").exists()
    with pytest.raises(lock_tool.LockError, match="settings.toml is required"):
        _thin_generate(definition, tmp_path)


def test_the_cli_reports_a_missing_settings_toml_without_a_traceback(tmp_path, monkeypatch, capsys):
    """(b) the command exits 1 with the LockError text, not a Python traceback."""
    definition, _ = _thin(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as caught:
        lock_tool.main([str(definition), "--work", str(tmp_path / "work")])
    assert isinstance(caught.value, lock_tool.LockError)
    assert "settings.toml is required" in str(caught.value.code)
    assert "Traceback" not in capsys.readouterr().err


def test_a_thin_robot_lock_regenerates_identically(tmp_path, monkeypatch):
    """Determinism: two runs on the same inputs give byte-identical JSON."""
    definition, _ = _thin(tmp_path, monkeypatch)
    _write_settings(definition, tag="v3.5.0")
    _write_workflow(tmp_path, "9" * 40, tag="v9.9.9")
    tag_of = lambda repository, commit: "v9.9.9"  # noqa: E731
    first, _ = _thin_generate(definition, tmp_path, tag_of=tag_of)
    second, _ = _thin_generate(definition, tmp_path, tag_of=tag_of)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_a_thin_robot_settings_edit_moves_only_the_reporter(tmp_path, monkeypatch):
    """Changing settings.toml changes releases.reporter and nothing else in the lock."""
    definition, _ = _thin(tmp_path, monkeypatch)
    _write_settings(definition, tag="v3.5.0")
    before, _ = _thin_generate(definition, tmp_path)
    _write_settings(definition, tag="v3.6.0")
    after, _ = _thin_generate(definition, tmp_path)
    assert after["releases"]["reporter"]["tag"] == "v3.6.0"
    for key in set(before) | set(after):
        if key != "releases":
            assert before.get(key) == after.get(key), key
    for key in set(before["releases"]) | set(after["releases"]):
        if key != "reporter":
            assert before["releases"].get(key) == after["releases"].get(key), key


def test_a_thin_robot_without_a_workflow_call_keeps_the_existing_verifier(tmp_path, monkeypatch):
    """Pins current behaviour: no verify.yml job means the verifier stays as locked."""
    definition, existing = _thin(tmp_path, monkeypatch)
    _write_settings(definition)
    lock, _ = _thin_generate(definition, tmp_path)
    assert lock["releases"]["verifier"] == existing["releases"]["verifier"]


def test_resolve_tag_retries_a_timeout_and_then_succeeds(monkeypatch):
    """(c) bounded retry: one timeout, then the second attempt's answer is used."""
    pauses = _no_sleep(monkeypatch)
    calls = _git_calls(monkeypatch, [_timeout(), LS_REMOTE])
    commit = lock_tool.resolve_tag("Living-Proof-Labs/livingproof-capability-report", "v3.7.0")
    assert commit == "7" * 40
    assert len(calls) == 2
    assert pauses == [lock_tool.GIT_REMOTE_PAUSE]


def test_resolve_tag_gives_up_after_the_configured_attempts(monkeypatch):
    """(c') exactly GIT_REMOTE_ATTEMPTS tries, then LockError naming them."""
    pauses = _no_sleep(monkeypatch)
    attempts = lock_tool.GIT_REMOTE_ATTEMPTS
    calls = _git_calls(monkeypatch, [_timeout() for _ in range(attempts + 2)])
    with pytest.raises(lock_tool.LockError, match=f"timed out after {attempts} attempts"):
        lock_tool.resolve_tag("Living-Proof-Labs/livingproof-capability-report", "v3.7.0")
    assert len(calls) == attempts
    assert len(pauses) == attempts - 1


def test_tag_of_commit_retries_a_timeout_and_then_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    commit = "9" * 40
    calls = _git_calls(monkeypatch, [_timeout(), f"{commit}\trefs/tags/v9.9.9\n"])
    assert lock_tool.tag_of_commit("Living-Proof-Labs/capability-verifier", commit) == "v9.9.9"
    assert len(calls) == 2


def test_a_git_failure_is_not_retried(monkeypatch):
    """Only timeouts are retried. A non-zero exit fails at once with git's last line."""
    pauses = _no_sleep(monkeypatch)
    failed = subprocess.CalledProcessError(
        128, ["git"], stderr="fatal: unable to access: Could not resolve host\n"
    )
    calls = _git_calls(monkeypatch, [failed, LS_REMOTE])
    with pytest.raises(lock_tool.LockError, match=r"exit 128\): fatal: unable to access"):
        lock_tool.resolve_tag("Living-Proof-Labs/livingproof-capability-report", "v3.7.0")
    assert len(calls) == 1
    assert pauses == []


def test_every_git_remote_call_has_the_timeout(monkeypatch):
    seen = []

    def run(args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(args, 0, stdout=LS_REMOTE, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    lock_tool.resolve_tag("Living-Proof-Labs/livingproof-capability-report", "v3.7.0")
    assert seen == [lock_tool.GIT_REMOTE_TIMEOUT]


def test_a_registry_timeout_is_a_lock_error(monkeypatch):
    """README: 'A registry timeout ... is LockError as well.' socket timeouts are OSError."""

    def stall(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", stall)
    with pytest.raises(lock_tool.LockError, match="cannot read the registry digest") as caught:
        lock_tool.image_digest("ghcr.io/living-proof-labs/livingproof-capability-report", "3.7.0")
    assert type(caught.value) is lock_tool.LockError


def test_the_readme_retry_policy_matches_the_code():
    """Docs-vs-code: the README numbers are the constants, and the policy words are there."""
    words = {1: "once", 2: "twice", 3: "three times", 4: "four times", 5: "five times"}
    readme = (ROOT / "README.md").read_text()
    paragraph = next(p for p in readme.split("\n\n") if "The `locks` job needs the network" in p)
    assert f"retry a timeout {words[lock_tool.GIT_REMOTE_ATTEMPTS]}" in paragraph
    assert f"({lock_tool.GIT_REMOTE_TIMEOUT} seconds each" in paragraph
    pause = lock_tool.GIT_REMOTE_PAUSE
    assert re.search(
        rf"\b{'one' if pause == 1 else pause} second{'s' if pause != 1 else ''} apart", paragraph
    )
    for phrase in ("not cached", "fails closed", "no cache"):
        assert phrase in paragraph, phrase


def test_settings_run_evaluator_is_resolved_and_recorded_tag_only(tmp_path, monkeypatch):
    base = tmp_path / "git"
    _robot(base)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    settings = _write_settings(definition, tag="v3.11.6")
    settings.write_text(
        settings.read_text()
        + "\n[evaluators.livingproof-evaluator-run]\n"
        + 'repository = "Living-Proof-Labs/livingproof-evaluator-run"\n'
        + 'tag = "v1.1.1"\n'
    )
    resolved = []

    def resolve(repository, tag):
        resolved.append((repository, tag))
        return "6" * 40 if repository.endswith("livingproof-evaluator-run") else EVALUATOR_COMMIT

    lock, _notes = _generate(tmp_path, definition, resolve=resolve)
    assert ("Living-Proof-Labs/livingproof-evaluator-run", "v1.1.1") in resolved
    assert lock["evaluators"]["livingproof-evaluator-run"] == {
        "repository": "Living-Proof-Labs/livingproof-evaluator-run",
        "tag": "v1.1.1",
    }
    assert lock_tool.v2_problems(lock) == []


def test_invalid_settings_run_evaluator_raises_lock_error(tmp_path, monkeypatch):
    base = tmp_path / "git"
    _robot(base)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    settings = _write_settings(definition, tag="v3.11.6")
    settings.write_text(
        settings.read_text()
        + "\n[evaluators.livingproof-evaluator-run]\n"
        + 'repository = "Living-Proof-Labs/livingproof-evaluator-run"\n'
        + 'tag = "main"\n'
        + f'commit = "{EVALUATOR_COMMIT}"\n'
    )
    with pytest.raises(lock_tool.LockError, match="livingproof-evaluator-run"):
        _generate(tmp_path, definition)


def test_v2_problems_checks_optional_run_evaluator_without_commit(tmp_path, monkeypatch):
    base = tmp_path / "git"
    _robot(base)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    lock, _ = _generate(tmp_path, definition)
    assert "livingproof-evaluator-run" not in lock["evaluators"]
    assert lock_tool.v2_problems(lock) == []
    lock["evaluators"]["livingproof-evaluator-run"] = {
        "repository": "Living-Proof-Labs/livingproof-evaluator-run",
        "tag": "main",
    }
    problems = lock_tool.v2_problems(lock)
    assert any("evaluators.livingproof-evaluator-run.tag" in p for p in problems), problems
