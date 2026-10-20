import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const picker = path.join(repo, 'scripts/sandbox/pick-release-tags.sh');
const matrix = path.join(repo, 'scripts/sandbox/generate-e2e-matrix.mjs');

function createFixtureRepositories(t) {
  const temp = mkdtempSync(path.join(os.tmpdir(), 'release-tag-picker-'));
  t.after(() => rmSync(temp, { recursive: true, force: true }));
  const repoWithoutTags = path.join(temp, 'without-tags');
  const repoWithTag = path.join(temp, 'with-tag');
  mkdirSync(repoWithoutTags);
  mkdirSync(repoWithTag);
  execFileSync('git', ['init', repoWithoutTags], { stdio: 'ignore' });
  execFileSync('git', ['init', repoWithTag], { stdio: 'ignore' });
  return { repoWithoutTags, repoWithTag };
}

function git(cwd, ...args) {
  execFileSync('git', args, { cwd, stdio: 'ignore' });
}

test('release picker distinguishes a pre-release empty tag set from normal strict use', t => {
  const { repoWithoutTags, repoWithTag } = createFixtureRepositories(t);
  git(repoWithTag, 'config', 'user.email', 'ci@example.invalid');
  git(repoWithTag, 'config', 'user.name', 'CI fixture');
  execFileSync('git', ['-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', 'tag fixture'], {
    cwd: repoWithTag,
    stdio: 'ignore'
  });
  git(repoWithTag, 'tag', 'v2026.9.24');

  const empty = spawnSync('bash', [picker, '--repo', repoWithoutTags, '--count', '2', '--allow-empty'], {
    encoding: 'utf8'
  });
  assert.equal(empty.status, 0, empty.stderr);
  assert.equal(empty.stdout.trim(), '[]');

  const strictEmpty = spawnSync('bash', [picker, '--repo', repoWithoutTags, '--count', '2'], {
    encoding: 'utf8'
  });
  assert.notEqual(strictEmpty.status, 0);
  assert.match(strictEmpty.stderr, /no release tags found/);

  const selected = spawnSync('bash', [picker, '--repo', repoWithTag, '--count', '2', '--allow-empty'], {
    encoding: 'utf8'
  });
  assert.equal(selected.status, 0, selected.stderr);
  assert.deepEqual(JSON.parse(selected.stdout), ['v2026.9.24']);
});

test('empty tag matrix is valid only when explicitly allowed', t => {
  createFixtureRepositories(t);
  const strict = spawnSync('node', [matrix, '--tags', '[]'], { encoding: 'utf8' });
  assert.equal(strict.status, 1);
  assert.match(strict.stderr, /no upgrade tags selected/);

  const generated = spawnSync('node', [matrix, '--allow-empty', '--tags', '[]'], { encoding: 'utf8' });
  assert.equal(generated.status, 0, generated.stderr);
  assert.deepEqual(JSON.parse(generated.stdout), {
    linux: { include: [] },
    windows: { include: [] },
    macos: { include: [] }
  });

  const plan = spawnSync('node', [matrix, '--allow-empty', '--tags', '[]', '--format', 'markdown'], { encoding: 'utf8' });
  assert.equal(plan.status, 0, plan.stderr);
  assert.match(plan.stdout, /No released versions are available/);
  assert.match(plan.stdout, /not applicable/);
});
