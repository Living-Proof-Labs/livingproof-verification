# Evidence-generation and assessment specs

The published report view and each history entry distinguish two recorded roles:

- **Evidence-generation spec (recorded pin)** comes from that run's `run.json`,
  `releases.specs`, copied from the caller's resolved verification lock at publication.
  It records the generation pin. In older runners it does not prove the runner read
  a scenario parameter file from that release.
- **Assessment spec** comes from the saved `report.json`, `summaries[].spec`.
  The reporter carries the verifier's bundle result spec into these summaries.
  This is the catalog identified by the report's existing **Spec** field, including
  scenario capability and revision. Distinct summary identities are all shown.

Both roles remain labelled when their values agree. Missing identities say
“Not recorded”; they are never filled from today's lock or the other role.
Full recorded commits are displayed, including when two entries have the same tag.
The source links lead to the saved JSON documents for that particular run.
The site's explanation is added only to the rendered `index.html` view and history;
report downloads, source bundles, recorded grades and evidence are not reassessed
or rewritten by this feature. No new field is backfilled into historical `run.json`.

## How the assessment catalog is chosen

The pipeline changed under [issue #100](https://github.com/Living-Proof-Labs/livingproof-verification/issues/100).
For the historical verifier v1.24.0, the assessment jobs fetched the verifier's
own `hallway-specs.lock.json` catalog. The caller's evidence pin could therefore
be different. A matching scenario identity/revision is distinct from matching
repository release tags; neither a displayed label nor matching tags proves that
an old runner loaded the assessment's bytes.

The current declared-input workflow calls
`fetch_hallway_specs.py --from evidence/manifest.json` and assesses with `--declared`.
It fetches the specs commit declared in the evidence's embedded caller lock,
checks the checkout identity and declared file digests, and uses those files for
assessment. The declared path checks parameter integrity, parameter schema and
supported evaluator check IDs before grading. Scenario parameters must name the
same scenario ID and revision selected from the evidence plan/lock (or the
frozen format mapping for legacy evidence). A missing file or mismatched revision
is an error, not permission to silently select another catalog. These are pipeline
checks, not checks repeated or inferred by the publishing UI.

Implementation inspected at capability-verifier
[`ad60e55c6c4d4f58bd05169db71bcbfbc375ea89`](https://github.com/Living-Proof-Labs/capability-verifier/tree/ad60e55c6c4d4f58bd05169db71bcbfbc375ea89):
[`scenario.yml`](https://github.com/Living-Proof-Labs/capability-verifier/blob/ad60e55c6c4d4f58bd05169db71bcbfbc375ea89/.github/workflows/scenario.yml),
[`declared.py`](https://github.com/Living-Proof-Labs/capability-verifier/blob/ad60e55c6c4d4f58bd05169db71bcbfbc375ea89/src/capability_verifier/declared.py),
[`scenario_parameters.py`](https://github.com/Living-Proof-Labs/capability-verifier/blob/ad60e55c6c4d4f58bd05169db71bcbfbc375ea89/src/capability_verifier/scenario_parameters.py).
This description does not claim that every historical run passed today's checks.

## Historical T1 example

[Run 43954868926](https://docs.livingprooflabs.com/livingproof-verification/booster-t1/stock-mujoco-hallway/runs/43954868926/)
records generation specs **v2.0.2** at
`16524cbfeef18ff0454ebf125668b010d38a3993` and assessment specs **v2.3.0** at
`57317e617014a6df97bb7fba9d92a890af8d42e5`, scenario `walk-hallway@0.1.0`.
Its recorded outcome stays **FAIL**. The fixture
`tests/fixtures/historical-spec-provenance.json` contains those provenance fields
from the published JSON, not reconstructed evidence. Tests publish a synthetic
report with those identities, restore its Pages archive, then publish another run
using current locks. Both old identities and the original saved bytes must survive.

This change does not deploy the site, regenerate observations, change thresholds,
or resolve the separate same-run collision/attempt-identity work in #100.
A deliberate reassessment still requires a new identity and retained original evidence.
