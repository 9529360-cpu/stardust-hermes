import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { afterEach, expect, test } from 'vitest'

import { hasGitMetadata, probeUpdateCheckout } from './update-checkout'

const roots: string[] = []
afterEach(() => roots.splice(0).forEach(root => fs.rmSync(root, { recursive: true, force: true })))

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'd9-checkout-'))
  roots.push(root)
  const clone = path.join(root, 'clone')
  fs.mkdirSync(clone)
  const git = (...args: string[]) => execFileSync('git', args, { cwd: clone, timeout: 2000 })
  git('init', '-b', 'main')
  git('-c', 'user.name=D9', '-c', 'user.email=d9@example.invalid', 'commit', '--allow-empty', '-m', 'fixture')
  const linked = path.join(root, 'linked')
  git('worktree', 'add', '-b', 'linked-branch', linked)

  return { root, clone, linked, git }
}

test('normal clone and linked worktree report their actual branch', async () => {
  const { clone, linked } = fixture()
  expect(hasGitMetadata(linked)).toBe(true)
  expect(await probeUpdateCheckout(clone)).toEqual({ kind: 'branch', currentBranch: 'main' })
  expect(await probeUpdateCheckout(linked)).toEqual({ kind: 'branch', currentBranch: 'linked-branch' })
})

test('linked detached HEAD is a checkout with an explicit unsupported state', async () => {
  const { root, git } = fixture()
  const detached = path.join(root, 'detached')
  git('worktree', 'add', '--detach', detached)
  expect(await probeUpdateCheckout(detached)).toEqual({ kind: 'detached-head', currentBranch: 'HEAD' })
})

test('missing Git returns distinctly within three seconds', async () => {
  const { clone, root } = fixture()
  const start = performance.now()
  expect(await probeUpdateCheckout(clone, path.join(root, 'missing-git'))).toEqual({ kind: 'git-missing' })
  expect(performance.now() - start).toBeLessThan(3000)
})

test('non-git directory and invalid gitdir files remain non-git', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'd9-nongit-'))
  roots.push(root)
  expect(await probeUpdateCheckout(root)).toEqual({ kind: 'not-a-git-checkout' })
  fs.writeFileSync(path.join(root, '.git'), 'gitdir: missing\n')
  expect(hasGitMetadata(root)).toBe(false)
})
