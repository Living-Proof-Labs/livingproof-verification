# Reporter/verifier bundle completion (#105)

This is a prepared automation slice, not an activated writer or a claim of an
end-to-end release. It builds on main `2da9942111b1e01d63e06c7cc454c24e1a72c57e`
(#104). It does not change the 0.2.0 manifest, robot/world/spec ownership, frozen
evidence, or published history. Those boundaries remain with #98 and #100.

## One command

```sh
python3 tools/update_bundle.py --reporter v3.11.2 --verifier v1.25.4
```

Select either option or both. Explicit release tags are mandatory; no lookup of
latest occurs. `update_verifier.py` coordinates workflow/image/requirements pins;
`lock.generate` resolves the entire resulting bundle. Reporter wheels are downloaded
and checked against GitHub's published release-asset SHA256 before adding a checksum.
An already recorded checksum is immutable. No downloaded dependency code is executed.

Resolution happens in a temporary tree. A failed resolution, inconsistent release,
unexpected layout, changed checkout, or unrelated input drift leaves source files
untouched. Successful generation copies the complete candidate back. Ordinary write
errors roll back; this is not a crash-safe filesystem transaction. Use a clean,
exclusive checkout, then commit the candidate as one Git transaction. A second run
must return `Updated:` with no paths. The publication path uses one atomic Git ref
compare-and-swap, not a sequence of contents-API file edits.

Current-version expectations remain independent literal assertions. Generation
updates only the three named `*_RELEASE` constants in `test_corner.py`, the reporter
assertion in `test_site.py`, and the existing verifier history assertion through
`update_verifier.py`. No assertion bodies, frozen robot/spec/world pins, history
fixtures or evidence digests change. There are no per-release manual test edits.

## The one proposed writer

The dormant `tools/automation/bundle-completion.yml` template selects eligible
proposals hourly and defines a repository-owned GitHub Actions runner with two isolated jobs and a single
repository-wide concurrency group (`verification-bundle-writer`, cancellation off).
Hosted Mend remains discovery only. Its [documented command restrictions](https://docs.renovatebot.com/mend-hosted/faq/)
do not establish permission for this repository to run `postUpgradeTasks`; none is
configured. Keep Renovate automerge disabled. Once our completion commit is present,
Renovate/custom proposals are refused by this writer, rather than rebased or reset.

1. **Prepare, read-only:** check out an immutable reviewed main SHA, with credentials
   not persisted. Never check out PR code. Give only read access. Use the repository's
   hash-pinned test environment. Capture the proposed PR number and head as data.
   Run with the environment's Python:

   ```sh
   python tools/bundle_proposal.py prepare --pr "$PR" --head "$HEAD" \
     --main "$MAIN" --output "$RUNNER_TEMP/bundle.json"
   ```

   It refuses forks, unexpected branches, changed main/head, multiple/custom commits,
   unrelated files, symlinks and non-pin edits. It only accepts a single Renovate
   commit directly on current main. It reads inert proposal blobs, takes the version,
   and generates with trusted main code. It verifies repeatability and lock/corner
   assertions, then emits a SHA256 for the JSON artifact. The artifact is a publication
   envelope, not a new verification manifest.
2. **Publish, isolated:** fresh checkout of the *same* trusted main, no dependency
   install, tests, package hooks, candidate checkout or candidate scripts. Download
   only the artifact from that prepare job in the same workflow run. Pass its digest
   through the trusted job output (never trust a digest inside the artifact). Inject
   the existing approved writer credential only into this step:

   ```sh
   python3 tools/bundle_proposal.py publish --plan "$RUNNER_TEMP/bundle.json" \
     --sha256 "$PREPARE_SHA256"
   ```

   The publisher hardcodes the repository, branches and file allowlist, rechecks main
   and proposal head, creates a commit parented to that exact head, then publishes
   with `--force-with-lease=refs/heads/EXPECTED_BRANCH:EXPECTED_HEAD`. Although Git
   calls the option force-with-lease, the new commit is always a direct descendant;
   a concurrent writer causes rejection, never loss of custom commits. Replaying a
   completed proposal is refused. If a push outcome is uncertain, inspect the remote
   ref before retrying; never regenerate against a guessed head.
3. **Final-head checks and queue:** do not enable automatic queue entry yet. Before
   activation, record the actual enforced checks and their PR/merge-group execution
   paths. After all exact-head gates pass, normal queue entry can use
   `gh pr merge --auto --match-head-commit "$COMPLETED_HEAD" --repo
   Living-Proof-Labs/livingproof-verification "$PR"` (no admin/bypass flag). Use a
   trusted coordinator that rechecks the completion commit and stops for changed
   heads, missing checks, major releases or custom commits. This coordinator is not
   activated by this patch.

## Exact activation boundary

Inspection found an authenticated GitHub connector with push access, but `gh auth
status` reports no CLI login in this environment. Connector access is not an Actions
credential. No existing job-scoped writer credential or dispatch installation was
verified. Do not create a token, add a secret, change App permissions, settings or
protections to make this work as part of #105's implementation slice. Selecting and
authorizing the existing credential and installing the two-job runner is a separate
activation decision. GitHub documents that [App credentials can trigger subsequent
checks automatically](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow);
ordinary `GITHUB_TOKEN` behavior must not be assumed sufficient for unattended checks.

Existing gates in this repository are `checks` (lint, tests, workflow lint, fixture
site), `renovate` (config/extraction), `locks` (fresh integrity resolution) and all
seven verification pipelines, including compatibility/report generation. Their
current workflows run on PRs; `verify` also runs on main pushes. Neither current
workflow declares `merge_group`. Publication remains main-only. The inspected
organization policy declares a merge queue for this repository, but only `.github`
has an entry in `required_checks_by_repository`. This is policy-source evidence,
not proof of the live protections: the unauthenticated protection read returned 401.
**Do not activate automatic enqueue until live requirements and applicable merge-group
execution are audited and repaired through their owners.** No gate or trigger is
changed in this slice. Robot capability FAIL remains a truthful result, distinct
from failed integrity, compatibility or pipeline execution.

## Acceptance evidence still required

- Run the prepared writer in the approved isolated jobs with the existing credential;
  verify the generated commit triggers all exact-head checks without manual approval.
- **Verifier release case:** a newly discovered verifier version automatically completes
  its workflow/image/requirements pins, seven locks, evaluator declaration and current
  expectations, passes exact-head checks, and automerges through the normal queue.
- **Reporter release case:** a separately discovered reporter version automatically
  completes its settings pin, verified wheel checksum, seven locks and current
  expectations, passes exact-head checks, and automerges through the normal queue.
  For each case, record byte-stable regeneration and links from discovery through
  published mainline evidence. A manually completed commit or manually merged
  adoption PR is not proof for either case. Local mocked tests are not live-release proof.
- Prove concurrent head changes, competing runs and custom commits fail closed on GitHub.
- Audit and record live required checks; prove the applicable merge-group path runs
  them, then demonstrate automatic normal-queue merge with no bypass.
- Verify the merged SHA's mainline run, Pages deployment and all seven published
  reports; link discovery, proposal, run and publication. Preserve earlier evidence.
- Obtain Deep independent review of the user-run command and publication boundary
  before merging. This document does not authorize activation or merge.
