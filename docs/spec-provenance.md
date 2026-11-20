# Evidence-generation and assessment specs

The published **report** view records two roles, in a block below the result card and the
evaluators (#146). The block is a collapsed `<details>`; its one-line summary names the
assessed spec, in sentence case at body size with Show / Hide after it, for example
**Spec** v2.3.0 · walk-hallway · revision 0.1.0 (the reporter's letter-spaced capitals for
`<summary>` are switched off for this line). Inside:

- **Generation** (the evidence-generation spec, recorded pin) comes from that run's `run.json`,
  `releases.specs`, copied from the caller's resolved verification lock at publication.
  It records the generation pin. In older runners it does not prove the runner read
  a scenario parameter file from that release.
- **Assessment** comes from the saved `report.json`, `summaries[].spec`.
  The reporter carries the verifier's bundle result spec into these summaries.
  This is the catalog identified by the report's existing **Spec** field, including
  scenario capability and revision. Distinct summary identities are all shown.

When the one assessed spec is the generation pin (same repository, tag and commit), one
**Generation and assessment** line is shown instead of two. Each line links the version to its
spec release, then names the spec repository, scenario and revision, for example
`v2.3.0 · robot-capability-specs · walk-hallway · revision 0.1.0`.

**Commit ids are links, never visible text** (Aaron's rule on #146), on the index, history and
report pages. When a tag resolves, the link text is the tag. When it does not, the link text is
"commit" and it opens the commit on GitHub. The full SHA stays in the data, the link's `href`
and its `title`; it is never body text. The same holds for the history rows (a row with no
bundle release says "commit") and the robot pin in the history header. The block ends with
"Recorded at run time; not reassessed." Missing identities say “Not recorded”; they are never
filled from today's lock or the other role.

The block is inserted by `with_spec_provenance()` in `tools/site.py` just before `</main>`
(`</body>` for older HTML), so it follows the evaluators without reading the reporter's layout.
It is added only to the rendered `index.html`; report downloads, source bundles, recorded
grades and evidence are not reassessed or rewritten by this feature. No new field is
backfilled into historical `run.json`.

The **history** page does not repeat the block. Its header names `specs v…` (linked to the spec
release) with the runner, verifier and reporter, and the robot pin as its tag, written without
an "@" (`livingproof-booster-t1 v2.3.5`). When no tag resolves the pin reads
`livingproof-booster-t1 commit`, linked to the commit; a record stored without a tag takes the
tag the reviewed locks give that commit. A commit that no lock pins any more (the booster-t1
v2.2.10 commit `a00c7d88`, replaced by v2.3.5 in #145) reads `commit`: the header never claims
the new tag for an old commit, nor the old tag once the locks no longer say it. A run published
after the bump records its own tag, so the header shows `v2.3.5` for it. A row shows one quiet chip,
`Spec changed: v2.3.0 → v2.4.0`, only when its spec version or assessment revision differs
from the run below it. An unrecorded part is never a change.

The reporter's own sections of a report page, including the commit ids it records in its
data, are the reporter's markup. This site does not rewrite them; `tests/test_site.py` checks
that the block this site adds shows none, and the PR browser check reads the rendered text.

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

[Run 43954868926](https://docs.livingprooflabs.com/livingproof-verification/reports/booster-t1/stock-mujoco-hallway/runs/43954868926/)
records generation specs **v2.0.2** at
`16524cbfeef18ff0454ebf125668b010d38a3993` and assessment specs **v2.3.0** at
`57317e617014a6df97bb7fba9d92a890af8d42e5`, scenario `walk-hallway@0.1.0`.
Its recorded outcome stays **FAIL**. The fixture
`tests/fixtures/historical-spec-provenance.json` contains those provenance fields
from the published JSON, not reconstructed evidence. Tests publish a synthetic
report with those identities, restore its Pages archive, then publish another run
using current locks. Both old identities and the original saved bytes must survive on the report page.

This change does not deploy the site, regenerate observations, change thresholds,
or resolve the separate same-run collision/attempt-identity work in #100.
A deliberate reassessment still requires a new identity and retained original evidence.
