# SPDX-License-Identifier: Apache-2.0
"""0.2.0 lock generator: owned fields, a readable 0.1.0 lock, and a thin robot."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

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
            "releases": _releases(),
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


def _generate(tmp_path, definition, **kwargs):
    kwargs.setdefault("resolve", _resolve)
    kwargs.setdefault("verifier_pyproject", VERIFIER_WITH_EVALUATOR)
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
        assert repository == "Living-Proof-Labs/livingproof-evaluator-scenario"
        assert tag == "v1.2.3"
        return "a" * 40

    lock, notes = lock_tool.generate(definition, tmp_path / "work", resolve=resolve)
    assert notes == []
    assert lock["evaluators"]["livingproof-evaluator-scenario"]["commit"] == "a" * 40


def _v2(definition: Path, repo: Path) -> dict:
    stock = (repo / "configurations" / "stock.json").read_bytes()
    playground = (repo / "configurations" / "playground.json").read_bytes()
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
        "releases": _releases(),
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


def test_a_thin_robot_keeps_owned_fields_and_refreshes_the_tree(tmp_path, monkeypatch, capsys):
    base = tmp_path / "git"
    repo = _robot(base, publish_lock=False)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    existing = _v2(definition, repo)
    kept = json.loads(json.dumps(existing["releases"]))
    lock_tool.lock_path(definition).write_text(json.dumps(existing))
    stock = repo / "configurations" / "stock.json"
    stock.write_text(_configuration("stock", "1.1.0") + "\n")
    head = _commit(repo)
    code = lock_tool.main([str(definition), "--work", str(tmp_path / "work")])
    assert code == 0
    text = capsys.readouterr().out
    assert "thin robot Org/robot@" in text
    assert "no verification.lock.json" in text
    assert "kept releases, scenario, plan, world and evaluators" in text
    assert "Review it before committing." in text
    written = json.loads(lock_tool.lock_path(definition).read_text())
    assert written["format"] == lock_tool.FORMAT_V2
    assert written["releases"] == kept
    assert written["plan"] == existing["plan"]
    assert written["world"] == existing["world"]
    assert written["evaluators"] == existing["evaluators"]
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
    assert "FileNotFoundError" not in str(caught.value)
    assert not isinstance(caught.value, FileNotFoundError)


def test_main_writes_the_lock_and_asks_for_review(tmp_path, monkeypatch, capsys):
    base = tmp_path / "git"
    _robot(base)
    _use(monkeypatch, base)
    definition = _definition(tmp_path)
    # The CLI resolves the evaluator tag. Stub it the same way the other tests do.
    monkeypatch.setattr(lock_tool, "resolve_tag", _resolve)
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
