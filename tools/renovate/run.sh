#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Install the Renovate release Mend runs for this repository, validate renovate.json,
# and extract the reporter pin from the real verifications/settings.toml.
set -euo pipefail
cd "$(dirname "$0")/../.."

version=44.228.0
node_version=$(node -p "process.versions.node")
node_major=${node_version%%.*}
node_rest=${node_version#*.}
node_minor=${node_rest%%.*}
if (( node_major < 24 || (node_major == 24 && node_minor < 15) )); then
  echo "Node >= 24.15 is required so Renovate 44.228.0 can load RE2 (found v${node_version})" >&2
  exit 1
fi

dest="${RENOVATE_INSTALL_DIR:-${RUNNER_TEMP:-/tmp}/livingproof-renovate-${version}}"
stamp="${dest}/.version"
if [[ ! -f "${stamp}" || "$(cat "${stamp}")" != "${version}" ]]; then
  rm -rf "${dest}"
  mkdir -p "${dest}"
  npm install --prefix "${dest}" "renovate@${version}" --no-fund --no-audit
  echo "${version}" > "${stamp}"
fi
export RENOVATE_ROOT="${dest}/node_modules/renovate"
installed=$(node -p "require('${RENOVATE_ROOT}/package.json').version")
if [[ "${installed}" != "${version}" ]]; then
  echo "installed renovate ${installed}, expected ${version}" >&2
  exit 1
fi
node --input-type=module -e "
import { regexEngineStatus } from '${RENOVATE_ROOT}/dist/util/regex.js';
if (regexEngineStatus.type !== 'available') {
  console.error('Renovate fell back from RE2:', regexEngineStatus);
  process.exit(1);
}
"

"${dest}/node_modules/.bin/renovate-config-validator" --strict --no-global renovate.json
node --test --test-reporter=spec tools/renovate/test.mjs
