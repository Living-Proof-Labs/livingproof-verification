# SPDX-License-Identifier: Apache-2.0
"""The pinned reporter labels the Spec and Assessed-by rows with tags, never commit IDs.

Aaron's rule: no visible commit IDs on a report page. livingproof-capability-report
v3.12.5 (pull 99, #98) renders the Summary "Spec" and "Assessed by" rows as a release
tag that links to the exact commit; the SHA lives only in ``href`` and ``title``. Reporter
v3.12.1 through v3.12.4 print the full commit as visible text. Vera used to check this by
hand on a fresh render; this renders a fixture bundle with the reporter this repository
installs from verifications/settings.toml (the command CI runs before pytest) and checks
the two rows. The reporter never grades, and neither does this test.
"""

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
IDENTICAL = ROOT / "tests/fixtures/repeats/identical"
HEX = re.compile(r"\b[0-9a-fA-F]{7,40}\b")
TAG = re.compile(r"v\d+\.\d+\.\d+")


def _reporter() -> str:
    found = shutil.which("livingproof-capability-report")
    if found is None:
        pytest.fail("livingproof-capability-report is not on PATH; checks installs the pin first")
    return found


def _render(directory: Path, version: str) -> tuple[str, dict]:
    """Render the identical-repeats bundle as a run of ``version`` of the verifier."""
    work = directory / "bundle"
    shutil.copytree(IDENTICAL, work)
    bundle = json.loads((work / "manifest.json").read_text())
    bundle["producer"]["version"] = version
    (work / "manifest.json").write_text(json.dumps(bundle, indent=2) + "\n")
    destination = work / "report.html"
    subprocess.run(
        [_reporter(), "render", str(work / "manifest.json"), "--format", "html", "-o", destination],
        check=True,
        capture_output=True,
        text=True,
    )
    return destination.read_text(), bundle


class _Rows(HTMLParser):
    """The visible text, link text and link attributes of each ``<dd>`` after a ``<dt>``."""

    def __init__(self, markup: str):
        super().__init__()
        self.rows: dict[str, dict] = {}
        self._term: str | None = None
        self._in_term = False
        self._current: dict | None = None
        self._link: dict | None = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == "dt":
            self._in_term = True
            self._term = ""
        elif tag == "dd" and self._term:
            self._current = {"text": "", "links": []}
            self.rows[self._term] = self._current
        elif tag == "a" and self._current is not None:
            self._link = {"attrs": dict(attrs), "text": ""}
            self._current["links"].append(self._link)

    def handle_endtag(self, tag):
        if tag == "dt":
            self._in_term = False
        elif tag == "dd":
            self._current = None
            self._term = None
        elif tag == "a":
            self._link = None

    def handle_data(self, data):
        if self._in_term:
            self._term += data
        elif self._current is not None:
            self._current["text"] += data
            if self._link is not None:
                self._link["text"] += data


def test_spec_and_assessed_by_rows_show_tags_not_commit_ids(tmp_path):
    markup, bundle = _render(tmp_path, "2.7.5")
    rows = _Rows(markup).rows
    spec_commit = bundle["description"]["source"]["commit"]
    assessor_commit = bundle["producer"]["commit"]
    for name, commit in (("Spec", spec_commit), ("Assessed by", assessor_commit)):
        row = rows[name]
        assert not HEX.search(row["text"]), f"{name} shows a commit ID: {row['text']!r}"
        [link] = row["links"]
        assert TAG.fullmatch(link["text"]), f"{name} label {link['text']!r} is not a tag"
        assert link["attrs"]["href"].endswith(f"/commit/{commit}")
        assert link["attrs"]["title"] == commit
    assert rows["Assessed by"]["links"][0]["text"] == "v2.7.5"


def test_an_assessor_without_a_release_version_is_labelled_commit_not_by_hash(tmp_path):
    """A 0.0.0 dev build has no tag; the row says "commit", linked to the exact commit."""
    markup, bundle = _render(tmp_path, "0.0.0")
    row = _Rows(markup).rows["Assessed by"]
    assert not HEX.search(row["text"]), row["text"]
    [link] = row["links"]
    assert link["text"] == "commit"
    assert link["attrs"]["title"] == bundle["producer"]["commit"]
