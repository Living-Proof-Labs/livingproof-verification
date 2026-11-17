# SPDX-License-Identifier: Apache-2.0
"""A run's evidence and assessment must record the same specs identity.

livingproof-verification#100 requires one declared specs identity for both
execution and assessment. The T1 hallway run 43954868926 recorded evidence
specs v2.0.2 and assessment specs v2.3.0; the match test must fail that split.
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent / "fixtures"
HISTORICAL = ROOT / "issue-100-t1-run-43954868926"
DECLARED = ROOT / "declared-specs-identities"
GITHUB = "https://github.com/"
SPECS_V202 = (
    "Living-Proof-Labs/robot-capability-specs",
    "v2.0.2",
    "16524cbfeef18ff0454ebf125668b010d38a3993",
)
SPECS_V230 = (
    "Living-Proof-Labs/robot-capability-specs",
    "v2.3.0",
    "57317e617014a6df97bb7fba9d92a890af8d42e5",
)


def specs_identity(spec: dict) -> tuple[str, str, str]:
    """Repository, tag and commit, with a github URL reduced to OWNER/NAME."""
    repository = spec["repository"].removeprefix(GITHUB).removesuffix(".git")
    return (repository, spec["tag"], spec["commit"])


def evidence_specs(run: dict, report: dict) -> dict:
    """The specs identity the evidence / caller lock recorded."""
    from_run = run["releases"]["specs"]
    identities = []
    for result in report["source_bundle"]["results"]:
        identities.append(result["provenance"]["lock"]["content"]["releases"]["specs"])
    assert identities, "report has no bundle results"
    first = specs_identity(identities[0])
    assert all(specs_identity(item) == first for item in identities)
    assert specs_identity(from_run) == first
    return identities[0]


def assessment_specs(report: dict) -> dict:
    """The specs identity each bundle result recorded for assessment."""
    results = report["source_bundle"]["results"]
    assert results, "report has no bundle results"
    first = specs_identity(results[0]["spec"])
    assert all(specs_identity(result["spec"]) == first for result in results)
    return results[0]["spec"]


def _load(directory: Path) -> tuple[dict, dict]:
    return (
        json.loads((directory / "run.json").read_text()),
        json.loads((directory / "report.json").read_text()),
    )


def test_issue_100_t1_run_recorded_a_v2_0_2_vs_v2_3_0_specs_split():
    """Historical evidence is left intact; the match test was written to reject this."""
    run, report = _load(HISTORICAL)
    evidence = evidence_specs(run, report)
    assessment = assessment_specs(report)
    assert specs_identity(evidence) == SPECS_V202
    assert specs_identity(assessment) == SPECS_V230
    assert specs_identity(evidence) != specs_identity(assessment)


def test_evidence_and_assessment_specs_identities_match():
    run, report = _load(DECLARED)
    evidence = evidence_specs(run, report)
    assessment = assessment_specs(report)
    assert specs_identity(evidence) == SPECS_V230
    assert specs_identity(evidence) == specs_identity(assessment)
