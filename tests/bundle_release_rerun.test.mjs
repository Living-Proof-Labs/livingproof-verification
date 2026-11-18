// SPDX-License-Identifier: Apache-2.0
// Re-run cases for this repository's bundle release (#131).
//
// Runs the shared semantic-release engine plus the shared release.config.mjs that
// verify.yml calls, in disposable git repositories. Only the commit analyser and
// notes generator are loaded: the GitHub plugin is left out, as in the guidelines'
// own engine.test.mjs, so the run is git-only and talks to a local bare remote
// (no GitHub, no token, no network).
//
// A re-run on an already-tagged commit, including an old tagged commit after main
// has moved on, must publish nothing and must not move or recreate the tag.
//
// This is not collected by pytest. The `bundle-release-engine` job in
// `.github/workflows/checks.yml` checks out collaboration-guidelines at the pinned
// SHA (v9.6.10, 54dcb297c628a4853c79bb31b5155e859afb7e93), installs the action
// from its lockfile, and runs:
//   SHARED_RELEASE_ACTION=... node --test tests/bundle_release_rerun.test.mjs
// Locally, check out that same commit, run
// `npm ci --omit=dev --ignore-scripts` in `.github/actions/semantic-release`,
// and use the same command.

import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
import { Writable } from 'node:stream';

const action = process.env.SHARED_RELEASE_ACTION;
assert.ok(
  action,
  'SHARED_RELEASE_ACTION must point at the pinned collaboration-guidelines '
    + '.github/actions/semantic-release directory (see the bundle-release-engine job)',
);

const semanticRelease = (
  await import(pathToFileURL(join(action, 'node_modules/semantic-release/index.js')).href)
).default;
const config = (await import(pathToFileURL(join(action, 'release.config.mjs')).href)).default;

function git(cwd, ...args) {
  return execFileSync('git', args, {
    cwd,
    encoding: 'utf8',
    stdio: ['pipe', 'pipe', 'pipe'],
  }).trim();
}

function tagList(remote) {
  const out = git(remote, 'tag', '--list', '--format=%(refname:short) %(objectname)');
  const tags = {};
  if (!out) return tags;
  for (const line of out.split('\n')) {
    const space = line.indexOf(' ');
    tags[line.slice(0, space)] = line.slice(space + 1);
  }
  return tags;
}

const sink = new Writable({
  write(_chunk, _encoding, done) {
    done();
  },
});

test('re-running the shared engine on an already tagged commit publishes nothing', async () => {
  const root = await mkdtemp(join(tmpdir(), 'lpv-bundle-release-'));
  const remote = join(root, 'origin.git');
  const work = join(root, 'work');
  const url = 'https://github.com/example/bundle';
  let n = 0;

  const run = async (sha) => {
    // What the hosted release job has: a full-history checkout detached at the
    // push SHA, with GITHUB_REF still refs/heads/main.
    const dir = join(root, `runner-${n++}`);
    execFileSync('git', ['clone', '-q', remote, dir], { stdio: 'pipe' });
    git(dir, 'config', 'user.name', 't');
    git(dir, 'config', 'user.email', 't@example.invalid');
    git(dir, 'config', `url.file://${remote}.insteadOf`, url);
    git(dir, 'checkout', '-q', '--detach', sha);
    const env = {
      PATH: process.env.PATH,
      HOME: root,
      GITHUB_ACTIONS: 'true',
      GITHUB_EVENT_NAME: 'push',
      GITHUB_REF: 'refs/heads/main',
      GITHUB_SHA: sha,
      GITHUB_REPOSITORY: 'example/bundle',
      GITHUB_SERVER_URL: 'https://github.com',
      GITHUB_RUN_ID: String(n),
      GIT_AUTHOR_NAME: 't',
      GIT_AUTHOR_EMAIL: 't@example.invalid',
      GIT_COMMITTER_NAME: 't',
      GIT_COMMITTER_EMAIL: 't@example.invalid',
    };
    const result = await semanticRelease(
      {
        ...config,
        plugins: config.plugins.slice(0, 2),
        extends: [],
        repositoryUrl: url,
      },
      { cwd: dir, env, stdout: sink, stderr: sink },
    );
    return {
      published: Boolean(result && result.nextRelease),
      gitTag: result?.nextRelease?.gitTag ?? '',
      tags: tagList(remote),
    };
  };

  const commit = (message) => {
    git(work, 'commit', '-q', '--allow-empty', '-m', message);
    git(work, 'push', '-q', 'origin', 'main');
    return git(work, 'rev-parse', 'HEAD');
  };

  try {
    execFileSync('git', ['init', '-q', '--bare', '--initial-branch=main', remote]);
    execFileSync('git', ['clone', '-q', remote, work], { stdio: 'pipe' });
    git(work, 'config', 'user.name', 't');
    git(work, 'config', 'user.email', 't@example.invalid');
    git(work, 'config', `url.file://${remote}.insteadOf`, url);

    const c1 = commit('feat: first bundle');
    const first = await run(c1);
    assert.equal(first.published, true);
    assert.equal(first.gitTag, 'v1.0.0');
    assert.equal(first.tags['v1.0.0'], c1);

    for (const label of ['first re-run', 'second re-run']) {
      const again = await run(c1);
      assert.equal(again.published, false, label);
      assert.equal(again.gitTag, '', label);
      assert.deepEqual(again.tags, first.tags, label);
      assert.equal(again.tags['v1.0.0'], c1, label);
    }

    const c2 = commit('fix: lock refresh');
    const stale = await run(c1);
    assert.equal(stale.published, false, 'old tagged commit after main moved');
    assert.equal(stale.tags['v1.0.0'], c1);
    assert.deepEqual(Object.keys(stale.tags), ['v1.0.0']);

    const second = await run(c2);
    assert.equal(second.published, true);
    assert.equal(second.gitTag, 'v1.0.1');
    assert.equal(second.tags['v1.0.0'], c1);
    assert.equal(second.tags['v1.0.1'], c2);

    const again = await run(c2);
    assert.equal(again.published, false);
    assert.deepEqual(again.tags, second.tags);
    assert.equal(again.tags['v1.0.0'], c1);
    assert.equal(again.tags['v1.0.1'], c2);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
