# livingproof-verification

Living Proof Labs' own thin verification project. It is a practical example of how a customer uses the shared Living Proof tools to verify selected robot configurations and publish evidence-backed results.

The planned contents are declarative verification definitions, reviewed dependency locks, thin local and CI entry points through `livingproof`, an explicit selection of runs and series for reporting, and publishing configuration. Grading stays in `capability-verifier`, and rendering stays in `livingproof-capability-report`. This repository supplies only the inputs and the records.

Scope and acceptance criteria: [Living-Proof-Labs/.github#11](https://github.com/Living-Proof-Labs/.github/issues/11).

## What is here

- `verifications/*.toml`: declarative definitions. Each one selects a robot repository, a ref and the configurations to verify. `verifications/settings.toml` is not a definition; it names the reporter release.
- `verifications/*.lock.json`: the reviewed lock for each definition. `python3 tools/lock.py` writes `livingproof-verification-lock/0.2.0`: the robot commit and adapter, each configuration's sha256, and the releases, plan, world and scenario-evaluator pin this repository owns. A `0.1.0` lock (the sha256 of the robot's `verification.lock.json`, and the releases copied out of it) stays readable during the migration. The published locks are `0.2.0`.
- `.github/workflows/verify.yml`: a thin call to the capability verifier's `walk-hallway.yml`. Each job's `uses` commit is that lock's verifier commit, and the `# vX.Y.Z` comment on that line is the locked tag. `verification-lock` passes this repository's 0.2.0 lock. The pinned release reads that lock even when the robot commit still publishes its own. The call also passes the locked robot repository, commit and configurations.

The first definition is `unitree-g1-stock-mujoco-hallway`: Unitree G1 with its stock controller in the MuJoCo hallway, 3 trials.

## Run a verification locally

The recommended path is the devcontainer (`.devcontainer/devcontainer.json`): the capability verifier's hallway image, pinned by digest, the same image CI's evidence, verify and media jobs use. It has Python 3.12, uv, git, jq, OSMesa, `livingproof` and the verifier, with the locked dependencies pre-synced. Without an editor, run the image directly from a clone of this repository (a clone, not a git worktree: the container must see `.git`):

```sh
docker run --rm -u $(id -u):$(id -g) -v $PWD:/work \
  ghcr.io/living-proof-labs/capability-verifier-hallway:1.15.2 \
  capability verify run --reporter source verifications/unitree-g1-stock-mujoco-hallway.toml
```

Manual setup needs Python 3.12 (the verifier requires >=3.12,<3.13) and the tools below.

You need git, jq, [uv](https://docs.astral.sh/uv/), Docker (for the locked reporter image), and OSMesa (`libosmesa6` on Debian or Ubuntu). Install `livingproof` with the capability verifier plugin in one environment:

```sh
uv venv .venv
VIRTUAL_ENV=.venv uv pip install \
  "livingproof-common @ https://github.com/Living-Proof-Labs/livingproof-common/releases/download/v1.0.0/livingproof_common-1.0.0-py3-none-any.whl" \
  "livingproof @ git+https://github.com/Living-Proof-Labs/livingproof@v1.4.9" \
  "capability-verifier @ git+https://github.com/Living-Proof-Labs/capability-verifier@<commit in .github/requirements/lock-check-git.txt>"
```

capability-verifier v1.10.0 was the first release to ship `livingproof capability verify`; the locks job installs the capability-verifier commit from `lock-check-git.txt` (the locked verifier, which accepts lock formats `0.1.0` and `0.2.0` and lets a caller lock override a robot lock). CI installs the same versions in its `locks` job from `.github/requirements/`: the two git installs pinned by commit in `lock-check-git.txt`; every other package (the `livingproof-common` wheel and uv itself included) by sha256, installed with `--require-hashes` apart from the git installs, since uv skipped a URL wheel's hash when a git requirement shared the install. The git installs build with `--no-build-isolation` from hashed build backends in `build.txt` (hatchling, hatch-vcs, setuptools), so nothing is fetched unhashed at build time. The job first proves a wrong hash refuses the install.

Check the lock and the workflow call, then run:

```sh
.venv/bin/livingproof capability verify lock --check \
  --workflow .github/workflows/verify.yml verifications/unitree-g1-stock-mujoco-hallway.toml
.venv/bin/livingproof capability verify run verifications/unitree-g1-stock-mujoco-hallway.toml
```

`run` checks out the robot at the locked commit and the verifier at the commit this repository's lock names. It then runs the same `walk-hallway.yml` step scripts as CI: plan gates, setup, evidence, assessment, and rendering with the reporter image by digest. Pass `--reporter source` to render without Docker. Recordings (MCAP and video) are CI-only.

Results go to `artifacts/runs/<id>/<configuration>/`:

- `evidence/`: the trials
- `verified/`: the verifier's decisions and bundle
- `reports/`: `report.md`, `.html`, `.json` and `.txt`

A pass/fail tally is written to `artifacts/runs/<id>/summary.json`. Everything under `artifacts/` is generated and ignored by git.

## Change what is verified

Edit a definition, re-lock it, and review the lock diff:

```sh
python3 tools/lock.py verifications/unitree-g1-stock-mujoco-hallway.toml
```

`lock` writes `livingproof-verification-lock/0.2.0`. While the robot still publishes `verification.lock.json`, plan, world and the specs and runner releases are copied from that file. The reporter tag, repository and image come from `verifications/settings.toml`; the commit and digest are resolved from that tag. The verifier pin is the walk-hallway commit that definition's job calls, and the scenario-evaluator pin is read from that verifier release (recorded as `livingproof-evaluator-scenario` `v1.0.0` when that release does not declare one). A thin robot publishes the adapter and configurations only. `lock` then keeps releases, plan, world and the evaluator pin from the existing `0.2.0` and refreshes the commit, adapter and configuration sha256s. With neither source it exits with that explanation. A `0.1.0` lock beside the definition stays readable. The published locks are `0.2.0`; the locks job accepts both.

Then update the call in `.github/workflows/verify.yml` to the new robot commit and, when the lock names a new verifier, to that verifier commit. `tests/test_locks.py` (the `locks` CI job, which every verification job waits for) and `lock --check --workflow` fail until `uses`, `verification-lock` and the locked verifier and reporter agree. Both parse the workflow YAML, so a commented-out pin does not count. Every configuration's `uses` commit is its locked verifier commit.

## Complete a capability-verifier workflow bump

Renovate opens a pull request that moves the walk-hallway `uses` commit in `.github/workflows/verify.yml`. The four `verifications/*.lock.json` files have to move in that same pull request. Hosted Mend Renovate does not run `postUpgradeTasks` here: `allowedCommands` is a self-hosted admin option, and this repository's Mend app has no allowlist, so the generator does not run inside Renovate. CI fails the workflow-only pull request and names the command below.

Check out the Renovate branch and regenerate the locks:

```sh
python3 tools/lock.py --update
git add verifications/*.lock.json .github/requirements/lock-check-git.txt
git commit -m "chore: refresh verification locks for the pinned verifier"
```

A `fix:` prefix is the other conventional-commit form that fits this change. `--update` reads each job's walk-hallway commit, resolves its `vX.Y.Z` tag and the hallway image digest, and writes those fields into every lock. It also points `.github/requirements/lock-check-git.txt` at that same commit. Robot pins and the scenario-evaluator pin stay as committed. Review the diff and commit the files the command writes.

## Published reports

On every push to `main`, after the verification jobs pass, the `site` job lays this run's
capability-report artifacts out as a static site and the `pages` job deploys it to GitHub Pages
(`actions/upload-pages-artifact` and `actions/deploy-pages`, pinned by commit; only `pages` has
`pages: write` and `id-token: write`). Pull requests never publish. Layout, for each definition
and configuration (`<robot>` from the robot repository, `<variant>` from the definition id):

- `/` lists every robot and variant with the verifier's latest grade and trial counts
- `/<robot>/<variant>/latest/` redirects to the newest run and stores no second copy of it
- `/<robot>/<variant>/` is the run history: run id, time, grade, commit, checksum file and the locked release tags, as text
- `/<robot>/<variant>/runs/<run-id>/` is that run's styled report (the reporter's HTML), plus `report.json`, `report.md`, `report.txt`, `SHA256SUMS` and `run.json`

`tools/site.py` lays the reports out and asks the pinned reporter to render each run page with
a relative link to that history, the site's evidence paths and the retention setting. The
verifier grades; the counts are read from `report.json`. Earlier runs are restored from the previous run's Pages
artifact (kept 90 days); if that artifact has expired the job fails rather than drop history.
If GitHub no longer lists any earlier artifact but the live site still answers (`site.json`,
written by every build, or `index.html`), the job also fails. Only a manual run of `verify` on
`main` with the `reset-history` input publishes a new site without the earlier runs.
Nothing is committed. Build the site locally from downloaded report artifacts:

```sh
python3 tools/site.py restore site --repository Living-Proof-Labs/livingproof-verification --dry-run \
  --site-url https://docs.livingprooflabs.com/livingproof-verification/
python3 tools/site.py build site --reports reports --evidence evidence --run-id 1 \
  --commit "$(git rev-parse HEAD)" --created "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --repository Living-Proof-Labs/livingproof-verification
```

### Report and evidence retention

| What | Where | Kept |
| --- | --- | --- |
| Reports (`report.*`, `SHA256SUMS`, `run.json`, the run page) | the Pages site, every run folder | for as long as the site's run history (each publish restores the previous Pages artifact, kept 90 days) |
| Linked evidence (MCAP, PASS/FAIL videos, media) | copied into `runs/<run-id>/evidence/<name>/` by the `site` job | for `site.evidence_retention_days` in `verifications/settings.toml`, then removed on the next publish |
| Run history | the Pages site | with the reports; `latest/` is only a redirect to the newest run |

The `site` job downloads the run's `*-links-*`, `*-mcap-*` and `*-video-*` artifacts and reads
the run's metadata (`actions: read`). `run.json` keeps, for every link, the id, name, URL,
expiry and digest next to the site copy's files, sizes and sha256. That record is not copied
onto the public pages. Evidence links passed to the reporter are relative paths of the copies
on this site. A pruned copy is passed with `expired: true`, and the reporter prints
"Evidence kept for N days" from the same setting. `report.json`, `report.md`, `report.txt` and
`SHA256SUMS` stay as the reporter wrote them. When the reporter's `report.html` itself would
put a run or download address on the public page, it is kept as `report.html.orig` so
`SHA256SUMS` still verifies (`sed 's/  report.html$/  report.html.orig/' SHA256SUMS | sha256sum -c`).
The page readers open is the re-rendered report. `site.py build` fails if any HTML file it
emits contains the word "artifact" (any case) or an `href` or `src` containing `/actions/`.
Runs that never received copies are left without new files.

A several-file MCAP copy links to `evidence/<name>.html`, which lists each file's size and
sha256. A walk-hallway run's evidence is about 5.2 MB (MCAP 3.9 MB, two videos 0.6 MB each).
`latest/` no longer stores that copy again. GitHub Pages sites are limited to 1 GB; past
`MAX_RUN_EVIDENCE_BYTES` (25 MB per run) a download is listed in `run.json` as not preserved
instead of copied.

Publishing needs GitHub Pages enabled for this repository with **Source: GitHub Actions**.

## Checks

```sh
uvx ruff@0.14.2 check . && uvx ruff@0.14.2 format --check .
uvx --from pytest==8.4.2 --with pyyaml==6.0.3 pytest
actionlint
```

Pull requests run the same checks in `.github/workflows/checks.yml` (tools installed by sha256
from `.github/requirements/`), plus a site build from fixture reports
(`tools/site_fixture.py`) whose links must resolve. That job has `contents: read` only and
deploys nothing.

## License

[Apache License 2.0](LICENSE)
