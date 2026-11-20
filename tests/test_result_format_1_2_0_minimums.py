"""The verifier and reporter pins understand result format 1.2.0 (#170, evaluator #38).

An INCONCLUSIVE run is counted as failed by capability-verifier before v2.8.0, and is shown as
satisfied by livingproof-capability-report before v3.13.0. evaluator-run must not move to 1.2.0
output until both pins are at or above these minimums. The reporter minimum is v3.13.2, which
also shows a Run Integrity ERROR with the failing cross, not the passing tick (capability-report #106).
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIN_VERIFIER = (2, 8, 0)
MIN_REPORTER = (3, 13, 2)


def _version(tag: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    assert match, tag
    return tuple(int(part) for part in match.groups())


def _locks() -> list[Path]:
    return sorted((ROOT / "verifications").glob("*.lock.json"))


def test_there_are_locks_to_check():
    assert _locks()


def test_every_lock_pins_the_verifier_at_or_above_2_8_0():
    for path in _locks():
        tag = json.loads(path.read_text(encoding="utf-8"))["releases"]["verifier"]["tag"]
        assert _version(tag) >= MIN_VERIFIER, (path.name, tag)


def test_every_lock_pins_the_reporter_at_or_above_3_13_2():
    for path in _locks():
        tag = json.loads(path.read_text(encoding="utf-8"))["releases"]["reporter"]["tag"]
        assert _version(tag) >= MIN_REPORTER, (path.name, tag)


def test_settings_choose_a_reporter_at_or_above_3_13_2():
    settings = tomllib.loads((ROOT / "verifications/settings.toml").read_text(encoding="utf-8"))
    assert _version(settings["reporter"]["tag"]) >= MIN_REPORTER
