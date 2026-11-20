# SPDX-License-Identifier: Apache-2.0
"""The recording gap limit is declared, not defaulted (livingproof-evaluator-run#26).

The measured gap in every real recording is 0.020 s (50 Hz). Aaron approved
``max_gap_seconds = 0.05`` on 2026-11-19. It is declared under ``plan.recording`` in each
definition, and ``tools/lock.py`` carries it into each lock, where livingproof-evaluator-run
reads it. The evaluator has no default for it.
"""

import json
from pathlib import Path
import tomllib

ROOT = Path(__file__).parents[1]
DEFINITIONS = sorted((ROOT / "verifications").glob("*.toml"))
DEFINITIONS = [p for p in DEFINITIONS if p.name != "settings.toml"]
EVALUATOR_RUN = {"repository": "Living-Proof-Labs/livingproof-evaluator-run", "tag": "v1.7.0"}


def test_there_are_ten_definitions():
    assert len(DEFINITIONS) == 10


def test_every_definition_declares_the_gap_limit():
    for path in DEFINITIONS:
        plan = tomllib.loads(path.read_text())["plan"]
        assert plan["recording"] == {"max_gap_seconds": 0.05}, path.name


def test_every_lock_carries_the_gap_limit_in_its_plan():
    for path in DEFINITIONS:
        lock = json.loads(path.with_suffix(".lock.json").read_text())
        assert lock["plan"]["recording"] == {"max_gap_seconds": 0.05}, path.name


def test_the_evaluator_run_pin_has_the_recording_check_that_reports_over_cap_files():
    settings = tomllib.loads((ROOT / "verifications" / "settings.toml").read_text())
    assert settings["evaluators"]["livingproof-evaluator-run"] == EVALUATOR_RUN
    for path in DEFINITIONS:
        lock = json.loads(path.with_suffix(".lock.json").read_text())
        assert lock["evaluators"]["livingproof-evaluator-run"] == EVALUATOR_RUN, path.name
