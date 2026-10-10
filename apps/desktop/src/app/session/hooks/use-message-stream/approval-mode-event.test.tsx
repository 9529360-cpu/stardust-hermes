import { act, cleanup } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $approvalModes, approvalModeForProfile } from '@/store/approval-mode'
import { $activeGatewayProfile } from '@/store/profile'
import { $approvalRequests, clearApprovalRequest, setApprovalRequest } from '@/store/prompts'

import { type MessageStreamHarness, renderMessageStream } from './test-harness'

const ACTIVE_SID = 'session-active'
let stream: MessageStreamHarness

function mountStream() {
  stream = renderMessageStream(ACTIVE_SID)
}

describe('live session.info approval mode reconciliation', () => {
  beforeEach(() => {
    $approvalModes.set({})
    $activeGatewayProfile.set('work')
  })

  afterEach(() => {
    cleanup()
    clearApprovalRequest(ACTIVE_SID)
    vi.restoreAllMocks()
  })

  it('reconciles an active-session event under its source gateway profile', () => {
    mountStream()
    setApprovalRequest({ command: 'queued', description: 'd', requestId: 'r1', sessionId: ACTIVE_SID })

    act(() =>
      stream.handleEvent({
        payload: { approval_mode: 'off' },
        profile: 'work',
        session_id: ACTIVE_SID,
        type: 'session.info'
      })
    )

    expect(approvalModeForProfile('work')).toBe('off')
    expect(approvalModeForProfile('default')).toBeUndefined()
    expect($approvalRequests.get()[ACTIVE_SID]).toBeUndefined()
  })

  it('keeps a target-policy locked approval visible when the profile switches off', () => {
    mountStream()
    setApprovalRequest({
      command: 'room action', description: 'd', policyLocked: true, requestId: 'room-r1', sessionId: ACTIVE_SID
    })

    act(() =>
      stream.handleEvent({
        payload: { approval_mode: 'off' },
        profile: 'work',
        session_id: ACTIVE_SID,
        type: 'session.info'
      })
    )

    expect($approvalRequests.get()[ACTIVE_SID]).toMatchObject({ requestId: 'room-r1', policyLocked: true })
  })

  it('ignores stale session.info from a non-active session on the active gateway', () => {
    mountStream()

    act(() =>
      stream.handleEvent({
        payload: { approval_mode: 'off' },
        profile: 'work',
        session_id: 'session-stale',
        type: 'session.info'
      })
    )

    expect(approvalModeForProfile('work')).toBeUndefined()
  })

  it('does not cache an event under a different active profile when its source profile is absent', () => {
    mountStream()
    $activeGatewayProfile.set('personal')

    act(() => stream.handleEvent({ payload: { approval_mode: 'off' }, session_id: ACTIVE_SID, type: 'session.info' }))

    expect(approvalModeForProfile('personal')).toBeUndefined()
    expect(approvalModeForProfile('work')).toBeUndefined()
  })
})
