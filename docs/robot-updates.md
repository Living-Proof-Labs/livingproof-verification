<!-- SPDX-License-Identifier: Apache-2.0 -->

# Completing robot release proposals

Keel owns completion of eligible robot release proposals in this repository.
Renovate proposes readable robot tags only; Keel generates the affected locks,
synchronizes workflow commits, and records exact-head checks for the normal queue.
Automerge remains off. Hosted Mend cannot execute this repository's generator.

## Definition policy

The [explicit hallway hold in issue #98](https://github.com/Living-Proof-Labs/livingproof-verification/issues/98#issuecomment-6931887400)
remains in force. The [later release-ref assignment](https://github.com/Living-Proof-Labs/livingproof-verification/issues/98#issuecomment-6999558108)
requests proposals for the three robot repositories; this implementation applies
that assignment to the six existing corner definitions, retaining the hallway
exception. Eligible means eligible for a proposal, not permission to change the
scenario, world, grading limits, or historical evidence.

Inventory at the PR #152 source baseline (paths are under `verifications/`):

| Definition | Robot tag | Robot-release policy |
| --- | --- | --- |
| `booster-t1-stock-mujoco-hallway.toml` | v2.3.5 | Frozen |
| `unitree-g1-stock-mujoco-hallway.toml` | v2.3.12 | Frozen |
| `unitree-g1-playground-mujoco-hallway.toml` | v2.3.12 | Frozen |
| `unitree-go1-playground-mujoco-hallway.toml` | v2.3.5 | Frozen |
| `booster-t1-stock-mujoco-corner-left.toml` | v2.3.13 | Eligible proposal |
| `booster-t1-stock-mujoco-corner-right.toml` | v2.3.13 | Eligible proposal |
| `unitree-g1-stock-mujoco-corner-left.toml` | v2.3.18 | Eligible proposal |
| `unitree-g1-stock-mujoco-corner-right.toml` | v2.3.18 | Eligible proposal |
| `unitree-go1-playground-mujoco-corner-left.toml` | v2.3.13 | Eligible proposal |
| `unitree-go1-playground-mujoco-corner-right.toml` | v2.3.13 | Eligible proposal |

The robot manager uses an exact file allowlist. All four hallway definitions,
settings, generated locks, fixtures, and new/unclassified definitions are outside
it. A new definition or a request to thaw a hallway baseline needs a recorded
freshness decision from Rivet before changing the allowlist. Do not infer policy
from a new filename. This robot-specific boundary does not change the separate
verifier/reporter completion policies.

## Completion

1. Confirm the proposal touches an eligible definition and names a published
   `vX.Y.Z` robot release. Preserve existing completion commits; do not ask Renovate
   for a destructive rebase. Leave frozen definitions and historical reports alone.
2. Run `python3 tools/lock.py verifications/<definition>.toml` for each affected
   definition in the prepared repository environment. Do not use `--update` as a
   robot updater: it refreshes verifier/evaluator pins and preserves robot pins.
3. Read `robot.commit` from each generated lock and copy that value to the matching
   job's `with.ref` in `.github/workflows/verify.yml`. Never invent or hand-edit lock
   hashes, commits, or digests. Keep the readable tag, generated lock and workflow
   commit together in the completion commit.
4. Inspect ownership. All ten current definitions explicitly own scenario, world,
   plan and specs; their runner comes from the selected robot release's `uv.lock`.
   The generator verifies that runner tag resolves to its recorded commit. A legacy
   robot-owned lock supplies its own scenario/world/plan/specs/runner. A thin robot
   **without** explicit definition ownership preserves those fields from an existing
   lock and emits a warning. Such preservation is a retained compatibility baseline,
   not proof of a current simulator stack; stop and obtain a recorded migration
   decision if the proposed outcome requires advancing it.
5. Run `pytest tests/test_locks.py` and the required checks/verification pipelines
   on the exact completed head. The definition hash detects ref-only changes and
   workflow consistency detects a stale `with.ref`. Record the generated diff and
   results before the normal queue; a robot FAIL is not itself an infrastructure
   failure. Keel hands implementation changes to a separate independent reviewer.

## Evidence and remaining work

`tools/renovate/run.sh` exercises real RE2 extraction, replacement and file selection
against the configured shared preset. `pytest
tests/test_robot_release_update.py` exercises real released robot inputs and the
actual lock generator in disposable copies, including stale-update rejection and
legacy/thin ownership. It changes no committed definition, lock or report.

These tests are not a hosted Renovate proposal or a publication. Keep issue #98
open until an eligible released change reaches a coherent hosted proposal, passes
the exact-head checks, and is verified on main with linked published evidence.
No repository writer, schedule, permission, or credential is enabled here.
