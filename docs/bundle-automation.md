# Reporter/verifier bundle completion (#105)

This is a prepared automation slice, not an activated writer or a claim of an
end-to-end release. It builds on main `2da9942111b1e01d63e06c7cc454c24e1a72c57e`
(#104). It does not change the 0.2.0 manifest, robot/world/spec ownership, frozen
evidence, or published history. Those boundaries remain with #98 and #100.

## Current draft admission contract (2026-11-19)

The writer drafts require all four server-enforced contexts: `checks`,
`bundle-release-engine`, `renovate`, and `verification`, each from GitHub Actions
App 15368. Strict checks, explicit `do_not_enforce_on_create: false`, ALLGREEN and
no bypass remain mandatory. The first three contexts belong to `checks.yml`; the
adapter validates each individual context check as well as successful trusted runs
of both `checks.yml` and `verify.yml`. Every check must be unique, Actions-owned,
same-suite, exact-head and literally successful. All four check identities and their
trusted run/attempt identities are snapshotted and revalidated before enqueue; a
successful workflow summary cannot substitute for an omitted or skipped check.
See the [central writer draft](https://github.com/Living-Proof-Labs/.github/pull/144)
for the current receipt-based publication and bounded recovery contract.

The dated design and provisioning sections below are historical. Their earlier
statements allowing informational-only checks, their pre-receipt queue commands,
and their claims that missing required contexts do not block activation are
superseded by this contract. No protection change or activation is authorized here.

## Live demonstration (not yet run)

[#189](https://github.com/Living-Proof-Labs/livingproof-verification/pull/189)
(`c5c52bf`) fixed the notes-push and Pages path. [#188](https://github.com/Living-Proof-Labs/livingproof-verification/pull/188)
pinned verifier v2.11.4, reporter v3.15.4 and evaluator-run v1.8.6, including a
settings edit this command does not take. Neither pull request is the demonstration
below. Nothing in this repository merges, dispatches a workflow, or edits a ruleset.
`.github/workflows/verify.yml` is unchanged, so this stays clear of draft #187.

### One command

```sh
python3 tools/update_bundle.py --reporter vX.Y.Z
python3 tools/update_bundle.py --verifier vX.Y.Z
```

Either flag, or both. The command is already on main. It stages the workflow calls,
hallway image, lock-check git requirements, reporter tag, the published wheel
checksum, the current-version constants in `tests/test_corner.py` and
`tests/test_site.py`, and every lock. Failed resolution writes nothing. A second run
with the same tags prints `Updated:` and changes no file. Evaluator-run stays the
pin in `verifications/settings.toml`. The v1.8.6 edit in #188 is not an argument of
this command and is not upgraded here. Prepare calls this same command from trusted
main. It does not run proposal code.

### Trusted writer

The writer is not a workflow in this repository.
`tools/automation/bundle-completion.yml` stays outside `.github/workflows` and must
not be installed beside the central writer. The central workflow is
[Living-Proof-Labs/.github#144](https://github.com/Living-Proof-Labs/.github/pull/144)
(`Complete selected repository bundles`), still a draft at
`55f6d44c7eeede5439e3481af19976fcde192043`. After that workflow is on `.github`
main it checks out this repository's main and runs, in one concurrency group:

1. Prepare, read-only token: `python3 tools/bundle_proposal.py discover`, then
   `prepare`. The checkout is the immutable main SHA. The pull request is data.
2. Publish, a fresh one-repository App token and no dependency install:
   `python3 tools/bundle_operations.py publish --plan PLAN --sha256 DIGEST --receipt RECEIPT`.
   The commit's parent is the expected head. The push is
   `--force-with-lease=refs/heads/BRANCH:HEAD`.
3. Queue, another fresh App token:
   `python3 tools/bundle_queue.py --receipt RECEIPT --sha256 RECEIPT_DIGEST`.
   It reads public check and ruleset evidence, revalidates it, then calls
   `enqueuePullRequest` with `expectedHeadOid` and `jump: false`. It does not call
   `enablePullRequestAutoMerge` or `mergePullRequest`. A missing check, a failed
   check, a moved head, or a ruleset that does not match the contract is a refusal.

### Grants required before that run

Read on 2026-11-22 with no credentials:

- Ruleset [26346316](https://github.com/Living-Proof-Labs/livingproof-verification/rules/26346316),
  "Protect main: pull requests only", enforcement `active`. Its rules are deletion,
  non-fast-forward, pull request, and merge queue. The queue grouping is ALLGREEN.
  There is no `required_status_checks` rule.
- `GET /repos/Living-Proof-Labs/livingproof-verification/rulesets/26346316` does not
  include `bypass_actors`.

`tools/bundle_queue.py` refuses while either fact remains. A missing `bypass_actors`
field is not treated as an empty list. Informational checks are not enough to enqueue.

These have to happen in order. This repository cannot do them.

1. Aaron approves the ruleset change, and it is applied through the protected Apply
   repository settings workflow ([.github #141](https://github.com/Living-Proof-Labs/.github/issues/141)).
   The rule must require `checks`, `bundle-release-engine`, `renovate`, and
   `verification`, each with integration id 15368, `strict_required_status_checks_policy`
   true, and `do_not_enforce_on_create` false. No bypass actor. The public ruleset
   read the queue uses must return `bypass_actors` as `[]`. If GitHub only returns
   that field to an authenticated admin, the organization owner has to make that
   evidence available to this unauthenticated read, or approve a read that returns
   it, without a bypass and without a new write scope for the writer.
2. A maintainer of Living-Proof-Labs/.github merges #144, or a successor rebased on
   current `.github` main, only after step 1. That merge installs the hourly and
   manual writer. The `ci-automation` environment on `.github` main must already hold
   variable `CI_AUTOMATION_APP_ID` (`5829119`) and secret `CI_AUTOMATION_APP_PRIVATE_KEY`
   for App `living-proof-labs-ci-automation`, installation `183589516`, limited to
   this repository, with metadata read and contents, pull requests, and workflows
   write. Those were recorded as configured. This change does not read or copy them.
   If the variable or secret is absent, the environment owner on `.github` sets them.
   If enqueue then fails for lack of permission, the organization owner adds the
   missing grant on that App. Do not substitute `GITHUB_TOKEN` or create
   `BUNDLE_WRITER_TOKEN`.

### Evidence that proves the demonstration

Use the next real Renovate pull request that changes only a reporter tag, or only
the verifier workflow call and hallway image. Do not open a stand-in pull request
and do not merge it by hand.

1. The Renovate pull request and its head, and the prepare job's `pr` and `head`.
2. The central workflow run. Prepare's digest. Publish's `head`, `pr`, `branch`,
   and `receipt_digest`. The new commit's parent is the Renovate head. The diff is
   only allowlisted generated files. The prepare log shows the second generation
   changed nothing.
3. On that published head, four successful `pull_request` checks named `checks`,
   `bundle-release-engine`, `renovate`, and `verification`, each from App 15368, and
   the `checks.yml` and `verify.yml` runs completed with success. A failed or skipped
   check leaves no merge-queue entry.
4. The queue job log: one `enqueuePullRequest` whose `expectedHeadOid` is the
   published head and whose `jump` is false, plus a `mergeQueueEntry` id. The pull
   request's queue entry is that id. There is no auto-merge mutation.
5. The merge-group run of those checks, then the merge commit on main. The main
   `verify` run succeeds, including `release`, `site`, and `pages`. The Pages
   deployment sha is that merge commit, and `version.html` shows it. One published
   run page shows the new reporter or verifier tag. A robot FAIL on that page is a
   simulated outcome, not a failed pipeline. Earlier reports remain on the site.

#188, #189, and a green local test run are not this evidence.

## Central operations proposal (2026-11-18)

The next infrastructure slice uses the confirmed name **CI Automation** for the
organization-owned CI service identity. Renovate bundle completion is its first use
case; the initial installation remains selected-repository access to verification
only. Its key is proposed to be held solely by `.github`'s trusted-main
`ci-automation` environment.
The dormant central template and provisioning review are in
[`.github/tools/automation`](https://github.com/Living-Proof-Labs/.github/tree/keel/105-central-bundle-operations/tools/automation).
This branch link is temporary review navigation; replace it with the merged reference
when the infrastructure is accepted. It is not an executable dependency.

This repository supplies the generation/publication/queue adapter. The new
`tools/bundle_operations.py publish --plan PATH --sha256 DIGEST` wraps the existing
publisher and emits validated `head`, `pr`, and `branch` workflow outputs only after
successful publication. The plan digest remains supplied by trusted prepare output;
queue arguments now have a concrete same-run source. Existing stale-main/head checks,
file restrictions, lease behavior and expected-head queue mutation remain unchanged.

The central template requests a fresh one-repository App token per publication/queue
job and verifies its effective repository boundary. Generation gets no key or write
token. No credentials belong here. The old template below is retained as historical
dormant scaffolding and **must not be activated alongside the central writer**;
workflow concurrency is local to a repository. The central design supersedes its
static writer-token placeholder, not the generator or publication guards.

The earlier rejected wiring is historical evidence. This new review proposal is
specifically authorized for code/design only; it creates no App, key, installation,
environment, permission grant, active workflow or merge. Registry identity and full
selected-membership audit evidence remain provisioning work. The audit must continue
reporting selected membership as unverified until authoritative evidence is available.
The real reporter/verifier automatic-release demonstrations below remain outstanding.

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
3. **Prepared exact-head coordinator:** `tools/bundle_queue.py --pr NUMBER --head
   PUBLISHED_SHA --branch EXPECTED_BRANCH` rechecks candidate eligibility and requests
   normal queue entry with `enqueuePullRequest`, `expectedHeadOid`, and `jump: false`.
   GitHub enforces the repository's actually configured protections. The coordinator
   neither requires named status contexts nor polls/waits for informational workflows.
   It never enables deferred auto-merge, merges directly, or bypasses requirements.
   The mutation fields are defined in [GitHub's published schema](https://github.com/octokit/graphql-schema/blob/master/schema.graphql).
   A changed head, invalid target, or server refusal stops the request. An uncertain
   mutation outcome must be inspected before retrying.

   **This coordinator is not wired into the dormant template.** Its head and branch
   arguments must come from the trusted publisher's output and sealed preparation
   data in the same run, never PR-controlled input, commit messages, or author heuristics.
   No PR code, dependencies, or candidate scripts may execute in that job. A failed
   queue job should be retried with those same trusted outputs; never rerun publication
   blindly or guess a replacement head. Durable recovery beyond retained run outputs
   is not implemented.

## Discovery and stale-base liveness

Discovery checks metadata and inert proposal bytes before choosing work, so major,
malformed, deleted-input and custom-edit proposals cannot starve eligible proposals.
The reporter and grouped verifier rules now specify `rebaseWhen: "behind-base-branch"`.
This asks hosted Renovate to refresh its untouched proposals even without conflicts;
other dependency rules retain their inherited policy. Automerge remains disabled in
Renovate; bundle completion has one intended writer.

[Renovate documents](https://docs.renovatebot.com/updating-rebasing/) that ordinary
updates stop when another author adds a commit. No rebase label/checkbox, reset,
amend or force refresh is used. A stale proposal remains refused until Renovate
refreshes it, and a custom or completed branch is never rebuilt by this writer.
Hosted proof must cover both non-conflicting main movement and preservation of a
custom commit. Main movement during generation still causes an expected-main refusal.

After completion, the normal queue handles non-conflicting main advancement. Both
workflows also run on merge groups for informational observation. `verification` aggregates the lock guard and all seven
reusable pipelines and fails on any failure, cancellation or skipped dependency.
It judges pipeline completion, not the robot's capability verdict; truthful robot
FAIL remains report data. Pages still runs only on main push or explicit history reset.
Conflicted completed branches require human review or a future reviewed append-only
regeneration strategy; this code does not discard custom commits to guarantee liveness.

## Observed integration and exact activation boundary (2026-11-17)

Main `3fa0c5bf7ea6e61f319cd2af9eec7a6ec31195b0` includes #110. Its
[main run 44028270422](https://github.com/Living-Proof-Labs/livingproof-verification/actions/runs/44028270422)
completed successfully, including site and Pages jobs. This is adoption evidence,
not either requested automatic-release demonstration.

The live repository ruleset [26346316](https://github.com/Living-Proof-Labs/livingproof-verification/rules/26346316)
is active on the default branch, requires PRs and the normal merge queue, and has no
bypass actors. Its response contains no required-status-check rule. A current classic
branch-protection read returns 403 with the connected integration. The existing
[administration audit run 43828994015](https://github.com/Living-Proof-Labs/.github/actions/runs/43828994015)
reported `unprotected` / null classic required checks for this repository on November
16 (artifact `classic-protection-evidence`, ID 14206784610). That historical audit is
not a current queue-readiness certificate.

Aaron's November 16 policy explicitly keeps non-required checks informational:
[repository operations](https://github.com/Living-Proof-Labs/.github/blob/main/docs/repository-operations.md#required-checks-capability-and-staged-adoption)
and [repository policy](https://github.com/Living-Proof-Labs/.github/blob/main/config/github/repository-policy.json)
select only `.github`'s own required-check contract. The absence of required contexts
here is not a defect to repair or an activation prerequisite. The earlier proposal to
require `checks`, `renovate`, and `verification` was stronger than that policy and is
withdrawn. Existing workflow jobs and merge-group triggers remain informational unless
separately enrolled by the owner; this correction changes no workflow or protection.

Internal bundle validity remains mandatory before publication: trusted generation,
coherent release/lock resolution, checksums, repeatability and the prepare-stage guards
must succeed. This is distinct from waiting for optional PR or merge-group CI.

**Risk and post-merge observation:** with no required contexts, the normal queue can
merge while informational tests are still pending; those tests can subsequently fail.
Post-merge observation reports those failures; it does not prevent or undo the merge.
Record the merged SHA, main run, all seven reports and Pages outcome, distinguishing
pending, failed and verified publication. Do not mark delivery verified from queue
acceptance or merge alone. Robot capability FAIL remains a truthful report result,
not by itself a pipeline failure. Observation must not silently become a new queue gate.

Existing infrastructure was inspected without reading secret values or minting tokens:

- The connected GitHub App can publish draft PRs, but local `gh` is unauthenticated.
  No relevant GH or Rivet credential environment variable is present. Connector access
  is not Actions access.
- `.github/config/github/app-access.json` declares Rivet contents/workflows/PR write
  and Actions read-capable permissions. It documents intent, not accepted installation
  permissions or an Actions-accessible key in this repository.
- The existing Keel comment pilot is restricted to collaboration-guidelines and issues
  write, with a one-use approval. It cannot be repurposed for bundle publication.
- The repository-administration App is for settings/Pages, not content and PR writes.
  Do not broaden either credential to activate this feature.

Minimum remaining approval/action: identify an already approved App installation/key
that is accessible to trusted Actions for **this repository** and already has contents,
workflows and PR write; confirm its accepted scopes and bind
short-lived tokens to isolated publication/queue steps. The existing App-token action
pattern is supported, but availability of such a key here has not been established.
If none exists, credential provisioning or permission expansion requires separate
owner approval; it is not performed or implied by this PR.

Then review the template wiring: trusted-main scheduled/manual preparation with read
access; isolated publisher; isolated queue job consuming only same-run trusted output;
one shared concurrency group. Prepare needs PR read access with its read-only token.
The corrected queue command no longer reads Actions runs or rulesets; Actions read is
not an enqueue prerequisite. A later read-only post-merge observer may need Actions
read access separately. The earlier proposed Actions/PR read declarations and queue
wiring were rejected by automatic approval review and did not execute. The template
remains unchanged. Any revised permission declaration or publisher-to-queue wiring
still requires its separate approval; this correction does not retry that change.
No new workflow has been installed, no secret
bound, no token minted, no settings changed, and no writer activated.

## Acceptance evidence still required

- Run the prepared writer in the approved isolated jobs with the existing credential;
  verify the generated commit triggers all exact-head checks without manual approval.
- **Verifier release case:** a newly discovered verifier version automatically completes
  its workflow/image/requirements pins, seven locks, evaluator declaration and current
  expectations, and automerges through the normal queue under its configured protections.
- **Reporter release case:** a separately discovered reporter version automatically
  completes its settings pin, verified wheel checksum, seven locks and current
  expectations, and automerges through the normal queue under its configured protections.
  For each case, record byte-stable regeneration and links from discovery through
  published mainline evidence. A manually completed commit or manually merged
  adoption PR is not proof for either case. Local mocked tests are not live-release proof.
- Prove concurrent head changes, competing runs and custom commits fail closed on GitHub.
- Independently verify all four required contexts above in the approved server
  protections and demonstrate that missing or failing fresh merge-group checks block
  merge. Demonstrate automatic normal-queue merge with no bypass.
- Verify the merged SHA's mainline run, Pages deployment and all seven published
  reports; link discovery, proposal, run and publication. Preserve earlier evidence.
- Obtain Deep independent review of the user-run command and publication boundary
  before merging. This document does not authorize activation or merge.
