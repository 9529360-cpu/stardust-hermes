/**
 * Regression: in long sessions a persisted assistant reply never reached the
 * open chat until the app restarted.
 *  - the transcript signature ignored display-only fields (display_content,
 *    codex_message_items, …), so a DB update of those was judged "unchanged";
 *  - a turn settled by the session.active_list snapshot (completion event
 *    lost) was retired only in the mirror store and never reconciled from DB.
 */
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { chatMessageText } from '@/lib/chat-messages'
import { createClientSessionState } from '@/lib/chat-runtime'
import { sessionMessagesSignature } from '@/lib/session-signatures'
import { $changeEventsAvailable, notifySessionsChanged, resetLiveSync } from '@/store/live-sync'
import { $activeSessionId, $busy, $selectedStoredSessionId, setBusy } from '@/store/session'
import {
  $sessionStates,
  $sessionTiles,
  clearAllSessionStates,
  publishSessionState,
  type SessionTileDelegate,
  setSessionTileDelegate
} from '@/store/session-states'
import type { SessionMessage } from '@/types/hermes'

import type { ClientSessionState } from '../../types'

import {
  reconcileActiveTranscript,
  rehydrateLiveSessionStatuses,
  resetLiveRuntimeTracking,
  useBackgroundSync
} from './use-background-sync'

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal()),
  getLatestSessionMessages: vi.fn()
}))

vi.mock('@/store/projects', async importOriginal => ({
  ...(await importOriginal()),
  refreshProjectTree: vi.fn(async () => undefined)
}))

const { getLatestSessionMessages } = await import('@/hermes')

const RUNTIME = 'runtime-long'
const STORED = 'stored-long'

function longHistory(turns = 70): SessionMessage[] {
  const rows: SessionMessage[] = []

  for (let i = 0; i < turns; i++) {
    rows.push({ content: `question ${i}`, role: 'user', timestamp: i * 2 + 1 })
    rows.push({ content: `answer ${i}`, role: 'assistant', timestamp: i * 2 + 2 })
  }

  return rows
}

function serve(messages: SessionMessage[]) {
  vi.mocked(getLatestSessionMessages).mockResolvedValue({ messages, session_id: STORED } as never)
}

function makeRefresh() {
  const states = new Map<string, ClientSessionState>([[RUNTIME, createClientSessionState(STORED)]])

  const updateSessionState = vi.fn((sessionId: string, updater: (value: ClientSessionState) => ClientSessionState) => {
    const next = updater(states.get(sessionId) ?? createClientSessionState(STORED))
    states.set(sessionId, next)

    return next
  })

  const refresh = () =>
    reconcileActiveTranscript({
      activeSessionIdRef: { current: RUNTIME },
      busyRef: { current: false },
      requestSequenceRef: { current: 0 },
      resolveSession: () => ({ profile: 'default' }),
      selectedStoredSessionIdRef: { current: STORED },
      signatureRef,
      updateSessionState
    })

  const signatureRef = { current: new Map<string, string>() }
  const lastText = () => chatMessageText(states.get(RUNTIME)!.messages.at(-1)!)

  return { lastText, refresh, states, updateSessionState }
}

beforeEach(() => {
  vi.spyOn(document, 'hasFocus').mockReturnValue(true)
})

afterEach(() => {
  cleanup()
  resetLiveSync()
  resetLiveRuntimeTracking()
  $activeSessionId.set(null)
  $selectedStoredSessionId.set(null)
  setBusy(false)
  clearAllSessionStates()
  $sessionTiles.set([])
  setSessionTileDelegate(null as unknown as SessionTileDelegate)
  vi.clearAllMocks()
  vi.restoreAllMocks()
})

describe('sessionMessagesSignature covers display-affecting fields', () => {
  const base = (): SessionMessage => ({ content: '', role: 'assistant', timestamp: 5 })

  it.each([
    ['display_content', { display_content: 'visible reply' }],
    ['codex_message_items', { codex_message_items: [{ content: [{ text: 'sidecar', type: 'output_text' }] }] }],
    ['reasoning', { reasoning: 'thinking' }],
    ['tool_calls', { tool_calls: [{ function: { arguments: '{}', name: 'x' }, id: 'c1' }] }],
    ['display_kind', { display_kind: 'hidden' }]
  ])('changes when only %s changes', (_field, patch) => {
    expect(sessionMessagesSignature([{ ...base(), ...patch } as SessionMessage])).not.toBe(
      sessionMessagesSignature([base()])
    )
  })

  it('is stable for an identical transcript with sidecars', () => {
    const row = () => ({ ...base(), codex_message_items: [{ text: 'a' }], display_content: 'x' }) as SessionMessage
    expect(sessionMessagesSignature([row()])).toBe(sessionMessagesSignature([row()]))
  })
})

describe('long session (>120 messages) DB reconcile', () => {
  it('shows a reply whose visible text only lands in display_content', async () => {
    const fixture = makeRefresh()
    const history = longHistory()
    expect(history.length).toBeGreaterThan(120)

    serve([...history, { content: '', role: 'assistant', timestamp: 999 }])
    await fixture.refresh()

    serve([...history, { content: '', display_content: 'final visible reply', role: 'assistant', timestamp: 999 }])
    await fixture.refresh()

    expect(fixture.lastText()).toBe('final visible reply')
  })

  it('shows a reply whose text only lands in codex_message_items', async () => {
    const fixture = makeRefresh()
    const history = longHistory()

    serve([...history, { content: '', reasoning: 'r', role: 'assistant', timestamp: 999 }])
    await fixture.refresh()

    serve([
      ...history,
      {
        codex_message_items: [
          { content: [{ text: 'sidecar reply', type: 'output_text' }], role: 'assistant', type: 'message' }
        ],
        content: '',
        reasoning: 'r',
        role: 'assistant',
        timestamp: 999
      }
    ])
    await fixture.refresh()

    expect(fixture.lastText()).toContain('sidecar reply')
  })

  it('shows the next reply after context compression rewrites the transcript', async () => {
    const fixture = makeRefresh()
    serve(longHistory())
    await fixture.refresh()

    const compressed: SessionMessage[] = [
      { content: '[summary of earlier conversation]', role: 'user', timestamp: 500 },
      { content: 'question after compression', role: 'user', timestamp: 501 }
    ]

    serve(compressed)
    await fixture.refresh()

    serve([...compressed, { content: 'reply after compression', role: 'assistant', timestamp: 502 }])
    await fixture.refresh()

    expect(fixture.lastText()).toBe('reply after compression')
  })
})

describe('missed completion event (active_list recovery)', () => {
  it('retires the busy claim through the wiring cache and reports the settled runtime', () => {
    publishSessionState(RUNTIME, { ...createClientSessionState(STORED), busy: true })
    const retireBusyClaim = vi.fn(() => true)
    setSessionTileDelegate({ retireBusyClaim } as unknown as SessionTileDelegate)

    expect(
      rehydrateLiveSessionStatuses({ sessions: [{ id: RUNTIME, session_key: STORED, status: 'working' }] })
    ).toEqual([])
    expect(rehydrateLiveSessionStatuses({ sessions: [] })).toEqual([RUNTIME])
    expect(retireBusyClaim).toHaveBeenCalledWith(RUNTIME)
    expect($sessionStates.get()[RUNTIME]?.busy).toBe(false)
  })

  it('reconciles the persisted reply into the open chat with no sessions.changed-driven refresh', async () => {
    // No change-event backend: the sessions.changed list path is inactive, so
    // before the fix nothing ever pulled the final reply from DB.
    $changeEventsAvailable.set(false)
    $activeSessionId.set(RUNTIME)
    $selectedStoredSessionId.set(STORED)
    publishSessionState(RUNTIME, { ...createClientSessionState(STORED), busy: true })
    setBusy(true)

    // Wiring cache stand-in: retiring the claim clears the focused view's busy.
    setSessionTileDelegate({
      retireBusyClaim: () => {
        setBusy(false)
        publishSessionState(RUNTIME, { ...$sessionStates.get()[RUNTIME]!, busy: false })

        return true
      }
    } as unknown as SessionTileDelegate)

    const snapshots = [{ sessions: [{ id: RUNTIME, session_key: STORED, status: 'working' }] }, { sessions: [] }]
    const requestGateway = vi.fn(async () => snapshots.shift() ?? { sessions: [] })
    const refreshActiveTranscript = vi.fn(async () => undefined)

    renderHook(() =>
      useBackgroundSync({
        activeConnectionId: 'local',
        activeGatewayProfile: 'default',
        activeIsMessaging: false,
        activeSessionId: RUNTIME,
        activeStoredSessionId: STORED,
        freshDraftReady: false,
        gatewayState: 'open',
        refreshActiveTranscript,
        refreshCronJobs: vi.fn(),
        refreshCurrentModel: vi.fn(),
        refreshHermesConfig: vi.fn(),
        refreshMessagingSessions: vi.fn(),
        refreshSessions: vi.fn(),
        requestGateway: requestGateway as never,
        updateSessionState: vi.fn() as never
      })
    )

    await waitFor(() => expect(requestGateway).toHaveBeenCalledTimes(1))
    refreshActiveTranscript.mockClear()

    // Next snapshot: the turn is gone (finished while its completion event was lost).
    act(() => notifySessionsChanged())

    await waitFor(() => expect(refreshActiveTranscript).toHaveBeenCalledTimes(1))
    expect($busy.get()).toBe(false)
  })
})
