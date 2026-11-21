<!-- SPDX-License-Identifier: Apache-2.0 -->

# Contribute

This guide covers what contributors can run and change in `livingproof-verification` today: running a verification locally, updating a definition or lock, and completing robot or bundle updates.

## Run a verification locally

The recommended environment is the devcontainer (`.devcontainer/devcontainer.json`), which uses the pinned `capability-verifier-hallway` image with Python 3.12, `uv`, `git`, `jq`, OSMesa, `livingproof`, and the verifier pre-installed.

Without an editor, run the container directly from a full clone of the repository:

```sh
docker run --rm -u $(id -u):$(id -g) -v $PWD:/work \
  ghcr.io/living-proof-labs/capability-verifier-hallway:2.8.0@sha256:762875820a5c17f639107c33fbc1fe458e6075368065b57e54d2a34a7bab9599 \
  capability verify run --reporter source verifications/unitree-g1-stock-mujoco-hallway.toml
```

Results are written under `artifacts/runs/<id>/<configuration>/` (`evidence/`, `verified/`, and `reports/`), with a pass/fail tally in `artifacts/runs/<id>/summary.json`. Recordings (MCAP and video) are CI-only.

## Change what is verified

1. Edit a definition in `verifications/*.toml` (for example, to update a robot tag or configuration).
2. Regenerate its lock and review the diff:

   ```sh
   python3 tools/lock.py verifications/unitree-g1-stock-mujoco-hallway.toml
   ```

3. Update the matching job's `with.ref` (and `uses` commit if the verifier changed) in `.github/workflows/verify.yml`.
4. Verify lock and workflow consistency before opening a pull request:

   ```sh
   uvx ruff@0.14.2 check . && uvx ruff@0.14.2 format --check .
   uvx --from pytest==8.4.2 --with pyyaml==6.0.3 pytest
   actionlint
   ```

For more on how locks, `.github/requirements/`, and `site.py` local builds work, see the repository [README](https://github.com/Living-Proof-Labs/livingproof-verification/blob/main/README.md).
