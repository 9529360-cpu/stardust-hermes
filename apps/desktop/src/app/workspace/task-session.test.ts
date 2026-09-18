import { describe, expect, it } from 'vitest'

import { createClientSessionState } from '@/lib/chat-runtime'
import type { SessionInfo } from '@/types/hermes'

import {
  findLiveTaskRuntimeId,
  findLiveTaskRuntimeIdByStoredId,
  findLiveTaskSession,
  findLiveTaskStoredId,
  resolveTaskWorkspaceCwd
} from './task-session'

function session(id: string, over: Partial<SessionInfo> = {}): SessionInfo {
  return {
    ended_at: null,
    id,
    input_tokens: 0,
    is_active: true,
    last_active: 0,
    message_count: 1,
    model: null,
    output_tokens: 0,
    preview: null,
    source: 'desktop',
    started_at: 0,
    title: id,
    tool_call_count: 0,
    ...over
  }
}

describe('findLiveTaskSession', () => {
  it('prefers a task waiting for user input over a merely running task', () => {
    const running = session('running')
    const attention = session('attention')

    expect(findLiveTaskSession([running, attention], ['attention'], ['running'])).toBe(attention)
  })

  it('matches durable lineage aliases, not only the current tip id', () => {
    const compressed = session('tip', {
      _lineage_ids: ['root', 'middle', 'tip'],
      _lineage_root_id: 'root'
    })

    expect(findLiveTaskSession([compressed], ['middle'], [])).toBe(compressed)
  })

  it('returns undefined when no loaded session owns a live task id', () => {
    expect(findLiveTaskSession([session('idle')], ['missing'], ['also-missing'])).toBeUndefined()
  })
})

describe('findLiveTaskStoredId', () => {
  it('uses the hydrated session tip when metadata is available', () => {
    const compressed = session('tip', {
      _lineage_ids: ['root', 'tip'],
      _lineage_root_id: 'root'
    })

    expect(findLiveTaskStoredId([compressed], ['root'], [])).toBe('tip')
  })

  it('keeps the projected attention id during a session-metadata hydration gap', () => {
    expect(findLiveTaskStoredId([], ['stored-attention'], ['stored-working'])).toBe('stored-attention')
  })

  it('falls back to a projected working id when there is no attention task', () => {
    expect(findLiveTaskStoredId([], [], ['stored-working'])).toBe('stored-working')
  })

  it('returns null when there is no live task projection', () => {
    expect(findLiveTaskStoredId([], [], [])).toBeNull()
  })
})

describe('findLiveTaskRuntimeId', () => {
  it('maps a stored task back to the runtime that owns its live status stack', () => {
    const target = session('stored-task')
    const state = { ...createClientSessionState(null), busy: true, storedSessionId: 'stored-task' }

    expect(findLiveTaskRuntimeId({ 'runtime-task': state }, target)).toBe('runtime-task')
  })

  it('prefers a needs-input runtime when an older matching busy runtime is retained', () => {
    const target = session('tip', {
      _lineage_ids: ['root', 'tip'],
      _lineage_root_id: 'root'
    })
    const busy = { ...createClientSessionState(null), busy: true, storedSessionId: 'root' }
    const attention = {
      ...createClientSessionState(null),
      busy: true,
      needsInput: true,
      storedSessionId: 'tip'
    }

    expect(findLiveTaskRuntimeId({ 'runtime-old': busy, 'runtime-live': attention }, target)).toBe('runtime-live')
  })

  it('returns null when no runtime mirror belongs to the stored task', () => {
    const other = { ...createClientSessionState(null), busy: true, storedSessionId: 'other' }

    expect(findLiveTaskRuntimeId({ 'runtime-other': other }, session('target'))).toBeNull()
  })
})

describe('findLiveTaskRuntimeIdByStoredId', () => {
  it('resolves the live runtime while the stored session row is still hydrating', () => {
    const state = { ...createClientSessionState(null), busy: true, storedSessionId: 'stored-task' }

    expect(findLiveTaskRuntimeIdByStoredId({ 'runtime-task': state }, 'stored-task')).toBe('runtime-task')
  })

  it('also accepts a runtime id when the backend has not assigned a stored id yet', () => {
    const state = { ...createClientSessionState(null), busy: true, storedSessionId: null }

    expect(findLiveTaskRuntimeIdByStoredId({ 'runtime-only': state }, 'runtime-only')).toBe('runtime-only')
  })

  it('prefers needs-input over another matching running runtime', () => {
    const running = { ...createClientSessionState(null), busy: true, storedSessionId: 'stored-task' }
    const attention = {
      ...createClientSessionState(null),
      busy: true,
      needsInput: true,
      storedSessionId: 'stored-task'
    }

    expect(
      findLiveTaskRuntimeIdByStoredId(
        { 'runtime-running': running, 'runtime-attention': attention },
        'stored-task'
      )
    ).toBe('runtime-attention')
  })
})

describe('resolveTaskWorkspaceCwd', () => {
  it('keeps a live transient cwd ahead of stored and scoped project paths', () => {
    expect(
      resolveTaskWorkspaceCwd(
        'D:/live-worktree',
        session('selected', { cwd: 'D:/selected' }),
        session('fallback', { cwd: 'D:/fallback' }),
        'D:/scope'
      )
    ).toBe('D:/live-worktree')
  })

  it('uses the selected stored session before a recovered background task', () => {
    expect(
      resolveTaskWorkspaceCwd(
        '',
        session('selected', { cwd: 'D:/selected' }),
        session('fallback', { cwd: 'D:/fallback' }),
        'D:/scope'
      )
    ).toBe('D:/selected')
  })

  it('inherits a recovered background task cwd before the project-scope fallback', () => {
    expect(resolveTaskWorkspaceCwd('', undefined, session('fallback', { cwd: 'D:/fallback' }), 'D:/scope')).toBe(
      'D:/fallback'
    )
  })

  it('keeps a recovered background task cwd ahead of an unrelated blank-draft cwd', () => {
    expect(
      resolveTaskWorkspaceCwd(
        'C:/Users/example',
        undefined,
        session('fallback', { cwd: 'D:/real-task-repo' }),
        'D:/scope'
      )
    ).toBe('D:/real-task-repo')
  })

  it('falls back to the explicit project scope when no conversation owns a cwd', () => {
    expect(resolveTaskWorkspaceCwd('', undefined, undefined, 'D:/scope')).toBe('D:/scope')
  })
})
