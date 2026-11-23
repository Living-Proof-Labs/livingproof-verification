<!-- SPDX-License-Identifier: Apache-2.0 -->

# How the reports work

Every push to `main` that passes the verification pipeline publishes the resulting reports under [Reports](reports/index.html), keeping earlier runs restored from the last successfully deployed site snapshot of a push of this repository's verify workflow. The repository match is by id. A release re-runs that report publication and the deploy together. Rolling runs do not wait for a release.

## The reports index

The [reports index](reports/index.html) shows the latest result for each robot and scenario:

- **Needs attention**: appears when any latest result failed, ordered newest first. Each card opens that run's report directly.
- **Robot sections**: one collapsible section per robot, showing scenario counts, status dots, and the latest release and time. Failing scenarios appear first inside each section.

## Run history

Opening a scenario's history (`reports/<robot>/<variant>/`) lists its recorded runs newest first:

- Each row shows the outcome badge, failing evaluator chips (when present), human-reviewer finding summary (when stored), bundle release tag or commit link, timestamp, poster (`CI` or reviewer), and a link to the full run report.
- The `Latest report` link at the top of the history page redirects to the newest run without storing a second copy.
- A `Spec changed` chip appears on a row only when its spec version or assessment revision differs from the run below it.

## The run report and evidence

Each run folder (`reports/<robot>/<variant>/runs/<run-id>/`) holds the styled HTML report rendered by `livingproof-capability-report`, alongside `report.json`, `report.md`, `report.txt`, `SHA256SUMS`, and `run.json`.

- **Evaluators**: each trial is checked by the configured evaluators. Clicking an evaluator chip on the index or history page jumps directly to that evaluator's section on the run report.
- **Preserved evidence**: MCAP recordings and PASS/FAIL videos linked by the report are copied into `evidence/` on the site and kept for `site.evidence_retention_days` (configured in `verifications/settings.toml`), then pruned on later publishes while the report pages and checksums remain.

## What the grades mean

- **PASS**: the robot met the scenario's pass criteria for the evaluated trials.
- **FAIL**: the robot did not meet the scenario's pass criteria. This is a robot capability verdict, not an infrastructure failure.
- **Not graded (`INSUFFICIENT_EVIDENCE` or `UNSUPPORTED`)**: missing or incomplete evidence, or a scenario/configuration the evaluator does not support.

## Times on the reports

Run creation times are stored as UTC in `run.json`. The index and history pages show relative times; hover over a time to see the date and time in Australia/Sydney (AEST or AEDT). Times do not yet follow the viewer's browser timezone.
