import assert from 'node:assert/strict'

import { test } from 'vitest'

import { desktopBackendSpawnEnv, guestOnboardingEnabled, skipIntroEnabled } from './guest-onboarding'
import { buildSpawnCommand } from './remote-lifecycle'

test('skipIntroEnabled: exactly "1" in env or --skip-intro on argv skips the first-run film', () => {
  assert.equal(skipIntroEnabled([], { HERMES_SKIP_INTRO: '1' }), true)
  assert.equal(skipIntroEnabled(['electron', '.', '--skip-intro'], {}), true)

  assert.equal(skipIntroEnabled([], {}), false)
  assert.equal(skipIntroEnabled([], { HERMES_SKIP_INTRO: 'true' }), false)
})

test('guestOnboardingEnabled: inherited guest switches cannot enable a built-in account', () => {
  assert.equal(guestOnboardingEnabled([], { HERMES_GUEST_ONBOARDING: '1' }), false)
  assert.equal(guestOnboardingEnabled(['electron', '.', '--guest-onboarding'], {}), false)
  assert.equal(
    guestOnboardingEnabled(['electron', '.', '--guest-onboarding'], { HERMES_GUEST_ONBOARDING: '1' }),
    false
  )

  assert.equal(guestOnboardingEnabled([], {}), false)
  assert.equal(guestOnboardingEnabled([], { HERMES_GUEST_ONBOARDING: '0' }), false)
})

test('desktopBackendSpawnEnv always tombstones the inherited guest switch', () => {
  const base = {
    HERMES_HOME: '/tmp/home',
    HERMES_DESKTOP: '1',
    HERMES_GUEST_ONBOARDING: '1',
    PATH: '/usr/bin'
  }

  const attemptedOn = desktopBackendSpawnEnv({ ...base, HERMES_GUEST_ONBOARDING: '0' }, true)
  const off = desktopBackendSpawnEnv(base, false)

  assert.equal(attemptedOn.HERMES_GUEST_ONBOARDING, '0')
  assert.equal(off.HERMES_GUEST_ONBOARDING, '0', 'a stray inherited "1" must never turn the guest account on')

  for (const env of [attemptedOn, off]) {
    assert.equal(env.HERMES_HOME, base.HERMES_HOME)
    assert.equal(env.HERMES_DESKTOP, base.HERMES_DESKTOP)
    assert.equal(env.PATH, base.PATH)
  }
})

test('remote SSH production-shaped spawns do not carry the retired guest switch', () => {
  const off = buildSpawnCommand('/x/hermes', 'work', { logPath: '~/.hermes/log', guestOnboarding: false })
  assert.match(off, /exec env HERMES_DESKTOP=1 /)
  assert.doesNotMatch(off, /HERMES_GUEST_ONBOARDING/)

  const unset = buildSpawnCommand('/x/hermes', 'work', { logPath: '~/.hermes/log' })
  assert.doesNotMatch(unset, /HERMES_GUEST_ONBOARDING/)
})
