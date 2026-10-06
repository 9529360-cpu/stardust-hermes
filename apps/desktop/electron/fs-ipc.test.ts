import assert from 'node:assert/strict'
import path from 'node:path'

import { beforeEach, test, vi } from 'vitest'

const handlers = vi.hoisted(() => new Map<string, (...args: any[]) => Promise<any>>())
const shellCalls = vi.hoisted(() => ({ revealed: [] as string[], trashed: [] as string[] }))

vi.mock('electron', () => ({
  ipcMain: { handle: (channel: string, handler: (...args: any[]) => Promise<any>) => handlers.set(channel, handler) },
  shell: {
    showItemInFolder: (target: string) => shellCalls.revealed.push(target),
    trashItem: async (target: string) => { shellCalls.trashed.push(target) },
    openPath: async () => ''
  }
}))

import { registerFsIpc } from './fs-ipc'

beforeEach(() => {
  handlers.clear()
  shellCalls.revealed.length = 0
  shellCalls.trashed.length = 0
})

function register() {
  const resolved: { purpose: string; value: string }[] = []
  registerFsIpc({
    hermesHome: path.resolve('test-home'),
    readActiveDesktopProfile: () => null,
    expandUserPath: value => value,
    resolveRequestedPathForIpc: (value, options) => {
      resolved.push({ purpose: options.purpose, value })
      if (value === 'unsafe-device') throw new Error('Blocked unsafe path')
      return path.resolve(value)
    },
    directoryExists: () => true,
    resolveGitBinary: () => 'git'
  })
  return { resolved, invoke: (channel: string, ...args: unknown[]) => handlers.get(channel)!({}, ...args) }
}

test('file actions resolve paths before reaching shell or filesystem operations', async () => {
  const { resolved, invoke } = register()
  for (const channel of ['reveal', 'openDir', 'rename', 'trash']) {
    const args = channel === 'rename' ? ['unsafe-device', 'safe-name'] : ['unsafe-device']
    await assert.rejects(invoke(`hermes:fs:${channel}`, ...args), /Blocked unsafe path/)
  }
  assert.deepEqual(resolved.map(entry => entry.purpose), [
    'Reveal path', 'Open directory', 'Rename path', 'Trash path'
  ])
  assert.deepEqual(shellCalls, { revealed: [], trashed: [] })
})

test('reveal and trash use the normalized path rather than renderer input', async () => {
  const { invoke } = register()
  await invoke('hermes:fs:reveal', './relative-item')
  await invoke('hermes:fs:trash', './relative-item')
  const normalized = path.resolve('./relative-item')
  assert.deepEqual(shellCalls.revealed, [normalized])
  assert.deepEqual(shellCalls.trashed, [normalized])
})
