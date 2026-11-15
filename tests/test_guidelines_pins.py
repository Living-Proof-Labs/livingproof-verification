# SPDX-License-Identifier: Apache-2.0
"""collaboration-guidelines bumps move every consumer pin together and keep the release interface.

Proposed by Vera in the capability-verifier#72 review (guidelines bump checklist items 2, 3 and 6),
adapted for this repository. Offline: the shared release interface is recorded here and checked
against the workflow call when this repo has one. When a guidelines release changes
`semantic-release.yml` inputs or outputs, update SHARED_RELEASE_* in the same PR, after reading
the diff. LIVINGPROOF_NETWORK=1 also compares them with the pinned tag.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

import pytest

REPO = "Living-Proof-Labs/collaboration-guidelines"
TAG = r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
USES = re.compile(
    rf"^\s+uses:\s*{re.escape(REPO)}/\.github/workflows/"
    rf"(?P<file>[\w.-]+\.ya?ml)@(?P<ref>\S+)\s*$",
    re.MULTILINE,
)
PRESET = re.compile(rf"^github>{REPO}:renovate-preset#(?P<ref>.+)$")
SHARED_RELEASE_INPUTS = {"dry-run", "draft-release"}
SHARED_RELEASE_OUTPUTS = {"draft-created", "published", "version", "git-tag", "release-url"}


def _root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "renovate.json").is_file():
            return candidate
    raise AssertionError("could not find the repository root (renovate.json)")


ROOT = _root()


def workflow_path() -> Path | None:
    for name in (".github/workflows/verify.yml", ".github/workflows/release.yml"):
        path = ROOT / name
        if path.is_file() and USES.search(path.read_text()):
            return path
    return None


def guideline_calls(text: str | None = None) -> dict[str, re.Match[str]]:
    path = workflow_path()
    if text is None:
        if path is None:
            return {}
        text = path.read_text()
    calls: dict[str, re.Match[str]] = {}
    for match in USES.finditer(text):
        headers = re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", text[: match.start()], re.MULTILINE)
        assert headers, "expected a job name above the guidelines workflow call"
        calls[headers[-1]] = match
    return calls


def preset_refs(config: dict | None = None) -> list[str]:
    config = config or json.loads((ROOT / "renovate.json").read_text())
    return [m.group("ref") for m in map(PRESET.match, config.get("extends", [])) if m]


def pinned_versions(text: str | None = None, config: dict | None = None) -> set[str]:
    refs = set(preset_refs(config))
    if text is not None or workflow_path() is not None:
        refs |= {match.group("ref") for match in guideline_calls(text).values()}
    return refs


def _job_block(text: str, job: str) -> str:
    match = re.search(rf"^  {re.escape(job)}:\n", text, re.MULTILINE)
    assert match, f"job {job} not found"
    start = match.start()
    end = len(text)
    for later in re.finditer(r"^  [A-Za-z0-9_-]+:\s*$", text[match.end() :], re.MULTILINE):
        end = match.end() + later.start()
        break
    return text[start:end]


def _mapping(block: str, key: str, indent: int = 4) -> dict[str, str]:
    pad = " " * indent
    inner = " " * (indent + 2)
    found = re.search(rf"^{pad}{re.escape(key)}:\n((?:{inner}.*\n)*)", block, re.MULTILINE)
    if not found:
        return {}
    return dict(re.findall(rf"^{inner}([A-Za-z0-9_-]+):\s*(\S+)", found.group(1), re.MULTILINE))


def _workflow_call_keys(shared: str, section: str) -> set[str]:
    call = re.search(r"^  workflow_call:\n((?:    .*\n)+)", shared, re.MULTILINE)
    assert call, "expected a workflow_call interface"
    body = call.group(1)
    found = re.search(rf"^    {re.escape(section)}:\n((?:      .*\n)+)", body, re.MULTILINE)
    if not found:
        return set()
    return set(re.findall(r"^      ([A-Za-z0-9_-]+):", found.group(1), re.MULTILINE))


def test_every_guidelines_pin_is_a_full_release_tag():
    refs = pinned_versions()
    assert refs, "expected a Renovate preset pin and/or a release workflow call"
    for ref in refs:
        assert re.fullmatch(TAG, ref), f"{ref} is not a full vX.Y.Z release tag"


def test_workflow_call_and_renovate_preset_name_one_guidelines_release():
    # A partial bump (one pin moved, the other left behind) fails here.
    # Repos that only pin the preset still need exactly one release tag.
    assert len(pinned_versions()) == 1, sorted(pinned_versions())


@pytest.mark.skipif(
    workflow_path() is None, reason="this repository has no guidelines workflow call"
)
def test_mismatched_pins_are_reported():
    text = workflow_path().read_text()
    mutated = USES.sub(lambda m: m.group(0).replace(m.group("ref"), "v2.2.0", 1), text, count=1)
    preset = {"extends": [f"github>{REPO}:renovate-preset#v9.2.0"]}
    assert len(pinned_versions(mutated, preset)) == 2


@pytest.mark.skipif(
    workflow_path() is None, reason="this repository has no guidelines workflow call"
)
def test_release_call_uses_only_the_shared_interface():
    path = workflow_path()
    text = path.read_text()
    calls = guideline_calls(text)
    assert calls, "expected a job that uses the shared semantic-release workflow"
    for name, match in calls.items():
        assert match.group("file") == "semantic-release.yml"
        block = _job_block(text, name)
        assert set(_mapping(block, "with")) <= SHARED_RELEASE_INPUTS
        assert _mapping(block, "permissions").get("contents") == "write"
        used = set(re.findall(rf"needs\.{re.escape(name)}\.outputs\.([\w-]+)", text))
        assert used <= SHARED_RELEASE_OUTPUTS, sorted(used - SHARED_RELEASE_OUTPUTS)


@pytest.mark.skipif(
    workflow_path() is None, reason="this repository has no guidelines workflow call"
)
def test_an_unknown_release_output_is_reported():
    text = workflow_path().read_text()
    mutated = (
        text + "\n# needs.release.outputs.tag\n"
        if "needs.release.outputs" in text
        else text.replace(
            "permissions:", "needs.release.outputs.tag: placeholder\n  permissions:", 1
        )
    )
    found = set(re.findall(r"needs\.release\.outputs\.([\w-]+)", mutated))
    assert "tag" in found
    assert found - SHARED_RELEASE_OUTPUTS >= {"tag"}


@pytest.mark.skipif(not os.environ.get("LIVINGPROOF_NETWORK"), reason="set LIVINGPROOF_NETWORK=1")
@pytest.mark.skipif(
    workflow_path() is None, reason="this repository has no guidelines workflow call"
)
def test_recorded_interface_matches_the_pinned_shared_workflow():
    refs = {match.group("ref") for match in guideline_calls().values()}
    (ref,) = refs
    url = f"https://raw.githubusercontent.com/{REPO}/{ref}/.github/workflows/semantic-release.yml"
    with urllib.request.urlopen(url, timeout=30) as response:
        shared = response.read().decode()
    assert _workflow_call_keys(shared, "inputs") == SHARED_RELEASE_INPUTS
    assert _workflow_call_keys(shared, "outputs") == SHARED_RELEASE_OUTPUTS
