// SPDX-License-Identifier: Apache-2.0
// The official Renovate regex manager, with RE2, against the real settings file.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readdirSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

const root = process.env.RENOVATE_ROOT;
if (!root) {
  throw new Error('RENOVATE_ROOT must be the installed renovate package');
}
const { extractPackageFile } = await import(
  join(root, 'dist/modules/manager/custom/regex/index.js')
);
const { doAutoReplace } = await import(
  join(root, 'dist/workers/repository/update/branch/auto-replace.js')
);
const { GlobalConfig } = await import(join(root, 'dist/config/global.js'));
const { init } = await import(join(root, 'dist/logger/index.js'));
const { mergeChildConfig } = await import(join(root, 'dist/config/utils.js'));
const { applyPackageRules } = await import(join(root, 'dist/util/package-rules/index.js'));
const { getMatchingFiles } = await import(
  join(root, 'dist/workers/repository/extract/file-match.js')
);
const { minimatch } = await import(join(root, 'dist/util/minimatch.js'));
const { regexEngineStatus } = await import(join(root, 'dist/util/regex.js'));

await init();
const work = mkdtempSync(join(tmpdir(), 'renovate-reporter-'));
GlobalConfig.set({ localDir: work, cacheDir: join(work, 'cache') });

const read = (file) => readFileSync(file, 'utf8');
const SETTINGS = 'verifications/settings.toml';
const REPORTER = 'Living-Proof-Labs/livingproof-capability-report';
const config = JSON.parse(read('renovate.json'));
const settings = read(SETTINGS);
// Read the pin from the real file. A reporter bump must not require editing this test.
const CURRENT = settings.match(/^\[reporter\][\s\S]*?^tag = "(v\d+\.\d+\.\d+)"$/m)[1];
const [major, minor] = CURRENT.slice(1).split('.').map(Number);
const PROPOSED = `v${major}.${minor + 1}.0`;
const locks = readdirSync('verifications')
  .filter((name) => name.endsWith('.lock.json'))
  .sort()
  .map((name) => `verifications/${name}`);

function reporterManager(cfg) {
  const found = (cfg.customManagers ?? []).filter((manager) =>
    (manager.managerFilePatterns ?? []).some(
      (pattern) => pattern.includes('settings') && pattern.includes('toml'),
    ),
  );
  assert.equal(found.length, 1, 'one custom manager for verifications/settings.toml');
  return found[0];
}

async function replace(content, manager, newValue) {
  const extracted = extractPackageFile(content, SETTINGS, manager);
  assert.ok(extracted?.deps?.length);
  let output = content;
  for (const [depIndex, dep] of extracted.deps.entries()) {
    output = await doAutoReplace(
      {
        ...manager,
        ...dep,
        manager: 'regex',
        packageFile: SETTINGS,
        depIndex,
        newValue,
        autoReplaceGlobalMatch: true,
      },
      output,
      false,
    );
    assert.equal(typeof output, 'string');
  }
  return output;
}

function changedLines(before, after) {
  const left = before.split('\n');
  const right = after.split('\n');
  const changes = [];
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    if (left[index] !== right[index]) {
      changes.push({ line: index + 1, from: left[index], to: right[index] });
    }
  }
  return changes;
}

test('RE2 is the regex engine', () => {
  assert.equal(regexEngineStatus.type, 'available');
});

test('the real settings file exposes the current reporter pin', () => {
  const manager = reporterManager(config);
  assert.equal(manager.customType, 'regex');
  assert.equal(manager.datasourceTemplate, 'github-releases');
  assert.equal(manager.versioningTemplate, 'semver');
  assert.match(settings, new RegExp(`^tag = "${CURRENT}"$`, 'm'));
  const extracted = extractPackageFile(settings, SETTINGS, manager);
  assert.equal(extracted.deps.length, 1);
  const [dep] = extracted.deps;
  assert.equal(dep.depName, REPORTER);
  // The extracted pin is whatever settings.toml currently contains, and a full
  // release tag. Renovate moves that tag, so this checks the live value and the
  // vX.Y.Z shape rather than one version. Fixture replacements below stay.
  assert.equal(dep.currentValue, CURRENT);
  assert.match(dep.currentValue, /^v\d+\.\d+\.\d+$/);
  assert.equal(dep.datasource, 'github-releases');
  assert.equal(dep.versioning, 'semver');
  assert.match(dep.replaceString, new RegExp(`tag = "${CURRENT}"`));
});

test('an update to the next release changes only that tag', async () => {
  const manager = reporterManager(config);
  const lockBytes = new Map(locks.map((path) => [path, read(path)]));
  const output = await replace(settings, manager, PROPOSED);
  assert.deepEqual(
    changedLines(settings, output).map((change) => [change.from, change.to]),
    [[`tag = "${CURRENT}"`, `tag = "${PROPOSED}"`]],
  );
  assert.match(output, new RegExp(`^repository = "${REPORTER}"$`, 'm'));
  assert.match(output, /^image = "ghcr.io\/living-proof-labs\/livingproof-capability-report"$/m);
  assert.match(output, /^evidence_retention_days = 7$/m);
  const again = extractPackageFile(output, SETTINGS, manager);
  assert.equal(again.deps.length, 1);
  assert.equal(again.deps[0].currentValue, PROPOSED);
  for (const [path, bytes] of lockBytes) assert.equal(read(path), bytes, path);
});

test('robot, verifier and evaluator pins beside the real reporter block stay put', async () => {
  const manager = reporterManager(config);
  const extra = [
    '',
    '# unrelated pins the reporter manager must not rewrite',
    'robot = "v1.0.0"',
    'verifier = "v1.20.2"',
    'evaluator = "v1.2.0"',
    'repository = "Living-Proof-Labs/capability-verifier"',
    'tag = "v1.20.2"',
    'repository = "Living-Proof-Labs/livingproof-evaluator-scenario"',
    'tag = "v1.2.0"',
    '',
  ].join('\n');
  const input = settings + extra;
  const output = await replace(input, manager, PROPOSED);
  assert.equal(output, input.replace(`tag = "${CURRENT}"`, `tag = "${PROPOSED}"`));
  assert.equal(extractPackageFile(output, SETTINGS, manager).deps.length, 1);
  assert.ok(output.endsWith(extra));
});

test('CRLF settings keep their line endings and still change only the tag', async () => {
  const manager = reporterManager(config);
  const input = settings.replaceAll('\n', '\r\n');
  const output = await replace(input, manager, PROPOSED);
  assert.equal(output, input.replace(`tag = "${CURRENT}"`, `tag = "${PROPOSED}"`));
  assert.match(output, /\r\n/);
});

test('the seven locks are not managed files and their pins do not match', () => {
  const manager = reporterManager(config);
  // Four walk-hallway and three walk-corner definitions (robot-capability-specs#40).
  assert.equal(locks.length, 7);
  const files = [
    SETTINGS,
    ...locks,
    'README.md',
    '.github/workflows/verify.yml',
    '.devcontainer/devcontainer.json',
    'tools/renovate/fixtures/settings.toml',
  ];
  assert.deepEqual(getMatchingFiles({ ...manager, manager: 'regex' }, files), [SETTINGS]);
  const excluded = minimatch('verifications/*.lock.json', { dot: true });
  assert.equal(excluded.match(SETTINGS), false);
  for (const path of locks) {
    assert.equal(excluded.match(path), true, path);
    const text = read(path);
    assert.equal(extractPackageFile(text, path, manager), null, path);
    const lock = JSON.parse(text);
    assert.match(lock.robot.repository, /^Living-Proof-Labs\//);
    assert.match(lock.robot.commit, /^[0-9a-f]{40}$/);
    assert.match(lock.releases.verifier.commit, /^[0-9a-f]{40}$/);
    assert.match(lock.releases.verifier.digest, /^sha256:[0-9a-f]{64}$/);
    assert.equal(lock.releases.reporter.repository, REPORTER);
    // Same live pin as the extraction above: the tag in settings.toml, shaped
    // vX.Y.Z. A reporter bump updates the locks; this does not freeze one version.
    assert.equal(lock.releases.reporter.tag, CURRENT);
    assert.match(lock.releases.reporter.tag, /^v\d+\.\d+\.\d+$/);
    assert.match(lock.releases.reporter.commit, /^[0-9a-f]{40}$/);
    assert.match(lock.releases.reporter.digest, /^sha256:[0-9a-f]{64}$/);
    const evaluator = lock.evaluators['livingproof-evaluator-scenario'];
    assert.match(evaluator.repository, /^Living-Proof-Labs\//);
    assert.match(evaluator.tag, /^v[0-9]+\.[0-9]+\.[0-9]+$/);
    assert.match(evaluator.commit, /^[0-9a-f]{40}$/);
  }
});

test('a manager that does not name the reporter sees nothing in the real file', () => {
  const absent = {
    customType: 'regex',
    matchStrings: ['repository = "(?<depName>Living-Proof-Labs/collaboration-guidelines)"'],
    datasourceTemplate: 'github-releases',
    versioningTemplate: 'semver',
  };
  assert.equal(extractPackageFile(settings, SETTINGS, absent), null);
});

test('the organization preset still governs the reporter and the locks stay disabled', async () => {
  // One organization preset, pinned to a full release tag. Renovate moves the tag,
  // so the test checks the shape rather than a version.
  assert.equal(config.extends.length, 1);
  assert.match(
    config.extends[0],
    /^github>Living-Proof-Labs\/collaboration-guidelines:renovate-preset#v\d+\.\d+\.\d+$/,
  );
  assert.equal(config.automerge, false);
  assert.equal(config.platformAutomerge, false);
  assert.equal('ignorePaths' in config, false);
  assert.equal('ignoreUnstable' in config, false);
  assert.equal('schedule' in config, false);
  assert.equal('postUpgradeTasks' in config, false);
  const presetUrl =
    'https://raw.githubusercontent.com/Living-Proof-Labs/collaboration-guidelines/v7.10.8/renovate-preset.json';
  const preset = await (await fetch(presetUrl)).json();
  const merged = mergeChildConfig(preset, config);
  for (const manager of preset.enabledManagers) {
    assert.ok(merged.enabledManagers.includes(manager), manager);
  }
  assert.deepEqual(merged.ignorePaths, preset.ignorePaths);
  assert.equal(merged.ignoreUnstable, true);
  assert.equal(merged.rangeStrategy, 'pin');
  assert.equal(merged.automerge, false);
  assert.equal(merged.platformAutomerge, false);
  assert.deepEqual(merged.schedule, ['before 6am on monday']);
  assert.equal(merged.customManagers.length, preset.customManagers.length + 1);
  assert.deepEqual(merged.customManagers.slice(0, preset.customManagers.length), preset.customManagers);
  assert.deepEqual(merged.excludeCommitPaths, ['verifications/*.lock.json']);

  const manager = reporterManager(config);
  const dep = extractPackageFile(settings, SETTINGS, manager).deps[0];
  const reporter = await applyPackageRules(
    {
      ...merged,
      ...dep,
      manager: 'regex',
      packageName: dep.depName,
      packageFile: SETTINGS,
    },
    'test',
  );
  assert.equal(reporter.automerge, false);
  assert.equal(reporter.rebaseWhen, 'behind-base-branch');
  assert.equal(reporter.platformAutomerge, false);
  assert.equal(reporter.skipReason, undefined);
  assert.equal(reporter.groupName, undefined);
  assert.deepEqual(reporter.schedule, ['at any time']);
  assert.equal(reporter.prHourlyLimit, 0);
  assert.equal(reporter.prConcurrentLimit, 0);
  assert.equal(reporter.allowedVersions, '/^v[0-9]+\\.[0-9]+\\.[0-9]+$/');
  const allowed = new RegExp(reporter.allowedVersions.slice(1, -1));
  assert.equal(allowed.test(CURRENT), true);
  assert.equal(allowed.test(PROPOSED), true);
  for (const tag of ['v3.8.0-rc.1', '3.9.0', 'v3.9', 'v3.9.0+build', 'latest']) {
    assert.equal(allowed.test(tag), false, tag);
  }
  assert.match(reporter.prBodyNotes[0], /python3 tools\/lock\.py verifications\/<definition>\.toml/);
  assert.match(reporter.prBodyNotes[0], /Never hand-edit/);

  const lockRule = await applyPackageRules(
    {
      ...merged,
      ...dep,
      manager: 'regex',
      packageName: dep.depName,
      packageFile: locks[0],
    },
    'test',
  );
  assert.equal(lockRule.enabled, false);
  assert.equal(lockRule.skipReason, 'package-rules');

  const thirdParty = await applyPackageRules(
    { ...merged, depName: 'actions/checkout', packageName: 'actions/checkout', manager: 'github-actions' },
    'test',
  );
  assert.deepEqual(thirdParty.schedule, ['before 6am on monday']);
  assert.equal(thirdParty.prHourlyLimit, 1);
  assert.equal(thirdParty.prConcurrentLimit, 2);
  assert.equal(thirdParty.automerge, false);
  assert.equal(thirdParty.rebaseWhen, preset.rebaseWhen);

  const verifier = await applyPackageRules(
    {
      ...merged,
      depName: 'Living-Proof-Labs/capability-verifier',
      packageName: 'Living-Proof-Labs/capability-verifier',
      manager: 'github-actions',
    },
    'test',
  );
  assert.equal(verifier.groupName, 'capability verifier and evidence');
  assert.equal(verifier.automerge, false);
  assert.equal(verifier.rebaseWhen, 'behind-base-branch');
  assert.deepEqual(verifier.schedule, ['at any time']);
});

test('two bumps in a row still find the one pin', async () => {
  const manager = reporterManager(config);
  const once = await replace(settings, manager, PROPOSED);
  const twice = await replace(once, manager, `v${major}.${minor + 2}.0`);
  const extracted = extractPackageFile(twice, SETTINGS, manager);
  assert.equal(extracted.deps.length, 1);
  assert.equal(extracted.deps[0].currentValue, `v${major}.${minor + 2}.0`);
});

test('excludeCommitPaths covers exactly the seven locks among tracked files', () => {
  const tracked = execFileSync('git', ['ls-files'], { encoding: 'utf8' }).trim().split('\n');
  const matchers = config.excludeCommitPaths.map((pattern) => minimatch(pattern, { dot: true }));
  const excluded = tracked.filter((file) => matchers.some((matcher) => matcher.match(file)));
  assert.deepEqual(excluded.sort(), [...locks].sort());
});

test('no tracked file other than settings.toml yields a reporter dependency', () => {
  const manager = reporterManager(config);
  const tracked = execFileSync('git', ['ls-files'], { encoding: 'utf8' }).trim().split('\n');
  for (const file of tracked) {
    if (file === SETTINGS) continue;
    let text;
    try {
      text = read(file);
    } catch {
      continue;
    }
    assert.equal(extractPackageFile(text, file, manager), null, file);
  }
});
