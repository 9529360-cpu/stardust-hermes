import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $backgroundStatusBySession, reconcileBackgroundProcesses, resetSessionBackground } from './composer-status'
import { recordSessionEventScope } from './session-states'

vi.mock('./native-notifications', () => ({ dispatchNativeNotification: vi.fn() }))

let serial = 0
let sid: string

const process = (id: string, status: string) => ({
  session_id: id,
  status,
  exit_code: 0,
  command: 'safe title',
  output_tail: 'never retain output'
})

beforeEach(() => {
  sid = `background-owner-${++serial}`
  vi.useFakeTimers()
  $backgroundStatusBySession.set({})
  recordSessionEventScope({ session_id: sid, connectionId: 'primary', profile: 'default' })
})

afterEach(() => {
  resetSessionBackground(sid)
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('background process status', () => {
  it('keeps live rows scoped to their session until the source dismisses them', () => {
    reconcileBackgroundProcesses(sid, [process('native-proc', 'running')])
    expect($backgroundStatusBySession.get()[sid]).toMatchObject([{ id: 'native-proc', state: 'running' }])

    reconcileBackgroundProcesses(sid, [process('native-proc', 'exited')])
    vi.advanceTimersByTime(5000)
    expect($backgroundStatusBySession.get()[sid]).toBeUndefined()
  })

  it('clears finished processes when the session timeline resets', () => {
    reconcileBackgroundProcesses(sid, [process('reset-proc', 'exited')])
    vi.advanceTimersByTime(5000)
    expect($backgroundStatusBySession.get()[sid]).toBeUndefined()
    resetSessionBackground(sid)
    expect($backgroundStatusBySession.get()[sid]).toBeUndefined()
    resetSessionBackground('no-proven-owner')
  })
})
