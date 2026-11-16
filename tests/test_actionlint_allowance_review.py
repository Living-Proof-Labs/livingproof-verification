# SPDX-License-Identifier: Apache-2.0
"""actionlint's runner-label allowance stays narrow (Vera review of #77).

`.github/actionlint.yaml` may only declare GitHub-hosted Ubuntu labels that a
workflow here actually runs on, so a newer hosted image that the pinned
actionlint predates is accepted while typos and every other rule still fail.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".github" / "actionlint.yaml"
HOSTED_UBUNTU = re.compile(r"ubuntu-\d{2}\.04(-arm)?")


def _runs_on_labels() -> set[str]:
    labels: set[str] = set()
    for path in (ROOT / ".github" / "workflows").glob("*.y*ml"):
        for job in (yaml.safe_load(path.read_text()).get("jobs") or {}).values():
            runs_on = job.get("runs-on")
            if isinstance(runs_on, str):
                labels.add(runs_on)
            elif isinstance(runs_on, list):
                labels.update(runs_on)
    return labels


def test_allowance_only_declares_runner_labels():
    if not CONFIG.exists():
        return
    config = yaml.safe_load(CONFIG.read_text()) or {}
    assert set(config) <= {"self-hosted-runner"}
    assert set(config.get("self-hosted-runner") or {}) <= {"labels"}


def test_allowed_labels_are_hosted_ubuntu_labels_in_use():
    if not CONFIG.exists():
        return
    config = yaml.safe_load(CONFIG.read_text()) or {}
    allowed = (config.get("self-hosted-runner") or {}).get("labels") or []
    assert allowed, "remove the empty allowance file instead"
    assert len(allowed) == len(set(allowed))
    in_use = _runs_on_labels()
    for label in allowed:
        assert HOSTED_UBUNTU.fullmatch(label), label
        assert label in in_use, f"{label} is allowed but no job runs on it"


def test_no_self_hosted_or_wildcard_runners():
    labels = _runs_on_labels()
    assert "self-hosted" not in labels
    assert not any("*" in label for label in labels)
