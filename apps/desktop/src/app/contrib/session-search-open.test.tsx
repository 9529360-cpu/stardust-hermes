import { useStore } from '@nanostores/react'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useRef, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearSingleFlightSessionResumeState } from '@/app/session/hooks/use-prompt-actions/single-flight-resume'
import { useRouteResume } from '@/app/session/hooks/use-route-resume'
import { useSessionActions } from '@/app/session/hooks/use-session-actions'
import type { ClientSessionState } from '@/app/types'
import { revealTreePane } from '@/components/pane-shell/tree/store'
import { getLatestSessionMessages, getSession, type SessionInfo } from '@/hermes'
import { createClientSessionState } from '@/lib/chat-runtime'
import { requestGatewayForAgent, requestGatewayForProfile } from '@/store/gateway'
import { $activeGatewayProfile } from '@/store/profile'
import {
  $activeSessionId,
  $messages,
  $resumeFailedSessionId,
  $selectedStoredSessionId,
  $sessionResumeRequest,
  $sessionSelectionOwner,
  $sessions,
  _resetSessionOwnerHintsForTests,
  setConnection,
  setMessages,
  setSessionOwnerHint
} from '@/store/session'
import { $sessionTiles } from '@/store/session-states'

import { createSessionRpcDispatcher } from './session-rpc-dispatcher'
import { openSessionSearchResult, retrySessionSelection } from './session-search-open'

vi.mock('@/hermes', async original => ({
  ...(await original<Record<string, unknown>>()),
  getSession: vi.fn(),
  getLatestSessionMessages: vi.fn()
}))
vi.mock('@/store/profile', async original => ({
  ...(await original<Record<string, unknown>>()),
  ensureGatewayAgent: vi.fn().mockResolvedValue(undefined),
  ensureGatewayProfile: vi.fn().mockResolvedValue(undefined)
}))
vi.mock('@/store/gateway', async original => ({
  ...(await original<Record<string, unknown>>()),
  requestGatewayForAgent: vi.fn(),
  requestGatewayForProfile: vi.fn(),
  retainGatewayForAgent: vi.fn(async () => () => undefined)
}))
vi.mock('@/components/pane-shell/tree/store', async original => ({
  ...(await original<Record<string, unknown>>()),
  revealTreePane: vi.fn(),
  noteActiveTreeGroup: vi.fn()
}))

const DEFAULT_OWNER = { connectionId: 'local', profile: 'default' }
const WORK_OWNER = { connectionId: 'local', profile: 'work' }

const SESSION: SessionInfo = {
  id: 'same-stored-id',
  connection_id: 'local',
  profile: 'work',
  title: 'Work conversation',
  preview: 'work-only-body',
  source: 'desktop',
  started_at: 1,
  last_active: 1,
  ended_at: null,
  is_active: false,
  model: null,
  message_count: 1,
  tool_call_count: 0,
  input_tokens: 0,
  output_tokens: 0
}

function useOpenHarness(warm = false) {
  const [pathname, setPathname] = useState('/command-center')
  const request = useStore($sessionResumeRequest)
  const selected = useStore($selectedStoredSessionId)
  const active = useStore($activeSessionId)
  const activeRef = useRef<string | null>(warm ? 'default-runtime' : null)
  const selectedRef = useRef<string | null>(SESSION.id)
  const runtimeMap = useRef(new Map(warm ? [[SESSION.id, 'default-runtime']] : []))
  const stateMap = useRef(new Map<string, ClientSessionState>())
  const busyRef = useRef(false)
  const creatingRef = useRef(false)
  const initial = useRef(false)

  if (!initial.current) {
    initial.current = true

    if (warm) {
      stateMap.current.set('default-runtime', {
        ...createClientSessionState(SESSION.id),
        messages: [
          {
            id: 'default-message',
            role: 'assistant',
            parts: [{ type: 'text', text: 'default-only-body' }]
          }
        ]
      })
    }
  }

  const actions = useSessionActions({
    activeSessionId: active,
    activeSessionIdRef: activeRef,
    busyRef,
    creatingSessionRef: creatingRef,
    ensureSessionState: () => createClientSessionState(SESSION.id),
    getRouteToken: () => 'search-open',
    getRoutedStoredSessionId: () => (pathname === '/command-center' ? null : SESSION.id),
    navigate: setPathname as never,
    requestGateway: vi.fn(async () => ({})) as never,
    resetViewSync: () => {},
    runtimeIdByStoredSessionIdRef: runtimeMap,
    selectedStoredSessionId: selected,
    selectedStoredSessionIdRef: selectedRef,
    sessionStateByRuntimeIdRef: stateMap,
    syncSessionStateToView: (_id, state) => setMessages(state.messages),
    updateSessionState: (id, update, storedId) => {
      const next = update(stateMap.current.get(id) ?? createClientSessionState(storedId ?? null))
      stateMap.current.set(id, next)
      setMessages(next.messages)

      return next
    }
  })

  useRouteResume({
    activeSessionId: active,
    activeSessionIdRef: activeRef,
    creatingSessionRef: creatingRef,
    currentView: pathname === '/command-center' ? 'command-center' : 'chat',
    freshDraftReady: false,
    gatewayState: 'open',
    locationPathname: pathname,
    resumeSession: actions.resumeSession,
    resumeFailedSessionId: null,
    resumeExhaustedSessionId: null,
    sessionResumeRequest: request,
    routedSessionId: pathname === '/command-center' ? null : SESSION.id,
    runtimeIdByStoredSessionIdRef: runtimeMap,
    selectedStoredSessionId: selected,
    selectedStoredSessionIdRef: selectedRef,
    startFreshSessionDraft: () => {}
  })

  const dispatch = createSessionRpcDispatcher({
    ambientRequest: vi.fn(async () => ({})) as never,
    runtimeIdByStoredSessionIdRef: runtimeMap,
    selectedStoredSessionIdRef: selectedRef,
    sessionStateByRuntimeIdRef: stateMap
  })

  return Object.assign((session = SESSION) => openSessionSearchResult(session, setPathname), {
    send: (text: string) => dispatch('prompt.submit', { session_id: activeRef.current, text }),
    retry: () => retrySessionSelection(SESSION.id, actions.resumeSession),
    resumeDefault: () => actions.resumeSession(SESSION.id, true)
  })
}

beforeEach(() => {
  clearSingleFlightSessionResumeState()
  vi.clearAllMocks()
  _resetSessionOwnerHintsForTests()
  setConnection({ mode: 'local', connectionId: 'local' } as never)
  $activeGatewayProfile.set('work')
  $sessions.set([SESSION])
  $selectedStoredSessionId.set(SESSION.id)
  $activeSessionId.set(null)
  $sessionResumeRequest.set(null)
  $sessionSelectionOwner.set(null)
  $resumeFailedSessionId.set(null)
  $sessionTiles.set([])
  setMessages([])
  setSessionOwnerHint(SESSION.id, DEFAULT_OWNER)
  vi.mocked(getSession).mockImplementation(async (_id, scope) => ({
    ...SESSION,
    profile: typeof scope === 'object' ? (scope?.profile ?? 'default') : (scope ?? 'default')
  }))
  vi.mocked(getLatestSessionMessages).mockImplementation(
    async (_id, scope) =>
      ({
        session_id: SESSION.id,
        messages: [
          {
            role: 'assistant',
            content: `${typeof scope === 'object' ? scope?.profile : scope}-only-body`,
            timestamp: 1
          }
        ]
      }) as never
  )
  vi.mocked(requestGatewayForAgent).mockImplementation(async (_connection, profile, method) => {
    if (method === 'session.activate') {
      throw new Error('404: Session not found')
    }

    return { session_id: `${profile}-runtime`, resumed: SESSION.id, messages: [], info: {}, running: false } as never
  })
})

afterEach(() => {
  cleanup()
  $sessionTiles.set([])
  $sessions.set([])
  $messages.set([])
  $sessionResumeRequest.set(null)
  $sessionSelectionOwner.set(null)
  $resumeFailedSessionId.set(null)
  $selectedStoredSessionId.set(null)
  $activeSessionId.set(null)
  _resetSessionOwnerHintsForTests()
  setConnection(null)
  $activeGatewayProfile.set('default')
})

describe('search result opens through the row owner', () => {
  it('captures the clicked search row owner for sidebar highlighting', () => {
    openSessionSearchResult(SESSION, () => {})

    expect($sessionSelectionOwner.get()).toMatchObject({
      owner: { connectionId: 'local', profile: 'work' },
      sessionId: SESSION.id
    })
  })

  it.each([
    ['exact', 'default-first'],
    ['exact', 'work-first'],
    ['legacy', 'default-first'],
    ['legacy', 'work-first']
  ] as const)('does not join a pending default resume (%s scope, %s completion)', async (scope, first) => {
    if (scope === 'legacy') {
      setConnection({ mode: 'remote', baseUrl: 'https://legacy.example.test' } as never)
      vi.mocked(getSession).mockImplementation(async (_id, owner) => ({
        ...SESSION,
        connection_id: undefined,
        profile: typeof owner === 'object' ? owner?.profile ?? 'default' : owner ?? 'default'
      }))
    }

    const row = scope === 'legacy' ? { ...SESSION, connection_id: undefined } : SESSION
    const pending = new Map<string, (value: never) => void>()

    const resume = (profile: string, method: string) => {
      if (method === 'session.resume') {
        return new Promise<never>(resolve => { pending.set(profile, resolve) })
      }

      return Promise.resolve({ accepted: true } as never)
    }

    vi.mocked(requestGatewayForAgent).mockImplementation((_connection, profile, method) => resume(profile, method))
    vi.mocked(requestGatewayForProfile).mockImplementation((profile, method) => resume(profile, method))
    const { result } = renderHook(() => useOpenHarness())
    let defaultFlight!: Promise<void>
    act(() => { defaultFlight = result.current.resumeDefault() })
    await waitFor(() => expect(pending.has('default')).toBe(true))
    act(() => result.current(row))
    await waitFor(() => expect(pending.has('work')).toBe(true), { timeout: 1000 })

    const finish = async (profile: string) => {
      await act(async () => {
        pending.get(profile)!({
          session_id: `${profile}-runtime`, resumed: SESSION.id, messages: [], info: {}, running: false
        } as never)
      })
    }

    if (first === 'default-first') {
      await finish('default')
      await finish('work')
    } else {
      await finish('work')
      await finish('default')
    }

    await defaultFlight
    await waitFor(() => expect($activeSessionId.get()).toBe('work-runtime'))
    expect(JSON.stringify($messages.get())).toContain('work-only-body')
    expect(JSON.stringify($messages.get())).not.toContain('default-only-body')
    await act(async () => { await result.current.send('work-continuation') })

    const calls = scope === 'legacy'
      ? vi.mocked(requestGatewayForProfile).mock.calls.map(call => [call[0], call[1], call[2]])
      : vi.mocked(requestGatewayForAgent).mock.calls.map(call => [call[1], call[2], call[3]])

    expect(calls).toContainEqual(['work', 'prompt.submit', { session_id: 'work-runtime', text: 'work-continuation' }])
    expect(calls.some(call => call[0] === 'default' && call[1] === 'prompt.submit')).toBe(false)
  })

  it('does not publish stale legacy metadata after selecting the same-id work conversation', async () => {
    setConnection({ mode: 'remote', baseUrl: 'https://legacy.example.test' } as never)
    $sessions.set([])
    let releaseDefault!: (value: SessionInfo) => void
    vi.mocked(getSession).mockImplementation((_id, owner) => {
      if (owner === 'default') {
        return new Promise(resolve => { releaseDefault = resolve })
      }

      return Promise.resolve({ ...SESSION, connection_id: undefined, profile: 'work' })
    })
    vi.mocked(requestGatewayForProfile).mockImplementation(async profile => ({
      session_id: `${profile}-runtime`, resumed: SESSION.id, messages: [], info: {}, running: false
    }) as never)
    const { result } = renderHook(() => useOpenHarness())
    act(() => result.current({ ...SESSION, connection_id: undefined, profile: 'default' }))
    await waitFor(() => expect(releaseDefault).toBeTypeOf('function'))
    act(() => result.current({ ...SESSION, connection_id: undefined }))
    await waitFor(() => expect($activeSessionId.get()).toBe('work-runtime'))
    await act(async () => {
      releaseDefault({ ...SESSION, connection_id: undefined, profile: 'default' })
    })
    expect(JSON.stringify($messages.get())).toContain('work-only-body')
    await act(async () => { await result.current.send('after-stale-metadata') })
    expect(requestGatewayForProfile).toHaveBeenCalledWith(
      'work', 'prompt.submit',
      { session_id: 'work-runtime', text: 'after-stale-metadata' }, undefined, undefined
    )
    expect(vi.mocked(requestGatewayForProfile).mock.calls.some(call =>
      call[0] === 'default' && call[1] === 'prompt.submit'
    )).toBe(false)
  })

  it('loads work body despite a unique persisted default hint for the same stored id', async () => {
    const { result } = renderHook(() => useOpenHarness())
    act(() => result.current())
    await waitFor(() => expect(JSON.stringify($messages.get())).toContain('work-only-body'))
    expect(JSON.stringify($messages.get())).not.toContain('default-only-body')
    expect(requestGatewayForAgent).toHaveBeenCalledWith('local', 'work', 'session.resume', expect.anything())
    act(() => result.current({ ...SESSION, profile: 'default' }))
    await waitFor(() => expect(JSON.stringify($messages.get())).toContain('default-only-body'))
    expect(JSON.stringify($messages.get())).not.toContain('work-only-body')
  })

  it('does not focus or reassign an already-open default tile when opening the work twin', async () => {
    const defaultTile = {
      storedSessionId: SESSION.id,
      workspaceMode: 'bots' as const,
      workspaceOwnerKey: 'default-owner',
      ownerRoute: DEFAULT_OWNER
    }

    $sessionTiles.set([defaultTile])
    $activeSessionId.set('default-runtime')
    setMessages([{ id: 'default-message', role: 'assistant', parts: [{ type: 'text', text: 'default-only-body' }] }])
    const publishedTileScopes: string[] = []

    const unlisten = $sessionTiles.listen(tiles => {
      publishedTileScopes.push(...tiles.map(tile => tile.workspaceMode || 'sessions'))
    })

    const { result } = renderHook(() => useOpenHarness(true))
    act(() => result.current())
    await waitFor(() => expect(JSON.stringify($messages.get())).toContain('work-only-body'))
    unlisten()
    expect(JSON.stringify($messages.get())).not.toContain('default-only-body')
    expect(publishedTileScopes).not.toContain('sessions')
    expect(defaultTile.ownerRoute).toEqual(DEFAULT_OWNER)
    expect(defaultTile.workspaceMode).toBe('bots')
    expect(revealTreePane).not.toHaveBeenCalledWith(`session-tile:${SESSION.id}`)
    expect(revealTreePane).toHaveBeenCalledWith('workspace')
  })

  it('keeps a profile-only search selection on a legacy remote without inventing a local connection', async () => {
    setConnection({ mode: 'remote', baseUrl: 'https://legacy.example.test' } as never)
    $sessions.set([{ ...SESSION, connection_id: undefined, profile: 'default' }])
    vi.mocked(getSession).mockResolvedValue({ ...SESSION, connection_id: undefined })
    vi.mocked(requestGatewayForProfile).mockImplementation(
      async profile =>
        ({
          session_id: `${profile}-runtime`,
          resumed: SESSION.id,
          messages: [],
          info: {},
          running: false
        }) as never
    )
    $activeSessionId.set('default-runtime')
    setMessages([{ id: 'legacy-default', role: 'assistant', parts: [{ type: 'text', text: 'default-only-body' }] }])
    const { result } = renderHook(() => useOpenHarness(true))
    act(() => result.current({ ...SESSION, connection_id: undefined }))
    await waitFor(() => expect(JSON.stringify($messages.get())).toContain('work-only-body'), { timeout: 1000 })
    expect(JSON.stringify($messages.get())).not.toContain('default-only-body')
    expect(getSession).toHaveBeenCalledWith(SESSION.id, 'work')
    expect(getLatestSessionMessages).toHaveBeenCalledWith(SESSION.id, 'work')
    expect(requestGatewayForProfile).toHaveBeenCalledWith(
      'work',
      'session.resume',
      expect.anything(),
      undefined,
      undefined
    )
    expect(requestGatewayForAgent).not.toHaveBeenCalled()
    await act(async () => {
      await result.current.send('legacy-continuation')
    })
    expect(requestGatewayForProfile).toHaveBeenCalledWith(
      'work',
      'prompt.submit',
      { session_id: 'work-runtime', text: 'legacy-continuation' },
      undefined,
      undefined
    )
    expect(vi.mocked(requestGatewayForProfile).mock.calls.every(call => call[0] === 'work')).toBe(true)
    expect(vi.mocked(requestGatewayForProfile).mock.calls.some(call => call[1] === 'session.activate')).toBe(false)
    act(() => result.current({ ...SESSION, connection_id: undefined, profile: 'default' }))
    await waitFor(() => expect(JSON.stringify($messages.get())).toContain('default-only-body'))
    expect(JSON.stringify($messages.get())).not.toContain('work-only-body')
    expect(requestGatewayForAgent).not.toHaveBeenCalled()
  })

  it('fails closed on the selected legacy profile instead of probing or painting the cached twin', async () => {
    setConnection({ mode: 'remote', baseUrl: 'https://legacy.example.test' } as never)
    $sessions.set([{ ...SESSION, connection_id: undefined, profile: 'default' }])
    vi.mocked(getSession).mockRejectedValue(new Error('404: missing work metadata'))
    vi.mocked(getLatestSessionMessages).mockRejectedValue(new Error('404: missing work history'))
    vi.mocked(requestGatewayForProfile).mockRejectedValue(new Error('404: missing work session'))
    const { result } = renderHook(() => useOpenHarness(true))
    act(() => result.current({ ...SESSION, connection_id: undefined }))
    await waitFor(() => expect($resumeFailedSessionId.get()).toBe(SESSION.id))
    expect(getSession).toHaveBeenCalledTimes(1)
    expect(getSession).toHaveBeenCalledWith(SESSION.id, 'work')
    expect(vi.mocked(getLatestSessionMessages).mock.calls.every(call => call[1] === 'work')).toBe(true)
    expect(vi.mocked(requestGatewayForProfile).mock.calls.every(call => call[0] === 'work')).toBe(true)
    expect(requestGatewayForAgent).not.toHaveBeenCalled()
    expect(JSON.stringify($messages.get())).not.toContain('default-only-body')
    vi.mocked(getSession).mockResolvedValue({ ...SESSION, connection_id: undefined })
    vi.mocked(getLatestSessionMessages).mockResolvedValue({
      session_id: SESSION.id,
      messages: [{ role: 'assistant', content: 'work-retried-body', timestamp: 1 }]
    } as never)
    vi.mocked(requestGatewayForProfile).mockResolvedValue({
      session_id: 'work-retried-runtime',
      resumed: SESSION.id,
      messages: [],
      info: {},
      running: false
    } as never)
    act(() => result.current.retry())
    await waitFor(() => expect(JSON.stringify($messages.get())).toContain('work-retried-body'))
    expect(vi.mocked(getSession).mock.calls.every(call => call[1] === 'work')).toBe(true)
    expect(vi.mocked(requestGatewayForProfile).mock.calls.every(call => call[0] === 'work')).toBe(true)
    expect(requestGatewayForAgent).not.toHaveBeenCalled()
  })

  it('keeps a stamped remote owner through REST and gateway calls', async () => {
    const { result } = renderHook(() => useOpenHarness())
    act(() => result.current({ ...SESSION, connection_id: 'remote-research' }))
    await waitFor(() => expect(JSON.stringify($messages.get())).toContain('work-only-body'))
    expect(requestGatewayForAgent).toHaveBeenCalledWith('remote-research', 'work', 'session.resume', expect.anything())
    expect(getLatestSessionMessages).toHaveBeenCalledWith(SESSION.id, {
      connectionId: 'remote-research',
      profile: 'work'
    })
  })
})
