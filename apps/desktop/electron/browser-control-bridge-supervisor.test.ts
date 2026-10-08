import { EventEmitter } from 'node:events'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { BrowserControlBridgeSupervisor, validateBridgeLaunchContext } from './browser-control-bridge-supervisor'

const context = {
  grant: 'normal-grant',
  session_id: 's',
  controller_id: 'c',
  browser_profile_id: 'p',
  capabilities: ['browser_snapshot'],
  protocol_version: 1
}

const payload = {
  gatewayUrl: 'http://127.0.0.1:8642',
  launchContext: context,
  chromeProfileDir: 'Default',
  packageSpec: '@playwright/mcp@0.0.83'
}

function child() {
  const process = Object.assign(new EventEmitter(), {
    pid: 42,
    stdout: new EventEmitter(),
    stderr: new EventEmitter(),
    kill: vi.fn(() => {
      queueMicrotask(() => process.emit('exit', 0))

      return true
    })
  })

  return process as any
}

afterEach(() => vi.useRealTimers())

describe('BrowserControlBridgeSupervisor', () => {
  it('escalates shutdown when the child ignores graceful termination', async () => {
    vi.useFakeTimers()
    const spawned = child()
    spawned.kill.mockImplementation((signal: string) => {
      if (signal === 'SIGKILL') {
        queueMicrotask(() => spawned.emit('exit', 1))
      }
      return true
    })
    const supervisor = new BrowserControlBridgeSupervisor({ spawn: () => spawned, bridgeEntry: 'bridge.mjs' })
    await supervisor.start(payload)
    const stopped = supervisor.stop()
    await vi.advanceTimersByTimeAsync(1500)
    expect((await stopped).status).toBe('inactive')
    expect(spawned.kill).toHaveBeenCalledWith('SIGTERM')
    expect(spawned.kill).toHaveBeenCalledWith('SIGKILL')
  })
  it('rejects unknown launch fields, remote gateways and invalid grants before spawning', async () => {
    const spawn = vi.fn()
    const s = new BrowserControlBridgeSupervisor({ spawn, bridgeEntry: 'bridge.mjs' })

    expect(() => validateBridgeLaunchContext({ ...context, extra: 1 })).toThrow()
    await expect(s.start({ ...payload, gatewayUrl: 'http://remote.example' })).rejects.toThrow('loopback')
    await expect(s.start({ ...payload, launchContext: { ...context, grant: 'a\nb' } })).rejects.toThrow('grant')
    expect(spawn).not.toHaveBeenCalled()
  })

  it('uses Node mode and a minimal environment; only a complete ready frame proves connection', async () => {
    const spawned = child()
    const spawn = vi.fn(() => spawned)

    const s = new BrowserControlBridgeSupervisor({
      spawn,
      bridgeEntry: 'bridge.mjs',
      env: { PATH: 'safe', API_KEY: 'secret', PLAYWRIGHT_MCP_EXTENSION_TOKEN: 'unrelated' }
    })

    expect((await s.start(payload)).status).toBe('starting')
    const env = (spawn.mock.calls[0] as any)[2].env

    expect(env).toMatchObject({
      ELECTRON_RUN_AS_NODE: '1',
      STARDUST_CONTROLLER_ID: 'c',
      STARDUST_BROWSER_CAPABILITIES: '["browser_snapshot"]'
    })
    expect(env.API_KEY).toBeUndefined()
    expect(env.PLAYWRIGHT_MCP_EXTENSION_TOKEN).toBeUndefined()
    spawned.stdout.emit('data', '{"event":"re')
    spawned.stderr.emit('data', 'an unrelated diagnostic\n')
    expect(s.status().status).toBe('starting')
    spawned.stdout.emit('data', 'ady"}\n')
    expect(s.status().status).toBe('connected')
    await s.stop()
  })

  it('deduplicates the same owner and replaces a different owner without stale events', async () => {
    const first = child()
    const second = child()
    const spawn = vi.fn().mockReturnValueOnce(first).mockReturnValueOnce(second)
    const s = new BrowserControlBridgeSupervisor({ spawn, bridgeEntry: 'bridge.mjs' })

    await s.start(payload)
    await s.start(payload)
    expect(spawn).toHaveBeenCalledTimes(1)
    await s.start({ ...payload, launchContext: { ...context, session_id: 'other', controller_id: 'new' } })
    expect(first.kill).toHaveBeenCalledWith('SIGTERM')
    first.stdout.emit('data', '{"event":"ready"}\n')
    first.emit('exit', 0)
    expect(s.status()).toMatchObject({ status: 'starting', session_id: 'other', controller_id: 'new' })
    second.stdout.emit('data', '{"event":"ready"}\n')
    expect(s.status().status).toBe('connected')
    await s.stop()
    expect(second.kill).toHaveBeenCalledWith('SIGTERM')
  })

  it('fails bounded startup and handles asynchronous spawn errors', async () => {
    vi.useFakeTimers()
    const spawned = child()
    const s = new BrowserControlBridgeSupervisor({ spawn: () => spawned, bridgeEntry: 'bridge.mjs' })

    await s.start(payload)
    await vi.advanceTimersByTimeAsync(15_000)
    expect(s.status()).toMatchObject({ status: 'error', error: expect.stringContaining('15 seconds') })
    expect(spawned.kill).toHaveBeenCalledWith('SIGTERM')
    await s.stop()
    const next = child()
    const failing = new BrowserControlBridgeSupervisor({ spawn: () => next, bridgeEntry: 'bridge.mjs' })

    await failing.start(payload)
    next.emit('error', new Error('ENOENT'))
    expect(failing.status()).toMatchObject({ status: 'error', error: 'Error: ENOENT' })
    await failing.stop()
  })
})
