# SPDX-License-Identifier: Apache-2.0
"""Published runs keep the exact thresholds bytes their evaluator results cite (#65).

capability-verifier v1.21+ copies each digest-checked ``thresholds.json`` into the
report artifact. The site copies it into the published run folder, beside the
report, after checking its sha256 against every citation. It is listed in the
published SHA256SUMS and in run.json, and expired-evidence pruning never removes
it. A missing, altered or substituted file fails the publish instead of showing a
citation that does not resolve. A report from before the export (no citation)
publishes unchanged, with no grading inputs claimed.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("site_tool_thresholds", ROOT / "tools/site.py")
site_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_tool)

STOCK = b'{"configuration":"stock","limits":{"lateral_limit":0.49}}\n'
PLAYGROUND = b'{"configuration":"playground","limits":{"lateral_limit":0.52}}\n'


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _result(trial: str, digest: str | None, uri: str = "thresholds.json") -> dict:
    inputs = [{"role": "observation", "uri": f"trials/{trial}.json", "sha256": "0" * 64}]
    if digest is not None:
        inputs.append({"role": "thresholds", "uri": uri, "sha256": digest})
    return {
        "id": trial,
        "evaluator_results": [
            {
                "format": "livingproof-evaluator-result/1.1.0",
                "evaluator": {"id": "livingproof-scenario"},
                "inputs": inputs,
                "descriptor": {"title": "Scenario", "purpose": "Checks each step."},
            }
        ],
    }


def _report(results: list[dict]) -> dict:
    return {
        "report_version": "1.0.0",
        "source_bundle": {"results": results},
        "summaries": [
            {
                "capability": "walk-hallway",
                "system": {"id": "unitree-g1/stock"},
                "grade": "FAIL",
                "k": 0,
                "n": len(results),
                "required_passes": len(results),
            }
        ],
    }


def _artifact(directory: Path, report: dict, thresholds: dict[str, bytes]) -> None:
    directory.mkdir(parents=True)
    (directory / "report.json").write_text(json.dumps(report))
    for name in ("report.html", "report.md", "report.txt"):
        (directory / name).write_text(name)
    for relative, data in thresholds.items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    names = [*site_tool.REPORT_FILES, *sorted(thresholds)]
    (directory / "SHA256SUMS").write_text(
        "".join(f"{_sha((directory / n).read_bytes())}  {n}\n" for n in names)
    )


RUN = {
    "id": "100",
    "commit": "a" * 40,
    "created": "2026-11-16T08:00:00Z",
    "repository": "Living-Proof-Labs/livingproof-verification",
    "server": "https://github.com",
}


def _publish(tmp_path: Path, report: dict, thresholds: dict[str, bytes]) -> Path:
    target = site_tool.targets()[0]
    reports = tmp_path / "reports"
    _artifact(reports / target["artifact"], report, thresholds)
    return site_tool.add_run(tmp_path / "site", reports, target, RUN)


def _published_runs(tmp_path: Path) -> list[Path]:
    return list((tmp_path / "site").rglob("run.json")) if (tmp_path / "site").exists() else []


def test_cited_thresholds_are_published_byte_for_byte_and_recorded(tmp_path):
    report = _report([_result("stock-seed-01", _sha(STOCK)), _result("stock-seed-02", _sha(STOCK))])
    folder = _publish(tmp_path, report, {"thresholds.json": STOCK})
    assert (folder / "thresholds.json").read_bytes() == STOCK
    record = json.loads((folder / "run.json").read_text())
    assert record["grading_inputs"] == [
        {"role": "thresholds", "path": "thresholds.json", "sha256": _sha(STOCK)}
    ]
    site_tool.write_published_checksums(folder)
    sums = (folder / "SHA256SUMS").read_text()
    assert f"{_sha(STOCK)}  thresholds.json" in sums


def test_pruning_expired_evidence_keeps_the_thresholds(tmp_path):
    folder = _publish(
        tmp_path, _report([_result("stock-seed-01", _sha(STOCK))]), {"thresholds.json": STOCK}
    )
    (folder / "evidence" / "clip").mkdir(parents=True)
    (folder / "evidence" / "clip" / "seed-01.mp4").write_bytes(b"video")
    removed = site_tool.prune_expired_evidence(tmp_path / "site", "2027-06-01T00:00:00Z", 7)
    assert removed and all("thresholds" not in path for path in removed)
    assert (folder / "thresholds.json").read_bytes() == STOCK


def test_missing_thresholds_fail_the_publish(tmp_path):
    with pytest.raises(site_tool.SiteError, match="missing: .*thresholds.json"):
        _publish(tmp_path, _report([_result("stock-seed-01", _sha(STOCK))]), {})
    assert _published_runs(tmp_path) == []


def test_altered_thresholds_fail_the_publish(tmp_path):
    altered = STOCK.replace(b"0.49", b"9.99")
    with pytest.raises(site_tool.SiteError, match="altered: .*thresholds.json"):
        _publish(
            tmp_path, _report([_result("stock-seed-01", _sha(STOCK))]), {"thresholds.json": altered}
        )
    assert _published_runs(tmp_path) == []


def test_another_configurations_thresholds_fail_the_publish(tmp_path):
    """The playground file in the stock artifact does not resolve the stock citation."""
    with pytest.raises(site_tool.SiteError, match="altered: .*thresholds.json"):
        _publish(
            tmp_path,
            _report([_result("stock-seed-01", _sha(STOCK))]),
            {"thresholds.json": PLAYGROUND},
        )
    assert _published_runs(tmp_path) == []


def test_one_path_cited_with_two_digests_is_substituted(tmp_path):
    report = _report(
        [_result("stock-seed-01", _sha(STOCK)), _result("stock-seed-02", _sha(PLAYGROUND))]
    )
    with pytest.raises(site_tool.SiteError, match="substituted: thresholds.json"):
        _publish(tmp_path, report, {"thresholds.json": STOCK})
    assert _published_runs(tmp_path) == []


@pytest.mark.parametrize(
    "uri", ["../thresholds.json", "/tmp/thresholds.json", "https://x/thresholds.json"]
)
def test_a_citation_outside_the_report_is_substituted(tmp_path, uri):
    with pytest.raises(site_tool.SiteError, match="substituted"):
        _publish(
            tmp_path,
            _report([_result("stock-seed-01", _sha(STOCK), uri)]),
            {"thresholds.json": STOCK},
        )


def test_thresholds_that_disagree_with_the_incoming_sha256sums_fail(tmp_path):
    target = site_tool.targets()[0]
    reports = tmp_path / "reports"
    directory = reports / target["artifact"]
    _artifact(
        directory, _report([_result("stock-seed-01", _sha(STOCK))]), {"thresholds.json": STOCK}
    )
    sums = (directory / "SHA256SUMS").read_text().replace(_sha(STOCK), "f" * 64)
    (directory / "SHA256SUMS").write_text(sums)
    with pytest.raises(site_tool.SiteError, match="thresholds.json: does not match SHA256SUMS"):
        site_tool.add_run(tmp_path / "site", reports, target, RUN)


def test_a_report_from_before_the_export_publishes_with_no_grading_inputs(tmp_path):
    folder = _publish(tmp_path, _report([_result("stock-seed-01", None)]), {})
    assert not (folder / "thresholds.json").exists()
    assert "grading_inputs" not in json.loads((folder / "run.json").read_text())
