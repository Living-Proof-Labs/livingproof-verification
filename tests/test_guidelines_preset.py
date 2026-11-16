# SPDX-License-Identifier: Apache-2.0
"""Guards for the collaboration-guidelines Renovate preset bump in this repo.

Proposed by Vera in the livingproof-verification#74 review. Offline: this repository
does not opt into docs-brand updates, and its own packageRules stay in place when the
preset pin moves. When a guidelines release enables docs-brand by default, or a later
change drops a consumer packageRule, these tests fail. LIVINGPROOF_NETWORK=1 also
fetches the pinned preset and release and checks that docs-brand stays opt-in.
"""

from __future__ import annotations

import json
import os
import urllib.request

import pytest

from test_guidelines_pins import REPO, ROOT, pinned_versions

CONSUMER_RULE_DESCRIPTIONS = (
    (
        "Coordinate verifier workflow and hallway image proposals; the engineering "
        "lead generates locked evidence before merge."
    ),
    (
        "Reporter release tags stay on the organization policy. Renovate edits only "
        "verifications/settings.toml."
    ),
    "The verification locks are generator output. Renovate must not edit them.",
)


def _config() -> dict:
    return json.loads((ROOT / "renovate.json").read_text())


def test_this_repository_does_not_opt_in_to_docs_brand_updates():
    assert not (ROOT / "docs-brand.lock.json").is_file()
    for rule in _config().get("packageRules", []):
        dumped = json.dumps(rule)
        if "docs-brand" in dumped or rule.get("matchDepTypes") == ["docs-brand"]:
            assert rule.get("enabled") is False, rule


def test_consumer_package_rules_stay_in_place_when_the_preset_pin_moves():
    rules = _config()["packageRules"]
    assert [rule["description"] for rule in rules] == list(CONSUMER_RULE_DESCRIPTIONS)
    assert rules[0]["automerge"] is False
    assert rules[1]["matchFileNames"] == ["verifications/settings.toml"]
    assert rules[1]["automerge"] is False
    assert rules[2]["matchFileNames"] == ["verifications/*.lock.json"]
    assert rules[2]["enabled"] is False


def test_a_dropped_consumer_package_rule_is_reported():
    rules = list(_config()["packageRules"])
    dropped = rules.pop(2)
    assert dropped["enabled"] is False
    assert [rule["description"] for rule in rules] != list(CONSUMER_RULE_DESCRIPTIONS)


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
def test_pinned_preset_keeps_docs_brand_disabled_by_default():
    refs = pinned_versions()
    assert refs, "expected a Renovate preset pin"
    (ref,) = refs
    url = f"https://raw.githubusercontent.com/{REPO}/{ref}/renovate-preset.json"
    with urllib.request.urlopen(url, timeout=30) as response:
        preset = json.loads(response.read().decode())
    brand_rules = [
        rule
        for rule in preset.get("packageRules", [])
        if "docs-brand" in json.dumps(rule) or rule.get("matchDepTypes") == ["docs-brand"]
    ]
    assert brand_rules, f"pinned {ref} preset has no docs-brand packageRule"
    for rule in brand_rules:
        assert rule.get("enabled") is False, rule
        assert rule.get("automerge") is False
    managers = [
        manager
        for manager in preset.get("customManagers", [])
        if manager.get("depTypeTemplate") == "docs-brand"
        or "docs-brand.lock.json" in json.dumps(manager)
    ]
    assert managers, f"pinned {ref} preset has no docs-brand.lock.json manager"
