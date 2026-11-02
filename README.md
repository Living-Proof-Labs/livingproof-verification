# livingproof-verification

Living Proof Labs' own thin verification project. It is a practical example of how a customer uses the shared Living Proof tools to verify selected robot configurations and publish evidence-backed results.

The planned contents are declarative verification definitions, reviewed dependency locks, thin local and CI entry points through `livingproof`, an explicit selection of runs and series for reporting, and publishing configuration. Grading stays in `capability-verifier`, and rendering stays in `livingproof-capability-report`. This repository supplies only the inputs and the records.

Scope and acceptance criteria: [Living-Proof-Labs/.github#11](https://github.com/Living-Proof-Labs/.github/issues/11).

## What is here

- `verifications/*.toml`: declarative definitions. Each one selects a robot repository, a ref and the configurations to verify. The robot repository's own `verification.lock.json` supplies the world, simulator runner, specs, trial plan, verifier and reporter releases.
- `verifications/*.lock.json`: the reviewed lock for each definition. It records the robot commit, the sha256 of the robot's lock, and the releases that lock names.
- `.github/workflows/verify.yml`: a thin call to the capability verifier's reusable `walk-hallway.yml` at the locked verifier commit, passing the locked robot repository, commit and configurations.

The first definition is `unitree-g1-stock-mujoco-hallway`: Unitree G1 with its stock controller in the MuJoCo hallway, 20 seeded trials.

## Run a verification locally

You need git, jq, [uv](https://docs.astral.sh/uv/), Docker (for the locked reporter image), and OSMesa (`libosmesa6` on Debian or Ubuntu). Install `livingproof` with the capability verifier plugin in one environment:

```sh
uv venv .venv
VIRTUAL_ENV=.venv uv pip install \
  "livingproof-common @ https://github.com/Living-Proof-Labs/livingproof-common/releases/download/v1.0.0/livingproof_common-1.0.0-py3-none-any.whl" \
  "livingproof @ git+https://github.com/Living-Proof-Labs/livingproof@v1.4.9" \
  "capability-verifier @ git+https://github.com/Living-Proof-Labs/capability-verifier@v1.10.0"
```

capability-verifier v1.10.0 is the first release that ships `livingproof capability verify`. CI installs the same versions in its `locks` job from `.github/requirements/`: the two git installs pinned by commit in `lock-check-git.txt`; every other package (the `livingproof-common` wheel and uv itself included) by sha256, installed with `--require-hashes` apart from the git installs, since uv skipped a URL wheel's hash when a git requirement shared the install. The git installs build with `--no-build-isolation` from hashed build backends in `build.txt` (hatchling, hatch-vcs, setuptools), so nothing is fetched unhashed at build time. The job first proves a wrong hash refuses the install.

Check the lock and the workflow call, then run:

```sh
.venv/bin/livingproof capability verify lock --check \
  --workflow .github/workflows/verify.yml verifications/unitree-g1-stock-mujoco-hallway.toml
.venv/bin/livingproof capability verify run verifications/unitree-g1-stock-mujoco-hallway.toml
```

`run` checks out the robot at the locked commit and the verifier at the commit the robot lock names. It then runs the same `walk-hallway.yml` step scripts as CI: plan gates, setup, evidence, assessment, and rendering with the reporter image by digest. Pass `--reporter source` to render without Docker. Recordings (MCAP and video) are CI-only.

Results go to `artifacts/runs/<id>/<configuration>/`:

- `evidence/`: the trials
- `verified/`: the verifier's decisions and bundle
- `reports/`: `report.md`, `.html`, `.json` and `.txt`

A pass/fail tally is written to `artifacts/runs/<id>/summary.json`. Everything under `artifacts/` is generated and ignored by git.

## Change what is verified

Edit a definition, re-lock it, and review the lock diff:

```sh
.venv/bin/livingproof capability verify lock verifications/unitree-g1-stock-mujoco-hallway.toml
```

Then update the call in `.github/workflows/verify.yml` to the new robot commit (and the verifier commit, if the robot lock names a new one). `tests/test_locks.py` (the `locks` CI job, which every verification job waits for) and `lock --check --workflow` fail until the two agree. Both parse the workflow YAML, so a commented-out pin does not count.

## Published reports

On every push to `main`, after the verification jobs pass, the `site` job lays this run's
capability-report artifacts out as a static site and the `pages` job deploys it to GitHub Pages
(`actions/upload-pages-artifact` and `actions/deploy-pages`, pinned by commit; only `pages` has
`pages: write` and `id-token: write`). Pull requests never publish. Layout, for each definition
and configuration (`<robot>` from the robot repository, `<variant>` from the definition id):

- `/` lists every robot and variant with the verifier's latest grade and trial counts
- `/<robot>/<variant>/latest/` is the newest run, `/<robot>/<variant>/` its run history
- `/<robot>/<variant>/runs/<run-id>/` keeps each run: the reporter's `report.{html,json,md,txt}`
  and `SHA256SUMS` unchanged, `run.json`, and a page linking the CI run, this repository's commit
  and the locked robot, specs, runner, verifier and reporter releases

`tools/site.py` only lays out and indexes: the verifier grades, the reporter renders, and the
counts are read from `report.json`. Earlier runs are restored from the previous run's Pages
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
| Reports (`report.*`, `SHA256SUMS`, `run.json`) | the Pages site, every run folder | for as long as the site's run history (each publish restores the previous Pages artifact, kept 90 days) |
| Linked evidence (MCAP, PASS/FAIL videos) | copied into `runs/<run-id>/evidence/<artifact>/` by the `site` job | with the report, same lifetime |
| Original Actions artifacts | GitHub Actions | until their `expires_at` (30 days for these runs); downloading needs a GitHub sign-in, even on a public repository |

The `site` job downloads the run's `*-links-*`, `*-mcap-*` and `*-video-*` artifacts and reads
the run's artifact metadata (`actions: read`). `run.json` keeps, for every link in the report,
the original artifact's id, name, URL, expiry and digest next to the site copy's files, sizes
and sha256, so evidence identity stays tied to the assessment. `report.json`, `report.md`,
`report.txt` and `SHA256SUMS` are published exactly as the reporter wrote them. Each run page lists the site copies first. The Actions link is shown only
while the artifact is unexpired, with its expiry date; at each later publish an expired one is
re-rendered as "expired <date>, no longer available", without a link. Runs published before
this change carry no copies: their pages say the evidence was not copied and that the links
inside the report expire.

The published `report.html` is the one exception: its evidence links are rewritten at every
publish to point at the site copies, each followed by the original Actions artifact as a
secondary reference with its expiry (unlinked once expired, or when no copy exists). The
reporter's own `report.html` is kept beside it as `report.html.orig`, which is the file
`SHA256SUMS` verifies (`sed 's/  report.html$/  report.html.orig/' SHA256SUMS | sha256sum -c`).
`report.json` and `report.md` keep the Actions URLs, as the lossless record.

A walk-hallway run's evidence is about 5.2 MB (MCAP 3.9 MB, two videos 0.6 MB each), so a run
and its `latest/` copy add about 10 MB. GitHub Pages sites are limited to 1 GB, which is
roughly 190 runs; past `MAX_RUN_EVIDENCE_BYTES` (25 MB per run) an artifact is listed as not
preserved instead of copied.

Publishing needs GitHub Pages enabled for this repository with **Source: GitHub Actions**.

## Checks

```sh
uvx ruff@0.14.2 check . && uvx ruff@0.14.2 format --check .
uvx --from pytest==8.4.2 --with pyyaml==6.0.3 pytest
actionlint
```

## License

[Apache License 2.0](LICENSE)
