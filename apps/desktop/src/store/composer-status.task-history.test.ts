import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $taskHistoryByScope, readTaskHistory } from '@/lib/task-history'

import { $backgroundStatusBySession, reconcileBackgroundProcesses, resetSessionBackground } from './composer-status'
import { recordSessionEventScope } from './session-states'

vi.mock('./native-notifications', () => ({ dispatchNativeNotification: vi.fn() }))

let serial = 0
let sid: string
const owner = () => ({ connectionId: 'primary', profileId: 'default', sessionId: sid })
const process = (id: string, status: string) => ({
  session_id: id,
  status,
  exit_code: 0,
  command: 'safe title',
  output_tail: 'never retain output'
})

beforeEach(() => {
  sid = `history-owner-${++serial}`
  vi.useFakeTimers()
  $taskHistoryByScope.set({})
  $backgroundStatusBySession.set({})
  recordSessionEventScope({ session_id: sid, connectionId: 'primary', profile: 'default' })
})

afterEach(() => {
  resetSessionBackground(sid)
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('background completion capture at the producer boundary', () => {
  it('captures outside the Tasks page and survives source auto-dismiss without output', () => {
    reconcileBackgroundProcesses(sid, [process('native-proc', 'running')])
    reconcileBackgroundProcesses(sid, [process('native-proc', 'exited')])
    vi.advanceTimersByTime(5000)
    expect($backgroundStatusBySession.get()[sid]).toBeUndefined()
    const rows = readTaskHistory(owner())
    expect(rows).toHaveLength(1)
    expect(rows[0].id).toBe('process:native-proc')
    expect(rows[0].state).toBe('completed')
    expect(JSON.stringify(rows)).not.toContain('never retain output')
  })

  it('uses the exact runtime owner rather than the active profile', () => {
    recordSessionEventScope({ session_id: sid, connectionId: 'remote-b', profile: 'work' })
    reconcileBackgroundProcesses(sid, [process('remote-proc', 'exited')])
    expect(readTaskHistory(owner())).toEqual([])
    expect(readTaskHistory({ ...owner(), connectionId: 'remote-b', profileId: 'work' })).toHaveLength(1)
  })

  it('fails closed for unowned sessions and clears archived work on timeline reset', () => {
    reconcileBackgroundProcesses('no-proven-owner', [process('unowned', 'exited')])
    expect(Object.values($taskHistoryByScope.get()).flat()).toHaveLength(0)
    reconcileBackgroundProcesses(sid, [process('reset-proc', 'exited')])
    expect(readTaskHistory(owner())).toHaveLength(1)
    resetSessionBackground(sid)
    expect(readTaskHistory(owner())).toEqual([])
    resetSessionBackground('no-proven-owner')
  })
})
