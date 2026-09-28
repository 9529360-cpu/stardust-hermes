import { afterEach, describe, expect, it, vi } from 'vitest'

import { createClientSessionState } from '@/lib/chat-runtime'
import { hasOpenServerRequest, resetServerRequestsForTests } from '@/store/server-requests'
import { $toursEnabled } from '@/store/tours'

import { handleServerRequest } from './server-requests'
import type { ServerRequestContext } from './server-requests'

const deps = {
  activeSessionIdRef: { current: null },
  sessionInterrupted: () => false,
  updateSessionState: (_sessionId, update) => update(createClientSessionState('stored-session')),
  upsertToolCall: () => undefined
} as ServerRequestContext['deps']

function deliver(method: string, params: Record<string, unknown>, activeSessionId: null | string) {
  const respond = vi.fn()
  const fail = vi.fn()
  const handled = handleServerRequest({ fail, id: 'srq-1', method, params, profile: 'default', respond }, deps, activeSessionId)

  return { fail, handled, respond }
}

describe('connection request routing', () => {
  it('does not route connection operations through the server-request rail', () => {
    const { handled, respond } = deliver(
      'connection',
      {
        deadline_at: 1_800_000_000,
        op_id: 'op-1',
        session_id: 'session-a',
        targets: [{ action: 'install', kind: 'mcp', name: 'linear' }],
        timeout_seconds: 60,
        tool_call_id: 'call-1'
      },
      'session-a'
    )

    expect(handled).toBe(false)
    expect(respond).not.toHaveBeenCalled()
  })
})

describe('preview action request routing', () => {
  it('leaves a scoped action request unanswered in a window showing another session', () => {
    const { handled, respond, fail } = deliver('preview.act', { action: 'elements', session_id: 'session-a' }, 'session-b')

    expect(handled).toBe(true)
    expect(respond).not.toHaveBeenCalled()
    expect(fail).not.toHaveBeenCalled()
  })

  it('fails fast for an unscoped request with no session in view', () => {
    const { respond } = deliver('preview.act', { action: 'elements' }, null)

    expect(respond).toHaveBeenCalledWith({
      value: JSON.stringify({
        error: 'The in-app browser only takes actions in the session the user is looking at.',
        success: false
      })
    })
  })
})

describe('tour request routing', () => {
  afterEach(() => {
    $toursEnabled.set(true)
  })

  it('leaves a scoped request unanswered in another session even when tours are disabled', () => {
    $toursEnabled.set(false)
    const { handled, respond } = deliver('tour', { action: 'discover', session_id: 'session-a' }, 'session-b')

    expect(handled).toBe(true)
    expect(respond).not.toHaveBeenCalled()
  })

  it('fails fast for an unscoped request with no session in view', () => {
    const { respond } = deliver('tour', { action: 'discover' }, null)

    expect(respond).toHaveBeenCalledWith({
      value: JSON.stringify({ error: 'Tours only run in the session the user is looking at.', success: false })
    })
  })
})

describe('blocking-input guard for interrupted sessions', () => {
  const depsWith = (interrupted: boolean) =>
    ({ ...deps, sessionInterrupted: () => interrupted }) as ServerRequestContext['deps']

  const approvalRequest = (id: string) => ({
    fail: vi.fn(),
    id,
    method: 'approval',
    params: { command: 'rm -rf /', description: 'dangerous', request_id: 'r1', session_id: 'session-a' },
    profile: 'default',
    respond: vi.fn()
  })

  afterEach(() => {
    resetServerRequestsForTests()
  })

  it('fails an approval request for an interrupted session instead of parking it', () => {
    const request = approvalRequest('srq-dead')

    expect(handleServerRequest(request, depsWith(true), 'session-a')).toBe(true)

    expect(hasOpenServerRequest('srq-dead')).toBe(false)
    expect(request.fail).toHaveBeenCalledWith(expect.any(Number), 'session interrupted')
    expect(request.respond).not.toHaveBeenCalled()
  })

  it('still parks an approval request for a live session', () => {
    const request = approvalRequest('srq-live')

    handleServerRequest(request, depsWith(false), 'session-a')

    expect(hasOpenServerRequest('srq-live')).toBe(true)
    expect(request.fail).not.toHaveBeenCalled()
  })
})

