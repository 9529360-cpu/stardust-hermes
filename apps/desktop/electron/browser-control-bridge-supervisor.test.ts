import { describe, expect, it, vi } from 'vitest'

import { BrowserControlBridgeSupervisor, validateBridgeLaunchContext } from './browser-control-bridge-supervisor'

const context = { grant: 'one-time', session_id: 's', controller_id: 'c', browser_profile_id: 'p', capabilities: ['browser_snapshot'], protocol_version: 1 }
const payload = { gatewayUrl: 'http://127.0.0.1:8642', launchContext: context, chromeProfileDir: 'Default', packageSpec: '@playwright/mcp@0.0.83' }

function child() {
  const stdoutListeners: Array<(value: string) => void> = []
  const stderrListeners: Array<(value: string) => void> = []
  const once = vi.fn()

  return {
    pid: 42,
    stdout: { on: (_event: string, fn: (value: string) => void) => { stdoutListeners.push(fn); fn('{"event":"ready"}') } },
    stderr: { on: (_event: string, fn: (value: string) => void) => { stderrListeners.push(fn) } },
    once: (event: string, fn: () => void) => once.mockImplementation(fn),
    kill: vi.fn(),
    stdoutListeners,
    stderrListeners
  } as any
}

describe('BrowserControlBridgeSupervisor', () => {
  it('validates loopback URL with or without slash and rejects remote', () => { expect(() => validateBridgeLaunchContext({ ...context, extra: 1 })).toThrow(); expect(() => new URL('http://127.0.0.1:1')).not.toThrow() })
  it('uses a minimal env and transitions to connected on ready', async () => { const spawned = child(); const spawn = vi.fn(() => spawned); const s = new BrowserControlBridgeSupervisor({ spawn, bridgeEntry: 'bridge.mjs', env: { PATH: 'safe', API_KEY: 'secret' } }); const result = await s.start(payload); expect(result.status).toBe('connected'); expect(spawn).toHaveBeenCalledWith(process.execPath, ['bridge.mjs'], expect.objectContaining({ env: expect.objectContaining({ STARDUST_CONTROLLER_ID: 'c', STARDUST_BROWSER_PROFILE_ID: 'p', STARDUST_BROWSER_CAPABILITIES: '["browser_snapshot"]' }) })); expect((spawn.mock.calls[0] as any)[2].env.API_KEY).toBeUndefined(); })
  it('does not spawn duplicates and stop only kills owned child', async () => { const spawned = child(); const spawn = vi.fn(() => spawned); const s = new BrowserControlBridgeSupervisor({ spawn, bridgeEntry: 'bridge.mjs' }); await s.start(payload); await s.start(payload); expect(spawn).toHaveBeenCalledTimes(1); await s.stop(); expect(spawned.kill).toHaveBeenCalledWith('SIGTERM') })
})
