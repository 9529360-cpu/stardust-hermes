import { afterEach, describe, expect, it, vi } from 'vitest'

import { $connectionRequests } from '@/store/connection-request'
import { $notifications, clearNotifications } from '@/store/notifications'
import { $approvalRequests, clearApprovalRequest, setApprovalRequest } from '@/store/prompts'
import { $routeRequest } from '@/store/recovery-requests'

import { handleInputRequestEvent } from './input-requests'
import type { GatewayEventContext } from './types'

// When the server withdraws an unanswered approval on timeout, the Run/Reject
// bar disappears and the tool row then shows a model-facing "BLOCKED …" result.
// The transcript must gain a human system line that says what happened and
// where the wait is configured; a cancel for any other reason stays silent.
function context(reason: string, updateSessionState: ReturnType<typeof vi.fn>): GatewayEventContext {
  const payload = { id: 'srv-1', method: 'approval', reason }

  return {
    deps: { flushQueuedDeltas: vi.fn(), updateSessionState } as unknown as GatewayEventContext['deps'],
    event: { payload, session_id: 's1', type: 'request.cancel' },
    explicitSid: 's1',
    fromActiveSource: () => true,
    isActiveEvent: true,
    occurredAt: 1_700_000_000,
    payload: payload as GatewayEventContext['payload'],
    scheduleConfigRefresh: vi.fn(),
    sessionId: 's1'
  }
}

function parkApproval() {
  setApprovalRequest({
    sessionId: 's1',
    command: 'rm -rf build',
    description: '',
    requestId: 'req-1',
    serverRequestId: 'srv-1'
  })
}

describe('approval request.cancel', () => {
  afterEach(() => {
    clearApprovalRequest('s1')
    clearNotifications()
  })

  it('timeout: tears the bar down and appends a plain system line with a Safety settings action', () => {
    parkApproval()
    const updateSessionState = vi.fn((_: string, updater: (s: { messages: unknown[] }) => unknown) =>
      updater({ messages: [] })
    )

    expect(handleInputRequestEvent(context('timeout', updateSessionState))).toBe(true)
    expect($approvalRequests.get()['s1']).toBeUndefined()

    const next = updateSessionState.mock.results[0]?.value as { messages: { role: string; parts: { text: string }[] }[] }
    expect(next.messages).toHaveLength(1)
    expect(next.messages[0].role).toBe('system')
    expect(next.messages[0].parts[0].text).toMatch(/timed out/i)
    expect(next.messages[0].parts[0].text).toMatch(/Settings → Safety/)
    expect(next.messages[0].parts[0].text).not.toMatch(/BLOCKED|Do NOT/)

    const toast = $notifications.get()[0]
    expect(toast?.action?.label).toBe('Open Safety settings')
    toast?.action?.onClick()
    expect($routeRequest.get()?.path).toBe('/settings?tab=config:safety')
  })

  it('answered elsewhere: tears the bar down silently', () => {
    parkApproval()
    const updateSessionState = vi.fn()

    expect(handleInputRequestEvent(context('answered', updateSessionState))).toBe(true)
    expect($approvalRequests.get()['s1']).toBeUndefined()
    expect(updateSessionState).not.toHaveBeenCalled()
    expect($notifications.get()).toHaveLength(0)
  })
})

describe('connection request after session interruption', () => {
  afterEach(() => {
    $connectionRequests.set({})
  })

  it('does not park a connection card for a stopped session', () => {
    const payload = {
      deadline_at: 1_800_000_000,
      op_id: 'op-1',
      targets: [{ action: 'install', kind: 'mcp', name: 'linear', state: 'pending' }],
      tool_call_id: 'call-1'
    }
    const updateSessionState = vi.fn()
    const upsertToolCall = vi.fn()
    const ctx = {
      deps: {
        sessionInterrupted: () => true,
        updateSessionState,
        upsertToolCall
      } as unknown as GatewayEventContext['deps'],
      event: { payload, session_id: 's1', type: 'connection.request' },
      explicitSid: 's1',
      fromActiveSource: () => true,
      isActiveEvent: true,
      occurredAt: 1_700_000_000,
      payload,
      scheduleConfigRefresh: vi.fn(),
      sessionId: 's1'
    } as unknown as GatewayEventContext

    expect(handleInputRequestEvent(ctx)).toBe(true)
    expect($connectionRequests.get()['s1']).toBeUndefined()
    expect(updateSessionState).not.toHaveBeenCalled()
    expect(upsertToolCall).not.toHaveBeenCalled()
  })
})

