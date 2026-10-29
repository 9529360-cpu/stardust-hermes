import { act, cleanup, render, waitFor } from '@testing-library/react'
import type { MutableRefObject } from 'react'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $currentBranch,
  $currentCwd,
  $newChatWorkspaceTarget,
  $workspaceCwdOwner,
  setCurrentBranch,
  setCurrentCwd,
  setCurrentCwdTransient,
  setNewChatWorkspaceTarget,
  setSelectedStoredSessionId,
  setWorkspaceCwdOwner
} from '@/store/session'

import { deferred } from '../../../test/deferred'

import { useCwdActions } from './use-cwd-actions'

type CwdActionsHandle = ReturnType<typeof useCwdActions>

function Harness({
  activeSessionIdRef,
  onReady,
  onSessionRuntimeInfo,
  requestGateway,
  selectedStoredSessionIdRef
}: {
  activeSessionIdRef: MutableRefObject<string | null>
  onReady: (handle: CwdActionsHandle) => void
  onSessionRuntimeInfo?: (info: { branch?: string; cwd?: string }) => void
  requestGateway: <T>(method: string, params?: Record<string, unknown>) => Promise<T>
  selectedStoredSessionIdRef: MutableRefObject<string | null>
}) {
  const actions = useCwdActions({
    activeSessionIdRef,
    onSessionRuntimeInfo,
    requestGateway,
    selectedStoredSessionIdRef
  })

  useEffect(() => {
    onReady(actions)
  }, [actions, onReady])

  return null
}

describe('useCwdActions', () => {
  beforeEach(() => {
    setCurrentCwd('')
    setCurrentBranch('')
    setSelectedStoredSessionId(null)
    setNewChatWorkspaceTarget(undefined)
    setWorkspaceCwdOwner(null)
  })

  afterEach(() => {
    cleanup()
    setCurrentCwd('')
    setCurrentBranch('')
    setSelectedStoredSessionId(null)
    setNewChatWorkspaceTarget(undefined)
    setWorkspaceCwdOwner(null)
    vi.restoreAllMocks()
  })

  it('routes a project workspace change to the current session and adopts the backend cwd', async () => {
    const requestGateway = vi.fn(async () => ({ branch: 'feature/projects', cwd: '/normalized/project' }) as never)
    const activeSessionIdRef: MutableRefObject<string | null> = { current: 'runtime-session' }
    const selectedStoredSessionIdRef: MutableRefObject<string | null> = { current: 'stored-session' }
    const onSessionRuntimeInfo = vi.fn()
    setSelectedStoredSessionId('stored-session')
    let handle: CwdActionsHandle | null = null

    render(
      <Harness
        activeSessionIdRef={activeSessionIdRef}
        onReady={value => (handle = value)}
        onSessionRuntimeInfo={onSessionRuntimeInfo}
        requestGateway={requestGateway}
        selectedStoredSessionIdRef={selectedStoredSessionIdRef}
      />
    )
    await waitFor(() => expect(handle).not.toBeNull())

    await act(async () => {
      await handle!.changeSessionCwd('/project')
    })

    expect(requestGateway).toHaveBeenCalledWith('session.cwd.set', {
      cwd: '/project',
      session_id: 'runtime-session'
    })
    expect($currentCwd.get()).toBe('/normalized/project')
    expect($workspaceCwdOwner.get()).toBe('stored-session')
    expect($currentBranch.get()).toBe('feature/projects')
    expect(onSessionRuntimeInfo).toHaveBeenCalledWith({ branch: 'feature/projects', cwd: '/normalized/project' })
    expect($workspaceCwdOwner.get()).toBe('stored-session')
  })

  it('discards a cwd response after the selected session changes', async () => {
    const pending = deferred<{ branch?: string; cwd?: string }>()
    const requestGateway = vi.fn(async () => pending.promise as never)
    const activeSessionIdRef: MutableRefObject<string | null> = { current: 'runtime-before' }
    const selectedStoredSessionIdRef: MutableRefObject<string | null> = { current: 'stored-before' }
    const onSessionRuntimeInfo = vi.fn()
    let handle: CwdActionsHandle | null = null

    setCurrentCwdTransient('/prior')
    setCurrentBranch('prior')
    render(
      <Harness
        activeSessionIdRef={activeSessionIdRef}
        onReady={value => (handle = value)}
        onSessionRuntimeInfo={onSessionRuntimeInfo}
        requestGateway={requestGateway}
        selectedStoredSessionIdRef={selectedStoredSessionIdRef}
      />
    )
    await waitFor(() => expect(handle).not.toBeNull())

    let changing!: Promise<boolean>
    await act(async () => {
      changing = handle!.changeSessionCwd('/stale-project')
    })
    activeSessionIdRef.current = 'runtime-after'
    selectedStoredSessionIdRef.current = 'stored-after'
    pending.resolve({ branch: 'stale-branch', cwd: '/stale-project' })

    await act(async () => {
      await changing
    })

    expect($currentCwd.get()).toBe('/prior')
    expect($workspaceCwdOwner.get()).toBe(null)
    expect($currentBranch.get()).toBe('prior')
    expect(onSessionRuntimeInfo).not.toHaveBeenCalled()
  })

  it('requires a selected stored identity before changing an active runtime cwd', async () => {
    const requestGateway = vi.fn(async () => ({ branch: 'main', cwd: '/wrong-project' }) as never)
    const activeSessionIdRef: MutableRefObject<string | null> = { current: 'runtime-session' }
    const selectedStoredSessionIdRef: MutableRefObject<string | null> = { current: null }
    let handle: CwdActionsHandle | null = null

    setCurrentCwdTransient('/session-project')
    render(
      <Harness
        activeSessionIdRef={activeSessionIdRef}
        onReady={value => (handle = value)}
        requestGateway={requestGateway}
        selectedStoredSessionIdRef={selectedStoredSessionIdRef}
      />
    )
    await waitFor(() => expect(handle).not.toBeNull())

    let changed: boolean | undefined

    await act(async () => {
      changed = await handle!.changeSessionCwd('/wrong-project')
    })

    expect(changed).toBe(false)
    expect(requestGateway).not.toHaveBeenCalled()
    expect($currentCwd.get()).toBe('/session-project')
  })

  it('does not treat a selected session that is still resuming as a new chat', async () => {
    const requestGateway = vi.fn(async () => ({ branch: 'main', cwd: '/wrong-project' }) as never)
    const activeSessionIdRef: MutableRefObject<string | null> = { current: null }
    const selectedStoredSessionIdRef: MutableRefObject<string | null> = { current: 'stored-session' }
    let handle: CwdActionsHandle | null = null

    setCurrentCwdTransient('/session-project')
    render(
      <Harness
        activeSessionIdRef={activeSessionIdRef}
        onReady={value => (handle = value)}
        requestGateway={requestGateway}
        selectedStoredSessionIdRef={selectedStoredSessionIdRef}
      />
    )
    await waitFor(() => expect(handle).not.toBeNull())

    let changed: boolean | undefined

    await act(async () => {
      changed = await handle!.changeSessionCwd('/wrong-project')
    })

    expect(changed).toBe(false)
    expect(requestGateway).not.toHaveBeenCalled()
    expect($currentCwd.get()).toBe('/session-project')
  })

  it('keeps the existing cwd when the backend rejects a workspace change', async () => {
    const requestGateway = vi.fn(async () => {
      throw new Error('4009: session busy')
    })

    const activeSessionIdRef: MutableRefObject<string | null> = { current: 'runtime-session' }
    const selectedStoredSessionIdRef: MutableRefObject<string | null> = { current: 'stored-session' }
    let handle: CwdActionsHandle | null = null

    setCurrentCwdTransient('/original')
    setCurrentBranch('original-branch')
    setSelectedStoredSessionId('stored-session')
    render(
      <Harness
        activeSessionIdRef={activeSessionIdRef}
        onReady={value => (handle = value)}
        requestGateway={requestGateway}
        selectedStoredSessionIdRef={selectedStoredSessionIdRef}
      />
    )
    await waitFor(() => expect(handle).not.toBeNull())

    let changed: boolean | undefined

    await act(async () => {
      changed = await handle!.changeSessionCwd('/new-project')
    })

    expect(changed).toBe(false)
    expect($currentCwd.get()).toBe('/original')
    expect($currentBranch.get()).toBe('original-branch')
  })

  it('keeps the newer workspace request authoritative when an older request fails later', async () => {
    const older = deferred<{ branch?: string; cwd?: string }>()

    const requestGateway = vi
      .fn()
      .mockImplementationOnce(async () => older.promise as never)
      .mockImplementationOnce(async () => ({ branch: 'main', cwd: '/newer-project' }) as never)

    const activeSessionIdRef: MutableRefObject<string | null> = { current: 'runtime-session' }
    const selectedStoredSessionIdRef: MutableRefObject<string | null> = { current: 'stored-session' }
    const onSessionRuntimeInfo = vi.fn()
    const { $notifications, clearNotifications } = await import('@/store/notifications')
    let handle: CwdActionsHandle | null = null

    setSelectedStoredSessionId('stored-session')
    setCurrentCwdTransient('/original')
    render(
      <Harness
        activeSessionIdRef={activeSessionIdRef}
        onReady={value => (handle = value)}
        onSessionRuntimeInfo={onSessionRuntimeInfo}
        requestGateway={requestGateway}
        selectedStoredSessionIdRef={selectedStoredSessionIdRef}
      />
    )
    await waitFor(() => expect(handle).not.toBeNull())

    let olderChange!: Promise<boolean>
    await act(async () => {
      olderChange = handle!.changeSessionCwd('/older-project')
      await handle!.changeSessionCwd('/newer-project')
    })
    expect($currentCwd.get()).toBe('/newer-project')

    clearNotifications()
    older.reject(new Error('session busy'))
    let olderResult: boolean | undefined
    await act(async () => {
      olderResult = await olderChange
    })

    expect(olderResult).toBe(false)
    expect($currentCwd.get()).toBe('/newer-project')
    expect($currentBranch.get()).toBe('main')
    expect($notifications.get()).toHaveLength(0)
    expect(onSessionRuntimeInfo).toHaveBeenCalledTimes(1)
  })

  it('ignores stale draft cwd normalization after a newer no-workspace target wins', async () => {
    const projectInfo = deferred<{ branch?: string; cwd?: string }>()
    const requestGateway = vi.fn(async () => projectInfo.promise as never)
    const activeSessionIdRef: MutableRefObject<string | null> = { current: null }
    const selectedStoredSessionIdRef: MutableRefObject<string | null> = { current: null }
    let handle: CwdActionsHandle | null = null

    render(
      <Harness
        activeSessionIdRef={activeSessionIdRef}
        onReady={h => (handle = h)}
        requestGateway={requestGateway}
        selectedStoredSessionIdRef={selectedStoredSessionIdRef}
      />
    )
    await waitFor(() => expect(handle).not.toBeNull())

    let pendingChange!: Promise<boolean>

    await act(async () => {
      pendingChange = handle!.changeSessionCwd('/stale-workspace')
    })

    expect($newChatWorkspaceTarget.get()).toBe('/stale-workspace')

    setNewChatWorkspaceTarget(null)
    setCurrentCwdTransient('')
    projectInfo.resolve({ branch: 'main', cwd: '/normalized-stale-workspace' })

    await act(async () => {
      await pendingChange
    })

    expect($newChatWorkspaceTarget.get()).toBeNull()
    expect($currentCwd.get()).toBe('')
    expect($currentBranch.get()).toBe('')
  })
})
