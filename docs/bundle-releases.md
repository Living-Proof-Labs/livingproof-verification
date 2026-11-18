<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Bundle releases

Every push to `main` that passes verification can publish one immutable release of this
repository. The release is the **verification bundle**: the definitions in `verifications/`,
their locks, and the publishing configuration (`.github/workflows/`, `tools/`, `renovate.json`).
It is not the robot software version and it is not one run. A robot, simulator, verifier or
reporter keeps the version of its own repository, and every run keeps its own run ID.

## How a release happens

The `release` job in `.github/workflows/verify.yml` calls the shared semantic-release
workflow of [collaboration-guidelines](https://github.com/Living-Proof-Labs/collaboration-guidelines/blob/main/docs/releases.md#call-after-verification)
at one published tag. The job follows the guidelines' *Call after verification* recipe:

```yaml
release:
  needs: verification
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'
  permissions:
    contents: write
  uses: Living-Proof-Labs/collaboration-guidelines/.github/workflows/semantic-release.yml@vX.Y.Z
```

Two additions to the recipe, both on purpose: the `if` also names the `push` event, so a manual
run on `main` never releases; and the `site` job waits for `release` (ordering only) so the run
that publishes the evidence can name the tag that now points at its own commit.

The version comes from the commit types of the squash-merge titles, as the guidelines'
[policy](https://github.com/Living-Proof-Labs/collaboration-guidelines/blob/main/docs/releases.md#policy)
says: `feat` is minor; `fix`, `perf`, `docs`, `chore`, `build`, `ci`, `test`, `refactor`,
`style` and `revert` are patch; `!` or a `BREAKING CHANGE:` footer is major. Use a Conventional
Commit title and keep the PR number.

The first release has no earlier tag, so semantic-release counts every commit in the history and
the first notes are long. Later releases only list commits since the previous tag.

## Gating

| Case | Why it cannot publish | Evidence |
| --- | --- | --- |
| Pull request, merge group, manual run | `if` needs `push` on `refs/heads/main` | `test_only_a_push_to_main_with_verification_passed_publishes` evaluates the `if` for every event |
| A verification job failed, was cancelled or skipped | `release` needs `verification`, and that job fails unless every job it needs succeeded; with no status function in the `if`, GitHub skips a job whose need did not succeed | `test_the_verification_job_gates_on_every_job_it_needs` runs the job's own check for each result of each needed job |
| Verification passed on `main`, push | the release runs and publishes only when there are releasable commits | the shared workflow's own tests and the repeat-run cases below |
| Re-run on a commit that is already tagged | semantic-release finds the tag in the history, sees no new releasable commits and returns `published=false`; it never retags or moves an existing tag | repeat-run cases below |

The shared job serialises releases per repository and never cancels one that has started
(`concurrency.cancel-in-progress: false`). Its own `if` repeats the `main` rule.

### Repeat-run cases (run against the shared engine, no publishing)

`.github/actions/semantic-release` at `v9.6.10` was run against a disposable repository and
bare remote with the shared `release.config.mjs`, the commit analyser and the notes generator
(only the GitHub plugin is left out, as in the guidelines' own `engine.test.mjs`):

```text
1. first push to main (no tags yet)                            published=true v1.0.0
   tags: v1.0.0 cb2c3de | v1.0.0 resolves to HEAD: true
2. re-run on the already tagged commit                         published=false
3. second re-run on the already tagged commit                  published=false
   tag unchanged: true | tags: v1.0.0 cb2c3de
4. re-run of the OLD tagged commit after main moved on         published=false
   tags: v1.0.0 cb2c3de | v1.0.0 still at first commit: true
5. next push to main (new fix commit) releases once            published=true v1.0.1
6. re-run of that run                                          published=false
```

## Provenance: the tag and the commit

`tools/site.py build` records, in each new run's `run.json`, the commit the run verified and,
when one exists, the bundle tag:

```json
"commit": { "sha": "…", "url": "…", "tag": "v1.0.0" }
```

These are the field names proposed in issue 129 (`commit.sha`, `commit.url`, `commit.tag`), so the
index and history redesign reads them unchanged.

- **Ordering.** `bundle_tag()` resolves the tag from the `site` job's checkout, after `release`
  has finished. A run shows a tag only if a plain `vX.Y.Z` tag resolves to the run's own commit
  (annotated or lightweight). A tag on another commit, no tag, a prerelease name, a branch with
  a tag-like name, or a commit that is not in the checkout all give the commit alone, and the
  `tag` key is omitted. The newest tag is never used as a guess.
- **Commits without a release.** A `main` commit whose changes publish nothing has no tag, so
  its run shows the commit alone.
- **Old runs.** No run is retro-tagged, no report or `run.json` is rewritten, and every run ID is
  kept. Runs before the first release, and any run published without a tag, show the commit only.
- **History.** The history table's *Commit* column shows `vX.Y.Z · <short commit>` for a tagged run
  and the short commit otherwise. The tag links to the GitHub Release and the commit to its page.
- A successful release or run record says nothing about a robot passing: the run's outcome is
  still the verifier's grade.

## Moving the pin

The call pins one reviewed tag of collaboration-guidelines, the same tag as the Renovate preset
in `renovate.json`; `tests/test_guidelines_pins.py` fails if the two differ. Adoption and bump
PRs record the tag's resolved commit as evidence in the PR description, not in the workflow:
a tag names one commit, and a second identity in code would only drift. Read the diff of
`semantic-release.yml`, its `inputs` and `outputs` between the old and new tag, and update
`SHARED_RELEASE_*` in that test if the interface changed. `LIVINGPROOF_NETWORK=1 pytest
tests/test_guidelines_pins.py tests/test_bundle_release.py` compares the call and the
tag format with the pinned release.

If the release job fails, the evidence still publishes with the commit alone, the run shows the
failure, and the next push to `main` batches the unreleased commits.
