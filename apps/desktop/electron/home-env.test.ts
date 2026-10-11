import assert from 'node:assert/strict'

import { test } from 'vitest'

import { configuredHomeFromEnv } from './home-env'

test('STARDUST_HOME is the configured home when both names are set', () => {
  assert.equal(configuredHomeFromEnv({ STARDUST_HOME: '/data/stardust', HERMES_HOME: '/data/legacy' }), '/data/stardust')
})

test('the legacy HERMES_HOME is the configured home when STARDUST_HOME is unset or blank', () => {
  assert.equal(configuredHomeFromEnv({ HERMES_HOME: 'D:\\Stardust\\home' }), 'D:\\Stardust\\home')
  assert.equal(configuredHomeFromEnv({ STARDUST_HOME: '   ', HERMES_HOME: '/data/legacy' }), '/data/legacy')
})

test('no home variable, or only blank ones, configures no home', () => {
  assert.equal(configuredHomeFromEnv({}), '')
  assert.equal(configuredHomeFromEnv({ STARDUST_HOME: '', HERMES_HOME: '  ' }), '')
})
