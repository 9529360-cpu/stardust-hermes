/**
 * Tests for electron/update-remote.ts — the remote-detection helpers that
 * keep passive update checks off the SSH origin for product installs.
 *
 * Run with: node --test electron/update-remote.test.ts
 * (Wired into npm test:desktop:platforms in package.json.)
 *
 * Why this matters: a Stardust install can carry
 * origin=git@github.com:9529360-cpu/stardust-hermes.git. A background
 * `git fetch origin` then authenticates over SSH and, with a FIDO2/passkey
 * key, triggers an unexplained hardware-touch prompt. isOfficialSshRemote
 * must reliably recognize the product SSH remote (in every URL form,
 * case-insensitively) so the caller can swap in the anonymous HTTPS path —
 * while NOT misclassifying upstream, other forks, other hosts, or HTTPS.
 */

import assert from 'node:assert/strict'

import { test } from 'vitest'

import {
  canonicalGitHubRemote,
  isOfficialSshRemote,
  isSshRemote,
  OFFICIAL_REPO_CANONICAL,
  OFFICIAL_REPO_HTTPS_URL
} from './update-remote'

const PRODUCT_HTTPS = 'https://github.com/9529360-cpu/stardust-hermes.git'
const PRODUCT_SSH = 'git@github.com:9529360-cpu/stardust-hermes.git'

test('canonicalGitHubRemote normalizes product SSH and HTTPS forms to the same value', () => {
  assert.equal(canonicalGitHubRemote(PRODUCT_SSH), OFFICIAL_REPO_CANONICAL)
  assert.equal(canonicalGitHubRemote('git@github.com:9529360-cpu/stardust-hermes'), OFFICIAL_REPO_CANONICAL)
  assert.equal(canonicalGitHubRemote('ssh://git@github.com/9529360-cpu/stardust-hermes.git'), OFFICIAL_REPO_CANONICAL)
  assert.equal(canonicalGitHubRemote(PRODUCT_HTTPS), OFFICIAL_REPO_CANONICAL)
  // Case-insensitive owner/repo normalization.
  assert.equal(canonicalGitHubRemote('git@github.com:9529360-CPU/STARDUST-HERMES.git'), OFFICIAL_REPO_CANONICAL)
  // Trailing slashes are stripped.
  assert.equal(canonicalGitHubRemote('https://github.com/9529360-cpu/stardust-hermes/'), OFFICIAL_REPO_CANONICAL)
})

test('canonicalGitHubRemote is empty for falsy input', () => {
  assert.equal(canonicalGitHubRemote(''), '')
  assert.equal(canonicalGitHubRemote(null), '')
  assert.equal(canonicalGitHubRemote(undefined), '')
})

test('isSshRemote detects scp-like and ssh:// forms only', () => {
  assert.equal(isSshRemote(PRODUCT_SSH), true)
  assert.equal(isSshRemote('ssh://git@github.com/9529360-cpu/stardust-hermes.git'), true)
  assert.equal(isSshRemote(PRODUCT_HTTPS), false)
  assert.equal(isSshRemote(''), false)
  assert.equal(isSshRemote(null), false)
})

test('isOfficialSshRemote is true only for the product repository over SSH', () => {
  assert.equal(isOfficialSshRemote(PRODUCT_SSH), true)
  assert.equal(isOfficialSshRemote('git@github.com:9529360-cpu/stardust-hermes'), true)
  assert.equal(isOfficialSshRemote('ssh://git@github.com/9529360-cpu/stardust-hermes.git'), true)
  assert.equal(isOfficialSshRemote('git@github.com:9529360-CPU/STARDUST-HERMES.git'), true)
})

test('isOfficialSshRemote does NOT match upstream, forks, other hosts, or HTTPS', () => {
  // The original upstream is a reference source, not this product's update authority.
  assert.equal(isOfficialSshRemote('git@github.com:NousResearch/hermes-agent.git'), false)
  assert.equal(isOfficialSshRemote('git@github.com:someuser/stardust-hermes.git'), false)
  assert.equal(isOfficialSshRemote('git@gitlab.com:9529360-cpu/stardust-hermes.git'), false)
  assert.equal(isOfficialSshRemote(PRODUCT_HTTPS), false)
  assert.equal(isOfficialSshRemote(''), false)
  assert.equal(isOfficialSshRemote(null), false)
})

test('OFFICIAL_REPO_HTTPS_URL canonicalizes to OFFICIAL_REPO_CANONICAL', () => {
  assert.equal(OFFICIAL_REPO_HTTPS_URL, PRODUCT_HTTPS)
  assert.equal(canonicalGitHubRemote(OFFICIAL_REPO_HTTPS_URL), OFFICIAL_REPO_CANONICAL)
})
