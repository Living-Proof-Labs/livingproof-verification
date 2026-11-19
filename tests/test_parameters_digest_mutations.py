# SPDX-License-Identifier: Apache-2.0
"""Vera (verification) #145: the digest check, run as CI runs it, fails each mutation of a good run.

Every lock (corner and hallway) gets a manifest that records what it declares; each mutation
below must exit non-zero from the command line, never pass vacuously.
"""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
TOOL = ROOT / "tools/parameters_digest.py"
LOCKS = sorted((ROOT / "verifications").glob("*.lock.json"))


def _good(lock: dict) -> dict:
    return {
        "sources": {"parameters": dict(lock["sources"]["parameters"])},
        "environment": {
            "runner": {"tag": "v3.6.2", "commit": lock["releases"]["runner"]["commit"]}
        },
    }


def _run(*args) -> int:
    return subprocess.run(
        [sys.executable, str(TOOL), *map(str, args)], capture_output=True
    ).returncode


def _tamper(m):
    sha = m["sources"]["parameters"]["sha256"]
    m["sources"]["parameters"]["sha256"] = sha[:-1] + ("0" if sha[-1] != "0" else "1")


def _drop_parameters(m):
    del m["sources"]["parameters"]


def _drop_sources(m):
    del m["sources"]


def _wrong_runner(m):
    m["environment"]["runner"]["commit"] = "0" * 40


def _wrong_path(m):
    m["sources"]["parameters"]["path"] = "elsewhere/parameters.json"


@pytest.mark.parametrize("lock_path", LOCKS, ids=lambda p: p.name.removesuffix(".lock.json"))
def test_a_good_run_passes_and_each_mutation_fails_on_the_command_line(lock_path, tmp_path):
    lock = json.loads(lock_path.read_text())
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_good(lock)))
    assert _run(lock_path, good) == 0
    for mutate in (_tamper, _drop_parameters, _drop_sources, _wrong_runner, _wrong_path):
        manifest = copy.deepcopy(_good(lock))
        mutate(manifest)
        bad = tmp_path / f"{mutate.__name__}.json"
        bad.write_text(json.dumps(manifest))
        assert _run(lock_path, bad) == 1, mutate.__name__
    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    assert _run(lock_path, empty) == 1
    no_runs = copy.deepcopy(lock)
    del no_runs["sources"]
    bare = tmp_path / "bare.lock.json"
    bare.write_text(json.dumps(no_runs))
    assert _run(bare, good) == 1


def test_an_evidence_directory_with_no_manifest_or_an_unknown_definition_fails(tmp_path):
    assert _run("--evidence", tmp_path, "--locks", ROOT / "verifications") == 1
    (tmp_path / "no-such-definition-evidence-x").mkdir()
    (tmp_path / "no-such-definition-evidence-x" / "manifest.json").write_text("{}")
    assert _run("--evidence", tmp_path, "--locks", ROOT / "verifications") == 1
