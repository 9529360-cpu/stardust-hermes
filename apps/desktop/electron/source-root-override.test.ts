import path from 'node:path'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { resetSourceRootOverrideWarningForTests, resolveSourceRootOverride } from './source-root-override'

const CURRENT_NAME = 'STARDUST_DESKTOP_HERMES_ROOT'
const LEGACY_NAME = 'HERMES_DESKTOP_HERMES_ROOT'

beforeEach(() => {
  resetSourceRootOverrideWarningForTests()
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('resolveSourceRootOverride', () => {
  it('prefers the current name over the legacy name and does not warn', () => {
    const warn = vi.fn()

    expect(resolveSourceRootOverride({ [CURRENT_NAME]: '/current/root', [LEGACY_NAME]: '/legacy/root' }, warn)).toBe(
      path.resolve('/current/root')
    )
    expect(warn).not.toHaveBeenCalled()
  })

  it('falls back to the legacy name when the current one is missing, with exactly one warning', () => {
    const warn = vi.fn()

    expect(resolveSourceRootOverride({ [LEGACY_NAME]: '/legacy/root' }, warn)).toBe(path.resolve('/legacy/root'))
    expect(warn).toHaveBeenCalledTimes(1)
    expect(warn.mock.calls[0][0]).toContain(LEGACY_NAME)
    expect(warn.mock.calls[0][0]).toContain(CURRENT_NAME)
  })

  it('does not warn again on later calls in the same process', () => {
    const warn = vi.fn()

    resolveSourceRootOverride({ [LEGACY_NAME]: '/legacy/root' }, warn)

    expect(resolveSourceRootOverride({ [LEGACY_NAME]: '/legacy/root' }, warn)).toBe(path.resolve('/legacy/root'))
    expect(warn).toHaveBeenCalledTimes(1)
  })

  it('does not warn when only the current name is set', () => {
    const warn = vi.fn()

    expect(resolveSourceRootOverride({ [CURRENT_NAME]: '/current/root' }, warn)).toBe(path.resolve('/current/root'))
    expect(warn).not.toHaveBeenCalled()
  })

  it('returns undefined and does not warn when neither name is set', () => {
    const warn = vi.fn()

    expect(resolveSourceRootOverride({}, warn)).toBeUndefined()
    expect(warn).not.toHaveBeenCalled()
  })

  it('reads process.env when no environment is passed', () => {
    vi.stubEnv(CURRENT_NAME, '/from/process/env')

    expect(resolveSourceRootOverride()).toBe(path.resolve('/from/process/env'))
  })
})
