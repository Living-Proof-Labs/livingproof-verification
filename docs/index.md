<!-- SPDX-License-Identifier: Apache-2.0 -->

# Living Proof Labs verification

This repository verifies selected robot configurations against capability scenarios on every release and publishes the evidence-backed reports.

[See the reports](reports/index.html){ .md-button }

## What this repository owns

`livingproof-verification` is Living Proof Labs' own verification project. It supplies only the inputs and the published records:

- **Verification definitions** (`verifications/*.toml`): declarative choices of robot repository, ref, scenario, world, and configurations.
- **Reviewed dependency locks** (`verifications/*.lock.json`): pinned commits, configuration digests, and release tags for the verifier, runner, specs, and reporter.
- **Workflow and site configuration** (`.github/workflows/verify.yml` and `tools/site.py`): thin workflow calls that run verification and lay out the published reports.

Grading stays in [capability-verifier](https://docs.livingprooflabs.com/capability-verifier/), and report rendering stays in [livingproof-capability-report](https://docs.livingprooflabs.com/livingproof-capability-report/).

## Where to go next

- [Reports](reports/index.html) — the latest result for each robot and scenario, run histories, and preserved evidence.
- [How the reports work](how-reports-work.md) — how to read the index, history, run reports, grades, times, and spec provenance.
- [Contribute](contribute.md) — how to run a verification locally, change what is verified, and update robot or bundle pins.
