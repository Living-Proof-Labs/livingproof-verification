"""evaluator-run is pinned at the release that writes result format 1.2.0 (#170, evaluator #38).

evaluator-run v1.7.0 (PR 29) emits an INCONCLUSIVE run, with no percentage, when no check
applies. The verifier (>= 2.8.0) and reporter (>= 3.13.0) pins that read it landed first (PR 171),
so the producer pin moves last. The site must accept a 1.2.0 INCONCLUSIVE document.
"""

from __future__ import annotations

import importlib.util
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIN_EVALUATOR_RUN = (1, 7, 0)
MIN_VERIFIER = (2, 8, 0)
MIN_REPORTER = (3, 13, 0)
spec = importlib.util.spec_from_file_location("site_tool_1_2_0", ROOT / "tools/site.py")
site_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_tool)


def _version(tag: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    assert match, tag
    return tuple(int(part) for part in match.groups())


def _locks() -> list[Path]:
    return sorted((ROOT / "verifications").glob("*.lock.json"))


def test_settings_pin_evaluator_run_at_or_above_1_7_0():
    settings = tomllib.loads((ROOT / "verifications/settings.toml").read_text(encoding="utf-8"))
    pin = settings["evaluators"]["livingproof-evaluator-run"]
    assert pin["repository"] == "Living-Proof-Labs/livingproof-evaluator-run"
    assert _version(pin["tag"]) >= MIN_EVALUATOR_RUN


def test_every_lock_pins_evaluator_run_at_or_above_1_7_0():
    assert _locks()
    for path in _locks():
        lock = json.loads(path.read_text(encoding="utf-8"))
        tag = lock["evaluators"]["livingproof-evaluator-run"]["tag"]
        assert _version(tag) >= MIN_EVALUATOR_RUN, (path.name, tag)


def test_the_producer_pin_never_leads_the_consumer_pins():
    for path in _locks():
        releases = json.loads(path.read_text(encoding="utf-8"))["releases"]
        assert _version(releases["verifier"]["tag"]) >= MIN_VERIFIER, path.name
        assert _version(releases["reporter"]["tag"]) >= MIN_REPORTER, path.name


def test_the_site_accepts_a_1_2_0_inconclusive_result():
    document = json.loads(
        (ROOT / "tests/fixtures/evaluator-result-run-integrity-all-not-applicable.json").read_text(
            encoding="utf-8"
        )
    )
    assert document["format"] == "livingproof-evaluator-result/1.2.0"
    assert "percentage" not in document["integrity"]["run"]
    report = {
        "source_bundle": {
            "assessment": {"integrity_results": {"livingproof-run-integrity": document}}
        },
        "summaries": [{"grade": "FAIL"}],
    }
    (found,) = site_tool.failing_evaluators(report)
    assert found["id"] == "livingproof-run-integrity"
    assert found["not_graded"] is True
