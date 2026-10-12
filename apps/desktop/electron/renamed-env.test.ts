import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  readRenamedDesktopEnv,
  RENAMED_DESKTOP_ENV,
  type RenamedDesktopEnvName,
  resetRenamedDesktopEnvForTests,
  setRenamedDesktopEnvWarningSink,
  warnAboutDeprecatedDesktopEnv
} from './renamed-env'

const NAMES = Object.keys(RENAMED_DESKTOP_ENV) as RenamedDesktopEnvName[]

beforeEach(() => {
  resetRenamedDesktopEnvForTests()
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe.each(NAMES)('%s', name => {
  const legacy = RENAMED_DESKTOP_ENV[name]

  it('prefers the current name over the legacy name without warning', () => {
    const warn = vi.fn()

    expect(readRenamedDesktopEnv(name, { [name]: 'current', [legacy]: 'legacy' }, warn)).toBe('current')
    expect(warn).not.toHaveBeenCalled()
  })

  it('reads the legacy name when the current one is missing, with exactly one warning', () => {
    const warn = vi.fn()
    const env = { [legacy]: 'legacy-value' }

    expect(readRenamedDesktopEnv(name, env, warn)).toBe('legacy-value')
    expect(readRenamedDesktopEnv(name, env, warn)).toBe('legacy-value')
    expect(warn).toHaveBeenCalledTimes(1)
    expect(warn.mock.calls[0][0]).toContain(legacy)
    expect(warn.mock.calls[0][0]).toContain(name)
    expect(warn.mock.calls[0][0]).not.toContain('legacy-value')
  })

  it('treats an empty current name as unset', () => {
    const warn = vi.fn()

    expect(readRenamedDesktopEnv(name, { [name]: '', [legacy]: 'legacy-value' }, warn)).toBe('legacy-value')
    expect(warn).toHaveBeenCalledTimes(1)
  })

  it('returns undefined and does not warn when neither name is set', () => {
    const warn = vi.fn()

    expect(readRenamedDesktopEnv(name, {}, warn)).toBeUndefined()
    expect(warn).not.toHaveBeenCalled()
  })
})

describe('warning delivery', () => {
  it('holds a warning raised before a sink is installed and delivers it on install', () => {
    readRenamedDesktopEnv('STARDUST_DESKTOP_TERMINAL', { HERMES_DESKTOP_TERMINAL: '1' })
    const sink = vi.fn()

    setRenamedDesktopEnvWarningSink(sink)
    expect(sink).toHaveBeenCalledTimes(1)
    expect(sink.mock.calls[0][0]).toContain('HERMES_DESKTOP_TERMINAL')

    readRenamedDesktopEnv('STARDUST_DESKTOP_TERMINAL', { HERMES_DESKTOP_TERMINAL: '1' })
    expect(sink).toHaveBeenCalledTimes(1)
  })

  it('reports each deprecated name present in the environment once at startup', () => {
    const warn = vi.fn()

    warnAboutDeprecatedDesktopEnv(
      { HERMES_DESKTOP_CWD: '/work', HERMES_DESKTOP_TERMINAL: '1', STARDUST_DESKTOP_TERMINAL: '1' },
      warn
    )
    warnAboutDeprecatedDesktopEnv({ HERMES_DESKTOP_CWD: '/work' }, warn)

    expect(warn).toHaveBeenCalledTimes(1)
    expect(warn.mock.calls[0][0]).toContain('HERMES_DESKTOP_CWD')
  })

  it('reads process.env when no environment is passed', () => {
    vi.stubEnv('STARDUST_DESKTOP_TERMINAL', '1')

    expect(readRenamedDesktopEnv('STARDUST_DESKTOP_TERMINAL')).toBe('1')
  })
})
