"""The integrity chip's link opens the Run Integrity tab, not the first tab (#168).

A real run page has the Scenario result first and Run Integrity second, so a link to
`#run-evaluators-tab-1` focuses Scenario. Strict xfail: it passes once the site links the
integrity chips to the tab that holds Run Integrity.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_site_evaluator_anchor import BUNDLE, ROOT, _chips, _document

SCENARIO = ROOT / "tests/fixtures/evaluator-result-scenario.json"


def _page(tmp_path: Path, document: dict) -> str:
    exe = shutil.which("livingproof-capability-report")
    if exe is None:
        pytest.skip("livingproof-capability-report is not installed")
    work = tmp_path / "bundle"
    shutil.copytree(BUNDLE, work)
    manifest = json.loads((work / "manifest.json").read_text())
    for run in manifest["assessment"]["runs"]:
        run["run_id"] = document["integrity"]["run"]["id"]
    manifest["results"][0]["evaluator_results"] = [json.loads(SCENARIO.read_text())]
    manifest["assessment"]["integrity_results"] = {"livingproof-run-integrity": document}
    (work / "manifest.json").write_text(json.dumps(manifest))
    out = work / "report.html"
    subprocess.run(
        [exe, "render", str(work / "manifest.json"), "--format", "html", "-o", str(out)],
        check=True,
        capture_output=True,
    )
    return out.read_text(encoding="utf-8")


@pytest.mark.xfail(strict=True, reason="chips link to tab 1, which is Scenario on a real run page")
@pytest.mark.parametrize("status", ["ERROR", "INCONCLUSIVE"])
def test_an_integrity_chip_links_to_the_run_integrity_tab(status: str, tmp_path: Path):
    extra = {"success": False, "percentage": 0} if status == "ERROR" else {}
    document = _document(status, **extra)
    fragment = re.search(r'href="[^"]*#([^"]+)"', _chips("PASS", document)).group(1)
    page = _page(tmp_path, document)
    tab = re.search(rf"id='{fragment}'.*?eval-name-full'>([^<]*)", page, re.S).group(1)
    assert tab == "Run Integrity"
